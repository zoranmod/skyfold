"""Coloured icon tiles that glow softly under the pointer (the panel's services and its brand mark)."""

from __future__ import annotations

import sys

from PySide6.QtCore import QRect, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .icons import glyph
from .theme import theme_kind


def animations_on() -> bool:
    """False when Windows is set to show no animations (Settings → Accessibility → Visual effects)."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        value = ctypes.c_int(1)
        spi_getclientareaanimation = 0x1042
        if ctypes.windll.user32.SystemParametersInfoW(spi_getclientareaanimation, 0, ctypes.byref(value), 0):
            return bool(value.value)
    except Exception:  # noqa: BLE001 - a missing API only means "animate as usual"
        pass
    return True


class GlowTile(QWidget):
    """A rounded tile tinted with the service colour, its glyph in that colour, and a glow that fades in
    while hot (pointer over the row, or keyboard focus)."""

    def __init__(self, glyph_name: str, palette: dict[str, str], color: str, size: int = 26,
                 icon: int = 16, radius: float = 9, hover_self: bool = False) -> None:
        super().__init__()
        self.glyph_name = glyph_name
        self.icon_size = icon
        self.radius = radius
        self.setFixedSize(size, size)
        if hover_self:  # glows under the pointer by itself (the brand mark)
            self.setAttribute(Qt.WA_Hover)
        else:
            self.setAttribute(Qt.WA_TransparentForMouseEvents)  # the row under it takes the clicks
        self._hover_self = hover_self
        self._level = 0.0
        self._hot = False
        # (no graphics effect: Qt renders an effect's widget into an image at whole pixels, which made
        # the tile and its icon soft and jagged at 125 % display scaling; hot only deepens the tint)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(180)
        self._anim.valueChanged.connect(self._set_level)
        self.set_palette(palette, color)

    def set_palette(self, palette: dict[str, str], color: str) -> None:
        self.c = palette
        self.color = QColor(color)
        self._light = theme_kind(palette) == "light"
        self._icon = glyph(self.glyph_name, color, self.icon_size)  # drawn at the screen's own pixel density
        self._set_level(self._level)
        self.update()

    def set_hot(self, hot: bool) -> None:
        if hot == self._hot:
            return
        self._hot = hot
        target = 1.0 if hot else 0.0
        self._anim.stop()
        if not animations_on() or not self.isVisible():
            self._set_level(target)
            return
        self._anim.setStartValue(self._level)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e) -> None:  # noqa: N802 - Qt naming
        if self._hover_self:
            self.set_hot(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802 - Qt naming
        if self._hover_self:
            self.set_hot(False)
        super().leaveEvent(e)

    def _set_level(self, level) -> None:
        self._level = float(level)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802 - Qt naming
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        fill = QColor(self.color)  # a flat tint of the service colour, no outline (1.1 design)
        fill.setAlphaF((0.13 if self._light else 0.17) + 0.07 * self._level)
        p.setBrush(fill)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(r, self.radius, self.radius)
        x = (self.width() - self.icon_size) // 2
        y = (self.height() - self.icon_size) // 2
        self._icon.paint(p, QRect(x, y, self.icon_size, self.icon_size))
        p.end()
