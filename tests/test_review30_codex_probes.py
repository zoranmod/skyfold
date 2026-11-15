"""Codex review 30 probes (of 3df8683), kept as regression tests. The characterize_* cases now
check the corrected behaviour (fixed_*), or the accepted one (known_*)."""
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from skyfold import config, drive_engine as de, engine as pe, notes_engine as ne
from tests.test_drive import env as drive_env, write
from tests.test_engine import env as photos_env
from tests.test_notes import env as notes_env
from tests.test_review28_codex_probes import deny_lookup


@pytest.mark.parametrize("marker", ["desktop.ini", "~$draft.docx", "draft.tmp"])
def test_cloud_folder_delete_does_not_recreate_folder_with_only_ignored_content(drive_env, marker):
    s, cloud, root, recycled = drive_env
    cloud.put("Trip/a.txt", b"A")
    s.run()
    assert de.ignored(marker)
    draft = write(root, "Trip/" + marker, b"LOCAL ONLY")
    cloud.trash(cloud._item("Trip"))
    s.run()
    assert draft.read_bytes() == b"LOCAL ONLY" and not (root / "Trip/a.txt").exists()
    s.run()
    assert "Trip" not in cloud.items and "Trip/" + marker not in cloud.items


@pytest.mark.parametrize("marker", ["desktop.ini", "~$draft.docx"])
def test_fixed_ignored_cloud_child_keeps_folder_without_rematerializing_it(drive_env, marker):
    s, cloud, root, _ = drive_env
    cloud.put("Trip/a.txt", b"A")
    s.run()
    cloud.put("Trip/" + marker, b"CLOUD ONLY")
    shutil.rmtree(root / "Trip")
    s.run()
    assert "Trip" in cloud.items and "Trip/a.txt" not in cloud.items
    s.run()
    assert not (root / "Trip").exists()  # not made on this PC again only for the never-synced item
    assert cloud.items["Trip/" + marker]["data"] == b"CLOUD ONLY"


def test_cloud_folder_delete_preserves_empty_ignored_child(drive_env):
    s, cloud, root, recycled = drive_env
    cloud.put("Trip/a.txt", b"A")
    s.run()
    assert de.ignored("kept.tmp")
    ignored_directory = root / "Trip/kept.tmp"
    ignored_directory.mkdir()
    cloud.trash(cloud._item("Trip"))
    s.run()
    assert ignored_directory.is_dir(), recycled


@pytest.mark.parametrize("tag", [None, "v1"], ids=["untagged", "tagged"])
def test_known_only_untagged_files_require_each_pass_download(drive_env, monkeypatch, tag):
    s, cloud, root, _ = drive_env
    cloud.put("a.txt", b"UNCHANGED")
    cloud.items["a.txt"]["etag"] = tag
    real_download = cloud.download
    calls = []
    def download(item, target, on_bytes):
        calls.append(item.path)
        return real_download(item, target, on_bytes)
    monkeypatch.setattr(cloud, "download", download)
    s.run()
    assert (root / "a.txt").read_bytes() == b"UNCHANGED"
    second, third = s.run(), s.run()
    assert calls == ["a.txt"] * (3 if tag is None else 1)
    assert second.downloaded == third.downloaded == 0 and not cloud.uploads


def private_paths(monkeypatch, tmp_path, origin):
    data = tmp_path / "data"
    local = tmp_path / "Local"
    source = local / "iSync" if origin == "isync" else data
    source.mkdir(parents=True)
    private = local / "Skyfold"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    for name, path in (("DATA_DIR", data), ("PRIVATE_DIR", private), ("APP_DIR", tmp_path / "App"),
                       ("SETTINGS_FILE", data / "settings.json")):
        monkeypatch.setattr(config, name, path)
    return source, private


def seed_database(path, value):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE sync_state(value TEXT)")
        conn.execute("INSERT INTO sync_state VALUES (?)", (value,))
        conn.commit()


def db_value(path):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("SELECT value FROM sync_state").fetchone()[0]


@pytest.mark.parametrize("origin", ["isync", "portable"])
def test_migration_preserves_latest_committed_source_write(notes_env, tmp_path, monkeypatch, origin):
    source, private = private_paths(monkeypatch, tmp_path, origin)
    src, dst = source / config.DATABASE, private / config.DATABASE
    seed_database(src, "before snapshot")
    publish = config.place_new
    commits = []
    def publish_with_last_source_commit(staging, destination):
        if destination == dst:
            with closing(sqlite3.connect(src)) as conn:
                conn.execute("UPDATE sync_state SET value = 'committed during migration'")
                conn.commit()
            commits.append(True)
        return publish(staging, destination)
    monkeypatch.setattr(config, "place_new", publish_with_last_source_commit)
    config.migrate_private_files()
    assert commits and (src.exists() or dst.exists())
    # A safe migration may decline to remove a source that changed mid-operation.
    surviving = db_value(src) if src.exists() else db_value(dst)
    assert surviving == "committed during migration"


@pytest.mark.parametrize("origin", ["isync", "portable"])
def test_database_migration_reads_committed_wal_without_clean_close(notes_env, tmp_path, monkeypatch, origin):
    source, private = private_paths(monkeypatch, tmp_path, origin)
    src, dst = source / config.DATABASE, private / config.DATABASE
    live = tmp_path / "live.sqlite3"
    seed_database(live, "checkpointed value")
    with closing(sqlite3.connect(live)) as conn:
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("UPDATE sync_state SET value = 'only in WAL'")
        conn.commit()
        shutil.copy2(live, src)
        shutil.copy2(Path(str(live) + "-wal"), Path(str(src) + "-wal"))
    assert (source / (config.DATABASE + "-wal")).stat().st_size > 0
    config.migrate_private_files()
    assert db_value(dst) == "only in WAL"
    assert db_value(src) == "only in WAL"  # the earlier copy is kept (nothing proves it is unused)


def test_sketch_cleanup_does_not_delete_file_that_predates_export(notes_env):
    from PySide6.QtGui import QColor, QImage
    _, _, root, _ = notes_env
    assets = root / "_assets/n1"
    assets.mkdir(parents=True)
    preview = assets / "user-preview.png"
    page = root / "export.html"
    image = QImage(4, 4, QImage.Format_RGB32)
    image.fill(QColor("red"))
    assert image.save(str(preview), "PNG")
    data = preview.read_bytes()
    born = preview.stat().st_birthtime
    export_started = born + 1.0  # deterministic ordering, no wall-clock race
    page.write_text('<img data-uti="com.apple.paper" data-id="D1" src="_assets/n1/user-preview.png">',
                    encoding="utf-8")
    def download(_url, folder):
        acquired = folder / "download"
        upgraded = QImage(32, 32, QImage.Format_RGB32)
        upgraded.fill(QColor("blue"))
        assert upgraded.save(str(acquired), "PNG")
        return acquired
    other = assets / "made-by-this-export.png"  # a file this export did download
    assert image.save(str(other), "PNG")
    assert born < export_started
    changed = ne.upgrade_sketches(page, {"D1": "synthetic"}, download, made={ne._file_identity(other)},
                                  assets_dir=assets)
    assert changed == 1
    assert preview.exists() and preview.read_bytes() == data


def test_fixed_unreadable_post_scan_drive_content_is_left_for_later(drive_env, monkeypatch):
    s, cloud, root, recycled = drive_env
    cloud.put("a.txt", b"ORIGINAL")
    s.run()
    cloud.trash(cloud._item("a.txt"))
    compute = de.file_hash
    def fail_hash(path):
        if path == root / "a.txt":
            raise PermissionError("simulated content read failure after metadata succeeds")
        return compute(path)
    monkeypatch.setattr(de, "file_hash", fail_hash)
    s.run()
    assert not recycled and "a.txt" not in cloud.items  # neither deleted here nor uploaded again
    assert (root / "a.txt").read_bytes() == b"ORIGINAL" and s.state.all().get("a.txt") is not None


def test_fixed_rebase_with_existing_destination_record_keeps_the_record(drive_env, monkeypatch):
    s, _, root, _ = drive_env
    old = write(root, "Old/a (previous version).txt", b"OLD")
    val = s._file_id(old)
    assert val
    s.state.set_meta("prev:Old/" + old.name, val)
    existing_key = "prev:New/" + old.name
    s.state.set_meta(existing_key, "1:2")
    rename = os.rename
    def deny_rename(src, dst):
        if Path(src) == root / "Old":
            raise PermissionError("simulated rename failure")
        return rename(src, dst)
    monkeypatch.setattr(de.os, "rename", deny_rename)
    assert not s._local_move("Old", "New", de.DriveResult())
    assert s.state.get_meta("prev:Old/" + old.name) == val
    assert s.state.get_meta(existing_key) == "1:2"


def test_known_borderline_offline_photos_directory_holds_missing_photo(photos_env, monkeypatch):
    s, cloud, root, _ = photos_env
    cloud.add("A1", "a.jpg", b"A")
    s.run(force_full=True)
    (root / s.state.files()[0].rel_path).unlink()
    locked = root / "Unavailable"
    locked.mkdir()
    real = os.scandir
    def scandir(p="."):
        if Path(p) == locked:
            raise PermissionError(13, "denied", str(p))
        return real(p)
    monkeypatch.setattr(os, "scandir", scandir)
    s.run(force_full=True)
    s.run(force_full=True)
    assert not cloud.deleted and s.state.files()
