"""Opt-in packaged GUI smoke test. Fake Apple calls; network and profile writes forbidden."""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import sys
import threading
import time


def run(report: str) -> str:
    from unittest.mock import patch
    from PySide6.QtCore import QThreadPool, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from . import app as appmod, auth
    from .config import Settings
    from .ui.dialogs import LoginDialog

    # This diagnostic runs in its own process, before app.main/single-instance startup.
    # A missed fake must fail the check, never make an Apple request.
    def offline(event, args):
        if event in ("socket.connect", "socket.getaddrinfo"):
            raise RuntimeError("Offline sign-in selftest forbids networking")
    sys.addaudithook(offline)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    settings = Settings(apple_id="skyfold-ui@example.invalid", paused=True,
                        photos_dir_confirmed=True, notifications=False)
    api = object()
    gate = threading.Event()
    code_gate = threading.Event()
    entered = threading.Event()
    calls = []
    accepted = []

    def login(email, password):
        assert email == settings.apple_id and password == "synthetic-password"
        calls.append("credentials")
        entered.set()
        assert gate.wait(10), "test did not release the fake Apple response"
        return auth.LoginStep(api, True, "Offline test: enter the synthetic code.")

    def submit(actual_api, code):
        assert actual_api is api and code == "123456"
        calls.append("code")
        assert code_gate.wait(10), "test did not release the fake code response"

    def pump(condition):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            app.processEvents()
            if condition():
                return
            time.sleep(.01)
        raise AssertionError("Qt UI did not reach the expected state")

    with ExitStack() as stack:
        for target, name, replacement in (
            (Settings, "load", classmethod(lambda cls: settings)),
            (Settings, "save", lambda self: None),
            (appmod.SyncWorker, "start", lambda self: None),
            (auth, "start_login", login),
            (auth, "submit_code", submit),
            (auth, "restore", lambda email: None),
            (auth, "commit_login", lambda current: None),
        ):
            stack.enter_context(patch.object(target, name, replacement))
        c = appmod.Controller(app)
        c.req.setApi.disconnect()
        c.req.applySettings.disconnect()
        c.req.setApi.connect(accepted.append)
        try:
            c._on_state("auth", "Offline sign-in test")
            c.flyout.show_at(c.tray.geometry())
            app.processEvents()
            QTest.mouseClick(c.flyout.sync_btn, Qt.LeftButton)
            pump(lambda: "login" in c._dialogs and c._dialogs["login"].isVisible())
            d = c._dialogs["login"]
            assert d.isWindow() and not d.isMinimized()
            pump(lambda: d.isActiveWindow() and d.password.hasFocus())
            assert d.grab().save(str(Path(report).with_suffix(".login.png")))
            QTest.keyClicks(app.focusWidget(), "synthetic-password")
            QTest.keyClick(app.focusWidget(), Qt.Key_Return)
            assert entered.wait(2)
            assert not d._cancelled, "Enter after password activated Cancel"
            d.close()
            assert not d._cancelled
            c.flyout.show_at(c.tray.geometry())
            app.processEvents()
            QTest.mouseClick(c.flyout.sync_btn, Qt.LeftButton)
            assert c._dialogs["login"] is d and d.isVisible()
            d.hide()
            gate.set()
            pump(lambda: d.isVisible() and d.code.isVisible() and d.code.isEnabled())
            pump(lambda: d.isActiveWindow() and d.code.hasFocus())
            assert d.grab().save(str(Path(report).with_suffix(".code.png")))
            QTest.keyClick(d, Qt.Key_Escape)
            c.open_login()
            assert c._dialogs["login"] is d and d.code.isVisible()
            QTest.keyClicks(app.focusWidget(), "123456")
            pump(lambda: "code" in calls)
            QTest.keyClick(app.focusWidget() or d, Qt.Key_Return)
            QTest.keyClick(app.focusWidget() or d, Qt.Key_Enter, Qt.KeypadModifier)
            assert not d._cancelled, "Enter after code activated Cancel"
            code_gate.set()
            pump(lambda: accepted == [api])
            assert calls == ["credentials", "code"] and not d.isVisible()
            return "real password typing+Enter -> preserved attempt -> six typed digits+Return+numpad Enter -> setApi; focused native windows, no network or profile writes"
        finally:
            gate.set()
            code_gate.set()
            QThreadPool.globalInstance().waitForDone(10000)
            app.processEvents()
            c.clock.stop()
            c.anim.stop()
            c.thread.quit()
            c.thread.wait(3000)
            c.tray.hide()
            c.flyout.hide()
            for w in app.topLevelWidgets():
                if isinstance(w, LoginDialog):
                    w._cancel_sign_in()
                    w.deleteLater()

