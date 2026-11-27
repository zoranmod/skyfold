"""Skyfold's own notification cards ("toasts"), shown near the tray instead of Windows' balloons.

Two parts:

* ``ToastCard`` - one card. Everything about its look (surface, border, shadow, the service colour,
  buttons, close cross) is drawn in this one class, so a later restyle touches only it.
* ``Toasts`` - places the cards in the taskbar's corner, stacks them (newest next to the taskbar, at
  most three), lets each go after a few seconds (not while the pointer is on it) and stays quiet
  while Windows says so (Do Not Disturb / Focus Assist, a full-screen game, video or presentation).

A card never takes the focus from the program the user is working in.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Callable, Sequence

from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation,
                            Signal)
from PySide6.QtGui import QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen, \
    QRadialGradient
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..config import APP_NAME
from ..i18n import t
from .glow import animations_on
from .icons import draw_logo, glyph
from .theme import service_color, theme_kind

log = logging.getLogger(__name__)

TIMEOUT_MS = 6000  # how long a card stays
LINGER_MS = 3000  # after the pointer leaves a card it stays this much longer
MAX_CARDS = 3  # a fourth pushes the oldest out
GAP = 10  # between stacked cards
EDGE = 12  # between a card and the screen edge / taskbar (the panel uses the same)

Action = tuple[str, Callable[[], None]]  # (button text, what it does)


# Windows: is now a good moment? -------------------------------------------------------------------
# SHQueryUserNotificationState (shellapi.h)
QUNS_NOT_PRESENT = 1  # screen saver, locked, or another user's session
QUNS_BUSY = 2  # a full-screen program, or presentation settings
QUNS_RUNNING_D3D_FULL_SCREEN = 3  # a full-screen game
QUNS_PRESENTATION_MODE = 4
QUNS_ACCEPTS_NOTIFICATIONS = 5
QUNS_QUIET_TIME = 6
QUNS_APP = 7
QUIET_STATES = {QUNS_NOT_PRESENT, QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE, QUNS_QUIET_TIME}

# Windows keeps the Do Not Disturb / Focus Assist profile in this notification-facility state
# (WNF_SHEL_QUIETHOURS_ACTIVE_PROFILE_CHANGED): 0 = off, 1 = priority only, 2 = alarms only.
_WNF_QUIET_HOURS = 0x0D83063EA3BF1C75


def notification_state() -> int | None:
    """What Windows says about showing notifications now (QUNS_*), None when it can't be asked."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0:  # S_OK
            return state.value
    except Exception:  # noqa: BLE001 - not knowing means "show as usual"
        pass
    return None


def do_not_disturb() -> bool:
    """True while Windows' Do Not Disturb (Windows 11) or Focus Assist (Windows 10) is on."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ntdll = ctypes.windll.ntdll
        name = ctypes.c_ulonglong(_WNF_QUIET_HOURS)
        stamp = ctypes.c_ulong(0)
        value = ctypes.c_ulong(0)
        size = ctypes.c_ulong(ctypes.sizeof(value))
        ntdll.NtQueryWnfStateData.restype = ctypes.c_long
        status = ntdll.NtQueryWnfStateData(ctypes.byref(name), None, None, ctypes.byref(stamp),
                                           ctypes.byref(value), ctypes.byref(size))
        return status == 0 and size.value >= 1 and value.value != 0
    except Exception:  # noqa: BLE001 - an older or newer Windows without it: not quiet
        return False


_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def fullscreen_app() -> bool:
    """True while the program in front covers its whole screen (a film, a game, a slide show), also
    the borderless kind Windows doesn't always report as busy. Maximised windows don't count."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = user32.GetForegroundWindow()
        if not hwnd or user32.IsZoomed(hwnd) or not user32.IsWindowVisible(hwnd):
            return False
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():  # one of Skyfold's own windows
            return False
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value in _SHELL_CLASSES:  # the desktop or the taskbar
            return False

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        mon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not mon or not user32.GetMonitorInfoW(mon, ctypes.byref(info)):
            return False
        m = info.rcMonitor
        return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom
    except Exception:  # noqa: BLE001
        return False


def quiet_reason() -> str | None:
    """Why no card should appear now (for the log), or None when one may."""
    state = notification_state()
    if state in QUIET_STATES:
        return f"Windows notification state {state}"
    if do_not_disturb():
        return "Do Not Disturb"
    if fullscreen_app():
        return "a full-screen program"
    return None


# The card -----------------------------------------------------------------------------------------
class _CardButton(QAbstractButton):
    """A button on a card. It only holds the text and the hover state: the card draws it."""

    def __init__(self, card: "ToastCard", text: str, kind: str) -> None:
        super().__init__(card)
        self._card = card
        self.kind = kind  # "primary" | "secondary" | "close"
        self.setText(text)
        self.setAccessibleName(text)
        self.setFocusPolicy(Qt.NoFocus)  # a card never takes the keyboard from the user's program
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        if kind == "close":
            self.setFixedSize(24, 24)
            self.setToolTip(text)
        else:
            self.setFixedHeight(28)
            w = self.fontMetrics().horizontalAdvance(text) + 28
            self.setMinimumWidth(max(72, w))

    def enterEvent(self, e) -> None:  # noqa: N802 - Qt naming
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802 - Qt naming
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt naming
        p = QPainter(self)
        self._card.paint_button(p, self)
        p.end()


class ToastCard(QWidget):
    """One notification card: Skyfold's mark and name, a tile in the service's colour, the title, the
    text, up to a few buttons and a close cross. All drawing lives here (``paintEvent``,
    ``paint_button`` and the colours in ``_colors``)."""

    WIDTH = 364  # the card itself; the window is larger by the shadow
    RADIUS = 12.0
    SHADOW = 18  # room around the card for its shadow
    SHADOW_DROP = 4  # the shadow falls a little below the card
    PAD = 14
    TILE = 32

    clicked = Signal()  # the card itself (not a button) was clicked
    closeRequested = Signal()  # the cross
    expired = Signal()  # its time is up

    def __init__(self, title: str, text: str, palette: dict[str, str], icon: str = "cloud",
                 tone: str | None = None, actions: Sequence[Action] = (), timeout: int = TIMEOUT_MS,
                 images: Sequence[str] = ()) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        from .backdrop import glass_supported

        # Windows 11 with transparency effects: the card is the window itself, Windows blurs what is
        # behind it and gives it its round corners and shadow; otherwise it paints its own shadow.
        self.glass = glass_supported()
        if self.glass:
            self.SHADOW = 0
            self.SHADOW_DROP = 0
            self.RADIUS = 8.0
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowTitle(f"{APP_NAME}: {title}")
        self.setAccessibleName(title)
        self.setAccessibleDescription(text)
        self.title, self.text, self.icon, self.tone = title, text, icon, tone
        self.timeout = timeout
        self.hovered = False
        self.leaving = False  # on its way out (fading)
        self._hover_close = False

        m = self.SHADOW
        outer = QHBoxLayout(self)
        outer.setContentsMargins(m + self.PAD, m + 14, m + 8, m + self.PAD + 2)
        outer.setSpacing(12)
        self._tile = QWidget()  # the service tile is painted in its place
        self._tile.setFixedSize(self.TILE, self.TILE)
        outer.addWidget(self._tile, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(2)
        outer.addLayout(words, 1)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 2)
        head.setSpacing(6)
        self._logo = QWidget()  # the mark is painted in its place
        self._logo.setFixedSize(14, 14)
        self._app = QLabel(APP_NAME + " · " + self._context(icon, tone) + " · " + t("now"))
        self.close_button = _CardButton(self, t("Close"), "close")
        self.close_button.clicked.connect(self.closeRequested)
        head.addWidget(self._logo, 0, Qt.AlignVCenter)
        head.addWidget(self._app, 1, Qt.AlignVCenter)
        head.addWidget(self.close_button, 0, Qt.AlignVCenter)
        words.addLayout(head)
        self._title = QLabel(title)
        self._text = QLabel(_short(text))
        for lbl in (self._app, self._title, self._text):
            lbl.setTextFormat(Qt.PlainText)  # file names can contain anything: never read as markup
            lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._title.setWordWrap(True)
        self._text.setWordWrap(True)
        words.addWidget(self._title)
        words.addWidget(self._text)

        # the new photos themselves, small, under the text (the design's photo strip)
        self._shots: list[QLabel] = []
        if images:
            from .panel_parts import Thumbs

            strip = QHBoxLayout()
            strip.setContentsMargins(0, 8, 0, 0)
            strip.setSpacing(4)
            for _ in images[:3]:
                shot = QLabel()
                shot.setFixedSize(44, 44)
                shot.setAttribute(Qt.WA_TransparentForMouseEvents)
                shot.hide()  # shown once its picture is read
                strip.addWidget(shot)
                self._shots.append(shot)
            strip.addStretch(1)
            words.addLayout(strip)
            self._thumbs = Thumbs(self)
            self._wanted = list(images[:3])
            self._thumbs.ready.connect(self._shot_ready)
            for path in self._wanted:
                self._thumbs.request(path)

        self.buttons: list[_CardButton] = []
        if actions:
            row = QHBoxLayout()
            row.setContentsMargins(0, 8, 4, 0)
            row.setSpacing(8)
            for i, (label, fn) in enumerate(actions):
                b = _CardButton(self, label, "primary" if i == 0 else "secondary")
                b.clicked.connect(fn)
                self.buttons.append(b)
                row.addWidget(b)
            row.addStretch(1)
            words.addLayout(row)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.expired)
        self._anim: QVariantAnimation | None = None
        self._move: QVariantAnimation | None = None
        self.set_palette(palette)

    @staticmethod
    def _context(icon: str, tone: str | None) -> str:
        """What the card is about, after Skyfold's name: "Skyfold · Photos · now"."""
        if tone == "warn":
            return t("Needs your OK")
        if tone == "err":
            return t("Needs attention")
        return {"photo": t("Photos"), "folder": t("iCloud Drive"), "note": t("Notes"), "bookmark": t("Bookmarks"),
                "download": t("New version")}.get(icon, t("Sync"))

    def _shot_ready(self, path: str, img: QImage) -> None:
        if path not in self._wanted or img.isNull():
            return
        from .panel_parts import rounded

        shot = self._shots[self._wanted.index(path)]
        shot.setPixmap(rounded(img, 44, 6, max(1.0, self.devicePixelRatioF())))
        shot.show()
        self.adjustSize()

    # look -----------------------------------------------------------------------------------------
    def _colors(self) -> dict[str, QColor]:
        c = self.c
        kind = theme_kind(c)
        if self.tone in ("warn", "err"):
            accent = c[self.tone]
        else:
            accent = service_color(c, self.icon) or c["accent_fg"]
        light = kind == "light"
        shadow = QColor(0, 0, 0)
        border = QColor(0, 0, 0, 26) if light else QColor(255, 255, 255, 30)
        return {
            "kind": kind,  # type: ignore[dict-item]
            "surface": QColor(c["surface"] if kind != "oled" else "#151517"),
            "border": border if self.glass else QColor(c["border"]),
            "highlight": QColor(255, 255, 255, 0 if light else 16),  # a hairline of light along the top
            "shadow": shadow,
            "shadow_alpha": 0.16 if light else 0.42,  # type: ignore[dict-item]
            "accent": QColor(accent),
            "glow_alpha": 0.07 if light else 0.13,  # type: ignore[dict-item]
            "text": QColor(c["text"]),
            "muted": QColor(c.get("muted2", c["muted"])),
            "hover": QColor(255, 255, 255, 26) if self.glass and not light else
            QColor(0, 0, 0, 14) if self.glass else QColor(c["hover"]),
            "button": QColor(255, 255, 255, 20) if not light else QColor(255, 255, 255, 200),
            "btn": QColor(c["btn"]),
            "btn_text": QColor(c["accent_text"]),
        }

    def set_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        col = self._colors()
        self._glyph = glyph(self.icon, col["accent"].name(), 17)
        base = QFont(self.font())
        for lbl, pt, weight, color in ((self._app, 9, QFont.Normal, col["muted"]),
                                       (self._title, 10.5, QFont.DemiBold, col["text"]),
                                       (self._text, 9.75, QFont.Normal, col["muted"])):
            f = QFont(base)
            f.setPointSizeF(pt)
            f.setWeight(weight)
            lbl.setFont(f)
            lbl.setStyleSheet(f"color: {color.name()}; background: transparent; font-size: {pt}pt;"
                              f" font-weight: {600 if weight == QFont.DemiBold else 400};")
        f = QFont(base)
        f.setPointSizeF(9)
        f.setWeight(QFont.DemiBold)
        for b in self.buttons:
            b.setFont(f)
        self.setFixedWidth(self.WIDTH + 2 * self.SHADOW)
        self.adjustSize()
        self.update()

    def card_rect(self) -> QRectF:
        """Where the card itself is inside the window (the rest is room for the shadow)."""
        m = self.SHADOW
        return QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt naming
        col = self._colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.card_rect()
        if self.glass:  # (the mouse would fall through fully transparent pixels to the window behind)
            p.fillRect(self.rect(), QColor(0, 0, 0, 1))

        # soft shadow: rounded rings that fade out, a little lower than the card (Windows draws it
        # itself around a card over its blur)
        p.setPen(Qt.NoPen)
        steps = self.SHADOW - 2
        for i in range(steps, 0, -1):
            a = col["shadow_alpha"] * (1 - i / steps) ** 2 / steps * 2.2
            sc = QColor(col["shadow"])
            sc.setAlphaF(min(1.0, a))
            p.setBrush(sc)
            g = r.adjusted(-i, -i + self.SHADOW_DROP, i, i + self.SHADOW_DROP * 0.5)
            p.drawRoundedRect(g, self.RADIUS + i, self.RADIUS + i)

        card = QPainterPath()
        card.addRoundedRect(r, self.RADIUS, self.RADIUS)
        if not self.glass:  # (over Windows' blur the card is see-through)
            p.fillPath(card, col["surface"])
        # the service colour as a soft light from the top-left corner
        glow = QRadialGradient(QPointF(r.left() + 30, r.top() + 34), r.width() * 0.75)
        a = QColor(col["accent"])
        a.setAlphaF(col["glow_alpha"])
        glow.setColorAt(0, a)
        a2 = QColor(col["accent"])
        a2.setAlphaF(0)
        glow.setColorAt(1, a2)
        p.fillPath(card, glow)
        # border, and a faint highlight along the top edge on dark themes
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(col["border"], 1))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS - 0.5, self.RADIUS - 0.5)
        if col["highlight"].alpha():
            hl = QLinearGradient(r.topLeft(), r.topLeft() + QPointF(0, 18))
            hl.setColorAt(0, col["highlight"])
            hl.setColorAt(1, QColor(255, 255, 255, 0))
            p.setPen(QPen(hl, 1))
            p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), self.RADIUS - 1.5, self.RADIUS - 1.5)

        # Skyfold's mark
        lg = self._logo.geometry()
        p.save()
        p.translate(lg.topLeft())
        draw_logo(p, lg.width())
        p.restore()

        # the service tile: a flat tint of the colour, the glyph in the colour (as in the panel)
        tr = QRectF(self._tile.geometry())
        fill = QColor(col["accent"])
        fill.setAlphaF(0.14 if col["kind"] == "light" else 0.18)
        p.setBrush(fill)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(tr, 9, 9)
        s = 17
        self._glyph.paint(p, QRect(int(tr.center().x() - s / 2 + 0.5), int(tr.center().y() - s / 2 + 0.5), s, s))
        # the time left, as a thin line along the bottom that stops while the pointer is on the card
        if self._timer.isActive() and self.timeout > 0:
            left = max(0.0, min(1.0, self._timer.remainingTime() / self.timeout))
            line = QColor(col["accent"])
            line.setAlphaF(0.75)
            p.save()
            p.setClipPath(card)
            p.fillRect(QRectF(r.left(), r.bottom() - 2, r.width() * left, 2), line)
            p.restore()
        p.end()

    def paint_button(self, p: QPainter, b: _CardButton) -> None:
        col = self._colors()
        p.setRenderHint(QPainter.Antialiasing)
        hot = b.underMouse()
        down = b.isDown()
        r = QRectF(b.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if b.kind == "close":
            if not (self.hovered or hot):
                return  # the cross shows while the pointer is on the card
            if hot:
                p.setPen(Qt.NoPen)
                p.setBrush(col["hover"])
                p.drawEllipse(r)
            pen = QPen(col["text"] if hot else col["muted"], 1.4, Qt.SolidLine, Qt.RoundCap)
            p.setPen(pen)
            c = r.center()
            d = 4.0
            p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
            p.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))
            return
        if b.kind == "primary":  # the app's main button: filled with the accent
            bg = QColor(col["btn"])
            if hot or down:
                bg = bg.lighter(108) if col["kind"] != "light" else bg.darker(106)
            fg = QColor(col["btn_text"])
            p.setPen(Qt.NoPen)
        else:
            bg = QColor(col["hover"] if (hot or down) else col["button"])
            fg = QColor(col["text"])
            p.setPen(QPen(col["border"], 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, 6, 6)
        p.setPen(fg)
        p.setFont(b.font())
        p.drawText(r, Qt.AlignCenter, b.text())

    # behaviour ------------------------------------------------------------------------------------
    def start_timer(self, ms: int | None = None) -> None:
        if not self.hovered and not self.leaving:
            self._timer.start(self.timeout if ms is None else ms)
            if not hasattr(self, "_tick"):
                self._tick = QTimer(self)  # redraws the time-left line
                self._tick.setInterval(80)
                self._tick.timeout.connect(self.update)
            self._tick.start()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if self.glass:
            from .backdrop import blur_behind
            from .flyout import round_corners

            light = theme_kind(self.c) == "light"
            from .backdrop import glass_colours

            r, g, b, a = (int(x) for x in glass_colours(self.c, not light)["tint"][5:-1].split(","))
            blur_behind(self, QColor(r, g, b, a))
            round_corners(self)

    def remaining(self) -> int:
        return self._timer.remainingTime() if self._timer.isActive() else -1

    def enterEvent(self, e) -> None:  # noqa: N802 - the card stays while the pointer is on it
        self.hovered = True
        self._timer.stop()
        self.close_button.update()
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.hovered = False
        self.start_timer(min(self.timeout, LINGER_MS))
        self.close_button.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton and self.card_rect().contains(e.position()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def announce(self) -> None:
        """Tell screen readers a notification appeared (the card can't take the focus)."""
        try:
            from PySide6.QtGui import QAccessible, QAccessibleEvent

            QAccessible.updateAccessibility(QAccessibleEvent(self, QAccessible.Event.Alert))
        except Exception:  # noqa: BLE001 - no accessibility bridge
            pass

    def glide(self, pos: QPoint, opacity: tuple[float, float] | None = None, done: Callable[[], None] | None = None,
              duration: int = 220) -> None:
        """Move to pos (and fade), smoothly unless Windows' animation effects are off."""
        if self._move is not None:
            self._move.stop()
        start, start_op = self.pos(), self.windowOpacity()
        end_op = opacity[1] if opacity else start_op
        if opacity:
            start_op = opacity[0]
        if not animations_on() or not self.isVisible() or duration <= 0:
            self.move(pos)
            self.setWindowOpacity(end_op)
            if done:
                done()
            return
        anim = QVariantAnimation(self)
        anim.setDuration(duration)
        anim.setEasingCurve(QEasingCurve.OutCubic if end_op >= start_op else QEasingCurve.InCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def step(f) -> None:
            f = float(f)
            self.move(QPoint(round(start.x() + (pos.x() - start.x()) * f), round(start.y() + (pos.y() - start.y()) * f)))
            self.setWindowOpacity(start_op + (end_op - start_op) * f)

        anim.valueChanged.connect(step)
        if done:
            anim.finished.connect(done)
        self._move = anim
        anim.start()


def _short(text: str, limit: int = 220) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# The stack ----------------------------------------------------------------------------------------
class Toasts:
    """Shows cards in the taskbar's corner of the tray's screen, newest next to the taskbar."""

    def __init__(self, palette: dict[str, str], anchor: Callable[[], QRect | None] | None = None,
                 quiet: Callable[[], str | None] = quiet_reason) -> None:
        self.c = palette
        self._anchor = anchor
        self._quiet = quiet
        self.cards: list[ToastCard] = []  # oldest first

    def show(self, title: str, text: str, *, icon: str = "cloud", tone: str | None = None,
             actions: Sequence[Action] = (), on_click: Callable[[], None] | None = None,
             timeout: int = TIMEOUT_MS, images: Sequence[str] = ()) -> ToastCard | None:
        """A new card, or None when Windows asks for quiet now."""
        why = self._quiet()
        if why:
            log.info("Notification not shown (%s): %s", why, title)
            return None
        made: list[ToastCard] = []  # the card, once built (its buttons close it first, then act)

        def run(fn: Callable[[], None] | None) -> Callable[[], None]:
            def go() -> None:
                if made:
                    self.dismiss(made[0])
                if fn is not None:
                    try:
                        fn()
                    except Exception:  # noqa: BLE001 - a button must never take the app down
                        log.exception("Notification action failed")
            return go

        card = ToastCard(title, text, self.c, icon, tone, [(label, run(fn)) for label, fn in actions], timeout,
                         images)
        made.append(card)
        card.clicked.connect(run(on_click))
        card.closeRequested.connect(lambda: self.dismiss(card))
        card.expired.connect(lambda: self.dismiss(card))
        card.destroyed.connect(lambda *_: self._forget(card))

        while len([c for c in self.cards if not c.leaving]) >= MAX_CARDS:
            self.dismiss(next(c for c in self.cards if not c.leaving))
        self.cards.append(card)
        target = self._positions().get(card)
        x0 = target.x() + (24 if self._side() == "right" else -24)
        card.move(QPoint(x0, target.y()))
        card.setWindowOpacity(0.0 if animations_on() else 1.0)
        card.show()
        card.raise_()
        card.glide(target, (0.0, 1.0), duration=260)
        self._reflow(skip=card)
        card.announce()
        card.start_timer()
        return card

    def dismiss(self, card: ToastCard) -> None:
        if card not in self.cards or card.leaving:
            return
        card.leaving = True
        card._timer.stop()
        dx = 24 if self._side() == "right" else -24
        card.glide(card.pos() + QPoint(dx, 0), (card.windowOpacity(), 0.0), done=card.close, duration=180)
        self._reflow()

    def close_all(self) -> None:
        for card in list(self.cards):
            card.leaving = True
            card.close()
        self.cards.clear()

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        for card in self.cards:
            card.set_palette(palette)
        self._reflow()

    def visible(self) -> list[ToastCard]:
        return [c for c in self.cards if not c.leaving]

    # placement --------------------------------------------------------------------------------------
    def _forget(self, card: ToastCard) -> None:
        if card in self.cards:
            self.cards.remove(card)
            self._reflow()

    def _screen(self):
        a = self._anchor() if self._anchor else None
        screen = QGuiApplication.screenAt(a.center()) if a is not None and a.isValid() else None
        return screen or QGuiApplication.primaryScreen()

    def _edge(self) -> str:
        """Where the taskbar is: bottom, top, left or right."""
        screen = self._screen()
        avail, full = screen.availableGeometry(), screen.geometry()
        if avail.top() > full.top():
            return "top"
        if avail.left() > full.left():
            return "left"
        if avail.right() < full.right():
            return "right"
        return "bottom"

    def _side(self) -> str:
        return "left" if self._edge() == "left" else "right"

    def _positions(self) -> dict[ToastCard, QPoint]:
        """Window positions for the cards staying: newest next to the taskbar, older ones further away."""
        avail = self._screen().availableGeometry()
        down = self._edge() == "top"  # a taskbar at the top: cards stack downward
        out: dict[ToastCard, QPoint] = {}
        offset = 0
        for card in reversed(self.visible()):
            m = card.SHADOW
            w, h = card.width(), card.height()
            inner_h = h - 2 * m
            if self._side() == "left":
                x = avail.left() + EDGE - m
            else:
                x = avail.right() + 1 - EDGE - (w - m)
            if down:
                y = avail.top() + EDGE + offset - m
            else:
                y = avail.bottom() + 1 - EDGE - offset - inner_h - m
            out[card] = QPoint(x, y)
            offset += inner_h + GAP
        return out

    def _reflow(self, skip: ToastCard | None = None) -> None:
        for card, pos in self._positions().items():
            if card is not skip and card.pos() != pos:
                card.glide(pos)
