"""The remaining windows in the 1.1 design: Drive folders, the update window, Find My, the message
boxes, the notifications list and the menus (made-up data only; nothing is read from iCloud)."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QWidget

from skyfold import updater
from skyfold.ui import dialogs, theme
from skyfold.ui.panel_parts import SoftCard


@pytest.fixture
def app():
    a = QApplication.instance() or QApplication([])
    yield a
    a.setStyleSheet("")


def _footer(w: QWidget) -> QWidget:
    return w.findChild(QWidget, "Footer")


def test_drive_folders_is_glass_with_a_card_mono_sizes_and_a_footer(app):
    d = dialogs.DriveFoldersDialog(None, theme.DARK, lambda: None, {"Documents": (1284, 3_400_000_000)})
    try:
        assert d.GLASS
        assert isinstance(d.tree.parentWidget(), SoftCard)  # the tree on a rounded card
        d.add_children(None, [SimpleNamespace(path="Desktop"), SimpleNamespace(path="Documents")])
        docs = d.tree.topLevelItem(1)
        assert "Cascadia Mono" in docs.font(1).families() and docs.text(1)
        assert not docs.icon(0).isNull()
        foot = _footer(d)
        names = {b.text(): b.objectName() for b in foot.findChildren(QPushButton)}
        assert names == {dialogs.t("Cancel"): "Link", dialogs.t("Save"): "Primary"}
        assert d.ok.isEnabled()
        dialogs.refresh_palette(theme.LIGHT)
        assert d.c is theme.LIGHT
        next(b for b in foot.findChildren(QPushButton) if b.objectName() == "Link").click()
        assert d.result() == 0
    finally:
        d.close()


def test_update_window_reads_well_and_keeps_its_buttons(app):
    app.setPalette(theme.qpalette(theme.OLED))
    rel = updater.Release("9.9.9", "### NEW THINGS\n\nFaster **sync**, see [the page](https://example.com).\n\n- one",
                          updater.PAGE, "z", "s")
    d = dialogs.UpdateDialog(rel, can_install=True)
    try:
        assert d.GLASS and d.c is theme.OLED  # the theme the app shows
        assert d.later.objectName() == "Link" and d.go.objectName() == "Primary"
        assert d.later.parentWidget() is _footer(d) and d.go.parentWidget() is _footer(d)
        assert isinstance(d.notes.parentWidget(), SoftCard)
        doc = d.notes.document()
        heading = doc.begin()
        assert heading.text() == "New things"  # sentence case
        body = heading.next()
        it = body.begin()
        assert it.fragment().charFormat().fontPointSize() == pytest.approx(10.5)  # 14 px
        version = next(lb for lb in d.findChildren(QLabel) if "Cascadia Mono" in lb.text())
        assert dialogs.VERSION in version.text()
    finally:
        d.close()
        app.setPalette(theme.qpalette(theme.LIGHT))


def test_find_my_rows_are_cards_with_small_buttons(app, monkeypatch):
    now = datetime.now().astimezone()
    dev = SimpleNamespace(id="1", name="<b>Phone</b>", model="Phone", battery=0.5, charging=False, online=True,
                          located_at=(now - timedelta(minutes=3)).isoformat(), lat=1.0, lon=2.0, can_sound=True)
    monkeypatch.setattr(dialogs, "run_async", lambda _task, done, _fail: done([dev]))
    d = dialogs.FindMyDialog(theme.DARK, lambda: object())
    try:
        assert d.GLASS
        row = d.rows.itemAt(0).widget()
        assert isinstance(row, dialogs.DeviceRow) and isinstance(row, SoftCard)
        assert row.map_btn.isEnabled() and row.sound_btn.isEnabled()
        assert row.map_btn.maximumHeight() <= 30  # small: 28 px and its edge
        assert "<b>Phone</b>" in row.accessibleName() and "50%" in row.accessibleName()
        detail = next(lb for lb in row.findChildren(QLabel) if "Cascadia Mono" in lb.text())
        assert "50%" in detail.text() and "<b>" not in detail.text()
        name = next(lb for lb in row.findChildren(QLabel) if lb.objectName() == "SetTitle")
        assert name.textFormat() == Qt.PlainText  # a name from iCloud is never read as markup
        assert _footer(d) is not None
    finally:
        d.close()


@pytest.mark.parametrize("answer", ["yes", "escape"])
def test_questions_keep_their_answers(app, answer):
    """ask() still answers QMessageBox.Yes / No, now from the app's own message window."""
    app.setStyleSheet(theme.stylesheet(theme.DARK))
    host = QWidget()
    host.c = theme.DARK
    seen = []

    def act():
        box = app.activeModalWidget()
        assert isinstance(box, dialogs.MessageDialog)
        yes, no = box.button("yes"), box.button("no")
        seen.append((box.kind, yes.objectName(), yes.x() > no.x()))
        if answer == "yes":
            yes.click()
        else:
            QTest.keyClick(box, Qt.Key_Escape)

    QTimer.singleShot(0, act)
    got = dialogs.ask(host, "Title", "Question?")
    assert got == (QMessageBox.Yes if answer == "yes" else QMessageBox.No)
    assert seen == [("question", "Primary", True)]  # the main answer on the right


def test_warn_and_inform_show_their_sign_on_a_tile(app):
    for fn, kind in ((dialogs.warn, "warning"), (dialogs.inform, "info")):
        seen = []

        def act():
            box = app.activeModalWidget()
            seen.append((box.kind, not box.tile.pixmap().isNull(), box.button("ok").objectName()))
            box.button("ok").click()

        QTimer.singleShot(0, act)
        assert fn(None, "T", "Text") is None
        assert seen == [(kind, True, "Primary")]


def test_notifications_list_groups_today_and_earlier(app):
    from skyfold.ui.notices import Notice, NoticeLog, NoticeRow, NoticesPage

    now = datetime.now().astimezone()
    log = NoticeLog()
    log.add(Notice("Old", "x", ts=(now - timedelta(days=2)).isoformat(timespec="seconds")))
    log.add(Notice("New", "y", "photo", ts=(now - timedelta(minutes=2)).isoformat(timespec="seconds")))
    page = NoticesPage(log, theme.LIGHT, lambda key: None)
    try:
        page.rebuild()
        assert [r.notice.title for r in page.rows()] == ["New", "Old"]
        heads = [lb.text() for lb in page.findChildren(QLabel) if lb.objectName() == "ListHead"]
        assert heads == [dialogs.t("Today"), dialogs.t("Earlier")]
        row = page.rows()[0]
        assert isinstance(row, NoticeRow) and row.when.objectName() == "ActWhen"
        assert row.tile.width() == 32
        assert page.clear_button.text() == dialogs.t("Clear") and page.clear_button.isEnabled()
        page.clear_button.click()
        page.rebuild()
        assert page.rows() == [] and not page.clear_button.isEnabled()
    finally:
        page.close()


def test_menus_have_icons_and_take_a_new_theme(app):
    from skyfold.ui.menus import Menu

    m = Menu(theme.LIGHT)
    ran = []
    a = m.add("sync", "Sync now", lambda: ran.append(1))
    before = a.icon().pixmap(16, 16).toImage()
    dialogs.refresh_palette(theme.DARK)  # (a menu is a window of its own: reached like the others)
    assert m.c is theme.DARK and a.icon().pixmap(16, 16).toImage() != before
    assert "border-radius: 8px" in m.styleSheet()
    a.trigger()
    assert ran == [1]
    m.popup(QPoint(10, 10))
    app.processEvents()
    assert m.actionGeometry(a).height() >= 30  # about 32 px
    m.close()


def test_the_tray_menu_is_see_through_like_the_panel(app, monkeypatch):
    """With Windows' transparency effects on, the menu has no fill of its own: Windows blurs the
    desktop behind it with the theme's tint and draws its rounded edge and shadow."""
    from skyfold.ui import backdrop, flyout
    from skyfold.ui.menus import Menu

    tints, rounded = [], []
    monkeypatch.setattr(backdrop, "glass_supported", lambda: True)
    monkeypatch.setattr(backdrop, "blur_behind", lambda w, tint=None: tints.append(tint.getRgb()) or True)
    monkeypatch.setattr(flyout, "round_corners", lambda w: rounded.append(w))
    m = Menu(theme.DARK)
    m.add("sync", "Sync now")
    m.popup(QPoint(10, 10))
    app.processEvents()
    try:
        assert m.glass and "rgba(0, 0, 0, 2)" in m.styleSheet() and rounded == [m]
        assert tints == [backdrop.rgba(backdrop.glass_colours(theme.DARK, True)["tint"]).getRgb()]
        dialogs.refresh_palette(theme.OLED)  # (a new theme keeps it see-through)
        assert "rgba(0, 0, 0, 2)" in m.styleSheet()
    finally:
        m.close()
        dialogs.refresh_palette(theme.DARK)
    monkeypatch.setattr(backdrop, "glass_supported", lambda: False)
    m.popup(QPoint(10, 10))  # transparency effects switched off: the solid menu
    app.processEvents()
    assert not m.glass and "rgba(0, 0, 0, 2)" not in m.styleSheet()
    m.close()
