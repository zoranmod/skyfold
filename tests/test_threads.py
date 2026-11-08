"""Regression tests for the freeze after sign-in: UI code must only ever run on the UI thread."""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from skyfold import app as app_mod
from skyfold.config import Settings
from skyfold.ui.dialogs import run_async


def pump(cond, timeout=5.0) -> bool:
    qapp = QApplication.instance()
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_run_async_callbacks_run_on_ui_thread(qapp):
    seen: list[str] = []
    run_async(lambda: 42, lambda r: seen.append(threading.current_thread().name), lambda m: None)
    run_async(lambda: 1 / 0, lambda r: None, lambda m: seen.append(threading.current_thread().name))
    assert pump(lambda: len(seen) == 2)
    assert seen == ["MainThread", "MainThread"]


def test_worker_signals_reach_ui_on_ui_thread(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod.Settings, "load", classmethod(lambda cls: Settings(
        apple_id="t@example.com", photos_dir=str(tmp_path / "S"), photos_dir_confirmed=True)))
    monkeypatch.setattr(app_mod.SyncWorker, "start", lambda self: None)
    ctl = app_mod.Controller(qapp)
    try:
        seen: dict[str, str] = {}

        def record(name):
            def fn(*_a, **_k):
                seen[name] = threading.current_thread().name
            return fn

        monkeypatch.setattr(ctl.flyout, "set_state", record("state"))
        monkeypatch.setattr(ctl.flyout, "set_progress", record("progress"))
        monkeypatch.setattr(ctl.tray, "showMessage", record("notify"))

        def from_worker_thread():
            ctl.worker.stateChanged.emit("idle", "ok")
            ctl.worker.progress.emit(5, 10, "x")
            ctl.worker.notify.emit("a", "b")

        t = threading.Thread(target=from_worker_thread, name="FakeWorker")
        t.start()
        t.join()
        assert pump(lambda: len(seen) == 3), seen
        assert set(seen.values()) == {"MainThread"}, seen
    finally:
        ctl.thread.quit()
        ctl.thread.wait(2000)
        ctl.tray.hide()


def test_drive_size_estimate_uses_docs_category(qapp):
    from skyfold.config import Settings
    from skyfold.ui import theme
    from skyfold.ui.dialogs import SettingsDialog

    storage = {"used": 1, "total": 2, "parts": [("Photos and Videos", 7_500_000_000, "photos"),
                                                ("Docs", 69_600_000_000, "docs")]}
    dlg = SettingsDialog(Settings(), theme.LIGHT, storage=storage)
    assert dlg.drive_estimate() == 69_600_000_000
    assert SettingsDialog(Settings(), theme.LIGHT).drive_estimate() is None
