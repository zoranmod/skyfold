"""Codex's Qt palette probe from review 28, kept as a test (synthetic devices, no network).

The characterize_ test asserted the defect; here it asserts the fix. Qt is shut down by conftest.py.
"""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

from skyfold.ui import dialogs, theme


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance
    for w in instance.topLevelWidgets():
        w.hide()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_fixed_refresh_redraws_device_icon_and_leaves_unrelated_widgets(app):
    fake_device = SimpleNamespace(name="Example", model="Phone", battery=None,
                                  online=False, located_at=None, lat=None, can_sound=False)
    row = dialogs.DeviceRow(fake_device, theme.LIGHT, lambda: None)
    icon = next(label for label in row.findChildren(QLabel) if not label.pixmap().isNull())
    icon_before = icon.pixmap().toImage()
    unrelated = QLineEdit()
    unrelated.c = {"profile": "unrelated"}
    app.setStyleSheet(theme.stylesheet(theme.DARK))
    app.setPalette(theme.qpalette(theme.DARK))
    row.show()
    dialogs.refresh_palette(theme.DARK)
    assert row.c is theme.DARK and icon.pixmap().toImage() != icon_before
    assert unrelated.c == {"profile": "unrelated"}  # only widgets with apply_palette are touched
    unrelated.ensurePolished()
    assert unrelated.palette().color(QPalette.HighlightedText).name() == theme.DARK["accent_text"]
    row.close()
    app.setStyleSheet("")
