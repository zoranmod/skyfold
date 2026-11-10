"""Regression tests for the Fable 5.2 audit of commit 4de45db (before the eighth Codex review)."""

import os
import shutil
import time
from datetime import datetime, timezone

import pytest

from tests.test_engine import env as photos_env  # noqa: F401
from tests.test_notes import env as notes_env  # noqa: F401


def _junction(target, link):
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


def _age(p, seconds=7200):
    t = time.time() - seconds
    os.utime(p, (t, t))


# --- Photos ----------------------------------------------------------------------------------------
def test_a_download_that_keeps_failing_doesnt_read_everything_on_every_poll(photos_env):  # noqa: F811
    s, r, root, _ = photos_env
    r.add("A1", "ok.jpg", b"x" * 10)
    r.add("BAD", "big.mov", b"v" * 1000, video=True)
    r.assets["BAD"].resources[0].size = 999  # always fails the size check
    tried = []
    real = r.download
    r.download = lambda res, dest, cb: (tried.append(res.filename), real(res, dest, cb))[1]
    for _ in range(4):
        s.run()
    assert r.full_reads == 1 and tried.count("big.mov") == 1


def test_bringing_back_a_deleted_folder_brings_back_live_photo_videos(photos_env):  # noqa: F811
    s, r, root, _ = photos_env
    for i in range(5):
        r.add(f"A{i}", f"IMG_{i}.HEIC", b"x" * (10 + i), live=b"v" * (5 + i))
    s.run()
    shutil.rmtree(root / "2025")
    res = s.run()
    assert res.pending and len(res.pending.to_cloud) == 5
    s.reject_to_cloud(res.pending.to_cloud)
    s.run(force_full=True)
    assert any(p.suffix.upper() == ".MOV" for p in (root / "2025/11").iterdir())


def test_album_entries_follow_the_album_when_names_collide(photos_env):  # noqa: F811
    s, r, root, _ = photos_env
    r.add("AAAAAA1", "IMG_1.JPG", b"x" * 10, date=datetime(2025, 1, 3, tzinfo=timezone.utc))
    r.add("BBBBBB2", "IMG_1.JPG", b"y" * 11, date=datetime(2025, 2, 3, tzinfo=timezone.utc))
    r.album_map = {"Trip": ["AAAAAA1", "BBBBBB2"]}
    s.run()
    r.album_map = {"Trip": ["BBBBBB2", "AAAAAA1"]}
    s.state.set("albums_refreshed", None)
    s.sync_albums(refresh=True)
    contents = {p.read_bytes() for p in (root / "Albums/Trip").iterdir()}
    assert contents == {b"x" * 10, b"y" * 11}


def test_moving_a_photo_into_the_albums_folder_is_not_a_deletion(photos_env):  # noqa: F811
    s, r, root, _ = photos_env
    r.add("A1", "a.jpg", b"x" * 10)
    s.run()
    (root / "Albums/Best").mkdir(parents=True)
    os.rename(root / "2025/11/a.jpg", root / "Albums/Best/a.jpg")
    s.run()
    assert not r.deleted


def test_a_photo_icloud_didnt_answer_for_is_not_forgotten(photos_env):  # noqa: F811
    s, r, root, _ = photos_env
    r.add("A1", "a.jpg", b"x" * 10)
    s.run()
    (root / "2025/11/a.jpg").unlink()
    real = r.lookup
    r.lookup = lambda ids: {}
    s.run()
    r.lookup = real
    res = s.run(force_full=True)
    assert res.downloaded == 0


def test_a_leftover_private_file_never_blocks_album_cleanup(photos_env):  # noqa: F811
    import threading

    from skyfold import engine as eng

    s, r, root, _ = photos_env
    r.add("A1", "a.jpg", b"x" * 10)
    r.add("A2", "b.jpg", b"y" * 10)
    r.album_map = {"Trip": ["A1", "A2"]}
    s.run()
    d = root / "Albums/Trip"
    (d / f"{eng.TMP_PREFIX}album-{os.getpid()}-{threading.get_ident()}.jpg").write_bytes(b"left over")
    s.settings.albums_as_folders = False
    s.sync_albums(refresh=False)
    assert not s.state.album_links()


# --- Notes -----------------------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
def test_the_notes_tidy_up_never_goes_through_a_junction(notes_env, tmp_path):  # noqa: F811
    s, r, root, trashed = notes_env
    for i in range(4):
        r.put(f"n{i}", f"T{i}")
    s.run()
    outside = tmp_path / "Backup"
    outside.mkdir()
    (outside / "Old.html").write_text((root / "Notes/T0.html").read_text(encoding="utf-8"), encoding="utf-8")
    _junction(outside, root / "Link")
    s.run(full=True)
    assert (outside / "Old.html").exists()


def test_copies_of_pages_kept_in_another_folder_are_left_alone(notes_env):  # noqa: F811
    s, r, root, trashed = notes_env
    for i in range(30):
        r.put(f"n{i}", f"T{i}")
    s.run()
    (root / "My keepers").mkdir()
    for i in range(5):
        shutil.copy2(root / f"Notes/T{i}.html", root / "My keepers" / f"T{i}.html")
    s.run(full=True)
    assert not trashed


def test_a_note_that_fails_twice_is_still_tried_again(notes_env):  # noqa: F811
    s, r, root, _ = notes_env
    r.put("n1", "A")
    s.run()
    r.put("n1", "A", body="new text")
    real = r.export

    def boom(*a, **k):
        raise ConnectionError("net down")

    r.export = boom
    s.run()
    s.run()
    r.export = real
    s.run()
    assert "new text" in (root / "Notes/A.html").read_text(encoding="utf-8")


def test_a_page_with_an_attachment_that_didnt_download_keeps_the_old_page(notes_env):  # noqa: F811
    s, r, root, _ = notes_env
    r.put("n1", "A", body="good version")
    s.run()
    r.put("n1", "A", body='<img src="https://cvws.icloud-content.com/B/abc/x.jpg">')
    s.run()
    assert "good version" in (root / "Notes/A.html").read_text(encoding="utf-8")


def test_a_long_folder_name_ending_in_a_space_still_exports(notes_env):  # noqa: F811
    s, r, root, _ = notes_env
    r.dirs = {"F1": ("a" * 119 + " b", None)}
    r.put("n1", "T1")
    res = s.run()
    assert res.exported == 1 and res.errors == 0


def test_pages_from_before_are_recognised_after_the_database_is_rebuilt(notes_env):  # noqa: F811
    s, r, root, trashed = notes_env
    for i in range(5):
        r.put(f"n{i}", f"T{i}")
    s.run()
    s.state.reset()
    s.run()
    assert not trashed


def test_private_files_left_by_a_crash_are_tidied(notes_env):  # noqa: F811
    s, r, root, _ = notes_env
    r.put("n1", "A")
    s.run()
    for p in (root / "Notes/.skyfold-tmp-note-dead.html", root / ".skyfold-index-dead.tmp"):
        p.write_text("<html>raw</html>", encoding="utf-8")
        _age(p)
    s.run(full=True)
    assert not [p for p in root.rglob(".skyfold-*") if p.name != ".skyfold-notes" and p.is_file()
                and p.name.startswith((".skyfold-tmp", ".skyfold-index"))]


def test_a_moved_aside_index_comes_back_after_a_crash(notes_env):  # noqa: F811
    from skyfold import notes_engine as ne

    s, r, root, _ = notes_env
    r.put("n1", "A")
    s.run()
    aside = root / f"{ne.STAGE_PREFIX}index-dead.html"
    os.rename(root / ne.INDEX, aside)
    _age(aside)
    s._tidy_leftovers()
    assert (root / ne.INDEX).exists() and not aside.exists()


# --- panel -----------------------------------------------------------------------------------------
def _flyout():
    from PySide6.QtWidgets import QApplication

    from skyfold.ui import theme
    from skyfold.ui.flyout import Flyout

    app = QApplication.instance() or QApplication([])
    c = theme.palette("dark")
    app.setStyleSheet(theme.stylesheet(c))
    f = Flyout(c)
    f.set_stats({"photos": 1, "videos": 0, "bytes": 1, "last_sync": None, "drive_enabled": True,
                 "notes_enabled": True})
    return app, f


@pytest.mark.parametrize("entries", [0, 1, 10])
def test_the_panel_content_needs_no_scrolling_when_the_screen_has_room(entries):
    app, f = _flyout()
    for i in range(entries):
        f.add_activity("2026-11-10T08:00:00+00:00", "download", f"Drive: downloaded: Docs/file{i}.txt", "x")
    f.show_at(None)
    app.processEvents()
    app.processEvents()
    assert f._scroll.verticalScrollBar().maximum() == 0
    f.hide()


def test_a_new_file_entry_doesnt_close_the_open_panel():
    app, f = _flyout()
    f.show_at(None)
    app.processEvents()
    f.add_activity("2026-11-10T08:00:00+00:00", "download", "Downloaded: 2026/11/IMG_9999.HEIC", "x")
    app.processEvents()
    assert f.isVisible()
    f.hide()


def test_activity_entries_open_with_the_keyboard(monkeypatch):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from skyfold.ui import flyout as fl

    app, f = _flyout()
    opened = []
    monkeypatch.setattr(fl, "reveal", opened.append)
    f.add_activity("2026-11-10T08:00:00+00:00", "download", "Drive: downloaded: Docs/a.txt", "C:/x/a.txt")
    row = f._activity_rows()[0]
    assert row.focusPolicy() & Qt.TabFocus and row.accessibleName()
    row.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert opened == ["C:/x/a.txt"]


def test_first_run_layout_choices_work_with_the_keyboard():
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    from skyfold.ui.dialogs import LayoutOption

    QApplication.instance() or QApplication([])
    opt = LayoutOption("Year / month", "2025/11/IMG.JPG")
    hits = []
    opt.clicked.connect(lambda: hits.append(1))
    opt.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))
    assert opt.focusPolicy() & Qt.TabFocus and hits == [1]


# --- sign-in and privacy ---------------------------------------------------------------------------
@pytest.fixture()
def offline_auth(tmp_path, monkeypatch):
    import requests

    from skyfold import auth as a

    monkeypatch.setattr(a, "SESSION_DIR", tmp_path / "session")
    monkeypatch.setattr(a, "SIGNED_OUT", tmp_path / "signed-out")
    monkeypatch.setattr(a, "_CURRENT", None)
    a._serialize_session_saves()
    monkeypatch.setattr(requests.Session, "request", lambda *k, **kw: (_ for _ in ()).throw(AssertionError("network")))
    return a


def _api(a, apple_id):
    from pyicloud import PyiCloudService

    a.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return PyiCloudService(apple_id, "pw", cookie_directory=str(a.SESSION_DIR), authenticate=False,
                           refresh_interval=None)


def test_signing_in_with_another_apple_id_removes_the_old_session(offline_auth):
    a = offline_auth
    first = _api(a, "alice@example.com")
    first.session.data.update(session_token="A-TOKEN")
    a.commit_login(first)
    assert any("alice" in p.name for p in a.SESSION_DIR.iterdir())
    second = _api(a, "bob@example.com")
    second.session.data.update(session_token="B-TOKEN")
    a.commit_login(second)
    assert not any("alice" in p.name for p in a.SESSION_DIR.iterdir())


def test_the_photos_database_of_one_apple_id_is_never_used_for_another(tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication

    from skyfold import engine as eng
    from skyfold import worker as wk
    from skyfold.config import Settings
    from tests.test_engine import FakeRemote

    QCoreApplication.instance() or QCoreApplication([])
    trashed = []
    monkeypatch.setattr(eng.Syncer, "_trash", lambda self, p: (trashed.append(p.name), p.unlink()))
    monkeypatch.setattr(wk, "STATE_DB", tmp_path / "state.db")
    remotes = {"A": FakeRemote(), "B": FakeRemote()}
    monkeypatch.setattr(wk, "ICloudRemote", lambda api: remotes[api])
    monkeypatch.setattr(wk.auth, "make_current", lambda api: None)
    for i in range(5):
        remotes["A"].add(f"A{i}", f"ALICE_{i}.JPG", b"alice")
    remotes["B"].add("B0", "BOB_0.JPG", b"bob")
    photos = tmp_path / "Pictures"
    monkeypatch.setattr(wk.auth, "restore", lambda _id: "A")
    w = wk.SyncWorker(Settings(apple_id="alice@example.com", photos_dir=str(photos), photos_dir_confirmed=True))
    w.start()
    w._stop_watchers()
    w.poll.stop()
    monkeypatch.setattr(wk.auth, "restore", lambda _id: None)
    w2 = wk.SyncWorker(Settings())  # settings were lost: no Apple ID remembered
    w2.start()
    s = w2.settings.copy()
    s.photos_dir, s.photos_dir_confirmed, s.apple_id = str(photos), True, "bob@example.com"
    w2.apply_settings(s, False, False)
    w2.set_api("B")
    w2.full_check()
    w2._stop_watchers()
    w2.poll.stop()
    assert not trashed and not w2._pending


@pytest.mark.parametrize("line, secret", [
    ('Cookie: X-APPLE-WEBAUTH-USER="v=1:s=0:d=12345678"; aasp=ABCDEF0123456789', "12345678"),
    ('{"fullName":"John Doe","firstName":"John"}', "John"),
    (r"Failed to load cookie jar C:\x\Skyfold\session\johndoeicloudcom.cookiejar", "johndoe"),
    ('{"password": "hunter2secret"}', "hunter2"),
    ("Authorization: Bearer abcDEF123456ghi", "abcDEF"),
    ("Authenticating as +38761123456 failed", "38761123456"),
])
def test_secrets_never_reach_the_log(line, secret):
    from skyfold.app import _RedactEmails

    assert secret not in _RedactEmails.clean(line)


@pytest.mark.parametrize("line", [
    "Couldn't save the session: disk full",
    "Notes: change token: unchanged since last read",
    "[download] Downloaded 2024-05-01_family_trip_to_the_mountains_and_lake_001.jpg",
])
def test_ordinary_log_lines_stay_readable(line):
    from skyfold.app import _RedactEmails

    assert _RedactEmails.clean(line) == line


def test_the_log_filter_is_fast_on_long_text():
    from skyfold.app import _RedactEmails

    start = time.perf_counter()
    _RedactEmails.clean("a" * 40_000 + " @")
    _RedactEmails.clean("word-" * 8_000)
    assert time.perf_counter() - start < 1.0


# --- iCloud Drive ----------------------------------------------------------------------------------
from skyfold import drive_engine as de  # noqa: E402
from skyfold.drive_remote import DriveItem  # noqa: E402
from tests.test_drive import env, write  # noqa: E402,F401


def test_a_folder_skyfold_made_that_vanished_in_icloud_is_made_again(env):  # noqa: F811
    s, d, root, _ = env
    d.put("keep.txt", b"k")
    s.run()
    write(root, "New/f.txt", b"important", age=30)
    real_upload = d.upload
    d.upload = lambda *a, **k: (_ for _ in ()).throw(ValueError("refused"))
    s.run()  # the folder is made in iCloud, the file couldn't go up yet
    d.upload = real_upload
    d.trash(DriveItem("New", "", "", "", None, True))  # another device removed the folder before the next read
    for _ in range(6):
        s.run()
    print(sorted(d.items), sorted(p.relative_to(root).as_posix() for p in root.rglob("*")), s.state.all().get("New"))
    assert "New/f.txt" in d.items  # never stuck: the folder is made again and the file follows


def test_new_nested_folders_go_up_in_one_pass(env):  # noqa: F811
    s, d, root, _ = env
    d.put("keep.txt", b"k")
    s.run()
    write(root, "A/B/C/D/f.txt", b"deep", age=30)
    s.run()
    assert "A/B/C/D/f.txt" in d.items


def test_a_folder_renamed_in_icloud_and_seen_by_a_quick_pass_is_renamed_here(env):  # noqa: F811
    s, d, root, trashed = env
    s.delete_threshold = 25
    for i in range(4):
        d.put(f"A/Old/f{i}.txt", f"data{i}".encode())
    d.put("A/keep.txt", b"k")
    s.run()
    d._relocate("A/Old", "A/New")
    write(root, "A/note.txt", b"n")
    r = s.run(changed={"A/note.txt"})
    assert r.moved == 1 and r.downloaded == 0 and not trashed


def test_a_file_replaced_by_a_folder_in_icloud_is_not_trashed(env):  # noqa: F811
    s, d, root, _ = env
    d.put("Notes", b"a file without extension")
    d.put("keep1.txt", b"1")
    s.run()
    d.trash(DriveItem("Notes", "", "", "", None, False))
    d.put("Notes/new1.txt", b"brand new")
    (root / "Notes").unlink()
    s.run()
    assert "Notes/new1.txt" in d.items


def test_a_move_never_passes_through_a_taken_name(env):  # noqa: F811
    s, d, root, _ = env
    d.put("G/a.txt", b"moved file")
    d.put("F/a.txt", b"another file")
    s.run()
    os.rename(root / "G/a.txt", root / "F/d.txt")
    collisions = []
    real_move = d.move

    def move(item, folder):
        if f"{folder.path}/{item.path.rsplit('/', 1)[-1]}" in d.items:
            collisions.append(item.path)
        return real_move(item, folder)

    d.move = move
    s.run(changed={"G/a.txt", "F/d.txt"})
    assert not collisions and d.items["F/a.txt"]["data"] == b"another file"


def test_a_failed_date_step_after_a_download_makes_no_false_conflict(env, monkeypatch):  # noqa: F811
    from pathlib import Path

    s, d, root, _ = env
    d.put("a.txt", b"base")
    s.run()
    d.put("a.txt", b"new from phone")
    real = os.utime
    calls = {"n": 0}

    def utime(p, *a, **k):
        if Path(p).name == "a.txt" and calls["n"] == 0:
            calls["n"] += 1
            raise PermissionError(13, "scanner has it open")
        return real(p, *a, **k)

    monkeypatch.setattr(de.os, "utime", utime)
    for _ in range(3):
        s.run()
    assert not d.uploads


def test_changes_saved_during_a_full_pass_stay_queued():
    import threading
    from types import SimpleNamespace

    from skyfold import worker as wk

    w = SimpleNamespace(_drive_lock=threading.Lock(), _drive_changes=set(), _drive_retry=set(), _drive_last=0.0,
                        state=SimpleNamespace(get=lambda k: "x", set=lambda k, v: None),
                        settings=SimpleNamespace(notifications=False),
                        _drivePendingSeen=SimpleNamespace(emit=lambda *a: None),
                        _driveChange=SimpleNamespace(emit=lambda: None), notify=SimpleNamespace(emit=lambda *a: None))

    def run(changed=None):
        w._drive_changes.add("Docs/saved-during-the-pass.txt")
        return de.DriveResult(full=True)

    w.drive = SimpleNamespace(run=run)
    assert wk.SyncWorker._run_drive(w, [], None)
    assert w._drive_changes


def test_a_crash_left_copy_of_the_synced_version_goes_to_the_recycle_bin(env):  # noqa: F811
    s, d, root, _ = env
    d.put("a.txt", b"base")
    s.run()
    st = (root / "a.txt").stat()
    aside = root / f"{de.ASIDE_PREFIX}1234abcd-a.txt"
    aside.write_bytes(b"base")
    os.utime(aside, (st.st_atime, st.st_mtime))
    recycled = []
    s._trash_local = lambda p: (recycled.append(p.name), p.unlink())
    s.run()
    assert recycled and not any("conflict" in p.name for p in root.iterdir())


def test_old_unfinished_downloads_are_removed(env):  # noqa: F811
    s, d, root, _ = env
    d.put("a.txt", b"x")
    s.run()
    left = root / f"{de.TMP_PREFIX}abcd1234"
    left.write_bytes(b"half")
    _age(left, 2 * 86400)
    s.run()
    assert not left.exists()


def test_long_names_get_a_short_private_name_while_published(tmp_path):
    target = tmp_path / ("n" * 236 + ".txt")
    name = de._aside_name(target)
    assert name.startswith(de.ASIDE_PREFIX) and len(name) <= 255 and name.endswith(".txt")
    assert de._aside_original(f"{de.ASIDE_PREFIX}1234abcd-report.pdf") == "report.pdf"
    assert de._aside_original(f"{de.ASIDE_PREFIX}12-34-report.pdf") == "report.pdf"


def test_nothing_is_deleted_on_a_drive_without_a_recycle_bin():
    from pathlib import Path

    from skyfold.engine import recycle

    with pytest.raises(OSError):
        recycle(Path(r"\server\share\photo.jpg"))


def test_a_synced_folder_removed_in_icloud_right_after_it_was_made_is_removed_here_too(env):  # noqa: F811
    s, d, root, trashed = env
    d.put("keep.txt", b"k")
    s.run()
    write(root, "New/f.txt", b"important", age=30)
    s.run()  # folder and file both went up
    d.trash(DriveItem("New", "", "", "", None, True))
    for _ in range(4):
        s.run()
    assert "New" not in d.items and "New" in trashed  # mirrored: to the Recycle Bin, nothing half
