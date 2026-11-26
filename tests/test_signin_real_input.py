"""User-input matrix. Keys go to the actual focus widget, never a disabled field."""
import logging
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QMessageBox, QListWidget

from skyfold import auth
from skyfold.ui import dialogs
from tests.test_signin_lifecycle import ui, pump


def key(app, value, modifiers=Qt.NoModifier):
    target = app.focusWidget() or app.activeWindow()
    assert target is not None, 'No keyboard target'
    QTest.keyClick(target, value, modifiers)
    app.processEvents()


def tab_to(app, target, limit=25):
    for _ in range(limit):
        if app.focusWidget() is target:
            return
        key(app, Qt.Key_Tab)
    raise AssertionError('Tab cannot reach ' + target.text())


@pytest.fixture
def flow(ui, monkeypatch):
    app, c, accepted, gates = ui
    credential_gate, code_gate = threading.Event(), threading.Event()
    gates.extend((credential_gate, code_gate))
    seen, codes = [], []
    api = object()
    def login(email, password):
        seen.append((email, password))
        assert credential_gate.wait(8)
        return auth.LoginStep(api, True, 'Enter the synthetic test code')
    def verify(actual, code):
        assert actual is api
        codes.append(code)
        assert code_gate.wait(8)
        if code == '111111':
            raise auth.LoginError(dialogs.t("That code isn't right. Try again."))
    monkeypatch.setattr(auth, 'start_login', login)
    monkeypatch.setattr(auth, 'submit_code', verify)
    monkeypatch.setattr(auth, 'restore', lambda email: None)
    c._on_state('auth', 'Sign-in required')
    c.flyout.show_at(c.tray.geometry()); app.processEvents()
    QTest.mouseClick(c.flyout.sync_btn, Qt.LeftButton)
    assert pump(app, lambda: 'login' in c._dialogs and c._dialogs['login'].isVisible())
    d = c._dialogs['login']
    assert pump(app, lambda: app.focusWidget() is d.password)
    yield SimpleNamespace(app=app, c=c, d=d, accepted=accepted, gate=credential_gate,
                          code_gate=code_gate, seen=seen, codes=codes, api=api)
    c._find_my_after_login = False
    for name, w in list(c._dialogs.items()):
        if name != 'login':
            w.close(); w.deleteLater()


def credentials(f, mode='return'):
    QTest.keyClicks(f.app.focusWidget(), 'synthetic-password')
    if mode == 'mouse':
        QTest.mouseClick(f.d.ok, Qt.LeftButton)
    elif mode == 'tab-space':
        key(f.app, Qt.Key_Tab)
        assert f.app.focusWidget() is f.d.ok
        key(f.app, Qt.Key_Space)
    elif mode == 'email':
        key(f.app, Qt.Key_Backtab)
        assert f.app.focusWidget() is f.d.email
        key(f.app, Qt.Key_Return)
    else:
        key(f.app, Qt.Key_Enter if mode == 'numpad' else Qt.Key_Return,
            Qt.KeypadModifier if mode == 'numpad' else Qt.NoModifier)
    assert pump(f.app, lambda: len(f.seen) == 1)
    assert not f.d._cancelled


def code_page(f):
    credentials(f)
    f.gate.set()
    assert pump(f.app, lambda: f.d.code.isVisible() and f.d.code.isEnabled())
    assert f.app.focusWidget() is f.d.code


@pytest.mark.parametrize('mode', ['return', 'numpad', 'tab-space', 'mouse', 'email'])
def test_password_submission_real_input(flow, mode):
    f = flow
    credentials(f, mode)
    for key_value in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
        key(f.app, key_value)
        assert not f.d._cancelled and len(f.seen) == 1
    f.gate.set()
    assert pump(f.app, lambda: f.d.code.isVisible() and f.d.code.isEnabled())


@pytest.mark.parametrize('mode', ['typing', 'return', 'numpad', 'paste'])
def test_code_submission_real_input(flow, mode):
    f = flow; code_page(f)
    if mode == 'paste':
        f.app.clipboard().setText('123 456')
        key(f.app, Qt.Key_V, Qt.ControlModifier)
    else:
        QTest.keyClicks(f.app.focusWidget(), '123456')
    assert pump(f.app, lambda: f.codes == ['123456'])
    if mode in ('return', 'numpad'):
        key(f.app, Qt.Key_Return if mode == 'return' else Qt.Key_Enter)
    key(f.app, Qt.Key_Return)  # habitual Enter while Checking... is visible
    key(f.app, Qt.Key_Space)
    assert not f.d._cancelled and f.codes == ['123456']
    f.code_gate.set()
    assert pump(f.app, lambda: f.accepted == [f.api])


def test_wrong_then_right_code_real_input(flow):
    f = flow; code_page(f); f.code_gate.set()
    QTest.keyClicks(f.app.focusWidget(), '111111')
    assert pump(f.app, lambda: f.d.error.isVisible())
    assert f.app.focusWidget() is f.d.code and f.d.code.text() == ''
    QTest.keyClicks(f.app.focusWidget(), '123456')
    key(f.app, Qt.Key_Return)
    assert pump(f.app, lambda: f.accepted == [f.api])
    assert f.codes == ['111111', '123456']


def set_stage(f, stage):
    if stage == 'credentials':
        return
    if stage == 'waiting':
        credentials(f); return
    code_page(f)
    if stage == 'code':
        return
    if stage == 'error':
        f.code_gate.set()
        QTest.keyClicks(f.app.focusWidget(), '111111')
        assert pump(f.app, lambda: f.d.error.isVisible())
    else:
        QTest.keyClicks(f.app.focusWidget(), '123456')
        assert pump(f.app, lambda: bool(f.codes))


def menu_choose(f, label):
    action = next(a for a in f.c.menu.actions() if a.text() == dialogs.t(label))
    f.c.menu.popup(f.d.pos())
    f.app.processEvents()
    QTest.mouseClick(f.c.menu, Qt.LeftButton, pos=f.c.menu.actionGeometry(action).center())
    f.app.processEvents()


def reopen(f, route):
    if route == 'findmy':
        menu_choose(f, 'Find My devices…')
        assert pump(f.app, lambda: 'findmy' in f.c._dialogs)
        findmy = f.c._dialogs['findmy']
        assert pump(f.app, lambda: findmy.signin_btn.isVisible())
        QTest.mouseClick(findmy.signin_btn, Qt.LeftButton)
    elif route == 'tray':
        menu_choose(f, 'Show')
        QTest.mouseClick(f.c.flyout.sync_btn, Qt.LeftButton)
    else:
        f.c.flyout.show_at(f.c.tray.geometry()); f.app.processEvents()
        QTest.mouseClick(f.c.flyout.status_card if route == 'card' else f.c.flyout.sync_btn,
                         Qt.LeftButton)
    assert pump(f.app, lambda: f.d.isVisible() and not f.d.isMinimized())
    assert f.c._dialogs['login'] is f.d and not f.d._cancelled


@pytest.mark.parametrize('stage', ['credentials', 'waiting', 'code', 'checking', 'error'])
@pytest.mark.parametrize('route', ['panel', 'card', 'findmy', 'tray'])
def test_escape_reopen_real_input(flow, stage, route):
    f = flow; set_stage(f, stage)
    key(f.app, Qt.Key_Escape)
    assert not f.d.isVisible() and not f.d._cancelled
    reopen(f, route)
    if stage in ('code', 'error'):
        assert f.app.focusWidget() is f.d.code


@pytest.mark.parametrize('stage', ['credentials', 'waiting', 'code', 'checking', 'error'])
def test_cancel_is_keyboard_accessible_but_enter_never_cancels(flow, stage):
    f = flow; set_stage(f, stage)
    tab_to(f.app, f.d.cancel)
    key(f.app, Qt.Key_Return)
    assert not f.d._cancelled and f.d.isVisible()
    key(f.app, Qt.Key_Enter)
    assert not f.d._cancelled
    key(f.app, Qt.Key_Space)
    assert f.d._cancelled and not f.d.isVisible()
    f.gate.set(); f.code_gate.set()
    QTest.qWait(30)
    assert not f.accepted


@pytest.mark.parametrize('stage', ['credentials', 'waiting', 'code', 'checking', 'error'])
def test_cancel_by_deliberate_mouse_click(flow, stage):
    f = flow; set_stage(f, stage)
    QTest.mouseClick(f.d.cancel, Qt.LeftButton)
    f.gate.set(); f.code_gate.set(); QTest.qWait(30)
    assert f.d._cancelled and not f.d.isVisible() and not f.accepted


def test_fake_server_failure_is_visible_without_secret_logging(flow, monkeypatch, caplog):
    f = flow
    def failed(email, password):
        raise RuntimeError('raw-cookie-response-123456-synthetic-password')
    monkeypatch.setattr(auth, 'start_login', failed)
    with caplog.at_level(logging.INFO):
        QTest.keyClicks(f.app.focusWidget(), 'synthetic-password')
        key(f.app, Qt.Key_Return)
        assert pump(f.app, lambda: f.d.error.isVisible())
    assert 'RuntimeError' in f.d.error.text()
    for secret in ('raw-cookie', '123456', 'synthetic-password'):
        assert secret not in caplog.text + f.d.error.text()


def test_pending_two_sides_one_enter_does_not_answer_other_side(ui):
    app, c, _, _ = ui
    pending = SimpleNamespace(to_pc=['pc1'], to_cloud=['cloud1'], names={})
    d = dialogs.PendingDialog(pending, c.c); seen = []
    d.decided.connect(lambda *args: seen.append(args)); d.show(); d.activateWindow(); app.processEvents()
    keep = next(b for b in d.findChildren(QPushButton) if b.text() == dialogs.t('Keep on this PC'))
    tab_to(app, keep)
    key(app, Qt.Key_Return)
    assert seen == [('reject', ['pc1'])] and d.isVisible()
    # The newly focused list must not implicitly approve another section.
    key(app, Qt.Key_Return)
    assert not any(x[0] == 'approve' for x in seen)
    d.close()


def test_settings_signout_requires_its_confirmation(ui):
    app, c, _, _ = ui
    d = dialogs.SettingsDialog(c.settings, c.c); seen=[]; answered=[]
    d.signOut.connect(lambda: seen.append(1)); d.show(); d.activateWindow(); app.processEvents()
    out = next(b for b in d.findChildren(QPushButton) if b.text() == dialogs.t('Sign out'))
    def dismiss():
        box = app.activeModalWidget()
        if isinstance(box, QMessageBox):
            answered.append(box.windowTitle())
            QTest.keyClick(box, Qt.Key_Escape)
    QTimer.singleShot(30, dismiss)
    tab_to(app, out); key(app, Qt.Key_Return)
    assert answered and not seen and d.isVisible()
    d.close()


def test_enter_on_cancel_neither_submits_credentials_nor_cancels(flow):
    f = flow
    QTest.keyClicks(f.app.focusWidget(), 'synthetic-password')
    tab_to(f.app, f.d.cancel)
    key(f.app, Qt.Key_Return); key(f.app, Qt.Key_Enter)
    assert f.seen == [] and not f.d._cancelled
    key(f.app, Qt.Key_Space)
    assert f.d._cancelled


def test_settings_field_enter_starts_nothing(ui, tmp_path):
    # Enter in a Settings field is deliberately inert: it neither signs out nor saves/closes
    # (saving can start folder moves or deletion questions; that takes a click on Save).
    app, c, _, _ = ui
    d = dialogs.SettingsDialog(c.settings, c.c)
    saved=[]; signed_out=[]
    d.accepted.connect(lambda: saved.append(True))
    d.signOut.connect(lambda: signed_out.append(True))
    d.show(); d.activateWindow(); app.processEvents()
    # Select Syncing with a real mouse click, then Enter in its unchanged number field (1.1: folders
    # are shown on cards and changed with Change…, so the fields left are numbers like this one).
    rect=d.nav.visualItemRect(d.nav.item(d.PAGES.index("general")))
    QTest.mouseClick(d.nav.viewport(), Qt.LeftButton, pos=rect.center())
    QTest.mouseClick(d.threshold, Qt.LeftButton)
    assert app.focusWidget() is d.threshold
    key(app, Qt.Key_Return)
    assert not saved and not signed_out and d.isVisible()
    d.reject()


@pytest.mark.parametrize('answer', ['approve', 'reject'])
def test_pending_explicit_keyboard_choice_affects_only_selected_section(ui, answer):
    app, c, _, _ = ui
    d = dialogs.PendingDialog(SimpleNamespace(to_pc=['pc'], to_cloud=['cloud'], names={}), c.c)
    seen=[]; d.decided.connect(lambda *args: seen.append(args))
    d.show(); d.activateWindow(); app.processEvents()
    label = 'Delete on this PC too' if answer == 'approve' else 'Keep on this PC'
    button=next(b for b in d.findChildren(QPushButton) if b.text()==dialogs.t(label))
    tab_to(app, button); key(app, Qt.Key_Space)
    assert seen == [(answer, ['pc'])] and d.isVisible()
    d.close()


def test_account_change_through_keyboard_keeps_services_disabled(flow, monkeypatch):
    f=flow
    f.c.settings.drive_enabled=True; f.c.settings.notes_enabled=True
    # Only the informational, non-confirmation popup is recorded instead of exec().
    messages=[]
    from skyfold import app as appmod
    monkeypatch.setattr(appmod, 'inform', lambda *args: messages.append(args))
    key(f.app, Qt.Key_Backtab)
    key(f.app, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(f.app.focusWidget(), 'second@example.invalid')
    key(f.app, Qt.Key_Tab)
    code_page(f)
    f.code_gate.set(); QTest.keyClicks(f.app.focusWidget(), '123456')
    assert pump(f.app, lambda: f.accepted == [f.api])
    assert f.c.settings.apple_id == 'second@example.invalid'
    assert not f.c.settings.drive_enabled and not f.c.settings.notes_enabled
    assert pump(f.app, lambda: bool(messages))
