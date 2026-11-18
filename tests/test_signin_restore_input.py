"""Real Controller/worker startup, engine and PCS translation; fake remote/auth only."""
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt, QMetaObject
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pyicloud.exceptions import PyiCloudAPIResponseException

from skyfold import app as appmod, auth, worker as wk, extras
from skyfold.config import Settings
from skyfold.remote import _translate
from tests.test_engine import FakeRemote
from tests.test_signin_lifecycle import pump


@pytest.fixture
def startup(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    settings = Settings(apple_id='restore@example.invalid', photos_dir=str(tmp_path/'Photos'),
                        photos_dir_confirmed=True, notifications=False)
    monkeypatch.setattr(Settings, 'load', classmethod(lambda cls: settings))
    monkeypatch.setattr(Settings, 'save', lambda self: None)
    monkeypatch.setattr(wk, 'STATE_DB', tmp_path/'state.db')
    monkeypatch.setattr(wk.SyncWorker, '_ensure_watchers', lambda self: None)
    monkeypatch.setattr(auth, 'make_current', lambda api: None)
    monkeypatch.setattr(auth, 'commit_login', lambda api: None)
    monkeypatch.setattr(auth, 'SIGNED_OUT', tmp_path/'signedout')
    monkeypatch.setattr(extras, 'storage', lambda api: {})
    gate = threading.Event(); calls=[]; remote=FakeRemote()
    def restore(email):
        assert email == settings.apple_id
        assert gate.wait(5)
        api=SimpleNamespace(account_name=email)
        calls.append(api)
        return api
    monkeypatch.setattr(auth, 'restore', restore)
    monkeypatch.setattr(wk, 'ICloudRemote', lambda api: remote)
    monkeypatch.setattr(auth, 'start_login', lambda email, password: auth.LoginStep(SimpleNamespace(), True, 'Test code'))
    monkeypatch.setattr(auth, 'submit_code', lambda api, code: None)
    c=appmod.Controller(app); notices=[]
    c.worker.notify.connect(lambda title, body: notices.append((title, body)))
    yield SimpleNamespace(app=app, c=c, gate=gate, calls=calls, notices=notices, remote=remote)
    gate.set(); c.clock.stop(); c.anim.stop()
    c.worker.cancel()
    QMetaObject.invokeMethod(c.worker, 'shutdown', Qt.BlockingQueuedConnection)
    c.thread.quit(); assert c.thread.wait(3000)
    c.tray.hide(); c.flyout.hide()
    for d in list(c._dialogs.values()):
        if hasattr(d, 'cancel'):
            QTest.mouseClick(d.cancel, Qt.LeftButton)
        d.deleteLater()
    c.flyout.deleteLater(); c.tray.deleteLater(); app.processEvents()


def test_saved_session_startup_never_opens_login(startup):
    f=startup
    f.c.start(show=True); f.gate.set()
    assert pump(f.app, lambda: f.c.state=='idle' and len(f.calls)==1 and f.c.worker.api is f.calls[0])
    QTest.qWait(450)  # allow the actual startup singleShot callbacks to run
    assert 'login' not in f.c._dialogs and not f.notices
    for _ in range(3):
        f.c.req.syncNow.emit(False)
        QTest.qWait(20)
    assert len(f.calls)==1 and 'login' not in f.c._dialogs


def test_pcs_failure_quietly_renews_three_times_then_one_prompt(startup, monkeypatch, caplog):
    f=startup
    def pcs():
        exc=PyiCloudAPIResponseException('RAW_APPLE_BODY_cookie-secret', 500,
            SimpleNamespace(url='https://setup.icloud.com/setup/ws/1/requestWebAccessState', text='RAW_BODY'))
        raise _translate(exc)
    monkeypatch.setattr(f.remote, 'cursor', pcs)
    f.c.start(show=True); f.gate.set()
    assert pump(f.app, lambda: f.c.state=='auth' and len(f.notices)==1, timeout=6)
    assert len(f.calls)==4  # initial restore plus exactly three quiet renewals
    assert 'login' not in f.c._dialogs  # one panel/notification prompt, not unsolicited dialogs
    for _ in range(8):
        f.c.req.syncNow.emit(False)
        QTest.qWait(15)
    assert len(f.calls)==4 and len(f.notices)==1
    f.c.flyout.show_at(f.c.tray.geometry()); f.app.processEvents()
    QTest.mouseClick(f.c.flyout.sync_btn, Qt.LeftButton)
    assert pump(f.app, lambda: 'login' in f.c._dialogs and f.c._dialogs['login'].isVisible())
    d=f.c._dialogs['login']
    QTest.keyClicks(f.app.focusWidget(), 'synthetic-password')
    QTest.keyClick(f.app.focusWidget(), Qt.Key_Return)
    assert pump(f.app, lambda: d.code.isVisible() and d.code.isEnabled())
    assert not d._cancelled
    assert 'RAW_APPLE_BODY' not in caplog.text and 'cookie-secret' not in caplog.text
