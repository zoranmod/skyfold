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



@pytest.mark.parametrize("animated", [False, True])
def test_a_card_that_grows_with_its_photos_stays_above_the_taskbar(qapp, monkeypatch, animated):
    """The photos are read after the card is placed; the taller card moves up instead of reaching
    down over the taskbar (where its time line can't be seen), and the older card makes room."""
    from PySide6.QtGui import QColor, QImage

    monkeypatch.setattr(toast, "animations_on", lambda: animated)
    toasts = Toasts(theme.DARK, quiet=loud)
    try:
        older = toasts.show("Drive synced", "Downloaded: 2", timeout=60_000)
        card = toasts.show("Photos synced", "Downloaded: 2", timeout=60_000, images=["a.jpg", "b.jpg"])
        short = card.height()
        img = QImage(60, 60, QImage.Format_ARGB32)
        img.fill(QColor("#3366cc"))
        card._shot_ready("a.jpg", img)  # (as Windows' preview arrives)
        assert card.height() > short
        avail = QApplication.primaryScreen().availableGeometry()
        bottom = avail.bottom() + 1 - toast.EDGE
        assert pump(lambda: card.y() + card.height() - card.SHADOW == bottom and card.windowOpacity() > 0.99)
        assert pump(lambda: older.y() + older.height() - older.SHADOW + toast.GAP == card.y() + card.SHADOW)
    finally:
        toasts.close_all()
        flush(qapp)


SHORT = "Downloaded: 2, Deleted on PC: 1"  # one line on the card (Qt's own guess breaks it in two)
LONG = "Your iCloud session has expired. Click to sign in again. " * 3  # several lines
BUTTONS = [("Open", lambda: None), ("Show in folder", lambda: None)]


def _slack(card: ToastCard) -> int:
    """How many pixels taller the card is than its rows need (negative: something is cut off). Worked
    out from where the parts ended up, not from any size hint."""
    low = max(w.geometry().bottom() + 1 for w in [card._tile, card._title, card._text, *card.buttons, *card._shots]
              if not w.isHidden())
    spare = card.height() - (low + card.SHADOW + card.PAD)  # under the last row: only the card's own padding
    need = [lbl.heightForWidth(lbl.width()) for lbl in (card._title, card._text)]
    have = [card._title.height(), card._text.height()]
    assert all(h >= n for h, n in zip(have, need)), "a label is cut off"
    # (any extra height the labels were given is slack too, except what the 32 px tile beside them asks for)
    return spare + max(0, sum(have) - max(sum(need), card.TILE - 2))


@pytest.mark.parametrize("pal", [theme.LIGHT, theme.DARK, theme.OLED], ids=["light", "dark", "oled"])
@pytest.mark.parametrize("glass", [False, True], ids=["plain", "glass"])
@pytest.mark.parametrize("title", ["Photos synced", "A title that is long enough to need more than one line on a card"])
@pytest.mark.parametrize("text", ["x", SHORT, LONG], ids=["one-word", "short", "long"])
@pytest.mark.parametrize("count", [0, 1, 2])
def test_card_is_exactly_as_tall_as_its_content(qapp, monkeypatch, pal, glass, title, text, count):
    """A card with a short text was up to 15 px taller than its rows (17 to 34 px with Windows'
    fonts), and one with a long text too short for it: its height came from the width Qt guessed for
    the wrapped labels, not from the card's own width."""
    from skyfold.ui import backdrop, flyout

    monkeypatch.setattr(backdrop, "glass_supported", lambda: glass)
    monkeypatch.setattr(backdrop, "blur_behind", lambda *a, **k: None)  # (Windows is not asked for the blur)
    monkeypatch.setattr(flyout, "round_corners", lambda *a, **k: None)
    card = ToastCard(title, text, pal, "photo", None, BUTTONS[:count])
    try:
        assert card.glass == glass
        card.show()
        qapp.processEvents()
        assert card.width() == card.WIDTH + 2 * card.SHADOW
        assert _slack(card) == 0
        assert card.height() == card.layout().totalHeightForWidth(card.width())
    finally:
        card.close()


@pytest.mark.parametrize("words_in_title", [1, 3, 10])
def test_card_height_follows_its_text_word_by_word(qapp, words_in_title):
    """Whatever the length: which texts Qt's guess breaks too early depends on the font, so every
    length of one sentence is tried (the old cards were too tall for some short ones, too short for
    the long ones)."""
    words = ("Downloaded: 2, Deleted on PC: 1, Deleted in iCloud: 3 and some more words that go on for a while "
             "until the text is long enough to need several lines on the card").split()
    wrong = []
    for n in range(1, len(words) + 1):
        card = ToastCard(" ".join(words[:words_in_title]), " ".join(words[:n]), theme.DARK, "photo", None, BUTTONS)
        try:
            card.show()
            qapp.processEvents()
            off = card.height() - card.layout().totalHeightForWidth(card.width())
            if off:
                wrong.append((n, off))
        finally:
            card.close()
    assert wrong == []  # (words in the text, pixels too tall)


@pytest.mark.parametrize("text", [SHORT, LONG], ids=["short", "long"])
@pytest.mark.parametrize("count", [0, 2])
def test_card_hugs_its_content_before_and_after_its_photos_arrive(qapp, text, count):
    from PySide6.QtGui import QColor, QImage

    card = ToastCard("Photos synced", text, theme.DARK, "photo", None, BUTTONS[:count], images=["a.jpg", "b.jpg"])
    try:
        card.show()
        qapp.processEvents()
        assert _slack(card) == 0  # no room kept for pictures that are not there yet
        before, grown = card.height(), []
        card.grown.connect(lambda: grown.append(card.height()))
        img = QImage(60, 60, QImage.Format_ARGB32)
        img.fill(QColor("#3366cc"))
        card._shot_ready("a.jpg", img)
        qapp.processEvents()
        first = card._shots[0]
        assert first.height() == 44 and first.y() == card._text.y() + card._text.height() + 2 + 8  # 8 px above it
        assert card.height() > before and grown == [card.height()]
        assert _slack(card) == 0
        taller = card.height()
        card._shot_ready("b.jpg", img)  # a second picture sits beside the first: nothing grows
        qapp.processEvents()
        assert card.height() == taller and len(grown) == 1 and _slack(card) == 0
    finally:
        card.close()


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


# the notifications list (the panel's bell) -------------------------------------------------------
def test_notice_log_keeps_the_newest_thirty_and_counts_new_ones():
    from skyfold.ui.notices import LIMIT, Notice, NoticeLog

    log = NoticeLog()
    seen = []
    log.on_change(lambda: seen.append(log.unseen))
    for i in range(LIMIT + 5):
        log.add(Notice(f"Note {i}", "x"))
    assert len(log.items) == LIMIT and log.items[0].title == f"Note {LIMIT + 4}"  # newest first
    assert log.unseen == LIMIT and seen
    log.mark_seen()
    assert log.unseen == 0
    log.add(Notice("One more", "x"))
    assert log.unseen == 1
    log.clear()
    assert log.items == [] and log.unseen == 0


def _panel(qapp, actions=None):
    from skyfold.ui.flyout import Flyout
    from skyfold.ui.notices import Notice, NoticeLog

    ran = []
    table = actions or {"folder_photos": ("Show in folder", lambda: ran.append("folder"), True),
                        "review": ("Review", lambda: ran.append("review"), False)}
    log = NoticeLog()
    f = Flyout(theme.DARK)
    f.setAttribute(Qt.WA_DontShowOnScreen)
    f.attach_notices(log, table.get)
    log.add(Notice("Confirmation needed", "7 items", "trash", "warn", ("review",)))
    log.add(Notice("Photos synced", "Downloaded: 2", "photo", None, ("folder_photos", "gone")))
    return f, log, ran


def test_bell_badge_counts_until_the_list_is_opened(qapp):
    f, log, _ran = _panel(qapp)
    try:
        assert f.bell.isVisibleTo(f) and f.bell.badge_text() == "2"
        assert "2" in f.bell.accessibleName()
        f.show_at(None)
        qapp.processEvents()
        size = f.size()
        f.show_notices()
        qapp.processEvents()
        assert f.notices_page.isVisible() and not f._scroll.isVisible() and not f._bottom.isVisible()
        assert f.size() == size  # the panel keeps its size
        assert log.unseen == 0 and f.bell.badge_text() == ""
        for i in range(12):
            log.add(toast_notice(i))
        assert f.bell.badge_text() == "9+"
    finally:
        f.close()


def toast_notice(i):
    from skyfold.ui.notices import Notice

    return Notice(f"N{i}", "x")


def test_list_shows_newest_first_with_the_buttons_that_still_apply(qapp):
    f, log, ran = _panel(qapp)
    try:
        f.show_at(None)
        f.show_notices()
        qapp.processEvents()
        rows = f.notices_page.rows()
        assert [r.notice.title for r in rows] == ["Photos synced", "Confirmation needed"]
        assert [b.text() for b in rows[0].buttons] == ["Show in folder"]  # an unknown key is left out
        assert rows[1].buttons == []  # the deletions were reviewed meanwhile: no Review button
        assert rows[0].tile.color.name() == theme.SERVICE_COLORS["dark"]["photo"]
        rows[0].buttons[0].click()
        assert ran == ["folder"]
        assert not f.notices_page.isVisible() and f._scroll.isVisible()  # back to the panel
    finally:
        f.close()


def test_clear_all_back_and_escape(qapp):
    from PySide6.QtGui import QKeyEvent

    f, log, _ran = _panel(qapp)
    try:
        f.show_at(None)
        f.show_notices()
        qapp.processEvents()
        f.notices_page.clear_button.click()
        qapp.processEvents()
        assert log.items == [] and f.notices_page.rows() == []
        assert f.notices_page.empty.isVisible() and not f.notices_page.clear_button.isEnabled()
        f.notices_page.back_button.click()
        assert not f.notices_page.isVisible() and f._bottom.isVisible()
        f.show_notices()
        QApplication.sendEvent(f, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        assert not f.notices_page.isVisible() and f.isVisible()  # Escape goes back first
        f.show_notices()
        f.hide()
        assert not f.notices_page.isVisible() and f._scroll.isVisibleTo(f)  # opens on the panel again
    finally:
        f.close()


def test_list_text_is_never_read_as_markup(qapp):
    from skyfold.ui.notices import Notice, NoticeRow

    row = NoticeRow(Notice("<b>x</b>", "<img src=x> a.jpg"), theme.LIGHT, lambda key: None)
    assert row.title.textFormat() == Qt.PlainText and row.text.textFormat() == Qt.PlainText
    assert "a.jpg" in row.accessibleName()


def test_every_shown_notification_lands_in_the_list(controller):
    from skyfold.i18n import t

    ctl, calls = controller
    ctl._notify(t("Photos synced"), "Downloaded: 2")  # Skyfold's own card
    ctl.settings.notification_style = "windows"
    ctl._notify(t("iCloud Drive synced"), "Uploaded: 1")  # Windows' balloon
    titles = [n.title for n in ctl.notices.items]
    assert titles == [t("iCloud Drive synced"), t("Photos synced")]
    assert ctl.notices.items[1].actions == ("open_photos", "folder_photos")
    assert ctl.flyout.bell.badge_text() == "2"


def test_off_collects_only_what_is_still_shown(controller):
    from skyfold.i18n import t

    ctl, _calls = controller
    ctl.settings.notifications = False
    ctl._notify(t("Photos synced"), "Downloaded: 2")
    assert ctl.notices.items == []
    ctl._notify(t("Sign-in required"), "Expired")  # shown even when Off, so kept too
    assert [n.title for n in ctl.notices.items] == [t("Sign-in required")]


def test_a_card_held_back_by_do_not_disturb_is_still_in_the_list(controller, monkeypatch):
    from skyfold.i18n import t

    ctl, _calls = controller
    monkeypatch.setattr(ctl.toasts, "show", Toasts.show.__get__(ctl.toasts))  # the real cards again
    ctl.toasts._quiet = lambda: "Do Not Disturb"
    ctl._notify(t("Photos synced"), "Downloaded: 2")
    assert ctl.toasts.cards == []  # no card…
    assert [n.title for n in ctl.notices.items] == [t("Photos synced")]  # …but nothing is missed


def test_list_buttons_apply_only_while_they_make_sense(controller):
    from types import SimpleNamespace

    ctl, _calls = controller
    applies = lambda key: ctl._notice_action(key)[2]  # noqa: E731
    assert applies("open_photos") and applies("folder_photos")
    assert not applies("review") and not applies("sign_in") and not applies("update")
    assert not applies("folder_drive")
    assert ctl._notice_action("nonsense") is None
    ctl._pending = object()
    ctl.state = "auth"
    ctl._release = SimpleNamespace(version="99.0.0")
    ctl.settings.drive_enabled = True
    assert applies("review") and applies("sign_in") and applies("update") and applies("folder_drive")


def test_card_buttons_share_the_whole_width_equally(qapp):
    """As in Windows 11's own notifications: the buttons are one row across the card, all the same
    width, lined up with the tile's left edge and the text's right edge."""
    card = ToastCard("Photos synced", "Downloaded: 2", theme.DARK, "photo", None,
                     [("Open", lambda: None), ("Show in folder", lambda: None)])
    try:
        card.show()
        qapp.processEvents()
        a, b = card.buttons
        assert a.width() == b.width() and a.y() == b.y() and a.height() == 32
        assert a.x() == card._tile.x()  # the row starts where the tile does
        assert b.x() + b.width() == card._text.x() + card._text.width()  # and ends with the text
        single = ToastCard("Confirmation needed", "x", theme.DARK, "trash", "warn", [("Review", lambda: None)])
        single.show()
        qapp.processEvents()
        (r,) = single.buttons
        assert r.x() == single._tile.x() and r.x() + r.width() == single._text.x() + single._text.width()
        single.close()
    finally:
        card.close()


def test_photo_card_shows_this_syncs_photos_and_their_folder(controller, tmp_path, monkeypatch):
    """A card about downloaded photos shows those photos, and Show in folder selects the newest one in
    its own folder; a card about deletions shows no photo and has no Show in folder (the file is gone)."""
    from skyfold.i18n import t
    from skyfold.ui import flyout

    ctl, calls = controller
    folder = tmp_path / "S" / "2026" / "11"
    folder.mkdir(parents=True)
    photo = folder / "IMG_0001.JPG"
    photo.write_bytes(b"made-up")
    ctl.flyout.add_activity("2026-11-28T09:00:00+00:00", "trash", "IMG_0002.JPG", str(folder / "IMG_0002.JPG"))
    ctl.flyout.add_activity("2026-11-28T09:01:00+00:00", "download", "Downloaded 1 new file", str(photo))
    revealed, opened = [], []
    monkeypatch.setattr(flyout, "reveal", lambda target: revealed.append(target))
    monkeypatch.setattr(ctl, "open_folder", lambda which="photos": opened.append(which))

    ctl._notify(t("Photos synced"), t("Downloaded: {n}", n=1))
    (_args, kw), = calls["own"]
    assert kw["images"] == [str(photo)]
    dict(kw["actions"])[t("Show in folder")]()
    assert revealed == [str(photo)] and not opened

    calls["own"].clear()
    ctl._notify(t("Photos synced"), t("Deleted on PC: {n}", n=1))
    (_args, kw), = calls["own"]
    assert kw["images"] == []
    assert [label for label, _fn in kw["actions"]] == [t("Open")]
    assert not revealed[1:] and not opened


def _synced(parts: dict) -> str:
    """The text of a "… synced" notification, put together as the worker does."""
    from skyfold.i18n import t

    return ", ".join(t(part, n=n) for part, n in parts.items())


@pytest.mark.parametrize("lang", ["en", "bs"])
@pytest.mark.parametrize("service, parts, folder", [
    ("photos", {"Downloaded: {n}": 2}, True),
    ("photos", {"Deleted on PC: {n}": 1}, False),
    ("photos", {"Deleted in iCloud: {n}": 3}, False),
    ("photos", {"Deleted on PC: {n}": 1, "Deleted in iCloud: {n}": 3}, False),
    ("photos", {"Downloaded: {n}": 2, "Deleted on PC: {n}": 1}, True),
    ("drive", {"Downloaded: {n}": 2}, True),
    ("drive", {"Uploaded: {n}": 1}, True),  # (the file is still in the folder)
    ("drive", {"Deleted on PC: {n}": 1}, False),
    ("drive", {"Deleted on PC: {n}": 1, "Deleted in iCloud: {n}": 3}, False),
    ("drive", {"Uploaded: {n}": 1, "Deleted in iCloud: {n}": 3}, True),
])
def test_show_in_folder_only_when_a_file_came(controller, qapp, lang, service, parts, folder):
    """A sync that only deleted leaves nothing to show in a folder: neither the card nor the bell's
    list offers it. Open stays for Photos."""
    from skyfold import i18n
    from skyfold.i18n import t
    from skyfold.ui.notices import NoticeRow

    ctl, calls = controller
    ctl.settings.drive_enabled = True
    i18n.set_language(lang)
    try:
        title = t("Photos synced") if service == "photos" else t("iCloud Drive synced")
        ctl._notify(title, _synced(parts))
        (_args, kw), = calls["own"]
        want = ([t("Open")] if service == "photos" else []) + ([t("Show in folder")] if folder else [])
        assert [label for label, _fn in kw["actions"]] == want
        row = NoticeRow(ctl.notices.items[0], theme.DARK, ctl._notice_action)  # the same notice under the bell
        assert [b.text() for b in row.buttons] == want
        row.deleteLater()
    finally:
        i18n.set_language("en")


def test_first_sync_notices_keep_show_in_folder(controller):
    """ "Your photos are on this PC" and "iCloud Drive is ready" are about files that came."""
    from skyfold.i18n import t

    ctl, calls = controller
    ctl._notify(t("Your photos are on this PC"), "The first download is done: 12 files.")
    ctl._notify(t("iCloud Drive is ready"), "The first sync is done.")
    photos, drive = (kw for _args, kw in calls["own"])
    assert [label for label, _fn in photos["actions"]] == [t("Open"), t("Show in folder")]
    assert [label for label, _fn in drive["actions"]] == [t("Show in folder")]


def test_tray_menu_says_why_a_pass_ended(controller):
    from skyfold.i18n import t

    ctl, _calls = controller
    ctl._on_state("idle", t("Sync stopped."))
    assert ctl.m_status.text() == t("Sync stopped.").rstrip(".")
    ctl._on_state("idle", t("Everything is in sync."))
    assert ctl.m_status.text() == t("Everything is in sync")
