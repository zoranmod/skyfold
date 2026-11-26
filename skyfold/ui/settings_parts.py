"""Parts of the 1.1 Settings window (and the other windows in its style): the account at the top of
the sidebar, the sidebar's marker for the page you are on, icons that follow the theme, labels that
shorten long folder paths in the middle, and setting cards you can click."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPalette
from PySide6.QtWidgets import QLabel, QSizePolicy, QStyle, QStyledItemDelegate, QWidget

from .panel_parts import SoftCard


class IconLabel(QWidget):
    """A line icon of the 1.1 set in a colour of the palette (default: the quiet text colour). It is
    drawn again when the theme changes."""

    def __init__(self, name: str, palette: dict[str, str], size: int = 20, role: str = "muted",
                 color: str | None = None) -> None:
        super().__init__()
        self.name, self.role, self.fixed = name, role, color
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.apply_palette(palette)

    def apply_palette(self, palette: dict[str, str]) -> None:
        from .icons import glyph

        colour = self.fixed or palette.get(self.role, palette["muted"])
        self._icon = glyph(self.name, colour, self.width())
        self.update()

    def set_name(self, name: str, palette: dict[str, str]) -> None:
        self.name = name
        self.apply_palette(palette)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        if not self.isEnabled():
            p.setOpacity(0.45)
        self._icon.paint(p, self.rect())


class Avatar(QWidget):
    """A round picture with the first letter of the Apple ID, as at the top of Windows' Settings."""

    def __init__(self, who: str, size: int = 40, picture: str | None = None) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self.letter = (who.strip()[:1] or "?").upper()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        from PySide6.QtGui import QImage

        self.picture = QImage(picture) if picture else QImage()  # the photo on the user's own card

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if not self.picture.isNull():
            from PySide6.QtGui import QPainterPath

            clip = QPainterPath()
            clip.addEllipse(r)
            p.setClipPath(clip)
            p.drawImage(r, self.picture)
            return
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, QColor("#6fa8ff"))
        g.setColorAt(1, QColor("#b6a3ff"))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(r)
        f = QFont(self.font())
        f.setFamilies(["Segoe UI Variable Display", "Segoe UI"])
        f.setPixelSize(int(self.height() * 0.4))
        f.setWeight(QFont.Bold)
        p.setFont(f)
        p.setPen(QColor("#0b1220"))
        p.drawText(r, Qt.AlignCenter, self.letter)


class NavDelegate(QStyledItemDelegate):
    """The sidebar of a window: 36 px rows, and a short accent bar beside the page you are on."""

    HEIGHT = 36

    def __init__(self, owner) -> None:
        super().__init__(owner)
        self.owner = owner  # has .c, the palette (it changes with the theme)

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        s = super().sizeHint(option, index)
        return QSize(s.width(), self.HEIGHT + 2)  # (+ the 1 px margins around each row)

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        if option.state & QStyle.State_Selected:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            r = option.rect
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(self.owner.c["accent"]))
            painter.drawRoundedRect(QRectF(r.left() + 0.5, r.top() + 11, 3, r.height() - 22), 1.5, 1.5)
            painter.restore()


class ElidedLabel(QLabel):
    """One line that shortens itself in the middle when there is no room (a long folder path keeps its
    start and its last folder); the whole text is in the tooltip."""

    def __init__(self, text: str = "", mode=Qt.ElideMiddle) -> None:
        super().__init__(text)
        self.mode = mode
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setToolTip(text)

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self.setToolTip(text)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(60, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:  # noqa: N802
        s = super().sizeHint()
        return QSize(min(s.width(), 360), s.height())

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setFont(self.font())
        group = QPalette.Active if self.isEnabled() else QPalette.Disabled
        p.setPen(self.palette().color(group, QPalette.WindowText))
        r = self.contentsRect()
        p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter, self.fontMetrics().elidedText(self.text(), self.mode, r.width()))


class ClickCard(SoftCard):
    """A setting card that does something when clicked (or with Enter/Space): opens a page, a file or
    a question. Lights up under the pointer like Windows' own."""

    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("SetCard")
        self.setProperty("click", True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_Hover)

    def _hot(self, on: bool) -> None:
        self.setProperty("hot", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hot(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hot(False)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()) and self.isEnabled():
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(e)

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        if self.hasFocus():  # the keyboard focus ring, as on Windows' own cards
            from PySide6.QtGui import QPen

            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(self.palette().color(QPalette.Highlight), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 7, 7)


__all__ = ["Avatar", "ClickCard", "ElidedLabel", "IconLabel", "NavDelegate"]
