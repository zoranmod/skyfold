"""Plain-text help for widgets and item views, using Qt's platform hover delay."""
from __future__ import annotations

import html
import re

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPalette, QPen
from PySide6.QtWidgets import QAbstractItemView, QWidget

from . import theme
from .backdrop import glass_colours, rgba


def _literal(value: str) -> str:
    # Only undo the small envelope emitted by safe_text.plain_tip. No HTML renderer is involved.
    if value.startswith("<qt>") and value.endswith("</qt>"):
        return html.unescape(value[4:-5].replace("<br>", "\n"))
    return value


def _lines(text: str, metrics: QFontMetrics, width: int, limit: int) -> list[str]:
    """Prefer whole words and path components. Preserve both ends of an overlong remainder."""
    lines = []
    for paragraph in text.splitlines():
        current = ""
        boundary = r"(?<=[/\\])" if "\\" in paragraph or "/" in paragraph else r"(?<=\s)"
        for token in re.split(boundary, paragraph):
            if current and metrics.horizontalAdvance(current + token) > width:
                lines.append(current)
                current = ""
            current += token
        if current:
            lines.append(current)
    if len(lines) > limit:
        lines[limit - 1:] = ["".join(lines[limit - 1:])]
    return [metrics.elidedText(line.rstrip(), Qt.ElideMiddle, width) for line in lines]


class TipWindow(QWidget):
    """A mouse-transparent, antialiased popup. Text is painted as glyphs, including all markup."""

    MARGIN = 7  # transparent space for the shadow

    def __init__(self, colours: dict[str, str]) -> None:
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)
        self.rows = []
        self.apply_palette(colours)

    def apply_palette(self, colours: dict[str, str]) -> None:
        self.colours = colours
        dark = QColor(colours["bg"]).lightness() < 128
        self.stroke = rgba(glass_colours(colours, dark)["stroke2"])
        self.update()

    def present(self, text: str, point: QPoint, screen) -> None:
        bounds = screen.availableGeometry()
        title, _, details = text.partition("\n")
        self.setAccessibleName(title)
        self.setAccessibleDescription(details)
        self.rows = []
        pad = self.MARGIN + 12
        width = max(1, min(336, bounds.width() - 2 * pad - 8))
        y = self.MARGIN + 8
        longest = 0
        for part, size, colour, limit in ((title, 14, "text", 3), (details, 12, "muted", 5)):
            if not part:
                continue
            font = QFont(self.font())
            font.setPixelSize(size)
            font.setWeight(QFont.Normal)
            metrics = QFontMetrics(font)
            if self.rows:
                y += 3
            for line in _lines(part, metrics, width, limit):
                self.rows.append((line, font, colour, QPoint(pad, y + metrics.ascent())))
                longest = max(longest, metrics.horizontalAdvance(line))
                y += metrics.height()
        self.resize(longest + 2 * pad, y + 8 + self.MARGIN)
        x = min(point.x() + 8, bounds.right() - self.width() + 1)
        y = point.y() + 18
        if y + self.height() > bounds.bottom() + 1:
            y = point.y() - self.height() - 4
        self.move(max(bounds.left(), x), max(bounds.top(), y))
        self.show()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        m = self.MARGIN
        body = QRectF(self.rect()).adjusted(m + 0.5, m + 0.5, -m - 0.5, -m - 0.5)
        p.setPen(Qt.NoPen)
        for spread in range(5, 0, -1):
            p.setBrush(QColor(0, 0, 0, 5 + 2 * (5 - spread)))
            p.drawRoundedRect(body.adjusted(-spread, 2 - spread, spread, 2 + spread), 8 + spread, 8 + spread)
        p.setBrush(QColor(self.colours["surface"]))
        p.setPen(QPen(self.stroke, 1.0))
        p.drawRoundedRect(body, 8, 8)
        for line, font, role, pos in self.rows:
            p.setFont(font)
            p.setPen(QColor(self.colours[role]))
            p.drawText(pos, line)
        p.end()


class ToolTips(QObject):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.app = app
        self.popup = None
        self.owner = None
        self.item_rect = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)
        app.installEventFilter(self)

    def dismiss(self) -> None:
        self.owner = self.item_rect = None
        self.timer.stop()
        if self.popup is not None:
            self.popup.hide()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        kind = event.type()
        if kind == QEvent.ToolTip and isinstance(obj, QWidget):
            point = event.globalPos()
            owner = obj
            area = None
            text = ""
            while owner is not None:
                parent = owner.parentWidget()
                if isinstance(parent, QAbstractItemView) and owner is parent.viewport():
                    index = parent.indexAt(owner.mapFromGlobal(point))
                    text = index.data(Qt.ToolTipRole) if index.isValid() else ""
                    if index.isValid() and not text and parent.property("tipForClippedItems"):
                        name = index.data(Qt.DisplayRole) or ""
                        visible = parent.visualRect(index).intersected(owner.rect())
                        if parent.fontMetrics().horizontalAdvance(name) + 8 > visible.width():
                            from .safe_text import plain_tip

                            text = plain_tip(name)
                    if text:
                        area = parent.visualRect(index)
                        break
                text = owner.toolTip()
                if text or owner.isWindow():
                    break
                owner = parent
            if isinstance(text, str) and text:
                colours = getattr(owner.window(), "c", None)
                if not isinstance(colours, dict) or "surface" not in colours:
                    background = self.app.palette().color(QPalette.Window).name()
                    colours = next((c for c in (theme.LIGHT, theme.DARK, theme.OLED)
                                    if c["bg"] == background), theme.palette())
                if self.popup is None:
                    self.popup = TipWindow(colours)
                elif colours != self.popup.colours:
                    self.popup.apply_palette(colours)
                self.owner, self.item_rect = owner, area
                screen = QGuiApplication.screenAt(point) or owner.screen()
                self.popup.present(_literal(text), point, screen)
                self.timer.start(max(5500, min(18000, 3500 + 55 * len(text))))
            else:
                self.dismiss()
            event.accept()
            return True
        if self.owner is not None:
            if kind in (QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.Wheel, QEvent.ApplicationDeactivate):
                self.dismiss()
            elif obj is self.owner:
                if kind in (QEvent.Leave, QEvent.Hide, QEvent.Destroy, QEvent.ToolTipChange):
                    self.dismiss()
                elif kind == QEvent.MouseMove and self.item_rect is not None:
                    if not self.item_rect.contains(event.position().toPoint()):
                        self.dismiss()
        return False
