"""Skyfold's own notification cards: stacking, timing, hover, Windows' quiet times, the setting, and
the Windows fallback. Made-up text only."""

from __future__ import annotations

import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication

from skyfold import config
from skyfold.config import Settings
from skyfold.ui import theme, toast
from skyfold.ui.toast import MAX_CARDS, ToastCard, Toasts


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def still(monkeypatch):
    """No motion (as with Windows' animation effects off): positions and closing happen at once."""
    monkeypatch.setattr(toast, "animations_on", lambda: False)


def pump(cond, timeout=3.0) -> bool:
    app = QApplication.instance()
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def flush(qapp) -> None:
    qapp.processEvents()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def enter(card: ToastCard) -> None:
    p = QPointF(40, 40)
    QApplication.sendEvent(card, QEnterEvent(p, p, card.mapToGlobal(p)))


def leave(card: ToastCard) -> None:
    QApplication.sendEvent(card, QEvent(QEvent.Leave))


def loud() -> None:
    return None


# the card ----------------------------------------------------------------------------------------
def test_card_never_takes_the_focus_and_tells_screen_readers(qapp):
    card = ToastCard("Photos synced", "Downloaded: 3", theme.DARK, "photo", None, [("Open", lambda: None)])
    try:
        assert card.windowFlags() & Qt.WindowDoesNotAcceptFocus
        assert card.windowFlags() & Qt.WindowStaysOnTopHint
        assert card.windowFlags() & Qt.Tool
        assert card.testAttribute(Qt.WA_ShowWithoutActivating)
        assert card.focusPolicy() == Qt.NoFocus
        assert all(b.focusPolicy() == Qt.NoFocus for b in card.buttons + [card.close_button])
        assert card.accessibleName() == "Photos synced"
        assert card.accessibleDescription() == "Downloaded: 3"
        assert [b.accessibleName() for b in card.buttons] == ["Open"]
        assert card.close_button.accessibleName()
    finally:
        card.close()


def test_card_text_is_never_read_as_markup(qapp):
    card = ToastCard("<b>x</b>", "<img src=x> a.jpg", theme.LIGHT)
    try:
        assert card._title.textFormat() == Qt.PlainText and card._text.textFormat() == Qt.PlainText
        assert card._text.text() == "<img src=x> a.jpg"
    finally:
        card.close()


@pytest.mark.parametrize("pal", [theme.LIGHT, theme.DARK, theme.OLED])
def test_card_paints_in_every_theme(qapp, pal):
    card = ToastCard("iCloud Drive synced", "Uploaded: 2", pal, "folder", None,
                     [("Show in folder", lambda: None), ("Later", lambda: None)])
    try:
        card.setAttribute(Qt.WA_DontShowOnScreen)
        card.show()
        img = card.grab().toImage()
        assert not img.isNull() and card.height() > 2 * card.SHADOW + 60
        # the corner pixel is transparent (round corners), the middle is the card's surface
        assert img.pixelColor(0, 0).alpha() == 0
        assert img.pixelColor(img.width() // 2, img.height() // 2).alpha() == 255
    finally:
        card.close()


def test_theme_change_restyles_open_cards(qapp, still):
    toasts = Toasts(theme.LIGHT, quiet=loud)
    card = toasts.show("Photos synced", "Downloaded: 1", icon="photo")
    try:
        toasts.apply_palette(theme.DARK)
        assert card.c is theme.DARK
    finally:
        toasts.close_all()
        flush(qapp)


# stacking ----------------------------------------------------------------------------------------
def test_cards_stack_upward_and_the_oldest_goes(qapp, still):
    toasts = Toasts(theme.DARK, quiet=loud)
    try:
        cards = [toasts.show(f"Note {i}", "Text", timeout=60_000) for i in range(MAX_CARDS + 1)]
        flush(qapp)
        assert toasts.visible() == cards[1:]  # at most three: the first one went
        assert cards[0] not in toasts.cards
        ys = [c.y() for c in toasts.visible()]
        assert ys == sorted(ys) and len(set(ys)) == len(ys)  # oldest highest, newest next to the taskbar
        avail = QApplication.primaryScreen().availableGeometry()
        newest = toasts.visible()[-1]
        bottom = newest.y() + newest.height() - newest.SHADOW
        assert bottom == avail.bottom() + 1 - toast.EDGE
        right = newest.x() + newest.width() - newest.SHADOW
        assert right == avail.right() + 1 - toast.EDGE
        # cards don't overlap
        for older, newer in zip(toasts.visible(), toasts.visible()[1:]):
            assert older.y() + older.height() - older.SHADOW + toast.GAP == newer.y() + newer.SHADOW
        # closing the middle one closes the gap
        middle = toasts.visible()[1]
        toasts.dismiss(middle)
        flush(qapp)
        assert middle not in toasts.visible() and len(toasts.visible()) == 2
        a, b = toasts.visible()
        assert a.y() + a.height() - a.SHADOW + toast.GAP == b.y() + b.SHADOW
    finally:
        toasts.close_all()
        flush(qapp)


def test_taskbar_at_the_top_stacks_downward(qapp, still, monkeypatch):
    monkeypatch.setattr(Toasts, "_edge", lambda self: "top")
    toasts = Toasts(theme.LIGHT, quiet=loud)
    try:
        first = toasts.show("One", "Text", timeout=60_000)
        second = toasts.show("Two", "Text", timeout=60_000)
        avail = QApplication.primaryScreen().availableGeometry()
        assert second.y() + second.SHADOW == avail.top() + toast.EDGE  # newest next to the taskbar
        assert first.y() > second.y()
    finally:
        toasts.close_all()
        flush(qapp)


# timing and hover --------------------------------------------------------------------------------
def test_card_goes_after_its_time(qapp, still):
    toasts = Toasts(theme.DARK, quiet=loud)
    try:
        card = toasts.show("Photos synced", "Downloaded: 2", timeout=80)
        assert card in toasts.visible()
        assert pump(lambda: card not in toasts.visible(), 2.0)
    finally:
        toasts.close_all()
        flush(qapp)


def test_default_time_is_about_six_seconds(qapp, still):
    toasts = Toasts(theme.DARK, quiet=loud)
    try:
        card = toasts.show("Photos synced", "Downloaded: 2")
        assert 5000 <= card.remaining() <= 6000
    finally:
        toasts.close_all()
        flush(qapp)


def test_card_stays_while_the_mouse_is_on_it(qapp, still, monkeypatch):
    monkeypatch.setattr(toast, "LINGER_MS", 100)
    toasts = Toasts(theme.DARK, quiet=loud)
    try:
        card = toasts.show("Photos synced", "Downloaded: 2", timeout=120)
        enter(card)
        assert card.hovered and card.remaining() == -1
        assert not pump(lambda: card not in toasts.visible(), 0.5)  # well past its time: still there
        leave(card)
        assert not card.hovered and 0 <= card.remaining() <= 120
        assert pump(lambda: card not in toasts.visible(), 2.0)
    finally:
        toasts.close_all()
        flush(qapp)


def test_click_and_buttons_act_and_close_the_card(qapp, still):
    toasts = Toasts(theme.DARK, quiet=loud)
    seen = []
    try:
        card = toasts.show("Photos synced", "x", actions=[("Open", lambda: seen.append("open")),
                                                          ("Show in folder", lambda: seen.append("folder"))],
                           on_click=lambda: seen.append("click"), timeout=60_000)
        card.buttons[1].click()
        assert seen == ["folder"] and card not in toasts.visible()
        card = toasts.show("Update available", "x", on_click=lambda: seen.append("click"), timeout=60_000)
        card.clicked.emit()
        assert seen == ["folder", "click"] and card not in toasts.visible()
        card = toasts.show("Other", "x", timeout=60_000)
        card.close_button.click()
        assert card not in toasts.visible()
    finally:
        toasts.close_all()
        flush(qapp)


def test_a_failing_action_never_takes_the_app_down(qapp, still):
    toasts = Toasts(theme.DARK, quiet=loud)
    try:
        card = toasts.show("x", "y", actions=[("Open", lambda: 1 / 0)], timeout=60_000)
        card.buttons[0].click()
        assert card not in toasts.visible()
    finally:
        toasts.close_all()
        flush(qapp)


# Windows' quiet times ----------------------------------------------------------------------------
@pytest.mark.parametrize("state", [toast.QUNS_BUSY, toast.QUNS_RUNNING_D3D_FULL_SCREEN,
                                   toast.QUNS_PRESENTATION_MODE, toast.QUNS_QUIET_TIME, toast.QUNS_NOT_PRESENT])
def test_no_card_while_windows_asks_for_quiet(qapp, still, monkeypatch, state):
    monkeypatch.setattr(toast, "notification_state", lambda: state)
    monkeypatch.setattr(toast, "do_not_disturb", lambda: False)
    monkeypatch.setattr(toast, "fullscreen_app", lambda: False)
    toasts = Toasts(theme.DARK)
    assert toasts.show("Photos synced", "x") is None
    assert toasts.cards == []


def test_no_card_during_do_not_disturb_or_a_full_screen_program(qapp, still, monkeypatch):
    monkeypatch.setattr(toast, "notification_state", lambda: toast.QUNS_ACCEPTS_NOTIFICATIONS)
    monkeypatch.setattr(toast, "do_not_disturb", lambda: True)
    monkeypatch.setattr(toast, "fullscreen_app", lambda: False)
    assert Toasts(theme.DARK).show("x", "y") is None
    monkeypatch.setattr(toast, "do_not_disturb", lambda: False)
    monkeypatch.setattr(toast, "fullscreen_app", lambda: True)
    assert Toasts(theme.DARK).show("x", "y") is None


def test_card_shows_when_windows_accepts_notifications(qapp, still, monkeypatch):
    monkeypatch.setattr(toast, "notification_state", lambda: toast.QUNS_ACCEPTS_NOTIFICATIONS)
    monkeypatch.setattr(toast, "do_not_disturb", lambda: False)
    monkeypatch.setattr(toast, "fullscreen_app", lambda: False)
    toasts = Toasts(theme.DARK)
    try:
        assert toasts.show("x", "y") is not None
    finally:
        toasts.close_all()
        flush(qapp)


def test_windows_queries_never_raise():
    # real calls on this PC: each answers or says "don't know", never fails
    assert toast.notification_state() is None or isinstance(toast.notification_state(), int)
    assert isinstance(toast.do_not_disturb(), bool)
    assert isinstance(toast.fullscreen_app(), bool)


# the setting -------------------------------------------------------------------------------------
def _load(tmp_path, monkeypatch, raw: dict) -> Settings:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text(json.dumps(raw), encoding="utf-8")
    return Settings.load()


def test_old_settings_keep_loading(tmp_path, monkeypatch):
    s = _load(tmp_path, monkeypatch, {"notifications": False})
    assert s.notifications is False and s.notification_style == "skyfold"  # off stays off
    s = _load(tmp_path, monkeypatch, {"notifications": True})
    assert s.notifications is True and s.notification_style == "skyfold"  # the new default
    s = _load(tmp_path, monkeypatch, {"notification_style": "windows"})
    assert s.notification_style == "windows"
    s = _load(tmp_path, monkeypatch, {"notification_style": "balloon"})  # hand-edited nonsense
    assert s.notification_style == "skyfold"


@pytest.mark.parametrize("settings, shown", [
    (Settings(), "skyfold"),
    (Settings(notification_style="windows"), "windows"),
    (Settings(notifications=False), "off"),
    (Settings(notifications=False, notification_style="windows"), "off"),
])
def test_settings_shows_the_choice(qapp, settings, shown):
    from skyfold.ui.dialogs import SettingsDialog

    dlg = SettingsDialog(settings, theme.LIGHT)
    try:
        assert dlg.notif.currentData() == shown
        assert [dlg.notif.itemData(i) for i in range(dlg.notif.count())] == ["skyfold", "windows", "off"]
    finally:
        dlg.close()


@pytest.mark.parametrize("choice, notifications, style", [
    ("skyfold", True, "skyfold"), ("windows", True, "windows"), ("off", False, "windows")])
def test_settings_saves_the_choice(qapp, tmp_path, choice, notifications, style):
    from skyfold.ui.dialogs import SettingsDialog

    photos = tmp_path / "Photos"
    photos.mkdir()
    dlg = SettingsDialog(Settings(photos_dir=str(photos), photos_dir_confirmed=True, notification_style="windows"),
                         theme.LIGHT)
    try:
        dlg.notif.setCurrentIndex(dlg.notif.findData(choice))
        dlg._save()
        s = dlg.result_settings()
        assert (s.notifications, s.notification_style) == (notifications, style)  # off keeps the style
    finally:
        dlg.close()


# the app: own cards or Windows' ------------------------------------------------------------------
@pytest.fixture
def controller(qapp, tmp_path, monkeypatch):
    from skyfold import app as app_mod

    made: dict = {}

    def load(cls):
        return made.setdefault("s", Settings(apple_id="t@example.com", photos_dir=str(tmp_path / "S"),
                                             photos_dir_confirmed=True))

    monkeypatch.setattr(app_mod.Settings, "load", classmethod(load))
    monkeypatch.setattr(app_mod.SyncWorker, "start", lambda self: None)
    ctl = app_mod.Controller(qapp)
    calls: dict[str, list] = {"windows": [], "own": []}
    monkeypatch.setattr(ctl.tray, "showMessage", lambda *a: calls["windows"].append(a))
    monkeypatch.setattr(ctl.toasts, "show", lambda *a, **k: calls["own"].append((a, k)))
    try:
        yield ctl, calls
    finally:
        ctl.toasts.close_all()
        ctl.thread.quit()
        ctl.thread.wait(2000)
        ctl.tray.hide()
        ctl.flyout.close()
        for obj in (ctl.flyout, ctl.tray, ctl.worker):
            obj.deleteLater()
        qapp.processEvents()


def test_own_cards_by_default(controller):
    from skyfold.i18n import t

    ctl, calls = controller
    ctl._notify(t("Photos synced"), "Downloaded: 2")
    assert not calls["windows"]
    (args, kw), = calls["own"]
    assert args == (t("Photos synced"), "Downloaded: 2")
    assert kw["icon"] == "photo" and [a[0] for a in kw["actions"]] == [t("Open"), t("Show in folder")]
    assert kw["on_click"] == ctl._on_message_clicked


def test_windows_style_still_uses_windows_balloons(controller):
    from skyfold.i18n import t

    ctl, calls = controller
    ctl.settings.notification_style = "windows"
    ctl._notify(t("iCloud Drive synced"), "Uploaded: 1")
    assert not calls["own"]
    (title, text, _icon, ms), = calls["windows"]
    assert (title, text, ms) == (t("iCloud Drive synced"), "Uploaded: 1", 6000)
    assert ctl._last_msg is None


def test_off_shows_only_what_needs_the_user(controller):
    from skyfold.i18n import t

    ctl, calls = controller
    ctl.settings.notifications = False
    ctl._notify(t("Photos synced"), "Downloaded: 2")
    assert not calls["own"] and not calls["windows"]
    ctl._notify(t("Confirmation needed"), "3 items")
    (args, kw), = calls["own"]
    assert kw["tone"] == "warn" and kw["actions"][0][1] == ctl.open_pending


def test_update_notice_in_both_styles(controller, monkeypatch):
    from types import SimpleNamespace

    from skyfold import updater
    from skyfold.ui import dialogs

    ctl, calls = controller
    rel = SimpleNamespace(version="99.0.0")
    monkeypatch.setattr(updater, "is_newer", lambda v: True)
    monkeypatch.setattr(ctl.settings, "save", lambda: None)
    monkeypatch.setattr(dialogs, "run_async", lambda fn, done, fail: done(rel))
    ctl.check_updates(False)
    (args, kw), = calls["own"]
    assert kw["on_click"] == ctl.open_update and kw["actions"][0][1] == ctl.open_update
    ctl.settings.update_told = ""
    ctl.settings.notification_style = "windows"
    ctl.check_updates(False)
    assert len(calls["windows"]) == 1 and ctl._last_msg == "update"


def test_cards_glide_in_and_out_when_windows_animates(qapp, monkeypatch):
    monkeypatch.setattr(toast, "animations_on", lambda: True)
    toasts = Toasts(theme.DARK, quiet=loud)
    try:
        first = toasts.show("One", "Text", timeout=60_000)
        assert first.windowOpacity() < 1.0  # starts faded, beside its place
        target = toasts._positions()[first]
        assert pump(lambda: first.pos() == target and first.windowOpacity() > 0.99)
        second = toasts.show("Two", "Text", timeout=60_000)
        assert pump(lambda: first.pos() == toasts._positions()[first] and first.y() < second.y())
        toasts.dismiss(first)
        assert first.leaving and first in toasts.cards  # fading out
        assert pump(lambda: first not in toasts.cards)
        assert pump(lambda: second.pos() == toasts._positions()[second])
    finally:
        toasts.close_all()
        flush(qapp)
