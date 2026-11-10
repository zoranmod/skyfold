"""Regression tests for the sixth independent (Codex) review, of commit 9709ded."""

import os
import shutil

import pytest

from skyfold import drive_engine as de
from skyfold.drive_remote import ROOT_ID, ICloudDrive
from skyfold.engine import SyncError
from tests.test_drive import env, write  # noqa: F401
from tests.test_engine import env as photos_env  # noqa: F401
from tests.test_notes import env as notes_env  # noqa: F401


def _junction(target, link):
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


# --- Photos ----------------------------------------------------------------------------------------
def test_a_file_put_in_place_of_an_album_link_is_never_deleted_for_good(photos_env):  # noqa: F811
    s, r, root, trashed = photos_env
    r.add("A1", "IMG.JPG", b"photo")
    r.album_map = {"Trip": ["A1"]}
    s.run()
    link = root / "Albums/Trip/IMG.JPG"
    link.unlink()
    link.write_bytes(b"my own edited copy")  # replaced the link with a real file
    s.settings.albums_as_folders = False
    s.run(force_full=True)
    assert link.exists() or "IMG.JPG" in trashed  # kept, or in the Recycle Bin - never gone for good


# --- Notes -----------------------------------------------------------------------------------------
def test_a_personal_page_at_a_notes_old_path_is_not_overwritten(notes_env):  # noqa: F811
    s, r, root, _ = notes_env
    r.put("n1", "Budget")
    s.run()
    page = root / "Notes/Budget.html"
    page.write_text("<html>my personal budget page</html>", encoding="utf-8")
    r.put("n1", "Budget", body="edited in iCloud")
    s.run()
    assert page.read_text(encoding="utf-8") == "<html>my personal budget page</html>"
    assert (root / "Notes/Budget (2).html").exists()


# --- Drive ---------------------------------------------------------------------------------------
def test_icloud_edit_inside_a_folder_renamed_on_the_pc_comes_down(env):  # noqa: F811
    s, d, root, _ = env
    d.put("Project/a.txt", b"base")
    s.run()
    d.items["Project/a.txt"]["data"] = b"edit"  # same size, edited on the phone
    d.items["Project/a.txt"]["etag"] = d._etag()
    d.items["Project/a.txt"]["mtime"] = 1_600_000_150.0
    real_item = d._item

    def item(p):
        it = real_item(p)
        m = d.items[p].get("mtime")
        return it if m is None else de.DriveItem(it.path, it.drivewsid, it.docwsid, it.zone, it.etag, it.is_dir,
                                                 it.size, m)

    d._item = item
    (root / "Project").rename(root / "Renamed")
    s.run()
    s.run()
    assert d.items["Renamed/a.txt"]["data"] == b"edit"
    assert (root / "Renamed/a.txt").read_bytes() == b"edit"


def test_refusing_another_folder_keeps_what_the_database_knows(env, tmp_path):  # noqa: F811
    s, d, root, _ = env
    d.put("a.txt", b"a")
    s.run()
    rows_before = len(s.state.all())
    other = tmp_path / "Other"
    other.mkdir()
    (other / de.MARKER).write_text("This folder is synced by Skyfold.\n", encoding="utf-8")
    s2 = de.DriveSyncer(s.state, d, other, delete_threshold=3)
    s2.account = "account-a"
    with pytest.raises(SyncError):
        s2.run()
    assert len(s.state.all()) == rows_before


def test_a_save_at_the_moment_of_publishing_is_kept(env):  # noqa: F811
    s, d, root, _ = env
    d.put("a.txt", b"v1")
    s.run()
    d.put("a.txt", b"from the phone")
    real = d.download

    def download(item, dest, cb):
        real(item, dest, cb)
        (root / "a.txt").write_bytes(b"saved by the user right now")  # after the last check

    d.download = download
    s.run()
    texts = [p.read_bytes() for p in root.iterdir() if p.is_file() and not p.name.startswith(".")]
    assert b"saved by the user right now" in texts


def test_a_folder_answer_with_an_error_is_never_read_as_empty(monkeypatch):
    remote = ICloudDrive(api=None)
    remote._drive = object()
    monkeypatch.setattr(remote, "_folders", lambda ids: [{"drivewsid": ROOT_ID, "status": "NOT_FOUND"}])
    with pytest.raises(Exception):
        remote.walk()


def test_a_partly_listed_subfolder_blocks_renaming_its_parent(env):  # noqa: F811
    s, d, root, _ = env
    d.put("Project/Sub/a.txt", b"a")
    d.put("Project/b.txt", b"b")
    s.run()
    (root / "Project").rename(root / "Renamed")
    real_walk = d.walk

    def short_walk(*a, **k):
        out = real_walk(*a, **k)
        d.incomplete = {"Project/Sub"}
        return out

    d.walk = short_walk
    s.run()
    assert d.renamed == [] and "Project/Sub/a.txt" in d.items


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
def test_approval_waits_when_a_junction_appears_inside(env, tmp_path):  # noqa: F811
    s, d, root, trashed = env
    s.delete_threshold = 1
    d.put("Project/a.txt", b"a")
    d.put("Project/b.txt", b"b")
    s.run()
    for p in [p for p in d.items if p.startswith("Project")]:
        del d.items[p]
    s.run()
    outside = tmp_path / "outside"
    outside.mkdir()
    _junction(outside, root / "Project/linked")
    s.approved |= {"Project"}
    s.run()
    assert "Project" not in trashed


# --- panel -------------------------------------------------------------------------------------------
def test_progress_never_pushes_the_buttons_off_screen():
    from PySide6.QtWidgets import QApplication

    from skyfold.ui import theme
    from skyfold.ui.flyout import Flyout

    app = QApplication.instance() or QApplication([])
    c = theme.palette("dark")
    app.setStyleSheet(theme.stylesheet(c))
    f = Flyout(c)
    f.set_stats({"photos": 1, "videos": 0, "bytes": 1, "last_sync": None, "drive_enabled": True,
                 "notes_enabled": True})
    for i in range(10):
        f.add_activity("2026-11-10T08:00:00+00:00", "download", f"Drive: downloaded: Docs/file{i}.txt", "x")
    f.show_at(None)
    app.processEvents()
    f.set_state("syncing", "Drive: 1 of 2")
    f.set_progress(1, 2, "Drive: 1 of 2 files · 2.1 MB/s · about 1 min left")
    app.processEvents()
    limit = f.screen().availableGeometry()
    assert f.geometry().bottom() <= limit.bottom() and f.height() <= limit.height()
    f.hide()


def test_extensionless_drive_files_show_name_and_folder():
    from skyfold.ui.flyout import split_activity

    title, detail = split_activity("Drive: downloaded: README", "download")
    assert title == "README" and detail.startswith("Downloaded")
