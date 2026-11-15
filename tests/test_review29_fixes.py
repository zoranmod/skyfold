"""Twenty-seventh follow-up review (Codex review 29): own regression tests next to the kept probes."""
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from skyfold import config, drive_engine as de, engine as pe, notes_engine as ne
from skyfold.drive_remote import DriveItem
from tests.test_drive import env as drive_env, write  # noqa: F401 - fixtures
from tests.test_engine import env as photos_env  # noqa: F401
from tests.test_notes import env as notes_env  # noqa: F401
from tests.test_review28_codex_probes import deny_lookup


def _private(monkeypatch, tmp_path):
    portable, private = tmp_path / "portable", tmp_path / "private"
    portable.mkdir()
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(config, "DATA_DIR", portable)
    monkeypatch.setattr(config, "SETTINGS_FILE", portable / "settings.json")
    monkeypatch.setattr(config, "PRIVATE_DIR", private)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    return portable, private


# A29-1 -----------------------------------------------------------------------------------------
def test_database_is_taken_over_with_its_journal_as_one_snapshot(tmp_path, monkeypatch):
    portable, private = _private(monkeypatch, tmp_path)
    db = portable / "state.sqlite3"
    keep = sqlite3.connect(db)
    keep.execute("PRAGMA journal_mode=WAL")
    keep.execute("PRAGMA wal_autocheckpoint=0")
    keep.execute("CREATE TABLE t (v TEXT)")
    keep.execute("INSERT INTO t VALUES ('in the journal')")
    keep.commit()
    assert (portable / "state.sqlite3-wal").stat().st_size > 0
    keep.close()
    config.migrate_private_files()
    with closing(sqlite3.connect(private / "state.sqlite3")) as conn:
        assert conn.execute("SELECT v FROM t").fetchone() == ("in the journal",)
    assert not any((portable / n).exists() for n in config.DATABASE_FILES)
    assert not (private / "state.sqlite3-wal").exists()


def test_a_journal_left_on_this_pc_keeps_the_old_database_out(tmp_path, monkeypatch):
    portable, private = _private(monkeypatch, tmp_path)
    private.mkdir()
    with closing(sqlite3.connect(portable / "state.sqlite3")) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.commit()
    (private / "state.sqlite3-wal").write_bytes(b"orphan journal")
    config.migrate_private_files()
    assert not (private / "state.sqlite3").exists() and (portable / "state.sqlite3").exists()
    assert (private / "state.sqlite3-wal").read_bytes() == b"orphan journal"


def test_a_database_that_cannot_be_read_stays_where_it_is(tmp_path, monkeypatch):
    portable, private = _private(monkeypatch, tmp_path)
    (portable / "state.sqlite3").write_bytes(b"not a database at all")
    config.migrate_private_files()
    assert (portable / "state.sqlite3").read_bytes() == b"not a database at all"
    assert not (private / "state.sqlite3").exists()
    assert not [p for p in private.iterdir() if p.name.endswith(".new")]  # no private copy left over


# A29-4 -----------------------------------------------------------------------------------------
def test_save_text_uses_no_fixed_scratch_name(tmp_path):
    target = tmp_path / "x.json"
    for name in ("x.tmp", "x.json.tmp", "x.json.new"):
        (tmp_path / name).write_bytes(b"MINE")
    config.save_text(target, "one")
    config.save_text(target, "two")
    assert target.read_text(encoding="utf-8") == "two"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.json", "x.json.new", "x.json.tmp", "x.tmp"]


# A29-3 -----------------------------------------------------------------------------------------
def test_an_empty_version_tag_is_no_tag():
    assert DriveItem("a", "d", "d", "z", "", False).etag is None
    assert DriveItem("a", "d", "d", "z", "e1", False).etag == "e1"


def test_content_of_an_untagged_item_is_never_reused(drive_env):
    s, cloud, _, _ = drive_env
    cloud.put("a.txt", b"ONE")
    s.run()
    r = cloud.walk()["a.txt"]
    s._hashes.clear()
    calls = []
    real = s.remote.download
    s.remote.download = lambda item, dest, on_bytes: (calls.append(item.etag), real(r, dest, on_bytes))
    untagged = DriveItem(r.path, r.drivewsid, r.docwsid, r.zone, None, False, r.size, r.mtime)
    s._cloud_hash(untagged)
    s._cloud_hash(untagged)
    s._cloud_hash(r)
    s._cloud_hash(r)
    assert calls == [None, None, r.etag]  # looked at again each time; a tagged version only once


# A29-2 -----------------------------------------------------------------------------------------
def test_never_synced_item_below_an_icloud_folder_keeps_it_only_files_go(drive_env):
    s, cloud, root, recycled = drive_env
    cloud.put("Trip/a.txt", b"A")
    s.run()
    write(root, "Trip/~$draft.docx", b"OFFICE LOCK")
    cloud.trash(cloud._item("Trip"))
    s.run()
    assert (root / "Trip/~$draft.docx").read_bytes() == b"OFFICE LOCK"
    assert not (root / "Trip/a.txt").exists()  # the synced file followed the deletion


# B29-1 -----------------------------------------------------------------------------------------
def test_unreadable_folder_still_holds_every_missing_photo(photos_env, monkeypatch):
    s, cloud, root, _ = photos_env
    cloud.add("A1", "actual.jpg", b"PHOTO")
    s.run(force_full=True)
    (root / s.state.files()[0].rel_path).unlink()
    (root / "locked").mkdir()
    real = os.scandir

    def scandir(p="."):
        if Path(p) == root / "locked":
            raise PermissionError(13, "denied", str(p))
        return real(p)

    with monkeypatch.context() as m:
        m.setattr(os, "scandir", scandir)
        s.run(force_full=True)
    assert cloud.deleted == []


# B29-2 -----------------------------------------------------------------------------------------
def test_quick_scan_holds_a_previous_version_in_any_letter_case(drive_env):
    s, cloud, root, _ = drive_env
    p = write(root, "Docs/A (previous version).txt", b"OLD")
    s.state.set_meta("prev:Docs/a (previous version).txt", s._file_id(p))
    local = s.scan_dirs(["docs"])
    assert "Docs/A (previous version).txt" not in local
    assert "docs/a (previous version).txt" in {x.lower() for x in s.unreadable}


def test_failed_local_move_puts_the_records_back(drive_env, monkeypatch):
    s, cloud, root, _ = drive_env
    p = write(root, "Trip/a (previous version).txt", b"OLD")
    s.state.set_meta("prev:Trip/a (previous version).txt", "1:2")

    def refuse(*_a, **_k):
        raise PermissionError(5, "denied")

    monkeypatch.setattr(de.os, "rename", refuse)
    assert not s._local_move("Trip", "Moved", de.DriveResult())
    assert s.state.get_meta("prev:Trip/a (previous version).txt") == "1:2"
    assert not s.state.get_meta("prev:Moved/a (previous version).txt")
    assert p.exists()


# B29-3 -----------------------------------------------------------------------------------------
def test_late_approval_needs_the_same_folder_not_one_made_again(drive_env):
    s, cloud, _, _ = drive_env
    cloud.put("Trip/a.txt", b"A")
    remote = cloud.walk()
    assert s._unchanged_in_icloud("Trip", dict(remote))
    cloud.items["Trip"]["id"] = "recreated"
    assert not s._unchanged_in_icloud("Trip", dict(remote))


# B29-6 -----------------------------------------------------------------------------------------
def test_unreadable_file_below_a_folder_deleted_in_icloud_keeps_everything(drive_env, monkeypatch):
    s, cloud, root, recycled = drive_env
    cloud.put("Trip/a.txt", b"A")
    cloud.put("Trip/b.txt", b"B")
    s.run()
    cloud.trash(cloud._item("Trip"))
    with monkeypatch.context() as m:
        deny_lookup(m, root / "Trip/b.txt", winerror=5)
        s.run()
    assert (root / "Trip/a.txt").exists() and (root / "Trip/b.txt").exists() and not recycled
    assert s.state.all().get("Trip/b.txt") is not None  # nothing forgotten: decided next time


def test_unreadable_pc_path_keeps_an_approved_icloud_deletion_waiting(drive_env, monkeypatch):
    s, cloud, root, _ = drive_env
    cloud.put("a.txt", b"A")
    s.run()
    remote = cloud.walk()
    res = de.DriveResult()
    base = s.state.all()
    (root / "a.txt").unlink()
    with monkeypatch.context() as m:
        deny_lookup(m, root / "a.txt", winerror=21)
        s._deletions(["a.txt"], [], remote, base, res)
    assert "a.txt" in cloud.items and s.state.all().get("a.txt") is not None


# A29-5 / A29-6 ---------------------------------------------------------------------------------
def test_pdf_page_images_take_a_free_name(tmp_path):
    from PySide6.QtGui import QPainter, QPdfWriter
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    pdf = tmp_path / "doc.pdf"
    writer = QPdfWriter(str(pdf))
    painter = QPainter(writer)
    painter.drawText(10, 10, "page")
    painter.end()
    del writer
    (tmp_path / "doc.pdf.p1.png").write_bytes(b"MINE")
    pages = ne.render_pdf_pages(pdf)
    assert len(pages) == 1 and pages[0].name != "doc.pdf.p1.png" and pages[0].read_bytes()[:4] == b"\x89PNG"
    assert (tmp_path / "doc.pdf.p1.png").read_bytes() == b"MINE"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(ne.STAGE_PREFIX)]


def test_theme_images_live_in_a_folder_of_their_own():
    from PySide6.QtWidgets import QApplication
    import tempfile
    from skyfold.ui import theme

    QApplication.instance() or QApplication([])
    path = Path(theme.chevron_file("#123456"))
    assert path.parent != Path(tempfile.gettempdir()) and path.parent.name.startswith("skyfold-ui-")
    assert path.read_bytes()[:4] == b"\x89PNG"


# B29-7 -----------------------------------------------------------------------------------------
def test_refresh_skips_a_skipped_window_also_inside_another():
    from PySide6.QtWidgets import QApplication, QWidget
    from skyfold.ui import dialogs, theme

    QApplication.instance() or QApplication([])
    outer = QWidget()
    inner = QWidget(outer)
    seen = []
    inner.apply_palette = lambda pal: seen.append(pal)
    try:
        dialogs.refresh_palette(theme.DARK, skip=(inner,))
        assert seen == []
        dialogs.refresh_palette(theme.DARK)
        assert seen == [theme.DARK]
    finally:
        outer.deleteLater()


# markers ---------------------------------------------------------------------------------------
def test_a_folder_at_the_marker_name_stops_the_sync(tmp_path):
    marker = tmp_path / pe.MARKER
    marker.mkdir()
    with pytest.raises(pe.SyncError):
        pe.marker_present(marker)


def test_an_unreadable_old_marker_name_does_not_stop_a_healthy_folder(photos_env, monkeypatch):
    s, cloud, root, _ = photos_env
    cloud.add("A1", "a.jpg", b"PHOTO")
    s.run(force_full=True)
    with monkeypatch.context() as m:
        for old in pe.LEGACY_MARKERS:
            deny_lookup(m, root / old, winerror=5)
        s.run(force_full=True)  # the current marker is there: the old names aren't looked at
