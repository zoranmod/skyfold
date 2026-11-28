"""The scheduled startup panel uses two Qt objects that may already have been torn down."""

import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget

from skyfold.app import Controller


@pytest.mark.parametrize("deleted", [None, "tray", "flyout"])
def test_deferred_startup_panel_obeys_widget_lifetime(monkeypatch, deleted):
    app = QApplication.instance() or QApplication([])
    shown = []
    failures = []

    class Panel(QWidget):
        def show_at(self, anchor):
            self.show()
            shown.append(anchor)

    ctl = SimpleNamespace(
        flyout=Panel(), tray=QSystemTrayIcon(), _browser_link=lambda: None,
        _warm_up=lambda: None,
        settings=SimpleNamespace(start_with_windows=False, photos_dir_confirmed=True,
                                 apple_id="garden@example.test"),
    )
    single_shot = QTimer.singleShot

    def schedule(delay, *args):
        if delay == 300:
            single_shot(0, *args)

    monkeypatch.setattr(QTimer, "singleShot", schedule)
    import traceback

    def caught(typ, exc, tb):
        # only what the deferred start-up panel itself raises: a timer left by an earlier test may
        # fire inside this test's short event loop too, and is not what this test is about
        if any(frame.filename.replace("\\", "/").endswith("skyfold/app.py") for frame in traceback.extract_tb(tb)):
            failures.append(str(exc))

    monkeypatch.setattr(sys, "excepthook", caught)
    Controller.start(ctl, show=True)
    if deleted:
        getattr(ctl, deleted).deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    loop = QEventLoop()
    single_shot(10, loop.quit)
    loop.exec()
    if deleted != "flyout":
        ctl.flyout.close()
    assert not failures
    assert bool(shown) == (deleted is None)
