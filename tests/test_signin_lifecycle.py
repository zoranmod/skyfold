"""Sign-in UI regressions: real Qt signals/tasks, fake Apple calls only."""
import gc
import logging
import threading
import time

import pytest
import shiboken6
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from skyfold import app as appmod, auth
from skyfold.config import Settings
from skyfold.ui import dialogs


def pump(app, condition, timeout=4):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        app.processEvents()
        if condition():
            return True
        time.sleep(.005)
    return False


@pytest.fixture
def ui(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    settings = Settings(apple_id='demo@example.invalid', photos_dir=str(tmp_path/'Photos'),
                        photos_dir_confirmed=True, paused=True, notifications=False)
    monkeypatch.setattr(Settings, 'load', classmethod(lambda cls: settings))
    monkeypatch.setattr(Settings, 'save', lambda self: None)
    monkeypatch.setattr(appmod.SyncWorker, 'start', lambda self: None)
    monkeypatch.setattr(auth, 'commit_login', lambda api: None)
    c = appmod.Controller(app)
    c.req.setApi.disconnect()
    c.req.applySettings.disconnect()
    accepted = []
    c.req.setApi.connect(accepted.append)
    gates = []
    yield app, c, accepted, gates
    for gate in gates:
        gate.set()
    QThreadPool.globalInstance().waitForDone(3000)
    app.processEvents()
    for w in list(app.topLevelWidgets()):
        if isinstance(w, dialogs.LoginDialog):
            w._cancel_sign_in()
            w.deleteLater()
    c.clock.stop(); c.anim.stop(); c.thread.quit(); c.thread.wait(3000)
    c.tray.hide(); c.flyout.hide()
    for obj in (c.flyout, c.tray, c.worker):
        obj.deleteLater()
    app.processEvents()


def start(ui, monkeypatch, needs_code=True, delayed=False):
    app, c, _, gates = ui
    gate, entered = threading.Event(), threading.Event()
    gates.append(gate)
    api = object()
    def login(email, password):
        assert email == 'demo@example.invalid' and password == 'synthetic-password'
        entered.set()
        if delayed:
            assert gate.wait(3)
        return auth.LoginStep(api, needs_code, 'Enter the test code')
    monkeypatch.setattr(auth, 'start_login', login)
    c.open_login()
    d = c._dialogs['login']
    d.password.setText('synthetic-password')
    d.ok.click()
    assert entered.wait(1)
    return d, gate, api


@pytest.mark.parametrize('entry', ['button', 'card', 'banner'])
def test_all_panel_signin_routes_open_a_real_dialog(ui, entry):
    app, c, _, _ = ui
    c._on_state('auth', 'Sign in required')
    c.flyout.show()
    app.processEvents()
    if entry == 'button':
        QTest.mouseClick(c.flyout.sync_btn, Qt.LeftButton)
    elif entry == 'card':
        QTest.mouseClick(c.flyout.status_card, Qt.LeftButton)
    else:
        c.flyout.auth_banner.clicked.emit()
    assert pump(app, lambda: 'login' in c._dialogs)
    d = c._dialogs['login']
    assert d.isVisible() and not d.isMinimized()


def test_reopening_hidden_inflight_login_preserves_the_same_attempt(ui, monkeypatch):
    app, c, _, _ = ui
    d, gate, _ = start(ui, monkeypatch, delayed=True)
    d.close()
    c.open_login()
    assert c._dialogs['login'] is d
    assert d.isVisible()
    gate.set()
    assert pump(app, lambda: d.code.isVisible() and d.code.isEnabled())


@pytest.mark.parametrize('action', ['close', 'escape'])
def test_code_ready_is_preserved_until_explicit_cancel(ui, monkeypatch, action):
    app, c, _, _ = ui
    d, _, api = start(ui, monkeypatch)
    assert pump(app, lambda: d.code.isVisible())
    d.close() if action == 'close' else QTest.keyClick(d, Qt.Key_Escape)
    assert not d._cancelled
    c.open_login()
    assert c._dialogs['login'] is d and d.api is api and d.code.isVisible()
    assert d.code.isEnabled()


def test_close_while_checking_code_does_not_discard_success(ui, monkeypatch):
    app, c, accepted, gates = ui
    d, _, api = start(ui, monkeypatch)
    assert pump(app, lambda: d.code.isVisible())
    gate, entered = threading.Event(), threading.Event(); gates.append(gate)
    def verify(actual, code):
        assert actual is api and code == '123456'
        entered.set(); assert gate.wait(3)
    monkeypatch.setattr(auth, 'submit_code', verify)
    d.code.setText('123456')
    assert entered.wait(1)
    d.close()
    assert not d._cancelled
    gate.set()
    assert pump(app, lambda: accepted == [api])


def test_hidden_code_failure_reappears_with_reason_and_log(ui, monkeypatch, caplog):
    app, _, _, gates = ui
    d, _, _ = start(ui, monkeypatch)
    assert pump(app, lambda: d.code.isVisible())
    gate, entered = threading.Event(), threading.Event(); gates.append(gate)
    def verify(*args):
        entered.set(); assert gate.wait(3)
        raise auth.LoginError(dialogs.t("That code isn't right. Try again."))
    monkeypatch.setattr(auth, 'submit_code', verify)
    with caplog.at_level(logging.INFO):
        d.code.setText('123456'); assert entered.wait(1); d.hide(); gate.set()
        assert pump(app, lambda: d.error.isVisible())
    assert d.isVisible() and d.code.isEnabled() and "isn't right" in d.error.text()
    assert 'Sign-in' in caplog.text and 'failed' in caplog.text.lower()


def test_destroyed_cached_dialog_is_replaced(ui):
    _, c, _, _ = ui
    c.open_login(); old = c._dialogs['login']
    shiboken6.delete(old)
    c.open_login()
    new = c._dialogs.get('login')
    assert new is not old and new.isVisible()


def test_open_failure_has_visible_feedback_not_just_a_log(ui, monkeypatch, caplog):
    _, c, _, _ = ui
    notices = []
    def broken(*args):
        raise RuntimeError('untrusted secret must not appear')
    monkeypatch.setattr(appmod, 'LoginDialog', broken)
    monkeypatch.setattr(appmod, 'warn', lambda *args: notices.append(args))
    with caplog.at_level(logging.INFO):
        c.open_login()
    assert notices and 'RuntimeError' in str(notices)
    assert 'untrusted secret' not in str(notices)


def test_duplicate_credential_submission_does_not_start_two_requests(ui, monkeypatch):
    app, c, _, gates = ui
    calls, gate = [], threading.Event(); gates.append(gate)
    def login(*args):
        calls.append(1); assert gate.wait(3)
        return auth.LoginStep(object(), True, 'Code')
    monkeypatch.setattr(auth, 'start_login', login)
    c.open_login(); d=c._dialogs['login']; d.password.setText('synthetic-password')
    d._submit_credentials(); d._submit_credentials()
    gate.set()
    assert pump(app, lambda: d.code.isVisible())
    assert calls == [1]


def test_cancelled_code_failure_never_reopens_or_updates_window(ui, monkeypatch):
    app, _, accepted, gates = ui
    d, _, _ = start(ui, monkeypatch)
    assert pump(app, lambda: d.code.isVisible())
    gate, entered = threading.Event(), threading.Event(); gates.append(gate)
    def verify(*args):
        entered.set(); assert gate.wait(3); raise RuntimeError('secret')
    monkeypatch.setattr(auth, 'submit_code', verify)
    d.code.setText('123456'); assert entered.wait(1); d.cancel.click(); gate.set()
    QThreadPool.globalInstance().waitForDone(3000); app.processEvents()
    assert not d.isVisible() and d.error.text() == '' and not accepted


def test_callback_failure_uses_ui_failure_channel_and_safe_log(ui, caplog):
    app, _, _, _ = ui
    failures = []
    def broken(value):
        raise RuntimeError('raw-password-cookie-server-body')
    with caplog.at_level(logging.INFO):
        task = dialogs.run_async(lambda: 42, broken, failures.append)
        assert pump(app, lambda: bool(failures))
    assert 'RuntimeError' in failures[0]
    assert 'raw-password-cookie-server-body' not in caplog.text + failures[0]


def test_login_commit_failure_keeps_window_open(ui, monkeypatch, caplog):
    app, c, accepted, _ = ui
    def broken(api):
        raise OSError(5, 'test disk refused')
    monkeypatch.setattr(auth, 'commit_login', broken)
    with caplog.at_level(logging.INFO):
        d, _, _ = start(ui, monkeypatch, needs_code=False)
        assert pump(app, lambda: d.error.isVisible())
    assert d.isVisible() and not accepted and 'failed' in caplog.text.lower()


def test_async_result_lives_until_delivery_even_without_caller_reference(ui):
    app, _, _, gates = ui
    gate = threading.Event(); gates.append(gate); seen = []
    dialogs.run_async(lambda: (gate.wait(3), 42)[1], seen.append, seen.append)
    gc.collect(); gate.set()
    assert pump(app, lambda: seen == [42])


def test_cancelled_attempt_cannot_complete_into_a_new_dialog(ui, monkeypatch):
    app, c, accepted, _ = ui
    old, gate, _ = start(ui, monkeypatch, needs_code=False, delayed=True)
    old.cancel.click()
    c.open_login()
    new = c._dialogs['login']
    assert new is not old
    gate.set()
    assert pump(app, lambda: not old._waiting)
    assert new.isVisible() and not accepted and c._dialogs['login'] is new


def test_worker_state_updates_do_not_replace_a_hidden_code_step(ui, monkeypatch):
    app, c, _, _ = ui
    d, _, api = start(ui, monkeypatch)
    assert pump(app, lambda: d.code.isVisible())
    d.hide()
    for state in ('offline', 'auth', 'idle', 'auth'):
        c.worker.stateChanged.emit(state, state)
    app.processEvents()
    c.flyout.show()
    QTest.mouseClick(c.flyout.sync_btn, Qt.LeftButton)
    assert c._dialogs['login'] is d and d.isVisible() and d.api is api


def test_reopening_minimised_dialog_hides_topmost_panel_and_focuses_field(ui):
    app, c, _, _ = ui
    c.open_login(); d = c._dialogs['login']; d.showMinimized()
    c.flyout.show(); c.open_login(); app.processEvents()
    assert d.isVisible() and not d.isMinimized() and not c.flyout.isVisible()
    assert d.focusWidget() is d.password


def test_hidden_credential_failure_reappears_and_does_not_leak_server_text(ui, monkeypatch, caplog):
    app, c, _, gates = ui
    gate, entered = threading.Event(), threading.Event(); gates.append(gate)
    def login(*args):
        entered.set(); assert gate.wait(3)
        raise RuntimeError('private-cookie-response-body')
    monkeypatch.setattr(auth, 'start_login', login)
    with caplog.at_level(logging.INFO):
        c.open_login(); d = c._dialogs['login']
        d.password.setText('synthetic-password'); d.ok.click()
        assert entered.wait(1); d.close(); gate.set()
        assert pump(app, lambda: d.error.isVisible())
    assert d.isVisible() and d.password.isEnabled() and d.ok.isEnabled()
    assert 'RuntimeError' in d.error.text()
    assert 'private-cookie-response-body' not in caplog.text + d.error.text()
    assert d.password.text() == '' and d._task.fn is None


def test_settings_save_failure_does_not_close_login_or_publish_api(ui, monkeypatch):
    app, c, accepted, _ = ui
    def broken(self):
        raise OSError(5, 'test settings refused')
    monkeypatch.setattr(Settings, 'save', broken)
    d, _, _ = start(ui, monkeypatch, needs_code=False)
    assert pump(app, lambda: d.error.isVisible())
    assert not accepted and getattr(c, '_login_api', None) is None


def test_code_can_be_retried_after_a_failed_verification(ui, monkeypatch):
    app, c, accepted, _ = ui
    d, _, api = start(ui, monkeypatch)
    assert pump(app, lambda: d.code.isVisible())
    codes = []
    def submit(actual, code):
        assert actual is api
        codes.append(code)
        if len(codes) == 1:
            raise auth.LoginError(dialogs.t("That code isn't right. Try again."))
    monkeypatch.setattr(auth, 'submit_code', submit)
    d.code.setText('111111')
    assert pump(app, lambda: d.error.isVisible())
    d.code.setText('222222')
    assert pump(app, lambda: accepted == [api])
    assert codes == ['111111', '222222'] and 'login' not in c._dialogs
