"""Regression tests for the eighth independent (Codex) review, of commit eddae51."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from skyfold import drive_engine as de
from tests.test_drive import env, write  # noqa: F401
from tests.test_notes import env as notes_env  # noqa: F401


def test_a_rename_from_icloud_never_overwrites_a_file_saved_at_that_moment(env, monkeypatch):  # noqa: F811
    s, d, root, _ = env
    d.put("old.txt", b"cloud file")
    s.run()
    d._relocate("old.txt", "new.txt")
    injected = []

    def hook(real):
        def save_then_rename(src, dst, *a, **k):
            if Path(src) == root / "old.txt" and Path(dst) == root / "new.txt" and not injected:
                Path(dst).write_bytes(b"UNIQUE PERSONAL FILE")  # saved after the check, before the rename
                injected.append(True)
            return real(src, dst, *a, **k)
        return save_then_rename

    monkeypatch.setattr(de.os, "rename", hook(os.rename))
    monkeypatch.setattr(de.os, "replace", hook(os.replace))
    for _ in range(3):
        s.run()
    assert injected
    assert b"UNIQUE PERSONAL FILE" in [p.read_bytes() for p in root.rglob("*") if p.is_file()]


def test_a_move_that_fails_after_the_rename_leaves_one_copy(env, monkeypatch):  # noqa: F811
    s, d, root, _ = env
    d.put("G/a.txt", b"moved content")
    d.put("F/a.txt", b"existing unrelated")
    s.run()
    os.rename(root / "G/a.txt", root / "F/d.txt")
    real_move, real_trash, real_rename = d.move, d.trash, d.rename
    fail = [True]

    def rename(item, name):
        real_rename(item, name)
        return next(it["etag"] for it in d.items.values() if it["id"] == item.docwsid)

    def trash(item):
        current = next((p for p, it in d.items.items() if it["id"] == item.docwsid), None)
        if current is not None:
            if d.items[current]["etag"] != item.etag:
                raise RuntimeError("iCloud refused deletion: stale etag")
            return real_trash(d._item(current))

    def move(item, parent):
        if fail.pop() if fail else False:
            raise RuntimeError("iCloud refused the move: CONFLICT")
        return real_move(item, parent)

    monkeypatch.setattr(d, "move", move)
    monkeypatch.setattr(d, "rename", rename)
    monkeypatch.setattr(d, "trash", trash)
    for _ in range(4):
        s.run()
    assert [p for p, it in d.items.items() if it["data"] == b"moved content"] == ["F/d.txt"]


def test_an_edit_of_the_notes_index_is_never_taken_for_skyfolds_own(notes_env, monkeypatch):  # noqa: F811
    from skyfold import notes_engine as ne

    s, r, root, _ = notes_env
    r.put("n1", "One")
    s.run()
    recycled = []
    s._trash = lambda p: (recycled.append(p.read_bytes()), p.unlink())
    target = root / ne.INDEX
    real = os.rename
    edited = []

    def rename(src, dst, *a, **k):
        real(src, dst, *a, **k)
        if Path(dst) == target and not edited:  # someone edits it the moment it is published
            edited.append(True)
            with target.open("ab") as fh:
                fh.write(b"PERSONAL ANNOTATION")

    monkeypatch.setattr(ne.os, "rename", rename)
    s.write_index()
    monkeypatch.setattr(ne.os, "rename", real)
    s.write_index()
    kept = recycled + [p.read_bytes() for p in root.rglob("*") if p.is_file()]
    assert any(b"PERSONAL ANNOTATION" in x for x in kept)


def test_a_broken_cookie_file_never_puts_a_cookie_in_the_log(tmp_path, monkeypatch):
    import io
    import logging

    import requests
    from pyicloud import PyiCloudService

    from skyfold.app import _RedactEmails

    monkeypatch.setattr(requests.Session, "request", lambda *a, **kw: pytest.fail("network"))
    storage = tmp_path / "session"
    storage.mkdir()
    api = PyiCloudService("review@example.invalid", "dummy", cookie_directory=str(storage),
                          authenticate=False, refresh_interval=None)
    secret = "0123456789abcdef" * 4
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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows mutex")
def test_a_restart_hands_over_the_single_instance_lock(monkeypatch):
    import uuid

    from PySide6.QtCore import QProcess

    from skyfold import app

    name = "Skyfold-review8-" + uuid.uuid4().hex
    handle = app._single_instance_mutex(name)
    monkeypatch.setattr(app, "_MUTEX", handle)
    started = []
    monkeypatch.setattr(QProcess, "startDetached", lambda exe, args: started.append(args) or True)
    app.Controller.restart(SimpleNamespace(server=None, quit=lambda: None))
    assert "--after" in started[0] and str(os.getpid()) in started[0]
    # review 9: the lock stays with the old copy until it has ended; nothing else can start meanwhile
    assert app._MUTEX == handle
    code = f"from skyfold.app import _single_instance_mutex; print(_single_instance_mutex({name!r}) is False)"
    child = subprocess.run([sys.executable, "-c", code], cwd=Path(app.__file__).parent.parent,
                           text=True, capture_output=True, timeout=60)
    assert child.stdout.strip() == "True"
    import ctypes

    ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(handle))


def test_the_new_copy_waits_for_the_old_one_only_while_it_runs():
    import time

    from skyfold.app import _wait_for_exit

    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"])
    start = time.monotonic()
    _wait_for_exit(p.pid, seconds=10)
    assert p.poll() is not None or time.monotonic() - start >= 0.9
    p.wait()


def test_sign_in_and_first_run_windows_fit_a_small_screen(monkeypatch):
    from PySide6.QtWidgets import QApplication

    from skyfold.ui import dialogs, theme

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(dialogs, "_fit_screen", lambda w, h: (min(w, 327), min(h, 235)))
    login = dialogs.LoginDialog("someone@example.com")
    setup = dialogs.FolderSetupDialog("C:/Pictures", theme.palette("light"))
    assert login.width() <= 327 and login.height() <= 235
    assert setup.width() <= 327
