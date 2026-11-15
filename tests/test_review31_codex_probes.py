"""Codex review 31 probes (of fffdc38), kept as regression tests. The characterize_* cases now
check the corrected behaviour (fixed_*), or the accepted one (known_*)."""
import os
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from skyfold import config, drive_engine as de, notes_engine as ne
from tests.test_drive import env as drive_env, write
from tests.test_notes import env as notes_env
from tests.test_review28_codex_probes import deny_lookup


@pytest.mark.parametrize("direction", ["cloud-to-pc", "pc-to-cloud"])
@pytest.mark.parametrize("approve", [False, True], ids=["unanswered", "approved-between-passes"])
def test_shell_deletion_does_not_restore_items_while_awaiting_or_after_approval(drive_env, direction, approve):
    s, cloud, root, recycled = drive_env
    s.delete_threshold = 1
    paths = {"Trip/a.txt", "Trip/b.txt"}
    for path in sorted(paths):
        cloud.put(path, b"SYNCED")
    s.run()

    if direction == "cloud-to-pc":
        write(root, "Trip/desktop.ini", b"LOCAL ONLY")
        cloud.trash(cloud._item("Trip"))
    else:
        cloud.put("Trip/desktop.ini", b"CLOUD ONLY")
        shutil.rmtree(root / "Trip")

    first = s.run()
    assert set(first.pending_to_pc if direction == "cloud-to-pc" else first.pending_to_cloud) == paths
    assert not recycled
    if approve:
        s.approved.update(paths)
    s.run()
    s.run()
    if direction == "cloud-to-pc":
        assert paths.isdisjoint(cloud.items), cloud.uploads
    else:
        assert not any((root / path).exists() for path in paths)


def local_shell(fixture):
    s, cloud, root, _ = fixture
    cloud.put("Trip/Sub/a.txt", b"SYNCED")
    s.run()
    write(root, "Trip/Sub/desktop.ini", b"LOCAL ONLY")
    cloud.trash(cloud._item("Trip"))
    s.run()
    assert s.state.get_meta("shell:pc:Trip")
    s.run()
    assert "Trip" not in cloud.items
    return s, cloud, root


def test_shell_survives_local_case_change_without_recreating_cloud_folder(drive_env):
    s, cloud, root = local_shell(drive_env)
    (root / "Trip").rename(root / "temporary-name")
    (root / "temporary-name").rename(root / "TRIP")
    assert any(p.name == "TRIP" for p in root.iterdir())
    s.run()
    s.run()
    assert not any(path.upper() == "TRIP" for path in cloud.items)


def test_new_child_under_shell_reopens_folder_on_full_pass(drive_env):
    s, cloud, root = local_shell(drive_env)
    write(root, "Trip/Sub/new.txt", b"NEW")
    for _ in range(3):
        s.run()
    assert cloud.items["Trip/Sub/new.txt"]["data"] == b"NEW"
    assert not s.state.get_meta("shell:pc:Trip")


def test_local_shell_normal_rename_publishes_new_name(drive_env):
    s, cloud, root = local_shell(drive_env)
    (root / "Trip").rename(root / "Renamed")
    s.run()
    assert "Renamed" in cloud.items and "Trip" not in cloud.items
    assert not s.state.get_meta("shell:pc:Trip")


def test_fixed_cloud_shell_selection_keeps_out_of_scope_record(drive_env):
    s, cloud, root, _ = drive_env
    cloud.put("Top/Trip/a.txt", b"SYNCED")
    cloud.put("Elsewhere/a.txt", b"IN SCOPE")
    s.run()
    cloud.put("Top/Trip/desktop.ini", b"CLOUD ONLY")
    shutil.rmtree(root / "Top/Trip")
    s.run()
    assert s.state.get_meta("shell:cloud:Top/Trip")
    s.folders = ["Elsewhere"]
    s.run()
    assert s.state.get_meta("shell:cloud:Top/Trip") is not None
    s.folders = None
    s.run()
    assert not (root / "Top/Trip").exists()


def test_known_cloud_shell_quick_nested_addition(drive_env):
    s, cloud, root, _ = drive_env
    cloud.put("Top/Trip/Sub/a.txt", b"SYNCED")
    s.run()
    cloud.put("Top/Trip/Sub/desktop.ini", b"CLOUD ONLY")
    shutil.rmtree(root / "Top/Trip")
    s.run()
    s.run()
    assert not (root / "Top/Trip").exists()
    cloud.put("Top/Trip/Sub/new.txt", b"NEW")
    s.run(changed={"Top/Trip/Sub/new.txt"})
    assert (root / "Top/Trip/Sub/new.txt").read_bytes() == b"NEW"


def private_paths(monkeypatch, tmp_path):
    source = tmp_path / "data"
    local = tmp_path / "Local"
    private = local / "Skyfold"
    source.mkdir()
    private.mkdir(parents=True)
    for name, path in (("DATA_DIR", source), ("PRIVATE_DIR", private),
                       ("APP_DIR", tmp_path / "App"), ("SETTINGS_FILE", source / "settings.json")):
        monkeypatch.setattr(config, name, path)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(config, "MIGRATION_PROBLEMS", [])
    return source, private


def live_wal_copy(tmp_path, dst):
    live = tmp_path / "live.sqlite3"
    with closing(sqlite3.connect(live)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("CREATE TABLE test_state (value TEXT)")
        db.execute("INSERT INTO test_state VALUES ('committed in WAL')")
        db.commit()
        shutil.copy2(live, dst)
        shutil.copy2(str(live) + "-wal", str(dst) + "-wal")


def test_fixed_snapshot_leaves_earlier_source_bytes_alone(notes_env, tmp_path, monkeypatch):
    source, private = private_paths(monkeypatch, tmp_path)
    legacy = source / config.DATABASE
    live_wal_copy(tmp_path, legacy)
    wal = source / (config.DATABASE + "-wal")
    before, wal_before = legacy.read_bytes(), wal.read_bytes()
    config.migrate_private_files()
    assert legacy.read_bytes() == before and wal.read_bytes() == wal_before
    assert not (source / (config.DATABASE + "-shm")).exists()  # (the index the read made is removed)
    for p in (legacy, private / config.DATABASE):
        with closing(sqlite3.connect(p)) as db:
            assert db.execute("SELECT value FROM test_state").fetchone() == ("committed in WAL",)


def test_startup_preserves_orphan_wal_without_legacy_database(notes_env, tmp_path, monkeypatch):
    from skyfold.state import State
    source, private = private_paths(monkeypatch, tmp_path)
    legacy = source / config.DATABASE
    live_wal_copy(tmp_path, legacy)
    orphan = private / (config.DATABASE + "-wal")
    (source / (config.DATABASE + "-wal")).rename(orphan)
    legacy.unlink()
    original = orphan.read_bytes()
    config.migrate_private_files()
    state = State(private / config.DATABASE)  # normal startup, with no legacy database to migrate
    state.set("synthetic", "initialized")
    copies = list(private.glob(config.DATABASE + "-wal.orphan-*"))
    if orphan.exists():
        copies.append(orphan)
    assert any(p.read_bytes() == original for p in copies), "the original committed WAL bytes are gone"


def test_fixed_inaccessible_legacy_path_is_added_to_startup_warning(notes_env, tmp_path, monkeypatch):
    src, private = private_paths(monkeypatch, tmp_path)
    db = src / config.DATABASE
    db.write_bytes(b"fixture")
    with monkeypatch.context() as m:
        deny_lookup(m, db, winerror=5)
        config.migrate_private_files()
    assert config.MIGRATION_PROBLEMS
    assert db.exists() and not (private / config.DATABASE).exists()


def test_busy_legacy_database_gives_warning_without_publishing(notes_env, tmp_path, monkeypatch):
    source, private = private_paths(monkeypatch, tmp_path)
    db = source / config.DATABASE
    monkeypatch.setattr(config, "BACKUP_SECONDS", 0.03)
    with closing(sqlite3.connect(db)) as writer:
        writer.execute("CREATE TABLE busy (value TEXT)")
        writer.commit()
        writer.execute("BEGIN EXCLUSIVE")
        try:
            config.migrate_private_files()
        finally:
            writer.rollback()
    assert config.MIGRATION_PROBLEMS and db.exists()
    assert not (private / config.DATABASE).exists()


def test_unknown_signout_marker_never_starts_session_restore(tmp_path, monkeypatch):
    import pyicloud
    from skyfold import auth

    session = tmp_path / "session"
    session.mkdir()
    signout = tmp_path / "signed-out"
    signout.touch()
    monkeypatch.setattr(auth, "SESSION_DIR", session)
    monkeypatch.setattr(auth, "SIGNED_OUT", signout)
    attempts = []

    def unexpected_restore(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError("synthetic guard")

    monkeypatch.setattr(pyicloud, "PyiCloudService", unexpected_restore)
    deny_lookup(monkeypatch, signout, winerror=21)
    assert auth.restore("synthetic@example.invalid") is None and not attempts


def test_clean_first_start_has_no_migration_warning(notes_env, tmp_path, monkeypatch):
    from skyfold.state import State

    _, private = private_paths(monkeypatch, tmp_path)
    config.migrate_private_files()
    assert not config.MIGRATION_PROBLEMS and not list(private.iterdir())
    state = State(private / config.DATABASE)
    state.set("synthetic", "first run")
    assert state.get("synthetic") == "first run"


def test_two_untagged_listings_get_different_content_in_one_pass(drive_env, monkeypatch):
    s, cloud, _, _ = drive_env
    cloud.put("a.txt", b"OLD")
    cloud.items["a.txt"]["etag"] = None
    s.run()
    first = cloud.walk()["a.txt"]
    s._hashes.clear()
    calls = []
    download = cloud.download

    def count(item, destination, on_bytes):
        calls.append(item)
        return download(item, destination, on_bytes)

    monkeypatch.setattr(cloud, "download", count)
    old_hash = s._cloud_hash(first)
    assert old_hash and s._cloud_hash(first) == old_hash
    cloud.put("a.txt", b"NEW")
    cloud.items["a.txt"]["etag"] = None
    second = cloud.walk()["a.txt"]
    assert second is not first and second == first
    new_hash = s._cloud_hash(second)
    assert new_hash and new_hash != old_hash and len(calls) == 2


def test_fixed_local_shell_quick_change_gets_a_full_pass(drive_env):
    s, cloud, root = local_shell(drive_env)
    p = "Trip/Sub/new.txt"
    write(root, p, b"NEW")
    before = cloud.full_walks
    s.run(changed={p})
    assert cloud.full_walks == before + 1  # (review 32: the quick pass really became a full one)
    s.run(changed={p})  # (the folders first, then the file: as for any new folder tree)
    assert cloud.items[p]["data"] == b"NEW"


def test_nested_shells_reopen_when_real_content_is_added(drive_env):
    s, cloud, root, _ = drive_env
    cloud.put("Trip/Sub/a.txt", b"A")
    cloud.put("Trip/parent.txt", b"P")
    s.run()
    write(root, "Trip/Sub/desktop.ini", b"LOCAL ONLY")
    cloud.trash(cloud._item("Trip/Sub"))
    s.run()
    assert s.state.get_meta("shell:pc:Trip/Sub")
    cloud.trash(cloud._item("Trip"))
    s.run()
    assert sorted(s.state.meta_items("shell:")) == ["shell:pc:Trip", "shell:pc:Trip/Sub"]
    write(root, "Trip/Sub/new.txt", b"NEW")
    for _ in range(4):
        s.run()
    assert cloud.items["Trip/Sub/new.txt"]["data"] == b"NEW"
    assert not s.state.meta_items("shell:")


def test_fixed_new_empty_directory_under_old_subfolder_name_reopens(drive_env):
    s, cloud, root = local_shell(drive_env)
    write(root, "Trip/desktop.ini", b"KEEP THE PARENT")
    shutil.rmtree(root / "Trip/Sub")
    s.run()
    (root / "Trip/Sub").mkdir()
    for _ in range(3):
        s.run()
    assert "Trip/Sub" in cloud.items and not s.state.get_meta("shell:pc:Trip")


def test_known_retained_database_is_not_used_on_second_start(notes_env, tmp_path, monkeypatch):
    source, private = private_paths(monkeypatch, tmp_path)
    legacy = source / config.DATABASE
    live_wal_copy(tmp_path, legacy)
    config.migrate_private_files()
    with closing(sqlite3.connect(legacy)) as db:
        db.execute("UPDATE test_state SET value = 'new source change'")
        db.commit()
    config.migrate_private_files()
    with closing(sqlite3.connect(private / config.DATABASE)) as db:
        assert db.execute("SELECT value FROM test_state").fetchone() == ("committed in WAL",)


def notes_adapter(raw):
    adapter = ne.ICloudNotes(api=None)
    adapter._svc = SimpleNamespace(_raw=raw, _lookup_note_record=lambda note_id: note_id)
    return adapter


def test_fixed_nested_export_preview_is_cleaned(notes_env, monkeypatch):
    from PySide6.QtGui import QColor, QImage
    import pyicloud.services.notes.rendering.exporter as ex

    _, _, root, _ = notes_env
    assets = root / "_assets"
    note_assets = assets / "n1"
    note_assets.mkdir(parents=True)
    preview = note_assets / "preview.png"

    class Raw:
        def download_asset_to(self, url, directory):
            path = Path(directory) / url
            size = 4 if url == "preview.png" else 32
            img = QImage(size, size, QImage.Format_RGB32)
            img.fill(QColor("blue"))
            assert img.save(str(path), "PNG")
            return path

    raw = Raw()
    original = raw.download_asset_to

    class Exporter:
        def __init__(self, client, **kwargs):
            self.client = client  # (like pyicloud's NoteExporter: downloads go through this client)

        def export(self, record, output_dir, filename):
            assert record == "n1"
            assert self.client.download_asset_to("preview.png", str(note_assets)) == preview
            (Path(output_dir) / (filename + ".html")).write_text(
                '<img data-uti="com.apple.paper" data-id="D1" src="_assets/n1/preview.png">', encoding="utf-8")
            ne._SKETCH_FALLBACKS["D1"] = "full"
            return True

    monkeypatch.setattr(ex, "NoteExporter", Exporter)
    monkeypatch.setattr(ne, "_prefer_sharpest_previews", lambda: None)
    notes_adapter(raw).export("n1", root, "page", assets)
    assert "full.png" in (root / "page.html").read_text(encoding="utf-8")
    assert not preview.exists() and raw.download_asset_to == original


def test_fixed_overlapping_notes_exports_leave_the_client_alone(notes_env, monkeypatch):
    import pyicloud.services.notes.rendering.exporter as ex

    _, _, root, _ = notes_env
    entered_a, entered_b, returned_a = threading.Event(), threading.Event(), threading.Event()

    class Raw:
        def download_asset_to(self, url, directory):
            return Path(directory) / url

    raw = Raw()
    original = raw.download_asset_to

    class Exporter:
        def __init__(self, *args, **kwargs):
            pass

        def export(self, record, output_dir, filename):
            if record == "a":
                entered_a.set()
                assert entered_b.wait(5)
            else:
                entered_b.set()
                assert returned_a.wait(5)
            return True

    monkeypatch.setattr(ex, "NoteExporter", Exporter)
    monkeypatch.setattr(ne, "_prefer_sharpest_previews", lambda: None)

    def a():
        try:
            notes_adapter(raw).export("a", root, "a", root)
        finally:
            returned_a.set()

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(a)
        assert entered_a.wait(5)
        second = pool.submit(notes_adapter(raw).export, "b", root, "b", root)
        first.result(timeout=6)
        second.result(timeout=6)
    assert raw.download_asset_to == original and "download_asset_to" not in vars(raw)
