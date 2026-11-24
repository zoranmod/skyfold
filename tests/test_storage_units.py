"""iCloud storage is shown in Apple's units: a "200 GB" plan is 200 x 1024^3 bytes."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

GIB = 1024 ** 3


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_200_gb_plan_reads_200_gb():
    from skyfold.ui.flyout import storage_text
    assert storage_text(200 * GIB) == "200 GB"
    assert storage_text(50 * GIB) == "50 GB"
    assert storage_text(2048 * GIB) == "2 TB"


def test_storage_bar_shows_the_plan_size(qapp):
    from skyfold.ui import flyout, theme
    bar = flyout.StorageBar(theme.palette("dark"))
    used = int(94.2e9)  # what was shown as "94.2 GB of 214.7 GB" before
    bar.set(used, 200 * GIB, [("Photos", int(7.5e9), "photos"), ("Drive", int(68.6e9), "docs")])
    assert bar.amount.text() == "87.7 GB of 200 GB"
    assert "214" not in bar.accessibleName()
