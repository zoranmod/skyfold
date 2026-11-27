"""The same plain-text help works for activity children, clipped labels and item-view rows."""
import pytest
from PySide6.QtCore import QEvent, QPoint
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QListWidget

from skyfold.ui import theme
from skyfold.ui.flyout import ActivityRow
from skyfold.ui.settings_parts import ElidedLabel
from skyfold.ui.tooltips import ToolTips
from tests.test_review27_codex_ui_probes import qapp  # noqa: F401


@pytest.fixture
def tips(qapp):  # noqa: F811
    help_filter = ToolTips(qapp)
    yield help_filter
    qapp.removeEventFilter(help_filter)
    help_filter.dismiss()
    if help_filter.popup is not None:
        help_filter.popup.deleteLater()
    help_filter.deleteLater()


def hover(app, widget, pos=None):
    pos = pos or widget.rect().center()
    app.sendEvent(widget, QHelpEvent(QEvent.ToolTip, pos, widget.mapToGlobal(pos)))


def test_activity_shows_action_and_literal_path_over_every_child(qapp, tips):  # noqa: F811
    path = "C:\\Garden\\Plots\\" + "Preservation\\" * 14 + "Garden plan & notes.txt"
    row = ActivityRow("2026-11-27T12:00:00Z", "info", "Downloaded <b>1</b> new file", theme.LIGHT, path)
    row.show()
    try:
        for child in (row.text, row.tile, row.when):
            hover(qapp, child)
            assert tips.owner is row
            assert tips.popup.accessibleName() == "Downloaded <b>1</b> new file"
            assert tips.popup.accessibleDescription() == path
            assert tips.popup.rows[-1][0].endswith("Garden plan & notes.txt")
            assert tips.popup.width() <= 374
            assert tips.popup.mask().isEmpty()
        row.apply_palette(theme.OLED)
        hover(qapp, row.text)
        assert tips.popup.colours == theme.OLED
        row.apply_palette(theme.DARK)
        hover(qapp, row.text)
        assert tips.popup.colours == theme.DARK
        row.hide()
        assert not tips.popup.isVisible() and not tips.timer.isActive()
    finally:
        row.close()


def test_item_view_reads_each_role_and_dismisses_on_an_empty_row(qapp, tips):  # noqa: F811
    view = QListWidget()
    view.addItems(["Garden", "Plan"])
    view.item(0).setToolTip("Garden & pond")
    view.item(1).setToolTip("<qt>&lt;b&gt;Plan&lt;/b&gt;</qt>")
    view.show()
    try:
        for n, text in enumerate(("Garden & pond", "<b>Plan</b>")):
            hover(qapp, view.viewport(), view.visualItemRect(view.item(n)).center())
            assert tips.popup.accessibleName() == text
        hover(qapp, view.viewport(), QPoint(4, view.viewport().height() - 2))
        assert not tips.popup.isVisible()
    finally:
        view.close()


def test_clipped_label_and_list_offer_help_only_when_needed(qapp, tips):  # noqa: F811
    name = "Garden plan for the trees near the river"
    label = ElidedLabel(name)
    view = QListWidget()
    view.setProperty("tipForClippedItems", True)
    view.addItem(name)
    try:
        for width, clipped in ((80, True), (640, False)):
            for widget in (label, view):
                widget.resize(width, 80)
                widget.show()
            qapp.processEvents()
            assert bool(label.toolTip()) == clipped
            shown = view.visualItemRect(view.item(0)).intersected(view.viewport().rect())
            assert not shown.isEmpty()
            hover(qapp, view.viewport(), shown.center())
            assert tips.popup.isVisible() == clipped
    finally:
        view.close()
        label.close()
