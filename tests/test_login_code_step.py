"""The two-factor code step always comes into sight, unless the user cancelled the sign-in."""
import time

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
    monkeypatch.setattr(auth, "start_login", lambda email, pw: (time.sleep(0.2), auth.LoginStep(object(), True, "Code"))[1])
    d = dialogs.LoginDialog("someone@example.invalid")
    d.show()
    d.password.setText("not a real password")
    return app, d


def test_the_code_step_comes_back_into_sight_when_the_window_was_hidden(monkeypatch):
    app, d = _dialog(monkeypatch)
    assert d.wait_note.isVisible() is False
    d._submit_credentials()
    assert not d.wait_note.isHidden()  # "this can take up to a minute"
    d.hide()  # e.g. gone behind other windows / hidden meanwhile
    _wait(app, lambda: d.isVisible())
    assert d.isVisible() and d.ok.text() == dialogs.t("Confirm") and d.code.isEnabled()
    assert d.wait_note.isHidden()
    d.reject()


def test_a_code_apple_sent_is_never_thrown_away_even_after_cancel(monkeypatch):
    app, d = _dialog(monkeypatch)
    d._submit_credentials()
    d.cancel.click()  # given up while Apple was slow - but Apple sends the code to the iPhone anyway
    _wait(app, lambda: d.isVisible())
    assert d.isVisible() and d.ok.text() == dialogs.t("Confirm") and not d._cancelled


def test_closing_the_window_while_apple_answers_only_steps_aside(monkeypatch):
    app, d = _dialog(monkeypatch)
    d._submit_credentials()
    d.close()  # the close button (or Esc) while waiting
    assert not d.isVisible() and not d._cancelled
    _wait(app, lambda: d.isVisible())
    assert d.isVisible() and d.ok.text() == dialogs.t("Confirm")  # back, for the code
    d.cancel.click()
    assert d._cancelled and not d.isVisible()


def test_signing_in_again_shows_the_same_window_with_its_code_step(monkeypatch):
    from types import SimpleNamespace
    from skyfold import app as A
    from skyfold.config import Settings

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(auth, "start_login", lambda email, pw: auth.LoginStep(object(), True, "Code"))
    ctl = SimpleNamespace(settings=Settings(apple_id="someone@example.invalid"), _dialogs={},
                          flyout=SimpleNamespace(hide=lambda: None))
    ctl._show_dialog = lambda k, d: A.Controller._show_dialog(ctl, k, d)
    A.Controller.open_login(ctl)
    first = ctl._dialogs["login"]
    first.password.setText("x")
    first._submit_credentials()
    _wait(app, lambda: first.ok.text() == dialogs.t("Confirm"))
    first.hide()  # lost behind other windows, or closed
    A.Controller.open_login(ctl)  # Sign in clicked again
    assert ctl._dialogs["login"] is first and first.isVisible() and first.ok.text() == dialogs.t("Confirm")
    first.cancel.click()


def test_apple_refusing_to_start_is_not_called_a_wrong_password(monkeypatch):
    import pytest
    from pyicloud import exceptions as ex

    class Refusing:
        def __init__(self, *a, **k):
            raise ex.PyiCloudFailedLoginException("Failed to initiate srp authentication.",
                                                  ex.PyiCloudAPIResponseException("Bad Request", 400))

    monkeypatch.setattr("pyicloud.PyiCloudService", Refusing)
    with pytest.raises(auth.LoginError) as err:
        auth.start_login("someone@example.invalid", "not a real password")
    assert "15" in str(err.value) and "password" not in str(err.value).lower()
