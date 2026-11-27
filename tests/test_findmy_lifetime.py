"""A Find My window belongs to the account that opened it. All replies are synthetic."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from skyfold.app import Controller, _close_stale_settings
from skyfold.extras import Device
from skyfold.ui import dialogs, theme


@pytest.fixture
def findmy(monkeypatch):
    app = QApplication.instance() or QApplication([])
    requests = []

    def queue(_work, done, failed):
        requests.append((done, failed))

    monkeypatch.setattr(dialogs, "run_async", queue)
    host = SimpleNamespace(_dialogs={}, c=theme.DARK, flyout=QWidget(),
                           worker=SimpleNamespace(api=object()))
    host._show_dialog = lambda key, dlg: Controller._show_dialog(host, key, dlg)
    yield host, requests
    _close_stale_settings(host)
    host.flyout.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_reopening_find_my_uses_the_existing_request(findmy):
    host, requests = findmy
    Controller.open_find_my(host)
    dialog = host._dialogs["findmy"]
    Controller.open_find_my(host)
    assert host._dialogs["findmy"] is dialog
    assert len(requests) == 1


def test_account_close_drops_devices_and_ignores_late_replies(findmy, monkeypatch):
    host, requests = findmy
    Controller.open_find_my(host)
    old = host._dialogs["findmy"]
    done, _ = requests.pop()
    done([Device("one", "Garden phone", "Phone", .8, False, True, None, None, None, True)])
    row = old.rows.itemAt(0).widget()
    row.sound_btn.click()
    sound_done, sound_failed = requests.pop()
    old.refresh_btn.click()
    lookup_done, lookup_failed = requests.pop()
    warnings = []
    monkeypatch.setattr(dialogs, "warn", lambda *_: warnings.append(True))

    _close_stale_settings(host)
    assert "findmy" not in host._dialogs
    Controller.open_find_my(host)
    new = host._dialogs["findmy"]
    assert new is not old
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(old) and not isValid(row)

    lookup_done([Device("two", "Old tablet", "Tablet", None, False, False, None, None, None, False)])
    lookup_failed("Old lookup error")
    sound_done(None)
    sound_failed("Old sound error")
    assert not warnings
    assert new.rows.count() == 0 and new.info.text() != "Old lookup error"
