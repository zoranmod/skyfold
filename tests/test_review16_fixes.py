"""Tests for the recovery redesign after the sixteenth (Codex) review: the database only describes
what this PC has; a new iCloud version tag is accepted only when size and modified time are those of
the synced version (drive_engine._tag_only); errors from Apple reach the log only as kinds and codes."""

import io
import logging
import shutil

import pytest
import requests

from skyfold import drive_engine as de
from skyfold.drive_remote import DriveItem
from tests.test_drive import env  # noqa: F401
from tests.test_review10_fixes import _with_mtimes


def test_tag_only_needs_same_size_and_time():
    b = de.Base("a.txt", False, "e1", 4, 1_600_000_000.4, 4, False)

    def item(etag, size, mtime):
        return DriveItem("a.txt", "d", "d", "z", etag, False, size, mtime)

    assert de._tag_only(b, item("e2", 4, 1_600_000_000.0))
    assert not de._tag_only(b, item("e1", 4, 1_600_000_000.0))  # same tag: nothing moved
    assert not de._tag_only(b, item("e2", 5, 1_600_000_000.0))  # another size: an edit
    assert not de._tag_only(b, item("e2", 4, 1_600_000_060.0))  # another time: an edit
    assert not de._tag_only(b, item("e2", 4, None))
    assert not de._tag_only(de.Base("a.txt", False, None, 4, 1.0, 4, False), item("e2", 4, 1.0))
    assert not de._tag_only(None, item("e2", 4, 1.0)) and not de._tag_only(b, None)


def test_a_folder_deleted_on_the_pc_right_after_an_icloud_rename_is_deleted_in_icloud(env):  # noqa: F811
    s, d, root, _ = env
    s.delete_threshold = 1000
    for i in range(5):
        d.put(f"A/f{i}.txt", f"file {i}".encode())
    d.put("keep.txt", b"keep")
    s.run()
    d.rename(d._item("A"), "B")  # every file in it gets a new version tag in iCloud
    s.run()
    assert (root / "B/f0.txt").exists()
    shutil.rmtree(root / "B")
    res = s.run()
    assert res.pending_to_cloud == ["B"] and res.downloaded == 0  # asked about, not downloaded again
    s.approved |= set(res.pending_to_cloud)
    for _ in range(3):
        s.run()
    assert not any(p.startswith("B") for p in d.items) and not (root / "B").exists()


def test_an_icloud_edit_inside_that_folder_survives_the_pc_deletion(env, monkeypatch):  # noqa: F811
    s, d, root, _ = env
    s.delete_threshold = 1000
    _with_mtimes(d, monkeypatch)
    for i in range(5):
        d.put(f"A/f{i}.txt", f"file {i}".encode())
    s.run()
    d.rename(d._item("A"), "B")
    s.run()
    d.items["B/f0.txt"].update(data=b"EDIT 0", etag=d._etag(), mtime=1_700_000_000.0)  # same size, edited
    shutil.rmtree(root / "B")
    for _ in range(4):
        res = s.run()
        s.approved |= set(res.pending_to_cloud)
    assert d.items["B/f0.txt"]["data"] == b"EDIT 0" and (root / "B/f0.txt").read_bytes() == b"EDIT 0"
    assert "B/f1.txt" not in d.items


def _apple_error(tmp_path, monkeypatch):
    from pyicloud import PyiCloudService

    from skyfold import auth

    monkeypatch.setattr(auth, "_CURRENT", None)
    auth._serialize_session_saves()
    api = PyiCloudService("review@example.invalid", None, cookie_directory=str(tmp_path),
                          authenticate=False, refresh_interval=None)

    def request(_session, method, url, **kw):
        r = requests.Response()
        r.url, r.status_code, r.reason = url, 500, "Server Error"
        r.headers["Content-Type"] = "text/html"
        r._content = b'<html><input name="firstName" value="PrivatePerson"></html>'
        return r

    monkeypatch.setattr(requests.Session, "request", request)
    try:
        api.session.get("https://setup.icloud.com/setup/ws/1/x")
    except Exception as exc:  # noqa: BLE001
        return exc
    raise AssertionError("no error")


def test_errors_from_apple_are_described_without_their_text(tmp_path, monkeypatch):
    from skyfold.engine import SyncError
    from skyfold.safelog import brief, describe

    exc = _apple_error(tmp_path, monkeypatch)
    assert "PrivatePerson" in str(exc)  # the leak this guards against
    for text in (brief(exc), describe(exc)):
        assert "PrivatePerson" not in text and "500" in text
    assert brief(SyncError("Our own message")) == "Our own message"
    assert "Access is denied" in brief(PermissionError(13, "Access is denied", "C:/x/a.txt"))


def test_the_log_filter_never_writes_an_exception_as_text(tmp_path, monkeypatch):
    from skyfold.app import _RedactEmails

    exc = _apple_error(tmp_path, monkeypatch)
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.addFilter(_RedactEmails())
    logger = logging.getLogger("skyfold.test-review16")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.warning("failed: %s", exc)
        try:
            raise exc
        except Exception:  # noqa: BLE001
            logger.exception("with traceback")
    finally:
        logger.removeHandler(handler)
    text = out.getvalue()
    assert "PrivatePerson" not in text and "Traceback" not in text
    assert text.count("500") >= 2 and "test_review16_fixes.py:" in text


def test_background_tasks_queued_before_quit_never_start():
    from skyfold.ui import dialogs

    ran = []
    before = dialogs._Task(lambda: ran.append("before"))
    dialogs.stop_tasks()
    before.run()
    after = dialogs._Task(lambda: ran.append("after"))
    after.run()
    assert ran == ["after"]
