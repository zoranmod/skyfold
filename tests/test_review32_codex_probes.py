"""Codex review 32 probes (of 57030ea), kept as regression tests. The characterize_* cases now
check the corrected behaviour (fixed_*), or the accepted one (known_*)."""
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from skyfold import config, drive_engine as de
from tests.test_drive import env as drive_env, write
from tests.test_notes import env as notes_env
from tests.test_review28_codex_probes import deny_lookup
from tests.test_review31_codex_probes import private_paths, live_wal_copy


def pending_shell(fixture, direction):
    s, cloud, root, _ = fixture
    s.delete_threshold = 1
    paths = ["Trip/Sub/a.txt", "Trip/Sub/b.txt"]
    for p in paths:
        cloud.put(p, b"ORIGINAL")
    s.run()
    if direction == "cloud-to-pc":
        write(root, "Trip/Sub/desktop.ini", b"LOCAL ONLY")
        cloud.trash(cloud._item("Trip"))
    else:
        cloud.put("Trip/Sub/desktop.ini", b"CLOUD ONLY")
        shutil.rmtree(root / "Trip")
    res = s.run()
    pending = res.pending_to_pc if direction == "cloud-to-pc" else res.pending_to_cloud
    assert set(pending) == set(paths)
    return s, cloud, root, paths


@pytest.mark.parametrize("direction", ["cloud-to-pc", "pc-to-cloud"])
def test_keep_restores_children_in_both_directions(drive_env, direction):
    from skyfold.worker import SyncWorker
    s, cloud, root, paths = pending_shell(drive_env, direction)
    notices = []
    # Invoke the real worker action with its offline drive/state and a fake signal.
    worker = SimpleNamespace(_pending=None, _drive_pending=object(), drive=s, drive_state=s.state,
                             pendingChanged=SimpleNamespace(emit=lambda value: notices.append(value)),
                             _kick_drive=lambda **_kw: s.run())
    SyncWorker.resolve_pending(worker, "reject", paths)
    for _ in range(4):
        after = s.run()
    assert notices == [None] and not after.pending_to_pc and not after.pending_to_cloud
    for p in paths:
        assert cloud.items[p]["data"] == b"ORIGINAL"
        assert (root / p).read_bytes() == b"ORIGINAL"
    assert not s.state.meta_items("shell:")


def test_folder_to_file_replacement_must_not_forget_pending_children(drive_env):
    s, cloud, root, _ = drive_env
    s.delete_threshold = 1
    paths = ["Trip/a.txt", "Trip/b.txt"]
    for p in paths:
        cloud.put(p, b"ORIGINAL")
    s.run()
    cloud.trash(cloud._item("Trip"))
    cloud.put("Trip", b"REPLACEMENT FILE")
    first = s.run()
    assert set(first.pending_to_pc) == set(paths)
    assert cloud.items["Trip"]["data"] == b"REPLACEMENT FILE"
    # Remove the conflicting cloud file before answering the pending prompt.
    cloud.trash(cloud._item("Trip"))
    s.run()
    s.run()
    assert not set(paths).intersection(cloud.items), cloud.uploads


def test_case_rename_does_not_release_pending_children(drive_env):
    s, cloud, root, paths = pending_shell(drive_env, "cloud-to-pc")
    (root / "Trip").rename(root / "temporary")
    (root / "temporary").rename(root / "TRIP")
    for _ in range(3):
        s.run()
    assert all((root / p).read_bytes() == b"ORIGINAL" for p in paths)
    assert not cloud.items, cloud.uploads


def test_unrelated_local_lookalike_cannot_bypass_cloud_deletion_confirmation(drive_env):
    s, cloud, root, _ = drive_env
    s.delete_threshold = 1
    names = ("a.txt", "b.txt")
    for name in names:
        cloud.put(f"Trip/{name}", b"ORIGINAL")
    s.run()
    (root / "Trip").rename(root.parent / "my-original-backup")
    for name in names:
        write(root, f"Different/{name}", b"NEW-WORK")
    first = s.run()
    s.run()
    assert all((root.parent / "my-original-backup" / name).read_bytes() == b"ORIGINAL"
               for name in names)
    assert first.pending_to_cloud == ["Trip"], (cloud.renamed, cloud.trashed, cloud.uploads)
    assert all(cloud.items[f"Trip/{name}"]["data"] == b"ORIGINAL" for name in names)


def test_unrelated_local_lookalike_file_cannot_bypass_cloud_deletion_confirmation(drive_env):
    s, cloud, root, _ = drive_env
    s.delete_threshold = 1
    cloud.put("a.txt", b"ORIGINAL")
    cloud.put("b.txt", b"B")
    s.run()
    original_mtime = (root / "a.txt").stat().st_mtime_ns
    for name in ("a.txt", "b.txt"):
        (root / name).rename(root.parent / name)
    replacement = write(root, "different.txt", b"NEW-WORK")
    os.utime(replacement, ns=(original_mtime, original_mtime))
    following = s.run()
    assert (root.parent / "a.txt").read_bytes() == b"ORIGINAL"
    assert set(following.pending_to_cloud) == {"a.txt", "b.txt"}, (cloud.renamed, cloud.trashed)
    assert cloud.items["a.txt"]["data"] == b"ORIGINAL" and cloud.items["b.txt"]["data"] == b"B"


@pytest.mark.parametrize("direction", ["cloud-to-pc", "pc-to-cloud"])
def test_late_approval_does_not_delete_new_empty_folder(drive_env, direction):
    s, cloud, root, trash = drive_env
    s.delete_threshold = 1
    for p in ("Trip/a.txt", "Trip/b.txt"):
        cloud.put(p, b"ORIGINAL")
    s.run()
    if direction == "cloud-to-pc":
        cloud.trash(cloud._item("Trip"))
        pending = s.run().pending_to_pc
        assert pending == ["Trip"]
        (root / "Trip/new empty").mkdir()
    else:
        shutil.rmtree(root / "Trip")
        pending = s.run().pending_to_cloud
        assert pending == ["Trip"]
        cloud.put("Trip/new empty", None)
    s.approved.update(pending)
    s.run()
    if direction == "cloud-to-pc":
        assert (root / "Trip/new empty").is_dir(), trash
    else:
        assert "Trip/new empty" in cloud.items, cloud.trashed


def test_same_pass_cloud_approval_stops_when_folder_changes(drive_env):
    s, cloud, root, _ = drive_env
    s.delete_threshold = 1
    for p in ("Trip/a.txt", "Trip/b.txt"):
        cloud.put(p, b"ORIGINAL")
    s.run()
    shutil.rmtree(root / "Trip")
    notices = []

    def approve_after_new_cloud_folder(cloud_paths, pc_paths):
        notices.append((cloud_paths, pc_paths))
        cloud.put("Trip/new empty", None)
        s.approved.update(cloud_paths)

    s.on_pending = approve_after_new_cloud_folder
    s.run()
    assert notices == [(["Trip"], [])]
    assert not cloud.trashed
    assert cloud.items["Trip/a.txt"]["data"] == b"ORIGINAL"
    assert "Trip/new empty" in cloud.items


@pytest.mark.parametrize("prompted_side", ["pc", "cloud"])
def test_drive_approval_cannot_switch_to_opposite_side(drive_env, prompted_side):
    from skyfold.worker import SyncWorker

    s, cloud, root, local_trash = drive_env
    s.delete_threshold = 1
    paths = ["Trip/a.txt", "Trip/b.txt"]
    for p in paths:
        cloud.put(p, b"ORIGINAL")
    s.run()
    backup = root.parent / "local-backup"
    if prompted_side == "pc":
        cloud.trash(cloud._item("Trip"))
        pending = s.run()
        assert pending.pending_to_pc == ["Trip"] and not pending.pending_to_cloud
        for p in paths:
            cloud.put(p, b"ORIGINAL")
        (root / "Trip").rename(backup)
        cloud.trashed.clear()
    else:
        (root / "Trip").rename(backup)
        pending = s.run()
        assert pending.pending_to_cloud == ["Trip"] and not pending.pending_to_pc
        backup.rename(root / "Trip")
        cloud.trash(cloud._item("Trip"))
    worker = SimpleNamespace(
        _pending=None, _drive_pending=SimpleNamespace(to_pc=pending.pending_to_pc,
                                                      to_cloud=pending.pending_to_cloud),
        drive=s, drive_state=s.state, pendingChanged=SimpleNamespace(emit=lambda _value: None),
        _kick_drive=lambda **_opts: s.run(),
    )
    SyncWorker.resolve_pending(worker, "approve", ["Trip"])
    if prompted_side == "pc":
        assert not cloud.trashed
        assert all(cloud.items[p]["data"] == b"ORIGINAL" for p in paths)
    else:
        assert not local_trash
        assert all((root / p).read_bytes() == b"ORIGINAL" for p in paths)


def test_fixed_unreadable_kept_subtree_is_kept(drive_env, monkeypatch):
    s, cloud, root, _ = drive_env
    p = "Trip/Sub/Deep"
    cloud.put(p + "/a.txt", b"SYNCED")
    s.run()
    write(root, p + "/desktop.ini", b"LOCAL ONLY")
    cloud.trash(cloud._item("Trip"))
    s.run()
    s.run()
    assert p in s.state.get_meta("shell:pc:Trip")
    assert not cloud.items and (root / p).is_dir()
    scandir = os.scandir

    def inaccessible(candidate):
        if Path(candidate) == root / "Trip/Sub":
            raise PermissionError(13, "synthetic enumeration failure", str(candidate))
        return scandir(candidate)

    with monkeypatch.context() as m:
        m.setattr(os, "scandir", inaccessible)
        s.run()
    assert p in s.state.get_meta("shell:pc:Trip")
    for _ in range(3):
        s.run()
    assert p not in cloud.items and "Trip" not in cloud.items
    assert p + "/desktop.ini" not in cloud.items


def test_fixed_move_keeps_an_unowned_empty_destination(drive_env, monkeypatch):
    s, cloud, root, _ = drive_env
    cloud.put("Old/a.txt", b"SYNCED")
    s.run()
    cloud.rename(cloud._item("Old"), "New")
    original = s._local_move

    def destination_appears(old, new, result):
        if new == "New":  # (only for the folder move: the file-level fallback is left as it is)
            (root / new).mkdir(exist_ok=True)
            made.append(os.stat(root / new).st_ino)
        # Creating the destination after the scan but before the real move models a concurrent app.
        return original(old, new, result)

    made = []
    monkeypatch.setattr(s, "_local_move", destination_appears)
    s.run()
    monkeypatch.setattr(s, "_local_move", original)
    s.run()
    # the folder someone made there is never removed to make room: the file goes into it instead
    assert os.stat(root / "New").st_ino == made[0]
    assert (root / "New/a.txt").read_bytes() == b"SYNCED" and not (root / "Old").exists()


def test_fixed_interrupted_move_without_identity_is_held(drive_env):
    s, cloud, root, _ = drive_env
    cloud.put("Old.txt", b"ORIGINAL")
    s.run()
    cloud.rename(cloud._item("Old.txt"), "New.txt")
    ident = cloud._item("New.txt").docwsid
    with s.state.transaction():
        s._journal_put("pc:" + ident, {"kind": "pc", "old": "Old.txt", "new": "New.txt", "fid": None})
    # Power loss before the local rename, then another program creates the destination.
    write(root, "New.txt", b"DIFFERENT")
    assert (root / "Old.txt").read_bytes() == b"ORIGINAL"
    s.run()
    assert s._journal()  # can't be told apart: kept written down, both names left alone
    assert "Old.txt" not in cloud.items and cloud.items["New.txt"]["data"] == b"ORIGINAL"
    assert not cloud.trashed and (root / "New.txt").read_bytes() == b"DIFFERENT"
    assert (root / "Old.txt").read_bytes() == b"ORIGINAL"


@pytest.mark.parametrize("journal", ["wal-without-shm", "wal-with-shm", "rollback"])
def test_healthy_legacy_database_modes_migrate_without_prompt(notes_env, tmp_path, monkeypatch, journal):
    source, private = private_paths(monkeypatch, tmp_path)
    src = source / config.DATABASE
    dst = private / config.DATABASE
    if journal == "wal-without-shm":
        live_wal_copy(tmp_path, src)
    elif journal == "wal-with-shm":
        live = tmp_path / "live.sqlite3"
        with closing(sqlite3.connect(live)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA wal_autocheckpoint=0")
            conn.execute("CREATE TABLE test_state (value TEXT)")
            conn.execute("INSERT INTO test_state VALUES ('committed in WAL')")
            conn.commit()
            for suffix in ("", "-wal", "-shm"):
                shutil.copy2(str(live) + suffix, str(src) + suffix)
    else:
        with closing(sqlite3.connect(src)) as conn:
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("CREATE TABLE test_state (value TEXT)")
            conn.execute("INSERT INTO test_state VALUES ('committed in WAL')")
            conn.commit()
    before = {f.name:f.read_bytes() for f in source.iterdir()}
    config.migrate_private_files()
    assert not config.MIGRATION_PROBLEMS
    with closing(sqlite3.connect(dst)) as conn:
        assert conn.execute("SELECT value FROM test_state").fetchone() == ("committed in WAL",)
    after = {f.name:f.read_bytes() for f in source.iterdir()}
    assert before.keys() == after.keys()
    assert all(after[name] == data for name, data in before.items() if not name.endswith("-shm"))


def test_fixed_hot_rollback_journal_is_recovered_in_a_private_copy(notes_env, tmp_path, monkeypatch):
    source, private = private_paths(monkeypatch, tmp_path)
    src = source / config.DATABASE
    code = """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA journal_mode=DELETE')
db.execute('PRAGMA cache_size=4')
db.execute('CREATE TABLE test_state (value BLOB)')
db.executemany('INSERT INTO test_state VALUES (?)', [(b'a'*8000,)] * 16)
db.commit()
db.execute('UPDATE test_state SET value=?', (b'b'*8000,))
os._exit(89)
"""
    child = subprocess.run([sys.executable, "-c", code, str(src)], check=False, timeout=12)
    assert child.returncode == 89
    journal = Path(str(src) + "-journal")
    assert journal.exists() and journal.stat().st_size > 1024
    before = {f.name: f.read_bytes() for f in source.iterdir()}
    config.migrate_private_files()
    assert not config.MIGRATION_PROBLEMS
    assert {f.name: f.read_bytes() for f in source.iterdir()} == before  # the earlier files untouched
    with closing(sqlite3.connect(private / config.DATABASE)) as db:
        assert db.execute("SELECT substr(value, 1, 1) FROM test_state LIMIT 1").fetchone() == (b"a",)


def test_fixed_not_ready_legacy_database_is_reported(notes_env, tmp_path, monkeypatch):
    source, private = private_paths(monkeypatch, tmp_path)
    legacy = source / config.DATABASE
    live_wal_copy(tmp_path, legacy)
    original = legacy.read_bytes()
    with monkeypatch.context() as m:
        deny_lookup(m, legacy, winerror=21)
        config.migrate_private_files()
    assert config.MIGRATION_PROBLEMS
    assert legacy.read_bytes() == original
    assert not (private / config.DATABASE).exists()
    config.migrate_private_files()
    assert not config.MIGRATION_PROBLEMS and (private / config.DATABASE).is_file()


@pytest.mark.parametrize("answer", ["Yes", "No"])
def test_orphan_wal_must_survive_start_if_preservation_fails(notes_env, tmp_path, monkeypatch, answer):
    from skyfold import app
    from skyfold.state import State
    source, private = private_paths(monkeypatch, tmp_path)
    legacy = source / config.DATABASE
    live_wal_copy(tmp_path, legacy)
    orphan = private / (config.DATABASE + "-wal")
    (source / (config.DATABASE + "-wal")).rename(orphan)
    legacy.unlink()
    original = orphan.read_bytes()
    rename = os.rename

    def blocked(old, new, *args, **kwargs):
        if Path(old) == orphan:
            raise PermissionError("synthetic rename refusal")
        return rename(old, new, *args, **kwargs)

    class ShellApp:
        def __init__(self, _argv):
            pass

        setHighDpiScaleFactorRoundingPolicy = staticmethod(lambda _policy: None)
        setApplicationName = setQuitOnLastWindowClosed = setStyle = lambda *_args: None
        exec = lambda self: 0

    class LocalServer:
        removeServer = staticmethod(lambda _name: None)
        listen = lambda self, _name: True
        newConnection = SimpleNamespace(connect=lambda _callback: None)

    class OfflineController:
        def __init__(self, _shell):
            pass

        def start(self, *, show):
            state = State(private / config.DATABASE)
            state.set("synthetic", "initialized")

    def copy_blocked(src, dst):
        if Path(src) == orphan:
            raise OSError("synthetic copy failure")
        return copy_new(src, dst)

    copy_new = config._copy_new
    monkeypatch.setattr(config.os, "rename", blocked)
    monkeypatch.setattr(config, "_copy_new", copy_blocked)
    monkeypatch.setattr(app, "setup_logging", config.migrate_private_files)
    monkeypatch.setattr(app, "QApplication", ShellApp)
    monkeypatch.setattr(app, "QLocalSocket", lambda: SimpleNamespace(
        connectToServer=lambda _name: None, waitForConnected=lambda _ms: False))
    monkeypatch.setattr(app, "QLocalServer", LocalServer)
    monkeypatch.setattr(app, "QSystemTrayIcon", SimpleNamespace(isSystemTrayAvailable=lambda: True))
    monkeypatch.setattr(app, "_single_instance_mutex", lambda _name: None)
    monkeypatch.setattr(app, "_MUTEX", None)
    monkeypatch.setattr(app, "Controller", OfflineController)
    monkeypatch.setattr(app, "ask", lambda *_args: getattr(app.QMessageBox, answer))
    monkeypatch.setattr(app, "warn", lambda *_args: None)
    monkeypatch.setattr(sys, "argv", ["Skyfold.exe"])
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    app.main()
    assert not (private / config.DATABASE).exists()  # (review 32: not started either way)
    copies = list(private.glob(config.DATABASE + "-wal.orphan-*"))
    if orphan.exists():
        copies.append(orphan)
    assert any(p.read_bytes() == original for p in copies), "committed journal bytes were overwritten"


def test_known_cloud_quick_pass_retains_pending_shell_children(drive_env):
    s, cloud, root, paths = pending_shell(drive_env, "pc-to-cloud")
    result = s.run(changed={paths[0]})
    assert set(paths).issubset(s.state.all())
    assert not any((root / p).exists() for p in paths)
    assert not cloud.trashed
    # (review 35: a deletion is only decided by a full read; B36-1: one already waiting for an answer
    # needs none, it keeps waiting as it was asked)
    assert result.full or set(result.pending_to_cloud) == set(paths)


def test_local_quick_change_escalates_without_releasing_pending_children(drive_env):
    s, cloud, root, paths = pending_shell(drive_env, "cloud-to-pc")
    new = "Trip/Sub/new.txt"
    write(root, new, b"NEW")
    before = cloud.full_walks
    first = s.run(changed={new})
    assert first.full and cloud.full_walks == before + 1
    for _ in range(3):
        s.run()
    assert cloud.items[new]["data"] == b"NEW"
    assert set(paths).isdisjoint(cloud.items)
    assert all((root / p).read_bytes() == b"ORIGINAL" for p in paths)


def test_tests_never_write_the_real_crash_log():
    """conftest keeps every test's crash.log in its own temporary folder (not the user's)."""
    from skyfold import app, config

    assert app.LOG_FILE != config.LOG_FILE and app.LOG_FILE.parent != config.PRIVATE_DIR
