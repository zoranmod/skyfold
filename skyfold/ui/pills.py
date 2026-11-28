"""The floating bars of the library windows (Photos: Years / Months / All Photos, the selection bar;
Photos and Notes: the size, − 100% +) and the design's layers they are drawn with."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from ..i18n import t

# Photos' own style sheet says the same for its buttons; a window that only has the size bar adds this
# (a style sheet's button padding, border and minimum height would otherwise win over the bar's own)
PILL_SHEET = ("QPushButton#PhotoPillButton {background:transparent;border:none;padding:0;min-height:0;"
              "font-size:13px;font-weight:600}")


def _is_light(c: dict) -> bool:
    return QColor(c["bg"]).lightness() > 128


def _qc(r: int, g: int, b: int, a: float = 1.0) -> QColor:
    c = QColor(r, g, b)
    c.setAlphaF(a)
    return c


def _alpha(colour, a: float) -> QColor:
    c = QColor(colour)
    c.setAlphaF(a)
    return c


def _tokens(c: dict, glass: bool) -> dict:
    """The design's layers for this palette: mica (the floating bars), solid (the chosen segment, the
    title band), layer / layer2 (cards, buttons), hover, stroke / stroke2 (hairlines), text2 / text3,
    shadows, and the window's own tint (what lies around the content pane)."""
    from .backdrop import glass_colours

    light = _is_light(c)
    text = QColor(c["text"])
    oled_solid = not light and QColor(c["bg"]).lightness() < 8 and not glass  # OLED without the material
    if light:
        t = {"mica": _qc(244, 245, 248, .84), "solid": _qc(244, 245, 248), "layer": _qc(255, 255, 255, .72),
             "layer2": _qc(255, 255, 255, .92), "hover": _qc(0, 0, 0, .04), "stroke": _qc(0, 0, 0, .06),
             "stroke2": _qc(0, 0, 0, .11), "shadow1": _qc(0, 0, 0, .07), "shadow2": _qc(20, 30, 60, .13),
             "text2": _alpha(text, .72), "text3": _alpha(text, .52)}
    else:
        solid = QColor(c["surface"]) if oled_solid else _qc(30, 33, 39)
        t = {"mica": _alpha(solid, .84), "solid": solid, "layer": _qc(255, 255, 255, .045),
             "layer2": _qc(255, 255, 255, .08), "hover": _qc(255, 255, 255, .09), "stroke": _qc(255, 255, 255, .07),
             "stroke2": _qc(255, 255, 255, .12), "shadow1": _qc(0, 0, 0, .3), "shadow2": _qc(0, 0, 0, .35),
             "text2": _alpha(text, .74), "text3": _alpha(text, .52)}
    t["text"] = text
    # the chosen segment: the solid material (dark); on the light one plain white, or it would vanish
    t["chosen"] = QColor("#ffffff") if light else t["solid"]
    t["accent"] = QColor(c["accent"])
    t["accent_soft"] = _alpha(c["accent"], .16 if not light else .10)
    t["focus"] = QColor(c["focus_ring"])
    t["head"] = _alpha(t["solid"] if (glass or light) else QColor(c["bg"]), .92)
    tint = glass_colours(c, not light)["tint"] if glass else c["bg"]
    t["tint"] = _css_colour(tint)
    return t


def _css_colour(css: str) -> QColor:
    if css.startswith("rgba("):
        r, g, b, a = (float(x) for x in css[5:-1].split(","))
        return QColor(int(r), int(g), int(b), int(a if a > 1 else a * 255))
    return QColor(css)


def _ui(w: QWidget) -> dict:
    u = getattr(w.window(), "_ui", None)
    if u is None:
        from .theme import DARK

        u = _tokens(DARK, False)
    return u


def _round(p: QPainter, r: QRectF, fill, ring=None, radius: float = 8) -> None:
    """A smooth rounded shape (Qt's style sheets draw these corners without anti-aliasing)."""
    p.setRenderHint(QPainter.Antialiasing)
    rad = min(radius, r.width() / 2, r.height() / 2)
    if ring is not None and QColor(ring).alpha():
        p.setPen(QPen(QColor(ring), 1))
        r = r.adjusted(0.5, 0.5, -0.5, -0.5)
    else:
        p.setPen(Qt.NoPen)
    p.setBrush(QColor(fill) if fill is not None else Qt.NoBrush)
    p.drawRoundedRect(r, rad, rad)


def _shadow(p: QPainter, r: QRectF, radius: float, blur: float, dy: float, colour: QColor) -> None:
    """A soft drop shadow (CSS box-shadow 0 dy blur colour): rounded shapes stacked from wide and faint
    to the shape itself, so the edge fades out smoothly."""
    if not colour.alpha():
        return
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    steps = max(3, int(blur / 2))
    per = 1 - (1 - colour.alphaF()) ** (1 / steps)
    c = QColor(colour)
    c.setAlphaF(per)
    p.setBrush(c)
    for i in range(steps):
        grow = blur * (steps - i) / steps * 0.75
        rr = r.adjusted(-grow, -grow + dy, grow, grow + dy)
        p.drawRoundedRect(rr, radius + grow, radius + grow)
    p.restore()


class _Shadow(QWidget):
    """The soft shadow under a floating bar, drawn beside it (so the bar itself takes clicks only where
    it is drawn): follows the bar wherever it moves, shows and hides with it."""

    BLUR, DY = 24, 8

    def __init__(self, target: QWidget, radius: float) -> None:
        super().__init__(target.parentWidget())
        self.target, self.radius = target, radius
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        target.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj, e):  # noqa: N802
        kind = e.type()
        if kind in (QEvent.Move, QEvent.Resize, QEvent.Show, QEvent.Hide, QEvent.ZOrderChange):
            self.follow()
        return False

    def follow(self) -> None:
        t = self.target
        b = self.BLUR
        self.setGeometry(t.geometry().adjusted(-b, -b, b, b + self.DY))
        self.setVisible(not t.isHidden())
        self.stackUnder(t)

    def paintEvent(self, _e) -> None:  # noqa: N802
        u = _ui(self)
        p = QPainter(self)
        inner = QRectF(self.rect()).adjusted(self.BLUR, self.BLUR, -self.BLUR, -self.BLUR - self.DY)
        hole = QPainterPath()
        hole.addRoundedRect(inner.adjusted(1, 1, -1, -1), self.radius, self.radius)
        outside = QPainterPath()
        outside.addRect(QRectF(self.rect()))
        p.setClipPath(outside.subtracted(hole))  # (the see-through bar never darkens what is under it)
        _shadow(p, inner, self.radius, self.BLUR, self.DY, u["shadow2"])
        _shadow(p, inner, self.radius, 3, 1, _alpha(u["shadow1"], min(1.0, u["shadow1"].alphaF())))


class _Pill(QWidget):
    """The floating bars (Years / Months / All Photos, − +, the selection bar): see-through, a hairline
    and a soft shadow (drawn by its _Shadow), as in the design."""

    RADIUS = 10

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.shadow = _Shadow(self, self.RADIUS)

    def paintEvent(self, e) -> None:  # noqa: N802
        u = _ui(self)
        p = QPainter(self)
        _round(p, QRectF(self.rect()), u["mica"], u["stroke2"], self.RADIUS)


class _PillButton(QPushButton):
    """A button on a pill: 13 px semibold in the quieter text colour; the chosen one a neutral raised
    layer (not blue), red for Delete."""

    def __init__(self, text: str, danger: bool = False, number: bool = False) -> None:
        super().__init__(text)
        self.danger = danger
        self.number = number  # a number ("100%"): in the app's number font, as every count
        self._by_keys = False
        self.setObjectName("PhotoPillButton")
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        f = self.font()
        f.setPixelSize(13)
        f.setWeight(QFont.DemiBold)
        self.setFont(f)

    def _font(self) -> QFont:
        if not self.number:
            return self.font()
        from .library_frame import count_font

        return count_font(self.font())  # (asked each time: a style sheet's font never replaces it)

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetricsF(self._font())
        return QSize(int(fm.horizontalAdvance(self.text()) + 26.5), 28)

    def focusInEvent(self, e) -> None:  # noqa: N802
        self._by_keys = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        super().focusInEvent(e)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, e) -> None:  # noqa: N802
        u = _ui(self)
        p = QPainter(self)
        r = QRectF(self.rect())
        if self.danger:
            _round(p, r, QColor("#d13438" if self.underMouse() else "#c42b1c"), None, 7)
        elif self.isChecked():  # the chosen view: the solid material, raised a little
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(u["shadow1"])
            p.drawRoundedRect(r.adjusted(0, 1, 0, 1), 7, 7)
            _round(p, r, u["chosen"], None, 7)
        elif not self.isEnabled():  # (the end of the range: nothing under the pointer, the sign faint)
            pass
        elif self.underMouse() or self.isDown():
            _round(p, r, u["hover"], None, 7)
        if self.hasFocus() and (self._by_keys or getattr(self.window(), "keyboard_used", False)):
            p.setPen(QPen(u["focus"], 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 6, 6)
        p.setPen(QColor("#ffffff") if self.danger else _alpha(u["text"], .30) if not self.isEnabled()
                 else u["text"] if self.isChecked() or self.underMouse() else u["text2"])
        p.setFont(self._font())
        p.drawText(self.rect(), Qt.AlignCenter, self.text())


class ZoomPill(_Pill):
    """The size of what a library window shows, as in Edge and Windows' Photos: − 100% +. The
    percentage is the size against the normal one and follows every way of changing it (these buttons,
    Ctrl + wheel, Ctrl + / − / 0); a click on it goes back to the normal size. One bar for the photos'
    size in Photos and the text's size in Notes.

    The buttons are reached with Tab but a click leaves the focus where it was: the keys keep going to
    the photos or the note."""

    stepped = Signal(int)  # 1 larger, -1 smaller
    reset = Signal()  # back to the normal size

    def __init__(self, parent: QWidget, smaller: str, larger: str) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(0)
        self.minus = _PillButton("−")
        self.percent = _PillButton("100%", number=True)
        self.plus = _PillButton("+")
        for button, name, keys in ((self.minus, smaller, "Ctrl −"), (self.plus, larger, "Ctrl +")):
            button.setFixedWidth(30)
            button.setAccessibleName(name)
            button.setToolTip(f"{name} ({keys})")
        # as wide as its widest text ("240%"), so − and + never move under the pointer
        wide = QFontMetricsF(self.percent._font()).horizontalAdvance("000%")
        self.percent.setFixedWidth(int(wide + 16.5))
        for button in (self.minus, self.percent, self.plus):
            button.setFocusPolicy(Qt.TabFocus)
            button.setAutoDefault(False)  # (in a dialog: Enter elsewhere never presses it)
            lay.addWidget(button)
        self.minus.clicked.connect(lambda: self.stepped.emit(-1))
        self.plus.clicked.connect(lambda: self.stepped.emit(1))
        self.percent.clicked.connect(self.reset.emit)
        self.show_zoom(100, True, True)
        self.adjustSize()

    def show_zoom(self, percent: int, smaller: bool, larger: bool) -> None:
        """percent: the size now against the normal one; smaller / larger: whether there is a step left."""
        self.percent.setText(f"{percent}%")
        name = t("Normal size (now {n}%)", n=percent)
        self.percent.setAccessibleName(name)
        self.percent.setToolTip(f"{name} (Ctrl 0)")
        self.minus.setEnabled(smaller)
        self.plus.setEnabled(larger)
        self.update()


