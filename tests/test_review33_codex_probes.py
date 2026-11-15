"""Codex review 33 probes (of e0bcef1), kept as regression tests. The characterize_* cases now
check the corrected behaviour (fixed_*)."""
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from skyfold import config, drive_engine as de
from tests.test_drive import env as drive_env, write
from tests.test_review31_codex_probes import live_wal_copy, private_paths
from tests.test_notes import env as notes_env


def seed_two_folders(s, cloud):
    paths = ["Alpha/a.txt", "Beta/b.txt"]
    s.delete_threshold = 1
    for p in paths:
        cloud.put(p, b"ORIGINAL")
    s.run()
    return paths


@pytest.mark.parametrize("side", ["cloud", "pc"])
def test_quick_scan_cannot_delete_an_unanswered_subset(drive_env, side):
    s, cloud, root, local_trash = drive_env
    paths = seed_two_folders(s, cloud)
    if side == "cloud":
        for p in paths:
            (root / p).unlink()
        assert set(s.run().pending_to_cloud) == set(paths)
    else:
        for p in paths:
            cloud.trash(cloud._item(p))
        cloud.trashed.clear()
        assert set(s.run().pending_to_pc) == set(paths)
    changed = "Alpha/unrelated-watcher-change.txt"
    write(root, changed, b"independent local edit")
    s.run(changed={changed})
    if side == "cloud":
        assert not cloud.trashed and set(paths) <= cloud.items.keys()
    else:
        assert not local_trash and all((root / p).read_bytes() == b"ORIGINAL" for p in paths)


def test_replacement_cloud_item_needs_fresh_confirmation(drive_env):
    s, cloud, root, _ = drive_env
    paths = seed_two_folders(s, cloud)
    for p in paths:
        (root / p).unlink()
    question = s.run()
    assert set(question.pending_to_cloud) == set(paths)
    old = cloud._item(paths[0]).docwsid
    cloud.trash(cloud._item(paths[0]))
    cloud.put(paths[0], b"ORIGINAL")
    new = cloud._item(paths[0]).docwsid
    assert old != new
    cloud.trashed.clear()
    s.approved.update(question.pending_to_cloud)
    s.run()
    assert paths[0] in cloud.items and cloud._item(paths[0]).docwsid == new, cloud.trashed


def test_an_open_dialog_cannot_approve_the_new_opposite_side(drive_env, monkeypatch):
    from skyfold import app
    from skyfold.worker import SyncWorker

    s, cloud, root, _ = drive_env
    paths = seed_two_folders(s, cloud)
    for p in paths:
        cloud.trash(cloud._item(p))
    cloud.trashed.clear()
    pending_before = s.run()
    assert set(pending_before.pending_to_pc) == set(paths)
    original_dialog = SimpleNamespace(service="drive", to_pc=pending_before.pending_to_pc, to_cloud=[])
    worker = SimpleNamespace(
        _pending=None, _drive_pending=original_dialog, drive=s, drive_state=s.state,
        pendingChanged=SimpleNamespace(emit=lambda _value: None), _kick_drive=lambda **_opts: s.run(),
    )
    captured = []
    monkeypatch.setattr(app, "PendingDialog", lambda pending, _palette: (
        captured.append(pending) or SimpleNamespace(decided=SimpleNamespace(connect=captured.append))))
    controller = SimpleNamespace(
        _pending=original_dialog, c={}, _show_dialog=lambda *_args: None,
        req=SimpleNamespace(resolvePending=SimpleNamespace(
            emit=lambda action, ids: SyncWorker.resolve_pending(worker, action, ids))),
    )
    app.Controller.open_pending(controller)
    assert captured[0].to_pc and not captured[0].to_cloud
    for p in paths:
        cloud.put(p, b"ORIGINAL")
        (root / p).unlink()
    pending_after = s.run()
    assert set(pending_after.pending_to_cloud) == set(paths)
    worker._drive_pending = SimpleNamespace(service="drive", to_pc=[], to_cloud=pending_after.pending_to_cloud)
    controller._pending = worker._drive_pending
    captured[1]("approve", captured[0].to_pc)
    assert not cloud.trashed and set(paths) <= cloud.items.keys()


def test_case_variant_new_file_is_not_the_old_synced_file(drive_env):
    s, cloud, root, local_trash = drive_env
    cloud.put("report.txt", b"ORIGINAL")
    s.run()
    original = root / "report.txt"
    moved_out = root.parent / "original.txt"
    original.rename(moved_out)
    newly_created = root / "REPORT.TXT"
    shutil.copy2(moved_out, newly_created)
    assert os.stat(moved_out).st_ino != os.stat(newly_created).st_ino
    cloud.trash(cloud._item("report.txt"))
    s.run()
    assert newly_created.exists(), local_trash


def test_identical_local_copy_cannot_move_a_cloud_only_file(drive_env):
    s, cloud, root, _ = drive_env
    s.delete_threshold = 1
    for name in ("a.txt", "b.txt"):
        cloud.put(f"Trip/{name}", b"ORIGINAL")
    s.run()
    shutil.copytree(root / "Trip", root / "Template")
    (root / "Trip").rename(root.parent / "original-tree")
    cloud.put("Trip/cloud-only.txt", b"NEVER SYNCED TO PC")
    s.run()
    assert "Trip/cloud-only.txt" in cloud.items, cloud.renamed
    assert "Template/cloud-only.txt" not in cloud.items


def test_fixed_live_checkpoint_cannot_publish_an_older_snapshot(notes_env, tmp_path, monkeypatch):
    source, private = private_paths(monkeypatch, tmp_path)
    src = source / config.DATABASE
    dst = private / config.DATABASE

    def read_row(path):
        with closing(sqlite3.connect(path)) as conn:
            return conn.execute("SELECT value FROM evidence").fetchone()[0]

    with closing(sqlite3.connect(src)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE evidence (value TEXT)")
        writer.execute("INSERT INTO evidence VALUES ('old')")
        writer.commit()
        assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
        writer.execute("UPDATE evidence SET value='committed before migration'")
        writer.commit()
        copy = shutil.copyfile

        def checkpoint_after_main(src_arg, dst_arg):
            result = copy(src_arg, dst_arg)
            if Path(src_arg) == src:  # (the read lock keeps the WAL in place: it can only be partial)
                writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            return result

        with monkeypatch.context() as m:
            m.setattr(shutil, "copyfile", checkpoint_after_main)
            config.migrate_private_files()
        assert not config.MIGRATION_PROBLEMS
        assert read_row(src) == "committed before migration"
        assert read_row(dst) == "committed before migration"


def test_fixed_closed_wal_upgrade_creates_no_source_sidecars(notes_env, tmp_path, monkeypatch):
    source, private = private_paths(monkeypatch, tmp_path)
    src = source / config.DATABASE
    with closing(sqlite3.connect(src)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE evidence (value TEXT)")
        conn.execute("INSERT INTO evidence VALUES ('safe')")
        conn.commit()
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    assert set(before) == {config.DATABASE}
    config.migrate_private_files()
    assert not config.MIGRATION_PROBLEMS
    with closing(sqlite3.connect(private / config.DATABASE)) as conn:
        assert conn.execute("SELECT value FROM evidence").fetchone() == ("safe",)
    # Characterize a recoverable source-side effect of reading a healthy closed WAL-mode database.
    after = {p.name: p.read_bytes() for p in source.iterdir()}
    assert after == before


def test_orphan_rollback_journal_must_survive_new_database(notes_env, tmp_path, monkeypatch):
    from skyfold.state import State

    source, private = private_paths(monkeypatch, tmp_path)
    src = source / config.DATABASE
    code = """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA journal_mode=DELETE')
db.execute('PRAGMA cache_size=4')
db.execute('CREATE TABLE evidence (value BLOB)')
db.executemany('INSERT INTO evidence VALUES (?)', [(b'EARLIER'*1300,)] * 16)
db.commit()
db.execute('UPDATE evidence SET value=?', (b'NEW-DAT'*1300,))
os._exit(91)
"""
    run = subprocess.run([sys.executable, "-c", code, str(src)], timeout=12, check=False)
    assert run.returncode == 91
    name = config.DATABASE + "-journal"
    orphan = private / name
    (source / name).rename(orphan)
    original_main = tmp_path / "original-main"
    src.rename(original_main)
    recorded = orphan.read_bytes()
    assert b"EARLIER" in recorded
    recovery_control = tmp_path / "recovery-control.sqlite3"
    shutil.copyfile(original_main, recovery_control)
    shutil.copyfile(orphan, str(recovery_control) + "-journal")
    with closing(sqlite3.connect(recovery_control)) as conn:
        assert conn.execute("SELECT count(*) FROM evidence WHERE substr(value, 1, 7)=?",
                            (b"EARLIER",)).fetchone() == (16,)
    config.migrate_private_files()
    if not config.JOURNAL_PROBLEMS:
        state = State(private / config.DATABASE)
        state.set("review33", "initialized")
    assert any(p.read_bytes() == recorded for p in private.glob(name + "*"))


def test_orphan_wal_survives_unknown_main_file_lookup(notes_env, tmp_path, monkeypatch):
    from skyfold.state import State

    source, private = private_paths(monkeypatch, tmp_path)
    src = source / config.DATABASE
    live_wal_copy(tmp_path, src)
    name = config.DATABASE + "-wal"
    orphan = private / name
    (source / name).rename(orphan)
    src.rename(tmp_path / "original-main")
    recorded = orphan.read_bytes()
    target = private / config.DATABASE
    lstat = os.lstat
    failed = False

    def fail_once(path, *args, **kwargs):
        nonlocal failed
        if not isinstance(path, int) and Path(path) == target and not failed:
            failed = True
            raise PermissionError("simulated temporary lookup failure before journal quarantine")
        return lstat(path, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(os, "lstat", fail_once)
        config.migrate_private_files()
    assert failed
    if not config.JOURNAL_PROBLEMS:
        state = State(target)
        state.set("review33", "initialized")
    assert any(p.read_bytes() == recorded for p in private.glob(name + "*"))


@pytest.mark.parametrize("side", ["cloud", "pc"])
@pytest.mark.parametrize("quick", [False, True])
def test_same_pass_approval_still_works(drive_env, side, quick):
    s, cloud, root, local_trash = drive_env
    if quick:
        s.delete_threshold = 1
        paths = ["Alpha/a.txt", "Alpha/b.txt"]
        for p in paths:
            cloud.put(p, b"ORIGINAL")
        s.run()
    else:
        paths = seed_two_folders(s, cloud)
    for p in paths:
        if side == "cloud":
            (root / p).unlink()
        else:
            cloud.trash(cloud._item(p))
    cloud.trashed.clear()
    asked = []

    def approve(cloud_paths, pc_paths):
        asked.append((cloud_paths, pc_paths))
        s.approved.update(cloud_paths + pc_paths)

    s.on_pending = approve
    result = s.run(changed={"Alpha/watcher.txt"} if quick else None)
    expected = (paths, []) if side == "cloud" else ([], paths)
    assert asked == [expected]
    assert set(cloud.trashed if side == "cloud" else local_trash) == set(paths)
    assert result.full  # (review 35: a quick pass hands deletions over to a full read)


@pytest.mark.parametrize("change", ["grow", "shrink"])
def test_approval_when_the_question_changes_size(drive_env, change):
    s, cloud, root, _ = drive_env
    s.delete_threshold = 1
    paths = ["a.txt", "b.txt", "c.txt"]
    for p in paths:
        cloud.put(p, b"ORIGINAL")
    s.run()
    initially_missing = paths[:2] if change == "grow" else paths
    backups = {p: root.parent / p for p in initially_missing}
    for p in initially_missing:
        (root / p).rename(backups[p])
    first = s.run()
    assert set(first.pending_to_cloud) == set(initially_missing)
    s.approved.update(first.pending_to_cloud)
    if change == "grow":
        (root / paths[2]).unlink()
        again = s.run()
        assert set(again.pending_to_cloud) == set(paths) and not cloud.trashed
        s.approved.update(again.pending_to_cloud)
        s.run()
        assert set(cloud.trashed) == set(paths)
    else:
        backups[paths[2]].rename(root / paths[2])
        s.run()
        assert set(cloud.trashed).isdisjoint({paths[2]})
        assert set(paths[:2]).isdisjoint(cloud.items)


def test_full_scan_cannot_delete_an_unanswered_shrunken_batch(drive_env):
    s, cloud, root, _ = drive_env
    paths = seed_two_folders(s, cloud)
    backups = [root.parent / Path(p).name for p in paths]
    for p, backup in zip(paths, backups):
        (root / p).rename(backup)
    assert set(s.run().pending_to_cloud) == set(paths)
    backups[1].rename(root / paths[1])
    s.run()
    assert paths[0] in cloud.items, cloud.trashed


def test_edited_folder_rename_preserves_old_cloud_copy_until_approval(drive_env):
    s, cloud, root, _ = drive_env
    s.delete_threshold = 1
    for name in ("a.txt", "b.txt"):
        cloud.put(f"Old/{name}", b"ORIGINAL")
    s.run()
    (root / "Old").rename(root / "New")
    write(root, "New/a.txt", b"NEW-WORK")
    result = s.run()
    assert result.pending_to_cloud == ["Old"]
    assert cloud.items["Old/a.txt"]["data"] == b"ORIGINAL" and not cloud.renamed


def test_move_proof_reads_all_files_in_a_large_tree(drive_env, monkeypatch, record_property):
    import time

    s, cloud, root, _ = drive_env
    nbytes = 4 * 1024 * 1024
    for i in range(8):
        cloud.put(f"Old/{i}.bin", bytes([i]) * nbytes)
    s.run()
    (root / "Old").rename(root / "New")
    reads = []
    original_hash = de.file_hash

    def count(path):
        if path.parent == root / "New":
            reads.append(path.stat().st_size)
        return original_hash(path)

    monkeypatch.setattr(de, "file_hash", count)
    start = time.perf_counter()
    s.run()
    elapsed = time.perf_counter() - start
    record_property("rename_pass_seconds", elapsed)
    assert reads == [nbytes] * 8
    assert cloud.renamed == [("Old", "New")]


def test_fixed_interrupted_move_with_a_mismatched_destination_keeps_both(drive_env):
    s, cloud, root, _ = drive_env
    cloud.put("Old.txt", b"ORIGINAL")
    s.run()
    st = os.lstat(root / "Old.txt")
    cloud.rename(cloud._item("Old.txt"), "New.txt")
    ident = cloud._item("New.txt").docwsid
    with s.state.transaction():
        s._journal_put("pc:" + ident, {
            "kind": "pc", "old": "Old.txt", "new": "New.txt", "fid": [st.st_dev, st.st_ino],
        })
    (root / "Old.txt").rename(root.parent / "original-backup")
    write(root, "New.txt", b"UNRELATED")
    assert os.lstat(root / "New.txt").st_ino != st.st_ino
    s.run()
    s.run()
    # not taken for the moved file: iCloud's version is never replaced or trashed, both are kept
    assert not s._journal() and not cloud.trashed
    assert any(it["data"] == b"ORIGINAL" for it in cloud.items.values())
    assert any(f.read_bytes() == b"UNRELATED" for f in root.iterdir() if f.is_file())  # (a conflict copy)


def test_fixed_move_proof_stops_hashing_after_cancel(drive_env, monkeypatch):
    s, cloud, root, _ = drive_env
    for p in ("a.txt", "b.txt", "c.txt"):
        cloud.put("Old/" + p, b"ORIGINAL")
    s.run()
    (root / "Old").rename(root / "New")
    original_hash = de.file_hash
    hashed = []

    def cancel_during_hash(path):
        if path.parent == root / "New":
            hashed.append(path.name)
            if len(hashed) == 1:
                s.cancel_flag.set()
        return original_hash(path)

    monkeypatch.setattr(de, "file_hash", cancel_during_hash)
    with pytest.raises(de.Cancelled):
        s.run()
    assert len(hashed) == 1  # stopped right after the first file
    assert not cloud.renamed
