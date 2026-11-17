"""A real Enter key in the sign-in window signs in; it never ends up pressing Cancel."""
import time

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from skyfold import auth
from skyfold.ui import dialogs


def _wait(app, cond, secs=3.0):
    end = time.time() + secs
    while time.time() < end and not cond():
        app.processEvents()
        time.sleep(0.02)


def _dialog(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(auth, "start_login",
                        lambda email, pw: (time.sleep(0.2), auth.LoginStep(object(), True, "Code"))[1])
    monkeypatch.setattr(auth, "submit_code", lambda api, code: (time.sleep(0.2), True)[1])
    d = dialogs.LoginDialog("someone@example.invalid")
    d.show()
    d.activateWindow()
    d.password.setFocus()
    app.processEvents()
    return app, d


def test_enter_in_the_password_field_does_not_cancel(monkeypatch):
    app, d = _dialog(monkeypatch)
    d.password.setText("not a real password")
    QTest.keyClick(d.password, Qt.Key_Return)
    assert not d._cancelled
    _wait(app, lambda: d.ok.text() == dialogs.t("Confirm"))
    assert not d._cancelled and d.isVisible() and d.code.isEnabled()
    d._cancel_sign_in()


def test_enter_after_typing_the_code_does_not_cancel(monkeypatch):
    app, d = _dialog(monkeypatch)
    got = []
    d.loggedIn.connect(lambda api, who: got.append(who))
    d.password.setText("not a real password")
    QTest.keyClick(d.password, Qt.Key_Return)
    _wait(app, lambda: d.ok.text() == dialogs.t("Confirm"))
    d.code.setFocus()
    QTest.keyClicks(d.code, "123456")  # six digits send the code by themselves
    QTest.keyClick(d.code, Qt.Key_Return)  # ...and the habitual Enter afterwards
    assert not d._cancelled
    _wait(app, lambda: bool(got))
    assert got and not d._cancelled
