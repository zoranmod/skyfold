"""Review 29 offline probes (Codex), kept as regression tests. The characterize_* probes recorded
behaviour that review 29 asked to change; they are kept as fixed_* tests of the corrected outcome."""
import os
import shutil
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from skyfold import config, drive_engine as de, engine as pe, notes_engine as ne
from tests.test_drive import env as drive_env, write
from tests.test_engine import env as photos_env
from tests.test_notes import env as notes_env
from tests.test_review28_codex_probes import deny_lookup


@pytest.mark.parametrize("writer", ["settings", "drive-cache"])
def test_save_preserves_unowned_staging_sibling(drive_env, tmp_path, monkeypatch, writer):
    s, cloud, _, _ = drive_env
    data = tmp_path / "data"
    data.mkdir()
    target = data / ("settings.json" if writer == "settings" else "drive_cache.json")
    user_file = target.with_suffix(".tmp")
    user_file.write_bytes(b"PERSONAL BYTES")
    if writer == "settings":
        monkeypatch.setattr(config, "DATA_DIR", data)
        monkeypatch.setattr(config, "SETTINGS_FILE", target)
        try:
            config.Settings().save()
        except FileExistsError:
            pass
    else:
        s.cache_file = target
        cloud.put("example.txt", b"PUBLIC TEST DATA")
        s.run()
    assert user_file.exists() and user_file.read_bytes() == b"PERSONAL BYTES"


@pytest.mark.parametrize("direction", ["cloud-to-pc", "pc-to-cloud"])
def test_folder_delete_preserves_out_of_scope_child(drive_env, direction):
    s, cloud, root, recycled = drive_env
    cloud.put("Trip/known.txt", b"TRACKED")
    s.run()
    child = "Trip/user-draft.tmp"
    assert de.ignored("user-draft.tmp")
    if direction == "cloud-to-pc":
        write(root, child, b"PERSONAL PC FILE")
        cloud.trash(cloud._item("Trip"))
        s.run()
        assert (root / child).exists(), recycled
    else:
        cloud.put(child, b"PERSONAL CLOUD FILE")
        shutil.rmtree(root / "Trip")
        s.run()
        assert cloud.items.get(child, {}).get("data") == b"PERSONAL CLOUD FILE", cloud.trashed


@pytest.mark.parametrize("tag", ["present", "none", "empty"])
def test_unknown_file_tag_does_not_authorize_cloud_trash(drive_env, tag):
    s, cloud, root, _ = drive_env
    path = "example.txt"
    cloud.put(path, b"BEFORE")
    if tag != "present":
        cloud.items[path]["etag"] = "" if tag == "empty" else None
    s.run()
    assert s.state.all()[path].hash
    (root / path).unlink()
    cloud.put(path, b"EDITED")
    if tag != "present":
        cloud.items[path]["etag"] = "" if tag == "empty" else None
    s.run()
    assert cloud.items.get(path, {}).get("data") == b"EDITED", cloud.trashed


def test_migration_keeps_current_database_when_legacy_wal_survives(notes_env, tmp_path, monkeypatch):
    # Use a live genuine SQLite WAL, as after an abrupt stop, without copying
    # the old main database over the destination.
    _, _, _, _ = notes_env
    portable = tmp_path / "portable"
    private = tmp_path / "private"
    origin = tmp_path / "old"
    for p in (portable, private, origin):
        p.mkdir()
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(config, "DATA_DIR", portable)
    monkeypatch.setattr(config, "SETTINGS_FILE", portable / "settings.json")
    monkeypatch.setattr(config, "PRIVATE_DIR", private)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    name = "state.sqlite3"
    current = private / name
    old = origin / name
    for db, value in ((current, "current"), (old, "legacy")):
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE marker (version TEXT)")
            conn.execute("INSERT INTO marker VALUES (?)", (value,))
            conn.commit()
    shutil.copy2(old, portable / name)
    with closing(sqlite3.connect(old)) as conn:
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("UPDATE marker SET version='stale WAL'")
        conn.commit()
        shutil.copy2(origin / (name + "-wal"), portable / (name + "-wal"))
    config.migrate_private_files()
    with closing(sqlite3.connect(current)) as conn:
        assert conn.execute("SELECT version FROM marker").fetchone() == ("current",)


def test_fixed_legacy_session_directory_is_migrated(notes_env, tmp_path, monkeypatch):
    _, _, _, _ = notes_env
    local = tmp_path / "local"
    source = local / "iSync/session"
    source.mkdir(parents=True)
    (source / "fake.session").write_text("synthetic session fixture", encoding="utf-8")
    private = local / "Skyfold"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "portable")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "portable/settings.json")
    monkeypatch.setattr(config, "PRIVATE_DIR", private)
    config.migrate_private_files()
    assert (private / "session/fake.session").read_text(encoding="utf-8") == "synthetic session fixture"
    assert not source.exists()
    # an existing session is never replaced
    source.mkdir(parents=True)
    (source / "other.session").write_text("older", encoding="utf-8")
    config.migrate_private_files()
    assert (source / "other.session").exists() and not (private / "session/other.session").exists()


def test_fixed_unrelated_unreadable_file_does_not_hold_deleted_photo(photos_env, monkeypatch):
    s, cloud, root, _ = photos_env
    cloud.add("A1", "actual.jpg", b"PHOTO")
    s.run(force_full=True)
    (root / s.state.files()[0].rel_path).unlink()
    other = write(root, "unrelated.txt", b"UNRELATED")
    with monkeypatch.context() as m:
        deny_lookup(m, other, winerror=5)
        s.run(force_full=True)
    assert cloud.deleted == ["A1"]  # a file of another name can't be the missing photo


def test_fixed_unreadable_file_of_the_same_name_holds_the_photo(photos_env, monkeypatch):
    s, cloud, root, _ = photos_env
    cloud.add("A1", "actual.jpg", b"PHOTO")
    s.run(force_full=True)
    (root / s.state.files()[0].rel_path).unlink()
    other = write(root, "elsewhere/actual.jpg", b"PHOTO")
    with monkeypatch.context() as m:
        deny_lookup(m, other, winerror=5)
        s.run(force_full=True)
        assert cloud.deleted == [] and s.state.files()  # it may be the photo, moved


@pytest.mark.parametrize("missing_tag", [False, True])
def test_fixed_unchanged_folder_late_approval(drive_env, missing_tag):
    s, cloud, root, _ = drive_env
    s.delete_threshold = 1
    for path in ("Trip/a.txt", "Trip/b.txt", "other.txt"):
        cloud.put(path, b"OLD")
    if missing_tag:
        cloud.items["Trip/a.txt"]["etag"] = None
    s.run()
    shutil.rmtree(root / "Trip")
    cloud.put("other.txt", b"another download")
    s.on_pending = lambda to_cloud, _: s.approved.update(to_cloud)
    s.run()
    # without a version tag the content proves it unchanged: no endless asking again
    assert "Trip" in cloud.trashed


def test_fixed_notes_read_failure_keeps_tracked_page(notes_env, monkeypatch):
    import builtins
    s, cloud, root, recycled = notes_env
    cloud.put("n1", "A")
    s.run(full=True)
    page = root / "Notes/A.html"
    cloud.delete("n1")
    real_open = builtins.open
    def blocked(file, *args, **kwargs):
        if file == page:
            raise PermissionError(13, "synthetic read failure", str(page))
        return real_open(file, *args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(builtins, "open", blocked)
        s.run()
    assert page.exists() and not recycled and "n1" in s.state.all()  # remembered: tried again
    s.run()  # the change feed wasn't moved on: the deletion comes again
    assert recycled and "n1" not in s.state.all()


def test_fixed_notes_unknown_index_presence_claims_nothing(notes_env, monkeypatch):
    s, cloud, root, recycled = notes_env
    root.mkdir()
    index = root / "Notes.html"
    index.write_text(ne.INDEX_MARK + "PERSONAL COPIED INDEX", encoding="utf-8")
    cloud.put("n1", "A")
    with monkeypatch.context() as m:
        deny_lookup(m, index, winerror=21)
        try:
            s.run()
        except (FileExistsError, pe.SyncError):
            pass
    assert index.exists() and not s.state.get("index_owned")
    s.run(full=True)
    assert not recycled and "PERSONAL COPIED INDEX" in index.read_text(encoding="utf-8")


def test_fixed_drive_move_carries_the_previous_version_record(drive_env, monkeypatch):
    s, cloud, root, _ = drive_env
    cloud.put("Trip/a.txt", b"CURRENT")
    s.run()
    prev = "Trip/a (previous version).txt"
    p = write(root, prev, b"PRIVATE OLD VERSION")
    fid = s._file_id(p)
    assert fid
    s._own(p, "prev", fid)  # a committed record from interrupted recycling
    original = s._trash_local
    def refuse_previous(path):
        if path == p:
            raise PermissionError("synthetic bin refusal")
        return original(path)
    monkeypatch.setattr(s, "_trash_local", refuse_previous)
    cloud.rename(cloud._item("Trip"), "Moved")
    res = s.run()
    assert res.moved == 1 and (root / "Moved" / p.name).exists()
    assert s.state.get_meta(f"prev:Moved/{p.name}") == fid and not s.state.get_meta(f"prev:{prev}")
    s.run()
    assert f"Moved/{p.name}" not in cloud.items  # never uploaded: on its way to the Recycle Bin
    assert not (root / "Moved" / p.name).exists()


def test_fixed_previous_record_matches_any_letter_case(drive_env, tmp_path):
    from tests.test_review28_codex_probes import interrupted_previous

    s, cloud, _, _, previous = interrupted_previous(drive_env, tmp_path)
    renamed = previous.with_name(previous.name.upper())
    intermediate = previous.with_name("temporary-case-rename.txt")
    previous.rename(intermediate)
    intermediate.rename(renamed)
    s.run()
    assert renamed.name not in cloud.items and renamed.name.lower() not in cloud.items


def test_fixed_missing_folder_identity_is_checked_at_top(drive_env):
    from dataclasses import replace

    s, cloud, _, _ = drive_env
    cloud.put("Trip/a.txt", b"KNOWN TAG")
    remote = cloud.walk()
    assert s._unchanged_in_icloud("Trip", dict(remote))
    remote["Trip"] = replace(remote["Trip"], docwsid="", drivewsid="")
    assert not s._unchanged_in_icloud("Trip", remote)


def test_notes_pdf_preview_preserves_unowned_output(notes_env):
    from PySide6.QtGui import QPainter, QPdfWriter
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    s, cloud, root, _ = notes_env
    assets = root / "_assets/n1"
    assets.mkdir(parents=True)
    pdf = assets / "document.pdf"
    writer = QPdfWriter(str(pdf))
    painter = QPainter(writer)
    painter.drawText(10, 10, "Dummy PDF")
    painter.end()
    del writer
    preview = pdf.with_name(pdf.name + ".p1.png")
    preview.write_bytes(b"MY FILE, NOT A GENERATED PREVIEW")
    cloud.put("n1", "With PDF", body='<object data="../_assets/n1/document.pdf"></object>')
    res = s.run(full=True)
    assert not res.errors
    assert preview.read_bytes() == b"MY FILE, NOT A GENERATED PREVIEW"


def test_notes_sketch_extension_preserves_unowned_output(notes_env):
    from PySide6.QtGui import QColor, QImage

    _, _, root, _ = notes_env
    assets = root / "_assets/n1"
    assets.mkdir(parents=True)
    page = root / "note.html"
    page.write_text('<img data-uti="com.apple.paper" data-id="D1" src="_assets/n1/prev.png">', encoding="utf-8")
    image = QImage(4, 4, QImage.Format_RGB32)
    image.fill(QColor("red"))
    assert image.save(str(assets / "prev.png"))
    collision = assets / "download.png"
    collision.write_bytes(b"MY FILE, NOT A GENERATED SKETCH")

    def download(_url, folder):
        output = folder / "download"
        upgraded = QImage(32, 32, QImage.Format_RGB32)
        upgraded.fill(QColor("red"))
        assert upgraded.save(str(output), "PNG")
        return output

    ne.upgrade_sketches(page, {"D1": "synthetic"}, download)
    assert collision.read_bytes() == b"MY FILE, NOT A GENERATED SKETCH"


@pytest.mark.parametrize("kind", ["chevron", "check", "part"])
def test_theme_image_cache_preserves_existing_bytes(notes_env, tmp_path, monkeypatch, kind):
    from PySide6.QtWidgets import QApplication
    from skyfold.ui import theme

    app = QApplication.instance() or QApplication([])
    color = "#000000"
    target = tmp_path / f"skyfold_{kind}_000000.png"
    target.write_bytes(b"USER FILE")
    monkeypatch.setattr(theme, "tempfile", SimpleNamespace(gettempdir=lambda: str(tmp_path)))
    with monkeypatch.context() as m:
        deny_lookup(m, target, winerror=21)
        if kind == "chevron":
            theme.chevron_file(color)
        else:
            theme.mark_file(color, kind)
    assert target.read_bytes() == b"USER FILE"


def test_fixed_find_my_refresh_uses_new_theme(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from skyfold.ui import dialogs, theme

    app = QApplication.instance() or QApplication([])
    device = SimpleNamespace(name="Test", model="Phone", battery=None, online=False,
                             located_at=None, lat=None, can_sound=False)
    monkeypatch.setattr(dialogs, "run_async", lambda _task, done, _fail: done([device]))
    dialog = dialogs.FindMyDialog(theme.LIGHT, lambda: object())
    try:
        original = dialog.rows.itemAt(0).widget()
        before = original._icon.pixmap().toImage()
        dialogs.refresh_palette(theme.DARK)
        assert original.c is theme.DARK and original._icon.pixmap().toImage() != before
        dialog.refresh_btn.click()
        replacement = dialog.rows.itemAt(0).widget()
        assert replacement.c is theme.DARK and replacement._icon.pixmap().toImage() != before
    finally:
        dialog.close()
