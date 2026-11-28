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


def test_a_cards_name_follows_its_labels(qapp):
    """The name was taken from the labels once, when the card was first shown: a card whose text
    changed later was still read out with the old words."""
    from PySide6.QtWidgets import QLabel, QVBoxLayout
    from skyfold.ui.settings_parts import ClickCard

    card = ClickCard()
    col = QVBoxLayout(card)
    title, detail = QLabel("Restore photos"), QLabel("3 photos can be brought back")
    col.addWidget(title)
    col.addWidget(detail)
    card.show()
    qapp.processEvents()
    assert card.accessibleName() == "Restore photos, 3 photos can be brought back"
    detail.setText("Nothing to bring back")
    qapp.processEvents()
    assert card.accessibleName() == "Restore photos, Nothing to bring back"
    col.addWidget(QLabel("Checked just now"))  # a label that comes later counts too
    qapp.processEvents()
    assert card.accessibleName() == "Restore photos, Nothing to bring back, Checked just now"
    detail.hide()  # (what is not shown is not read)
    qapp.processEvents()
    assert card.accessibleName() == "Restore photos, Checked just now"
    card.close()


def test_a_name_given_by_hand_stays_when_the_labels_change(qapp):
    from PySide6.QtWidgets import QLabel, QVBoxLayout
    from skyfold.ui.settings_parts import ClickCard

    card = ClickCard()
    label = QLabel("Help")
    QVBoxLayout(card).addWidget(label)
    card.setAccessibleName("Open the help page")
    card.show()
    qapp.processEvents()
    label.setText("Help and tips")
    qapp.processEvents()
    assert card.accessibleName() == "Open the help page"
    # also one given after the card had already named itself
    auto = ClickCard()
    words = QLabel("Help")
    QVBoxLayout(auto).addWidget(words)
    auto.show()
    qapp.processEvents()
    assert auto.accessibleName() == "Help"
    auto.setAccessibleName("Open the help page")
    words.setText("Help and tips")
    qapp.processEvents()
    assert auto.accessibleName() == "Open the help page"
    card.close()
    auto.close()


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
