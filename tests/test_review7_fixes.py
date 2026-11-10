"""Regression tests for the seventh independent (Codex) review, of commit 3b10a4b."""

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from skyfold import drive_engine as de
from skyfold.engine import SyncError
from tests.test_drive import env, write  # noqa: F401
from tests.test_engine import env as photos_env  # noqa: F401
from tests.test_notes import env as notes_env  # noqa: F401


def _junction(target, link):
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


def _visible_bytes(root: Path) -> list[bytes]:
    return [p.read_bytes() for p in root.rglob("*") if p.is_file() and not p.name.startswith(".")]


# --- Drive: putting a download in place ------------------------------------------------------------
def test_a_displaced_edit_with_the_old_size_and_time_is_recoverable(env):  # noqa: F811
    s, d, root, _ = env
    d.put("a.txt", b"base")
    s.run()
    recycled = []
    s._trash_local = lambda p: (recycled.append(p.read_bytes()), p.unlink())
    d.put("a.txt", b"a longer version from the phone")
    real = d.download

    def download(item, dest, cb):
        real(item, dest, cb)
        p = root / "a.txt"
        st = p.stat()
        p.write_bytes(b"edit")  # same size as "base"
        os.utime(p, (st.st_atime, st.st_mtime))  # and the same time

    d.download = download
    s.run()
    assert b"edit" in recycled or b"edit" in _visible_bytes(root)


def test_a_failed_final_step_never_hides_the_pc_version(env, monkeypatch):  # noqa: F811
    s, d, root, _ = env
    d.put("a.txt", b"v1")
    s.run()
    d.put("a.txt", b"from the phone")
    real_download, real_rename = d.download, os.rename

    def download(item, dest, cb):
        real_download(item, dest, cb)
        (root / "a.txt").write_bytes(b"my new local revision")

    def rename(src, dst, *a, **k):
        if Path(src).name.startswith(de.TMP_PREFIX) and not Path(src).name.startswith(de.ASIDE_PREFIX) \
                and Path(dst).name == "a.txt":
            raise PermissionError(13, "locked")
        return real_rename(src, dst, *a, **k)

    d.download = download
    monkeypatch.setattr(de.os, "rename", rename)
    s.run()
    assert b"my new local revision" in _visible_bytes(root)


def test_a_version_left_aside_by_a_crash_is_shown_again(env):  # noqa: F811
    s, d, root, _ = env
    d.put("a.txt", b"cloud")
    s.run()
    (root / f"{de.ASIDE_PREFIX}123-456-a.txt").write_bytes(b"left by a crash")
    s.run()
    assert b"left by a crash" in _visible_bytes(root)


# --- Drive: partly listed folders ------------------------------------------------------------------
def test_a_partly_listed_root_keeps_the_synced_twin(env):  # noqa: F811
    s, d, root, _ = env
    d.put("docs/a.txt", b"a")
    s.run()
    d.put("Docs/b.txt", b"twin")
    s.run()
    real_walk = d.walk

    def short_walk(*a, folders=None, **k):
        out = real_walk(*a, folders=folders, **k)
        if folders is None:
            for p in [p for p in out if p == "docs" or p.startswith("docs/")]:
                del out[p]
            d.incomplete = {""}
        return out

    d.walk = short_walk
    s.run()
    d.walk = real_walk
    d.incomplete = set()
    write(root, "docs/c.txt", b"new", age=30)
    s.run(changed={"Docs/c.txt"})
    assert "Docs/c.txt" not in d.items


def test_a_rename_held_for_a_partial_listing_does_nothing_half(env):  # noqa: F811
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
    assert "Project/b.txt" in d.items and not any(p.startswith("Renamed") for p in d.items)


# --- Drive: approval given while the pass runs -----------------------------------------------------
def test_an_approved_folder_deletion_waits_if_icloud_got_something_new(env):  # noqa: F811
    import shutil

    s, d, root, _ = env
    s.delete_threshold = 1
    d.put("Project/a.txt", b"a")
    d.put("Project/b.txt", b"b")
    d.put("Other/c.txt", b"c")
    s.run()
    shutil.rmtree(root / "Project")
    d.put("Other/c.txt", b"changed")  # gives the pass a download, during which the approval arrives

    def on_pending(to_cloud, to_pc):
        d.put("Project/new.txt", b"added in iCloud after the question")
        s.approved |= set(to_cloud)

    s.on_pending = on_pending
    s.run()
    assert "Project/new.txt" in d.items


# --- Notes -----------------------------------------------------------------------------------------
def test_a_personal_page_saved_during_the_export_is_kept(notes_env):  # noqa: F811
    s, r, root, _ = notes_env
    r.put("n1", "Budget")
    real = r.export

    def export(note_id, out_dir, filename, assets_dir):
        (root / "Notes").mkdir(parents=True, exist_ok=True)
        (root / "Notes/Budget.html").write_text("<html>mine</html>", encoding="utf-8")
        real(note_id, out_dir, filename, assets_dir)

    r.export = export
    s.run()
    assert (root / "Notes/Budget.html").read_text(encoding="utf-8") == "<html>mine</html>"


def test_a_comment_added_to_an_exported_page_is_recoverable(notes_env):  # noqa: F811
    s, r, root, _ = notes_env
    r.put("n1", "Budget")
    s.run()
    recycled = []
    s._trash = lambda p: (recycled.append(p.read_text(encoding="utf-8")), p.unlink())
    page = root / "Notes/Budget.html"
    page.write_text(page.read_text(encoding="utf-8") + "<!-- my comment -->", encoding="utf-8")
    r.put("n1", "Budget", body="edited in iCloud")
    s.run()
    texts = recycled + [p.read_text(encoding="utf-8") for p in root.rglob("*.html")]
    assert any("my comment" in x for x in texts)


def test_a_personal_index_saved_at_the_last_moment_is_kept(notes_env, monkeypatch):  # noqa: F811
    from skyfold import notes_engine as ne

    s, r, root, _ = notes_env
    r.put("n1", "Budget")
    real = ne.tempfile.mkstemp

    def mkstemp(*a, **k):
        if k.get("prefix") == ".skyfold-index-":
            (root / ne.INDEX).write_text("<html>my own index</html>", encoding="utf-8")
        return real(*a, **k)

    monkeypatch.setattr(ne.tempfile, "mkstemp", mkstemp)
    with pytest.raises(SyncError):
        s.run()
    assert (root / ne.INDEX).read_text(encoding="utf-8") == "<html>my own index</html>"


# --- Photos ----------------------------------------------------------------------------------------
def test_a_file_saved_where_an_album_link_goes_is_never_overwritten(photos_env, monkeypatch):  # noqa: F811
    s, r, root, _ = photos_env
    r.add("A1", "IMG.JPG", b"photo")
    r.album_map = {"Trip": ["A1"]}
    real = os.link

    def link(src, dst, *a, **k):
        Path(dst).write_bytes(b"saved right now")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(os, "link", link)
    s.run()
    assert (root / "Albums/Trip/IMG.JPG").read_bytes() == b"saved right now"


def test_album_cleanup_removes_only_the_file_it_checked(photos_env, monkeypatch):  # noqa: F811
    s, r, root, _ = photos_env
    r.add("A1", "IMG.JPG", b"photo")
    r.album_map = {"Trip": ["A1"]}
    s.run()
    entry = root / "Albums/Trip/IMG.JPG"
    real = os.path.samefile

    def samefile(a, b):
        same = real(a, b)
        if entry.exists():
            entry.unlink()
        entry.write_bytes(b"saved right after the check")
        return same

    monkeypatch.setattr(os.path, "samefile", samefile)
    s.settings.albums_as_folders = False
    s.run(force_full=True)
    assert entry.exists() and entry.read_bytes() == b"saved right after the check"


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
def test_photos_never_trash_through_a_junction(photos_env, tmp_path):  # noqa: F811
    s, r, root, _ = photos_env
    r.add("A1", "IMG.JPG", b"photo")
    s.run()
    month = next(p.parent for p in root.rglob("IMG.JPG"))
    month.rename(month.with_name(month.name + "b"))
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "IMG.JPG").write_bytes(b"photo")  # unrelated file, same name and size
    _junction(archive, month)
    r.remove("A1")
    s.run(force_full=True)
    assert (archive / "IMG.JPG").exists()


# --- sign-in ---------------------------------------------------------------------------------------
def test_a_late_save_of_an_old_sign_in_never_overwrites_the_new_one(monkeypatch, tmp_path):
    from pyicloud.session import PyiCloudSession

    from skyfold import auth

    old = SimpleNamespace(_cookie_directory=str(tmp_path))
    new = SimpleNamespace(_cookie_directory=str(tmp_path))
    written = []
    monkeypatch.setattr(PyiCloudSession, "_save_session_data", lambda self: written.append(self))
    monkeypatch.setattr(auth, "_CURRENT", old)
    auth._serialize_session_saves()
    save = PyiCloudSession._save_session_data
    auth._SAVE_LOCK.acquire()
    th = threading.Thread(target=save, args=(old,))
    th.start()
    time.sleep(0.2)  # the old writer is now waiting for the lock
    auth._CURRENT = new  # signed out and back in meanwhile
    auth._SAVE_LOCK.release()
    th.join(5)
    assert old not in written


def test_cookie_values_never_reach_the_log():
    from skyfold.app import _RedactEmails

    text = ("TokenException: Invalid X-APPLE-WEBAUTH-VALIDATE cookie value "
            "X-APPLE-WEBAUTH-VALIDATE=v=1:t=AQAAAABnSecretSecret123; session_token=AbCdEf123456")
    clean = _RedactEmails.clean(text)
    assert "SecretSecret" not in clean and "AbCdEf123456" not in clean


def test_ordinary_log_lines_are_left_readable():
    from skyfold.app import _RedactEmails

    line = "Drive: downloaded: Documents/Invoices/2025 March.pdf (3 files)"
    assert _RedactEmails.clean(line) == line


# --- release ---------------------------------------------------------------------------------------
def test_the_release_zip_is_built_from_an_allowlist_and_checked():
    yml = (Path(__file__).parent.parent / ".github/workflows/build.yml").read_text(encoding="utf-8")
    assert "zip-stage" in yml and "Runtime data found in the zip" in yml and "is not on main" in yml


# --- panel -----------------------------------------------------------------------------------------
def _flyout():
    from PySide6.QtWidgets import QApplication

    from skyfold.ui import theme
    from skyfold.ui.flyout import Flyout

    app = QApplication.instance() or QApplication([])
    c = theme.palette("light")
    app.setStyleSheet(theme.stylesheet(c))
    return app, Flyout(c)


def test_the_buttons_and_service_rows_stay_whole_with_everything_shown():
    app, f = _flyout()
    f.set_stats({"photos": 1, "videos": 0, "bytes": 1, "last_sync": None, "drive_enabled": True,
                 "notes_enabled": True})
    for i in range(10):
        f.add_activity("2026-11-10T08:00:00+00:00", "download", f"Drive: downloaded: Docs/file{i}.txt", "x")
    f.show_at(None)
    app.processEvents()
    f.set_state("syncing", "Drive: 1 of 2")
    f.set_progress(1, 2, "Drive: 1 of 2 files · 2.1 MB/s · about 1 min left")
    f.set_pending(SimpleNamespace(to_pc=[], to_cloud=["Project"], files_to_cloud=12))
    f.set_state("error", "Something went wrong")
    app.processEvents()
    limit = f.screen().availableGeometry()
    assert f.geometry().bottom() <= limit.bottom() and f.width() <= limit.width()
    assert f._bottom.isVisible() and f._bottom.geometry().bottom() <= f.height()
    for row in (f.row_photos, f.row_drive, f.row_notes):
        assert row.height() >= row.minimumSizeHint().height()
    f.hide()


def test_service_rows_work_with_the_keyboard():
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    app, f = _flyout()
    assert f.row_drive.focusPolicy() & Qt.TabFocus
    hits = []
    f.serviceClicked.connect(hits.append)
    f.row_drive.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))
    assert hits == ["drive"]
