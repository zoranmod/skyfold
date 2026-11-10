"""Regression tests for the ninth independent (Codex) review, of commit 60e9528."""

import io
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from skyfold import drive_engine as de
from tests.test_drive import env, write  # noqa: F401
from tests.test_notes import env as notes_env  # noqa: F401


def test_an_edited_index_is_recycled_when_its_fingerprint_was_never_saved(notes_env):  # noqa: F811
    from skyfold import notes_engine as ne

    s, r, root, trashed = notes_env
    r.put("n1", "One")
    s.run()
    s.state.set("index_digest", None)  # crashed after publishing, before the fingerprint was saved
    with (root / ne.INDEX).open("ab") as fh:
        fh.write(b"PERSONAL ANNOTATION")
    fresh = ne.NotesSyncer(ne.NotesState(root.parent / "n.db"), r, root)  # restarted after the crash
    recycled = []
    fresh._trash = lambda p: (recycled.append(p.read_bytes()), p.unlink())
    fresh.write_index()
    assert any(b"PERSONAL ANNOTATION" in x for x in recycled)


def _one_copy(d, data):
    return sorted(p for p, it in d.items.items() if it["data"] == data)


def test_a_rename_refused_after_the_move_leaves_one_copy(env, monkeypatch):  # noqa: F811
    s, d, root, _ = env
    d.put("G/a.txt", b"moved content")
    d.put("F/keep.txt", b"other")
    s.run()
    (root / "F").mkdir(exist_ok=True)
    import os

    os.rename(root / "G/a.txt", root / "F/d.txt")
    real_rename = d.rename
    fail = [True]

    def rename(item, name):
        if fail and fail.pop():
            raise RuntimeError("iCloud refused the rename: CONFLICT")
        return real_rename(item, name)

    monkeypatch.setattr(d, "rename", rename)
    for _ in range(4):
        s.run()
    assert _one_copy(d, b"moved content") == ["F/d.txt"]
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*.txt")) == ["F/d.txt", "F/keep.txt"]


def test_a_crash_right_after_the_rename_in_icloud_leaves_one_copy(env, monkeypatch, tmp_path):  # noqa: F811
    s, d, root, _ = env
    d.put("G/a.txt", b"moved content")
    d.put("F/a.txt", b"existing unrelated")  # the target folder has the old name: rename goes first
    s.run()
    import os

    os.rename(root / "G/a.txt", root / "F/d.txt")
    real_rename = d.rename
    crashed = []

    class Crash(BaseException):
        pass

    def rename(item, name):
        real_rename(item, name)
        if not crashed:
            crashed.append(True)
            raise Crash  # the process ends here: iCloud renamed it, the database never heard

    monkeypatch.setattr(d, "rename", rename)
    with pytest.raises(Crash):
        s.run()
    fresh = de.DriveSyncer(de.DriveState(tmp_path / "d.db"), d, root, delete_threshold=3)
    for _ in range(4):
        fresh.run()
    assert _one_copy(d, b"moved content") == ["F/d.txt"]
    assert _one_copy(d, b"existing unrelated") == ["F/a.txt"]
    assert not (root / "G/d.txt").exists() and (root / "F/d.txt").read_bytes() == b"moved content"


def test_a_broken_cookie_line_never_reaches_the_log_whatever_the_value(tmp_path, monkeypatch):
    import requests
    from pyicloud import PyiCloudService

    from skyfold.app import _RedactEmails

    monkeypatch.setattr(requests.Session, "request", lambda *a, **kw: pytest.fail("network"))
    storage = tmp_path / "session"
    storage.mkdir()
    api = PyiCloudService("review@example.invalid", "dummy", cookie_directory=str(storage),
                          authenticate=False, refresh_interval=None)
    secret = "Ab3dE" * 12 + "x9Q4"  # 64 mixed letters and digits: not hex, shorter than 80
    Path(api.session.cookiejar_path).write_text(
        "#LWP-Cookies-2.0\nSet-Cookie3: aasp=" + secret + '; path="/"; version=0\n')
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.addFilter(_RedactEmails())
    api.session.logger.addHandler(handler)
    try:
        api.session._load_session_data()
    finally:
        api.session.logger.removeHandler(handler)
    assert secret not in out.getvalue()
    rec = logging.LogRecord("x", logging.WARNING, __file__, 1,
                            "Failed: invalid Set-Cookie3 format file 'C:\\s\\session': 'aasp=%s; path=\"/\"'",
                            (secret,), None)
    _RedactEmails().filter(rec)
    assert secret not in rec.getMessage() and "invalid Set-Cookie3 format file" in rec.getMessage()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows processes")
def test_the_new_copy_gives_up_while_the_old_one_still_runs():
    from skyfold.app import _wait_for_exit

    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert _wait_for_exit(p.pid, seconds=0.3) is False
    finally:
        p.kill()
        p.wait()
    assert _wait_for_exit(p.pid, seconds=5) is True


def test_a_rename_whose_answer_never_came_is_settled_by_the_item_id(env, monkeypatch):  # noqa: F811
    import os

    import requests

    s, d, root, _ = env
    d.put("Docs/a.txt", b"renamed content")
    s.run()
    os.rename(root / "Docs/a.txt", root / "Docs/b.txt")
    real_rename = d.rename
    lost = [True]

    def rename(item, name):
        real_rename(item, name)  # iCloud did it ...
        if lost and lost.pop():
            raise requests.exceptions.Timeout("read timed out")  # ... but the answer never arrived

    monkeypatch.setattr(d, "rename", rename)
    for _ in range(4):
        s.run()
    assert _one_copy(d, b"renamed content") == ["Docs/b.txt"]
    assert s.state.get_meta(de.PENDING_MOVE) is None
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*.txt")) == ["Docs/b.txt"]
