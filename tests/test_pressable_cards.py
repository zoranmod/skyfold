"""Cards that are clicked (Settings help cards, the restore-photos card, the Photos Years and Months
cards) have a name for screen readers (their visible text when none is given) and work with Enter and
Space like buttons."""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _keys_press(qapp, widget, hits):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    widget.show()
    widget.setFocus()
    qapp.processEvents()
    for key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
        QTest.keyClick(widget, key)
    assert len(hits) == 3


def test_a_click_card_is_named_after_what_it_says(qapp):
    from PySide6.QtWidgets import QLabel, QVBoxLayout
    from skyfold.ui.settings_parts import ClickCard

    card = ClickCard()
    col = QVBoxLayout(card)
    col.addWidget(QLabel("Restore photos"))
    col.addWidget(QLabel("Bring back deleted photos"))
    hits = []
    card.clicked.connect(lambda: hits.append(1))
    card.show()
    qapp.processEvents()
    assert card.accessibleName() == "Restore photos, Bring back deleted photos"
    named = ClickCard()
    named.setAccessibleName("Restore photos")
    named.show()
    assert named.accessibleName() == "Restore photos"  # a given name wins
    _keys_press(qapp, card, hits)
    card.close()
    named.close()


def test_years_and_months_cards_are_named_and_work_with_the_keyboard(qapp):
    from skyfold.ui import photos_window as pw, theme

    card = pw.Card(SimpleNamespace(c=theme.palette("dark"), thumbs=None), "2024", "12 items · 3 months", [],
                   mosaic=False)  # (no covers: nothing to load)
    card.resize(320, 180)
    hits = []
    card.clicked.connect(lambda: hits.append(1))
    card.show()
    qapp.processEvents()
    assert card.accessibleName() == "2024, 12 items · 3 months"
    _keys_press(qapp, card, hits)
    card.close()
