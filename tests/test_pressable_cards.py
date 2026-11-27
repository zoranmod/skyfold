"""Cards that are clicked (Settings help cards, the restore-photos card, the Photos Years and Months
cards) are buttons for a screen reader too: role, name and a Press action, plus Enter/Space."""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _press_by_screen_reader(widget):
    from PySide6.QtGui import QAccessible
    iface = QAccessible.queryAccessibleInterface(widget)
    acts = iface.actionInterface()
    assert acts is not None
    assert acts.pressAction() in acts.actionNames()
    acts.doAction(acts.pressAction())
    return iface


def _keys_press(qapp, widget, hits):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    widget.show()
    widget.setFocus()
    qapp.processEvents()
    for key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
        QTest.keyClick(widget, key)
    assert len(hits) == 3


def test_a_click_card_is_a_button_with_a_name_and_a_press_action(qapp):
    from PySide6.QtGui import QAccessible
    from PySide6.QtWidgets import QLabel, QVBoxLayout
    from skyfold.ui.settings_parts import ClickCard

    card = ClickCard()
    col = QVBoxLayout(card)
    col.addWidget(QLabel("Restore photos"))
    col.addWidget(QLabel("Bring back deleted photos"))
    hits = []
    card.clicked.connect(lambda: hits.append(1))
    card.show()
    iface = _press_by_screen_reader(card)
    assert hits == [1]
    assert iface.role() == QAccessible.Role.Button
    # no name given: what the card says; a given name wins
    assert iface.text(QAccessible.Text.Name) == "Restore photos, Bring back deleted photos"
    card.setAccessibleName("Restore photos")
    assert iface.text(QAccessible.Text.Name) == "Restore photos"
    assert iface.state().focusable
    card.setEnabled(False)  # a disabled card can't be pressed this way either
    iface.actionInterface().doAction(iface.actionInterface().pressAction())
    assert hits == [1]
    card.setEnabled(True)
    hits.clear()
    _keys_press(qapp, card, hits)
    card.close()


def test_the_link_cards_in_settings_are_pressable(qapp):
    from PySide6.QtGui import QAccessible
    from PySide6.QtWidgets import QVBoxLayout, QWidget
    from skyfold.ui import dialogs, theme

    host = QWidget()
    group = QVBoxLayout(host)
    fake = SimpleNamespace(c=theme.palette("dark"))
    hits = []
    card = dialogs.SettingsDialog._link_card(fake, group, "help", "Report a problem", "Opens GitHub",
                                             lambda: hits.append(1))
    host.show()
    iface = _press_by_screen_reader(card)
    assert hits == [1] and iface.role() == QAccessible.Role.Button
    assert iface.text(QAccessible.Text.Name) == "Report a problem"
    host.close()


def test_years_and_months_cards_are_pressable_buttons(qapp):
    from PySide6.QtGui import QAccessible
    from skyfold.ui import photos_window as pw, theme

    card = pw.Card(SimpleNamespace(c=theme.palette("dark"), thumbs=None), "2024", "12 items · 3 months", [],
                   mosaic=False)  # (no covers: nothing to load)
    card.resize(320, 180)
    hits = []
    card.clicked.connect(lambda: hits.append(1))
    card.show()
    iface = _press_by_screen_reader(card)
    assert hits == [1]
    assert iface.role() == QAccessible.Role.Button
    assert iface.text(QAccessible.Text.Name) == "2024, 12 items · 3 months"
    hits.clear()
    _keys_press(qapp, card, hits)
    card.close()
