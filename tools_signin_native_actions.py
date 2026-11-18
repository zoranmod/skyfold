"""Offline native-frame acceptance harness: operate the named X/minimise/Alt+F4 action.

Run with Python on Windows (without QT_QPA_PLATFORM=offscreen). No Apple calls or
profile writes. Prints READY/PASS for each of 15 cases; timeout/failure exits nonzero.
QtTest enters the fake credentials/code; the operator drives only the real native frame.
"""
import sys
import threading
import time
from contextlib import ExitStack
from unittest.mock import patch

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from skyfold import app as appmod, auth
from skyfold.config import Settings
from skyfold.ui.dialogs import t


def offline(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo'):
        raise RuntimeError('Native input diagnostic is offline')


def main():
    sys.addaudithook(offline)
    app=QApplication([]); app.setQuitOnLastWindowClosed(False)
    settings=Settings(apple_id='native@example.invalid', photos_dir_confirmed=True, paused=True,
                      notifications=False, start_with_windows=False)
    def pump(condition, seconds=5):
        until=time.monotonic()+seconds
        while time.monotonic()<until:
            app.processEvents()
            if condition():
                return
            time.sleep(.01)
        raise AssertionError('Timed out waiting for native input/state')
    with ExitStack() as stack:
        for obj,name,value in ((Settings,'load',classmethod(lambda cls: settings)),
                               (Settings,'save',lambda self: None),
                               (appmod.SyncWorker,'start',lambda self: None),
                               (auth,'restore',lambda email: None),
                               (auth,'commit_login',lambda api: None)):
            stack.enter_context(patch.object(obj,name,value))
        c=appmod.Controller(app); c.req.setApi.disconnect(); c.req.applySettings.disconnect()
        gates=[]
        try:
            for stage in ('credentials','waiting','code','checking','error'):
                for action in ('close','minimise','alt-f4'):
                    gate,code_gate=threading.Event(),threading.Event(); gates.extend((gate,code_gate))
                    entered=[]; checked=[]; api=object()
                    def login(email, password):
                        assert email==settings.apple_id and password=='synthetic-password'
                        entered.append(True); assert gate.wait(300)
                        return auth.LoginStep(api,True,'OFFLINE TEST: no Apple requests')
                    def submit(actual, code):
                        assert actual is api
                        checked.append(True); assert code_gate.wait(300)
                        if stage=='error':
                            raise auth.LoginError(t("That code isn't right. Try again."))
                    with patch.object(auth,'start_login',login), patch.object(auth,'submit_code',submit):
                        c._on_state('auth','Offline native input test')
                        c.flyout.show_at(c.tray.geometry()); app.processEvents()
                        QTest.mouseClick(c.flyout.sync_btn,Qt.LeftButton)
                        d=c._dialogs['login']
                        if stage!='credentials':
                            QTest.keyClicks(app.focusWidget(),'synthetic-password')
                            QTest.keyClick(app.focusWidget(),Qt.Key_Return)
                            pump(lambda:bool(entered))
                        if stage in ('code','checking','error'):
                            gate.set(); pump(lambda:d.code.isVisible() and d.code.isEnabled())
                        if stage in ('checking','error'):
                            if stage=='error': code_gate.set()
                            QTest.keyClicks(app.focusWidget(),'123456')
                            pump(lambda:bool(checked))
                            if stage=='error': pump(lambda:d.error.isVisible())
                        name=f'native_{stage}_{action}'
                        d.setWindowTitle('Skyfold OFFLINE INPUT - '+name)
                        app.processEvents()
                        print('READY '+name,flush=True)
                        pump(lambda:d.isMinimized() if action=='minimise' else not d.isVisible(),300)
                        assert not d._cancelled, name+' discarded the attempt'
                        c.flyout.show_at(c.tray.geometry()); app.processEvents()
                        QTest.mouseClick(c.flyout.sync_btn,Qt.LeftButton)
                        pump(lambda:d.isVisible() and not d.isMinimized())
                        assert c._dialogs['login'] is d and not d._cancelled
                        if stage in ('code','error'):
                            assert d.code.isEnabled() and d.code.isVisible() and d.api is api
                        QTest.mouseClick(d.cancel,Qt.LeftButton)
                        gate.set(); code_gate.set()
                        QThreadPool.globalInstance().waitForDone(3000); app.processEvents()
                        assert d._cancelled and not d.isVisible()
                        print('PASS '+name,flush=True)
                        d.deleteLater(); app.processEvents()
            print('RESULT: 15 PASS',flush=True)
        finally:
            for gate in gates: gate.set()
            QThreadPool.globalInstance().waitForDone(3000)
            c.clock.stop(); c.anim.stop(); c.thread.quit(); c.thread.wait(3000)
            c.tray.hide(); c.flyout.hide()
            app.processEvents()


if __name__=='__main__':
    main()
