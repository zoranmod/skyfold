"""The Photos window: your synced photos and videos as on an iPad (Years, Months, All Photos), with
Favourites, Recently added, videos, Live Photos, screenshots and albums, and a viewer.

Everything shown comes from the sync database and the files in the photos folder; pictures come
from Windows (see photo_images). Deleting moves the files to the Recycle Bin, exactly as deleting
them in the folder would, so the sync then does what it always does with a deleted photo. Only the
heart talks to iCloud, through the sync's own thread."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (QAbstractListModel, QMargins, QEasingCurve, QEvent, QItemSelection, QItemSelectionModel, QObject,
                            QMimeData, QModelIndex, QPoint, QRect, QRectF, QSize, Qt, QTimer, QUrl, QVariantAnimation,
                            Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QFontMetricsF, QGuiApplication, QIcon, QImage, QKeySequence,
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractScrollArea, QApplication, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPushButton, QScrollArea, QScrollBar, QSizePolicy, QStackedWidget, QStyle,
                               QStyledItemDelegate, QStyleOptionSlider, QToolButton, QVBoxLayout, QWidget)
from shiboken6 import isValid

from .. import photo_library as lib
from ..i18n import language, t
from .icons import glyph
from .library_frame import LibraryDialog, SearchField, count_font
from .panel_parts import SoftCard
from .settings_parts import NavDelegate, pressable
from .theme import service_color
from .photo_images import ImageLoader, fit_short_side
from .pills import (ZoomPill, _alpha, _css_colour, _is_light, _Pill, _PillButton, _qc, _round, _shadow,  # noqa: F401
                    _tokens, _ui)

log = logging.getLogger(__name__)

COLUMNS = (3, 5, 7, 9, 13)  # the five grid sizes, as pinching on an iPad
ZOOM_DEFAULT = 1  # five columns: the design's photo size (100 %)
GAP = 2
ITEM = Qt.UserRole + 1
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December")


def _month(m: int) -> str:
    return t(MONTH_NAMES[m - 1])


def _day(d: datetime) -> str:
    return f"{d.day}. {_month(d.month)} {d.year}" if language() == "bs" else f"{d.day} {_month(d.month)} {d.year}"


def _num(n: int) -> str:
    """1,284 in English, 1.284 in Bosnian."""
    s = f"{n:,}"
    return s.replace(",", ".") if language() == "bs" else s


def _duration(sec: float | None) -> str:
    if not sec:
        return ""
    s = int(round(sec))
    return f"{s // 60}:{s % 60:02d}"


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


# --- the look (the 1.1 design's tokens) --------------------------------------------------------------
# Everything the window draws over the material is a see-through layer (rgba), in every theme: OLED
# too is see-through like Dark (the user's choice). Only the photos themselves are solid.
HEAD = 74  # the title band over the photos: they scroll on under it


def _css(c: QColor) -> str:
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()})"


def _layer_button(p: QPainter, w: QWidget, r: QRectF, radius: float, checked: bool = False) -> None:
    """The design's .btn: a light layer with a hairline and a 1 px shadow; a little lighter under the
    pointer, accent-tinted when switched on."""
    u = _ui(w)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(u["shadow1"])
    p.drawRoundedRect(r.translated(0, 1), radius, radius)
    _round(p, r, u["layer2"], u["stroke2"], radius)
    if checked:
        _round(p, r, u["accent_soft"], _alpha(u["accent"], .55), radius)
    if w.isEnabled() and (w.underMouse() or getattr(w, "isDown", lambda: False)()):
        _round(p, r, u["hover"], None, radius)
    if w.hasFocus() and getattr(w.window(), "keyboard_used", False):
        p.setPen(QPen(u["focus"], 2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1), radius - 1, radius - 1)


class _Toast(QLabel):
    def paintEvent(self, e) -> None:  # noqa: N802
        u = _ui(self)
        p = QPainter(self)
        _round(p, QRectF(self.rect()), u["solid"], u["stroke2"], 12)
        p.end()
        super().paintEvent(e)


class _SoftButton(QPushButton):
    """The design's small button (.btn.sm, ‹ Library): 30 px, corners 8, 12.5 px semibold, a light
    layer with a hairline; an optional icon before the text."""

    def __init__(self, text: str = "", height: int = 30, radius: float = 8) -> None:
        super().__init__(text)
        self.setObjectName("PhotoButton")
        self.radius = radius
        self.setFixedHeight(height)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        f = self.font()
        f.setPointSizeF(9.375)  # 12.5 px
        f.setWeight(QFont.DemiBold)
        self.setFont(f)

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetricsF(self.font())
        w = fm.horizontalAdvance(self.text()) + 24 + (20 if not self.icon().isNull() else 0)
        return QSize(int(w + 0.5), self.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, _e) -> None:  # noqa: N802
        u = _ui(self)
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(0, 0, 0, -1)
        _layer_button(p, self, r, self.radius)
        p.setFont(self.font())
        p.setPen(u["text"] if self.isEnabled() else u["text3"])
        x = 12
        if not self.icon().isNull():
            box = QRect(x - 1, (self.height() - 1 - 14) // 2, 14, 14)
            self.icon().paint(p, box, Qt.AlignCenter)
            x += 19
        p.drawText(QRectF(x, 0, self.width() - x - 12, r.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())


class _SquareButton(QToolButton):
    """The viewer's heart / info / delete / more: 34 × 34 squares, corners 8, a light layer."""

    def __init__(self, tip: str) -> None:
        super().__init__()
        self.setObjectName("PhotoSquare")
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.setIconSize(QSize(16, 16))
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(0, 0, 0, -1)
        _layer_button(p, self, r, 8, self.isChecked())
        box = QRect(QPoint(), self.iconSize())
        box.moveCenter(r.center().toPoint())
        self.icon().paint(p, box, Qt.AlignCenter, QIcon.Normal if self.isEnabled() else QIcon.Disabled)


class _QuietButton(QToolButton):
    """A quiet icon button (the sidebar switch before the title): no layer until the pointer is on it."""

    def __init__(self, tip: str, size: int = 30) -> None:
        super().__init__()
        self.setObjectName("PhotoQuiet")
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.setIconSize(QSize(16, 16))
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)

    def paintEvent(self, _e) -> None:  # noqa: N802
        u = _ui(self)
        p = QPainter(self)
        r = QRectF(self.rect())
        if self.underMouse() or self.isDown():
            _round(p, r, u["hover"], None, 6)
        if self.hasFocus() and getattr(self.window(), "keyboard_used", False):
            p.setPen(QPen(u["focus"], 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 5, 5)
        box = QRect(QPoint(), self.iconSize())
        box.moveCenter(self.rect().center())
        p.setOpacity(1.0 if self.underMouse() or self.hasFocus() else 0.55)  # (quiet: the text-3 colour)
        self.icon().paint(p, box, Qt.AlignCenter)


class _NavButton(QToolButton):
    """The viewer's round ‹ › buttons: quiet, shown while the pointer is over the photo (or the button
    has the keyboard); the arrow keys do the same."""

    def paintEvent(self, e) -> None:  # noqa: N802
        if not self.isEnabled():
            return
        host = self.parentWidget()
        if not (host is not None and host.underMouse() or self.hasFocus()):
            return
        u = _ui(self)
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -3)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(u["shadow1"])
        p.drawEllipse(r.translated(0, 1))
        p.setBrush(u["mica"])
        p.setPen(QPen(u["stroke2"], 1))
        p.drawEllipse(r.adjusted(0.5, 0.5, -0.5, -0.5))
        if self.underMouse() or self.isDown():
            p.setPen(Qt.NoPen)
            p.setBrush(u["hover"])
            p.drawEllipse(r)
        if self.hasFocus():
            p.setPen(QPen(u["focus"], 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
        box = QRect(QPoint(), self.iconSize())
        box.moveCenter(r.center().toPoint())
        self.icon().paint(p, box, Qt.AlignCenter)


def _icon_button(tip: str, size: int = 34, kind=QToolButton) -> QToolButton:
    b = kind()
    b.setToolTip(tip)
    b.setAccessibleName(tip)
    b.setIconSize(QSize(18, 18))
    b.setFixedSize(size, size)
    b.setCursor(Qt.PointingHandCursor)
    return b


class _OverlayBar(QScrollBar):
    """A scroll bar that floats over the photos instead of taking a column beside them (as in Windows
    11's own apps): a thin line while you scroll or bring the pointer near, wider under the pointer,
    gone after a moment. It mirrors the area's own (hidden) bar, so the wheel, the keys and the window
    keep moving that one."""

    WIDTH = 14

    def __init__(self, area: QAbstractScrollArea, parent: QWidget) -> None:
        super().__init__(Qt.Vertical, parent)
        self.setObjectName("PhotoOverlayBar")
        self.area = area
        self.src = area.verticalScrollBar()
        area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedWidth(self.WIDTH)
        self.setAttribute(Qt.WA_Hover)
        self.setFocusPolicy(Qt.NoFocus)
        self.level = 0.0
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(160)
        self._fade.valueChanged.connect(self._faded)
        self._idle = QTimer(self, singleShot=True, interval=1100)
        self._idle.timeout.connect(self._maybe_hide)
        self.src.rangeChanged.connect(self._sync)
        self.src.valueChanged.connect(self._moved)
        self.valueChanged.connect(self._push)
        area.viewport().installEventFilter(self)
        self._sync()

    def _sync(self, *_a) -> None:
        self.blockSignals(True)
        self.setRange(self.src.minimum(), self.src.maximum())
        self.setPageStep(self.src.pageStep())
        self.setSingleStep(self.src.singleStep())
        self.setValue(self.src.value())
        self.blockSignals(False)
        self.update()

    def _moved(self, v: int) -> None:
        self.blockSignals(True)
        self.setValue(v)
        self.blockSignals(False)
        if self.area.isVisible() and self.src.maximum() > self.src.minimum():
            self.poke()
        self.update()

    def _push(self, v: int) -> None:
        if self.src.value() != v:
            self.src.setValue(v)

    def poke(self) -> None:
        self._to(1.0)
        self._idle.start()

    def _maybe_hide(self) -> None:
        if self.underMouse() or self.isSliderDown():
            self._idle.start()
            return
        self._to(0.0)

    def _to(self, level: float) -> None:
        if self._fade.endValue() == level and self._fade.state() == QVariantAnimation.Running:
            return
        if not _animations_on():
            self._faded(level)
            return
        self._fade.stop()
        self._fade.setStartValue(float(self.level))
        self._fade.setEndValue(float(level))
        self._fade.start()

    def _faded(self, v) -> None:
        self.level = float(v)
        self.update()

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() == QEvent.MouseMove and obj.width() - e.position().x() < 28 and self.src.maximum() > self.src.minimum():
            self.poke()
        return False

    def enterEvent(self, e) -> None:  # noqa: N802
        self.poke()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._idle.start()
        super().leaveEvent(e)

    def wheelEvent(self, e) -> None:  # noqa: N802
        QApplication.sendEvent(self.area.viewport(), e)  # (the area's own glide)

    def paintEvent(self, _e) -> None:  # noqa: N802
        if self.level <= 0.01 or self.maximum() <= self.minimum():
            return
        u = _ui(self)
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        handle = QRectF(self.style().subControlRect(QStyle.CC_ScrollBar, opt, QStyle.SC_ScrollBarSlider, self))
        wide = self.underMouse() or self.isSliderDown()
        w = 7.0 if wide else 4.0
        r = QRectF(self.width() - w - 3, handle.top() + 2, w, max(24.0, handle.height() - 4))
        p = QPainter(self)
        p.setOpacity(self.level)
        _round(p, r, u["text2"] if wide else u["text3"], None, w / 2)


class _Corners(QObject):
    """The content pane's rounded corners over the photos that reach its edges: a child widget can't
    be clipped to a rounded shape, so the four corners are drawn again on top, smooth, with the window's
    own tint around them (cut out and put back, so the material still shows through)."""

    def __init__(self, pane: "SoftCard") -> None:
        super().__init__(pane)
        self.pane = pane
        self.parts = [_Corner(pane, k) for k in range(4)]
        pane.installEventFilter(self)

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() in (QEvent.Resize, QEvent.Show):
            self.place()
        return False

    def place(self) -> None:
        w, h = self.pane.width(), self.pane.height()
        s = _Corner.SIZE
        for k, part in enumerate(self.parts):
            part.setGeometry(0 if k in (0, 2) else w - s, 0 if k < 2 else h - s, s, s)
            part.raise_()

    def refresh(self) -> None:
        for part in self.parts:
            part.update()


class _Corner(QWidget):
    SIZE = 12

    def __init__(self, pane, k: int) -> None:
        super().__init__(pane)
        self.k = k
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _e) -> None:  # noqa: N802
        pane = self.parentWidget()
        u = _ui(self)
        path = pane.card_path().translated(-self.x(), -self.y())
        box = QPainterPath()
        box.addRect(QRectF(self.rect()))
        outside = box.subtracted(path)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        tint = u["tint"]
        if tint.alpha() >= 255:
            p.fillPath(outside, tint)
        else:  # the photo out, the window's tint in (partly, along the smooth edge)
            p.setCompositionMode(QPainter.CompositionMode_DestinationOut)
            p.fillPath(outside, QColor(0, 0, 0, 255))
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            p.fillPath(outside, tint)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        stroke = pane.stroke
        if stroke.alpha():
            p.setPen(QPen(stroke, 1))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)


class _HeaderBand(QWidget):
    """The title over the photos: the material fading out downwards, so the photos pass softly under it."""

    def __init__(self, win: "PhotosWindow", parent: QWidget) -> None:
        super().__init__(parent)
        self.win = win

    def paintEvent(self, _e) -> None:  # noqa: N802
        u = _ui(self)
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, u["head"])
        g.setColorAt(0.55, _alpha(u["head"], u["head"].alphaF() * 0.62))  # (eased: the title stays clear)
        g.setColorAt(1, _alpha(u["head"], 0))
        p.fillRect(self.rect(), g)

    def wheelEvent(self, e) -> None:  # noqa: N802
        inner = self.win.inner.currentWidget()
        if isinstance(inner, QAbstractScrollArea):
            QApplication.sendEvent(inner.viewport(), e)


# --- the grid --------------------------------------------------------------------------------------
class PhotoModel(QAbstractListModel):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[lib.PhotoItem] = []

    def set_items(self, items: list[lib.PhotoItem]) -> None:
        self.beginResetModel()
        self.items = items
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.items)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        it = self.items[index.row()]
        if role == ITEM:
            return it
        if role in (Qt.AccessibleTextRole, Qt.ToolTipRole):
            kind = t("Video") if it.is_video else t("Live Photo") if it.live else t("Photo")
            text = f"{kind}, {_day(it.date)} {it.date:%H:%M}, {it.name}"
            if role == Qt.ToolTipRole:
                from .safe_text import plain_tip

                return plain_tip(text)
            return text
        return None


class Thumbs:
    """Pictures of the photos that are (or were just) on screen, kept in memory a while."""

    # Bytes of pictures kept, the oldest going first. The grid's small pictures and the big ones (the
    # viewer's neighbours, the Years and Months cards) are kept apart, so looking at a few big ones
    # never pushes out the whole grid. Coming back to pictures already gone is quick all the same:
    # Windows keeps its own previews on disk (see photo_images).
    BUDGET = {False: 200 * 1024 * 1024, True: 100 * 1024 * 1024}  # big: True
    STEP = 80  # pictures are made in sizes of 160, 240, … 640 pixels: never much more than shown

    def __init__(self, loader: ImageLoader) -> None:
        self.loader = loader
        self.cache: dict[bool, OrderedDict[tuple[str, int], QPixmap]] = {False: OrderedDict(), True: OrderedDict()}
        self.used = {False: 0, True: 0}
        self.missing: set[tuple[str, int]] = set()
        self.seen: set[str] = set()

    @classmethod
    def bucket(cls, px: float) -> int:
        return max(160, min(640, -(-int(px) // cls.STEP) * cls.STEP))

    @staticmethod
    def _big(size: int) -> bool:
        return size > 400

    def get(self, it: lib.PhotoItem, px: float, ahead: bool = False) -> QPixmap | None:
        b = self.bucket(px)
        key = (it.key, b)
        self.seen.add(it.key)
        pool = self.cache[self._big(b)]
        pm = pool.get(key)
        if pm is not None:
            pool.move_to_end(key)
            return pm
        best = None  # a sharper or rougher one meanwhile: the closest
        for other in range(640, 159, -self.STEP):
            got = self.cache[self._big(other)].get((it.key, other))
            if got is not None and (best is None or abs(other - b) < abs(best[0] - b)):
                best = (other, got)
        if key not in self.missing:
            self.loader.request(it.key, it.path, b, ahead=ahead)
        return best[1] if best else None

    def put(self, key: str, size: int, img: QImage) -> None:
        if img.isNull():
            self.missing.add((key, size))
            return
        img = fit_short_side(img, size)  # (already done by the loader; kept for pictures from elsewhere)
        big = self._big(size)
        pool = self.cache[big]
        old = pool.pop((key, size), None)
        if old is not None:
            self.used[big] -= self._bytes(old)
        pm = QPixmap.fromImage(img)
        pool[(key, size)] = pm
        self.used[big] += self._bytes(pm)
        while self.used[big] > self.BUDGET[big] and len(pool) > 1:
            _k, gone = pool.popitem(last=False)
            self.used[big] -= self._bytes(gone)

    @staticmethod
    def _bytes(pm: QPixmap) -> int:
        return pm.width() * pm.height() * 4

    def retry_missing(self) -> None:
        """Pictures that couldn't be made are tried again (the file may be there now)."""
        self.missing.clear()

    def clear(self) -> None:
        for big in (False, True):
            self.cache[big].clear()
            self.used[big] = 0
        self.missing.clear()


def _cover(p: QPainter, pm: QPixmap, r: QRectF) -> None:
    """Fill r with the picture, cut to fit (as the square tiles on an iPhone)."""
    sw, sh = pm.width(), pm.height()
    if sw <= 0 or sh <= 0:
        return
    scale = max(r.width() / sw, r.height() / sh)
    w, h = r.width() / scale, r.height() / scale
    p.drawPixmap(r, pm, QRectF((sw - w) / 2, (sh - h) / 2, w, h))


def _heart(r: QRectF) -> QPainterPath:
    path = QPainterPath()
    x, y, w, h = r.x(), r.y(), r.width(), r.height()
    path.moveTo(x + w / 2, y + h * 0.95)
    path.cubicTo(x - w * 0.15, y + h * 0.45, x + w * 0.15, y - h * 0.15, x + w / 2, y + h * 0.28)
    path.cubicTo(x + w * 0.85, y - h * 0.15, x + w * 1.15, y + h * 0.45, x + w / 2, y + h * 0.95)
    return path


def _favorite_icon(color: str) -> QIcon:
    icon = QIcon()
    for dpr in (1, 2, 3):
        pm = QPixmap(18 * dpr, 18 * dpr)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillPath(_heart(QRectF(2, 2, 14, 14)), QColor(color))
        painter.end()
        icon.addPixmap(pm)
    return icon


def _live_mark(p: QPainter, c: QPointF, r: float, color: QColor) -> None:
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(color, max(1.0, r / 6)))
    p.drawEllipse(c, r * 0.35, r * 0.35)
    p.drawEllipse(c, r, r)
    pen = QPen(color, max(1.0, r / 7))
    pen.setStyle(Qt.DotLine)
    p.setPen(pen)
    p.drawEllipse(c, r * 0.68, r * 0.68)


from PySide6.QtCore import QPointF  # noqa: E402  (used by the painters above and below)


class TileDelegate(QStyledItemDelegate):
    def __init__(self, win: "PhotosWindow") -> None:
        super().__init__(win)
        self.win = win

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        s = self.win.tile
        return QSize(s, s)

    def paint(self, p: QPainter, option, index) -> None:
        it: lib.PhotoItem = index.data(ITEM)
        if it is None:
            return
        win, c = self.win, self.win.c
        r = QRectF(option.rect).adjusted(0, 0, -GAP, -GAP)
        edge = win.grid.viewport().width()
        if option.rect.right() + win.tile > edge:  # the last in its row: to the pane's edge (no strip left over)
            r.setRight(edge)
        cols = max(1, edge // max(1, win.tile))
        if index.row() // cols == (index.model().rowCount() - 1) // cols:  # the last row: to the bottom edge
            r.setBottom(option.rect.bottom() + 1)
        p.save()
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        dpr = win.devicePixelRatioF()
        pm = win.thumbs.get(it, r.width() * dpr)
        if pm is None:
            p.fillRect(r, QColor(c["tile"]))
        else:
            _cover(p, pm, r)
        white = QColor("#ffffff")
        shadow = QColor(0, 0, 0, 140)
        small = r.width() < 70
        f = QFont(p.font())
        f.setPixelSize(max(10, min(13, int(r.width() / 9))))
        f.setBold(True)
        p.setFont(f)
        selected = option.state & QStyle.State_Selected
        if it.is_video and not win.selecting and not small:  # (the selection circle sits there)
            text = _duration(it.duration)
            if text:
                tr = r.adjusted(4, 0, -6, -4)
                p.setPen(shadow)
                p.drawText(tr.translated(0, 1), Qt.AlignRight | Qt.AlignBottom, text)
                p.setPen(white)
                p.drawText(tr, Qt.AlignRight | Qt.AlignBottom, text)
        if not small and r.width() >= 90:
            if it.live:
                _live_mark(p, QPointF(r.left() + 13, r.top() + 13), 7, white)
            if it.favorite:
                p.setPen(Qt.NoPen)
                p.setBrush(white)
                p.drawPath(_heart(QRectF(r.left() + 6, r.bottom() - 18, 13, 12)))
        if selected:
            p.fillRect(r, QColor(255, 255, 255, 50))
        if win.selecting or selected:
            cr = QRectF(r.right() - 26, r.bottom() - 26, 20, 20)
            p.setPen(QPen(white, 2))
            p.setBrush(QColor(c["accent"]) if selected else QColor(0, 0, 0, 60))
            p.setRenderHint(QPainter.Antialiasing)
            p.drawEllipse(cr)
            if selected:
                path = QPainterPath()
                path.moveTo(cr.left() + 5.5, cr.center().y())
                path.lineTo(cr.left() + 9, cr.bottom() - 6)
                path.lineTo(cr.right() - 5, cr.top() + 6)
                p.setPen(QPen(QColor(c["accent_text"]), 2))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
        if option.state & QStyle.State_HasFocus and win.grid.hasFocus() and win.keyboard_used:
            p.setPen(QPen(QColor(c["focus_ring"]), 3))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(1.5, 1.5, -1.5, -1.5))
        p.restore()


def _animations_on() -> bool:
    """Windows' "Animation effects" (Settings → Accessibility → Visual effects): off, nothing glides."""
    try:
        import ctypes

        on = ctypes.c_int(1)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0):  # CLIENTAREAANIMATION
            return bool(on.value)
    except (AttributeError, OSError):
        pass
    return True


class SmoothScroll(QObject):
    """The mouse wheel glides instead of jumping, as on an iPad: each notch moves the view a little
    further and eases out, and notches in quick succession add up. A touchpad that sends pixels already
    moves smoothly and is followed as it is. Ctrl + wheel (the size) is left to the view."""

    DURATION = 320  # ms

    def __init__(self, area: QAbstractItemView | QScrollArea, step: int = 50) -> None:
        super().__init__(area)
        self.bar = area.verticalScrollBar()
        self.step = step
        self.target: float | None = None
        self._moving = False
        self.anim = QVariantAnimation(self)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.valueChanged.connect(self._to)
        self.anim.finished.connect(self._done)
        self.bar.valueChanged.connect(self._moved)
        area.viewport().installEventFilter(self)

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() != QEvent.Wheel or e.modifiers() & Qt.ControlModifier:
            return False
        dy = e.angleDelta().y()
        if not dy or abs(e.angleDelta().x()) > abs(dy):
            return False
        if not e.pixelDelta().isNull():  # a touchpad: it already moves in pixels
            self.stop()
            self.bar.setValue(self.bar.value() - e.pixelDelta().y())
            return True
        self.scroll_by(-dy / 120 * QApplication.wheelScrollLines() * self.step)
        return True

    def scroll_by(self, dy: float) -> None:
        start = self.target if self.target is not None else float(self.bar.value())
        target = max(float(self.bar.minimum()), min(float(self.bar.maximum()), start + dy))
        if not _animations_on():
            self.stop()
            self.bar.setValue(round(target))
            return
        self.target = target
        self.anim.stop()
        self.anim.setDuration(self.DURATION)
        self.anim.setStartValue(float(self.bar.value()))
        self.anim.setEndValue(target)
        self.anim.start()

    def stop(self) -> None:
        self.anim.stop()
        self.target = None

    def _to(self, v) -> None:
        self._moving = True
        try:
            self.bar.setValue(round(v))
        finally:
            self._moving = False

    def _moved(self, _v) -> None:
        if not self._moving and self.anim.state() == QVariantAnimation.Running:
            self.stop()  # the window (or the scroll bar) moved it meanwhile: that wins

    def _done(self) -> None:
        self.target = None


class MouseNav(QObject):
    """The mouse's side buttons (Back, Forward) anywhere in the window: the photos, the viewer and the
    cards all take mouse clicks themselves, so these are caught before them. Some mice send them as
    keys instead; those too."""

    def __init__(self, win: "PhotosWindow") -> None:
        super().__init__(win)
        self.win = win
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, e):  # noqa: N802
        kind = e.type()
        if kind == QEvent.MouseButtonPress:
            button = e.button()
            if button not in (Qt.BackButton, Qt.ForwardButton):
                return False
            forward = button == Qt.ForwardButton
        elif kind == QEvent.KeyPress:
            if e.key() not in (Qt.Key_Back, Qt.Key_Forward):
                return False
            forward = e.key() == Qt.Key_Forward
        elif kind in (QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick):
            return e.button() in (Qt.BackButton, Qt.ForwardButton) and self._ours(obj)
        else:
            return False
        if not self._ours(obj):
            return False
        self.win._mouse_nav(forward)
        return True

    def _ours(self, obj) -> bool:
        return (isinstance(obj, QWidget) and isValid(self.win) and obj.window() is self.win
                and not self.win.closed)

    def stop(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)


class Grid(QListView):
    zoomed = Signal(int, QPoint)  # +1 larger / -1 smaller, mouse position

    def __init__(self, win: "PhotosWindow") -> None:
        super().__init__()
        self.win = win
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setLayoutMode(QListView.SinglePass)  # (uniform tiles: quick, and every photo has its place at once)
        self.setSpacing(0)
        self.setFrameShape(QFrame.NoFrame)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.verticalScrollBar().setSingleStep(40)
        self.setAccessibleName(t("Photos"))
        self._wheel = 0
        self.smooth = SmoothScroll(self)

    def updateGeometries(self) -> None:  # noqa: N802
        """The photos start below the title band and scroll on under it: the scroll range begins
        HEAD pixels before the first row (the band floats over the top of the view)."""
        super().updateGeometries()
        sb = self.verticalScrollBar()
        content = self.contentsSize().height()
        sb.setRange(-HEAD, max(-HEAD, content - self.viewport().height()))

    def scrollTo(self, index, hint=QAbstractItemView.EnsureVisible) -> None:  # noqa: N802
        super().scrollTo(index, hint)
        if hint == QAbstractItemView.EnsureVisible and index.isValid():
            top = self.visualRect(index).top()
            if top < HEAD:  # (not under the title band)
                sb = self.verticalScrollBar()
                sb.setValue(sb.value() - (HEAD - top))

    def wheelEvent(self, e) -> None:  # noqa: N802
        if e.modifiers() & Qt.ControlModifier:
            self._wheel += e.angleDelta().y()
            if abs(self._wheel) >= 120:  # a touchpad sends many small steps: one size per notch
                self.zoomed.emit(1 if self._wheel > 0 else -1, e.position().toPoint())
                self._wheel = 0
            e.accept()
            return
        super().wheelEvent(e)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.win.keyboard_used = False
        idx = self.indexAt(e.position().toPoint())
        mods = e.modifiers()
        if e.button() != Qt.LeftButton and not self.win.selecting:
            if idx.isValid():  # (the right-click menu is for this photo; nothing gets selected)
                self.selectionModel().setCurrentIndex(idx, QItemSelectionModel.NoUpdate)
            return
        if e.button() == Qt.LeftButton and idx.isValid() and not self.win.selecting and not (
                mods & (Qt.ControlModifier | Qt.ShiftModifier)):
            self._open_on_release = idx.data(ITEM).asset_id
            return
        self._open_on_release = None
        if e.button() == Qt.LeftButton and not idx.isValid() and not (mods & (Qt.ControlModifier | Qt.ShiftModifier)):
            return  # between or below the photos: nothing (never a rubber band that selects hundreds)
        if e.button() == Qt.LeftButton and idx.isValid() and self.win.selecting and not (
                mods & (Qt.ControlModifier | Qt.ShiftModifier)):
            self.selectionModel().select(idx, QItemSelectionModel.Toggle)
            self.selectionModel().setCurrentIndex(idx, QItemSelectionModel.NoUpdate)
            self.win._selection_changed()
            return
        super().mousePressEvent(e)
        if mods & (Qt.ControlModifier | Qt.ShiftModifier) and not self.win.selecting:
            self.win.set_selecting(True)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if e.buttons() & Qt.LeftButton and not (e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)):
            return  # dragging over the photos selects nothing by itself
        super().mouseMoveEvent(e)

    def resizeEvent(self, e) -> None:  # noqa: N802
        """The window got larger or smaller (maximized, say): the same photos stay in view."""
        win = self.win
        if getattr(win, "inner", None) is None:  # (still being built)
            super().resizeEvent(e)
            return
        anchor = win._anchor() if win.inner.currentWidget() is self and win.model.items else None
        end = anchor is not None and win._at_end()
        self.smooth.stop()
        super().resizeEvent(e)
        win._layout_grid()
        if anchor is not None:
            self.doItemsLayout()
            if end:
                self.scrollToBottom()
            else:
                win._restore_anchor(anchor)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        pressed = getattr(self, "_open_on_release", None)
        self._open_on_release = None
        idx = self.indexAt(e.position().toPoint())
        if pressed is not None and idx.isValid() and idx.data(ITEM).asset_id == pressed:
            self.win.open_viewer(idx.row())
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        self.win.keyboard_used = True
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self.currentIndex().isValid():
            self.win.open_viewer(self.currentIndex().row())
            return
        if e.key() == Qt.Key_Space and self.currentIndex().isValid():
            if not self.win.selecting:
                self.win.set_selecting(True)
            self.selectionModel().select(self.currentIndex(), QItemSelectionModel.Toggle)
            self.win._selection_changed()
            return
        super().keyPressEvent(e)
        if e.modifiers() & Qt.ShiftModifier and not self.win.selecting and self.selectionModel().hasSelection():
            self.win.set_selecting(True)

    def selectionCommand(self, index, event=None):  # noqa: N802
        """Moving with the arrow keys (or the window moving the current photo) never selects: only
        Select, Space, Ctrl or Shift do, as on an iPad."""
        if event is None or event.type() == QEvent.KeyPress and not (
                event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier)):
            return QItemSelectionModel.NoUpdate
        return super().selectionCommand(index, event)

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        idx = self.indexAt(e.pos())
        if not idx.isValid():
            return
        chosen = self.win.selecting and self.selectionModel().isSelected(idx)
        self.win.context_menu(self.win.chosen_items() if chosen else [idx.data(ITEM)], e.globalPos())


# --- Years and Months --------------------------------------------------------------------------------
@pressable  # (a screen reader gets a button to press; Enter/Space open it too)
class Card(QWidget):
    clicked = Signal()

    def __init__(self, win: "PhotosWindow", title: str, caption: str, covers: list[lib.PhotoItem],
                 mosaic: bool) -> None:
        super().__init__()
        self.win, self.title, self.caption, self.covers, self.mosaic = win, title, caption, covers, mosaic
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(f"{title}, {caption}")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        return int(w * (10 / 16 if self.mosaic else 9 / 16))

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def resizeEvent(self, e) -> None:  # noqa: N802
        self.setFixedHeight(self.heightForWidth(self.width()))
        super().resizeEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        c = self.win.c
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r, 12, 12)
        p.setClipPath(clip)
        p.fillRect(r, QColor(c["tile"]))
        dpr = self.devicePixelRatioF()
        if self.mosaic and len(self.covers) >= 3:
            big = QRectF(r.left(), r.top(), r.width() * 2 / 3 - 1, r.height())
            small = [QRectF(big.right() + 2, r.top(), r.right() - big.right() - 2, r.height() / 2 - 1),
                     QRectF(big.right() + 2, r.top() + r.height() / 2 + 1, r.right() - big.right() - 2,
                            r.height() / 2 - 1)]
            rects = [big] + small
        else:
            rects = [r]
        for it, rr in zip(self.covers, rects):
            pm = self.win.thumbs.get(it, max(rr.width(), rr.height()) * dpr)
            if pm is not None:
                _cover(p, pm, rr)
        g = QLinearGradient(0, max(0.0, r.height() - 96), 0, r.height())  # (the design: 30 px over the text)
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(1, QColor(0, 0, 0, 153))
        p.fillRect(r, g)
        p.setPen(QColor("#ffffff"))
        f = QFont(self.font())
        f.setFamilies(["Segoe UI Variable Display", "Segoe UI"])
        f.setPixelSize(28 if not self.mosaic else 20)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.drawText(r.adjusted(16, 0, -16, -32), Qt.AlignLeft | Qt.AlignBottom, self.title)
        f = QFont(self.font())
        f.setPointSizeF(9.375)  # 12.5 px
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 217))
        p.drawText(r.adjusted(16, 0, -16, -12), Qt.AlignLeft | Qt.AlignBottom, self.caption)
        if self.hasFocus():
            p.setClipping(False)
            p.setPen(QPen(QColor(c["focus_ring"]), 3))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), 11, 11)


def _key_photos(items: list[lib.PhotoItem], n: int) -> list[lib.PhotoItem]:
    """A month's or year's cover: favourites first, then photos spread over it (no screenshots)."""
    pool = [i for i in items if not i.screenshot] or items
    fav = [i for i in pool if i.favorite]
    rest = [i for i in pool if not i.favorite]
    out = fav[:n]
    if len(out) < n and rest:
        step = max(1, len(rest) // (n - len(out) + 1))
        out += rest[step // 2::step][: n - len(out)]
    return out[:n]


# --- the viewer ------------------------------------------------------------------------------------
class Picture(QWidget):
    """The photo, fitted to the window. Click: hide everything around it; double-click: 100%."""

    tapped = Signal()

    def __init__(self, win: "PhotosWindow") -> None:
        super().__init__()
        self.win = win
        self.pm: QPixmap | None = None
        self.full = False
        self.zoom = False
        self.offset = QPointF(0, 0)
        self._drag: QPointF | None = None
        self._moved = False
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

    def set_picture(self, pm: QPixmap | None, full: bool) -> None:
        self.pm, self.full = pm, full
        if not full:
            self.zoom = False
            self.offset = QPointF(0, 0)
        self.update()

    def _room(self) -> QRectF:
        """Where the fitted photo may go: 60 px at the sides (the ‹ › buttons sit there), a little above
        and below for its shadow; less when only the photo is shown."""
        r = QRectF(self.rect())
        if not self.win.vtop.isVisible():
            return r.adjusted(12, 12, -12, -12)
        side = 60 if r.width() >= 480 else 16
        return r.adjusted(side, 2, -side, -18)  # (room for its shadow above the strip)

    def _target(self) -> QRectF:
        pm = self.pm
        if pm is None or pm.isNull():
            return QRectF(self.rect())
        if self.zoom:  # 100 %: one pixel of the photo on one pixel of the screen
            tw, th = pm.width() / self.devicePixelRatioF(), pm.height() / self.devicePixelRatioF()
            r = QRectF(self.rect())
        else:
            r = self._room()
            dpr = pm.devicePixelRatio() or 1
            w, h = pm.width() / dpr, pm.height() / dpr
            scale = min(r.width() / w, r.height() / h) if w and h else 1.0
            tw, th = w * scale, h * scale
        x = r.center().x() - tw / 2 + self.offset.x()
        y = r.center().y() - th / 2 + self.offset.y()
        return QRectF(x, y, tw, th)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        if self.pm is not None and not self.pm.isNull():
            target = self._target()
            if not self.zoom:  # rounded 6, on a soft shadow, as in the design
                u = _ui(self)
                _shadow(p, target, 6, 24, 8, u["shadow2"])
                _shadow(p, target, 6, 3, 1, u["shadow1"])
                frame = QPainterPath()
                frame.addRoundedRect(target, 6, 6)
                p.setClipPath(frame)
            p.drawPixmap(target, self.pm, QRectF(self.pm.rect()))
        elif self.win.viewer_message:
            p.setPen(QColor(self.win.viewer_fg()))
            p.drawText(QRectF(self.rect()).adjusted(40, 0, -40, 0), Qt.AlignCenter | Qt.TextWordWrap,
                       self.win.viewer_message)

    def enterEvent(self, e) -> None:  # noqa: N802
        super().enterEvent(e)
        self._arrows()

    def leaveEvent(self, e) -> None:  # noqa: N802
        super().leaveEvent(e)
        self._arrows()

    def _arrows(self) -> None:
        for b in (self.win.prev, self.win.next):  # (shown while the pointer is over the photo)
            b.update()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton:
            self._drag = e.position()
            self._moved = False

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drag is not None and self.zoom:
            d = e.position() - self._drag
            if abs(d.x()) + abs(d.y()) > 3:
                self._moved = True
            self.offset += d
            self._drag = e.position()
            self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if getattr(self, "_after_double", False):  # the end of a double-click: not a tap as well
            self._after_double = False
        elif e.button() == Qt.LeftButton and not getattr(self, "_moved", False):
            QTimer.singleShot(QApplication.doubleClickInterval(), self, self._single)
            self._pending_single = True
        self._drag = None

    def _single(self) -> None:
        if getattr(self, "_pending_single", False):
            self._pending_single = False
            self.tapped.emit()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        self._pending_single = False
        self._after_double = True
        opened = getattr(self.win, "_viewer_opened_at", 0.0)
        if time.monotonic() - opened < QApplication.doubleClickInterval() / 1000 + 0.15:
            return  # the second click of a double-click on a photo in the grid: it only opens it
        self.toggle_zoom()

    def toggle_zoom(self) -> None:
        if self.pm is None:
            return
        self.zoom = not self.zoom
        self.offset = QPointF(0, 0)
        self.win.want_full()
        self.update()

    def wheelEvent(self, e) -> None:  # noqa: N802
        if e.modifiers() & Qt.ControlModifier:
            if (e.angleDelta().y() > 0) != self.zoom:
                self.toggle_zoom()
            e.accept()
            return
        super().wheelEvent(e)


class FilmStrip(QWidget):
    """The photos around the one shown, centred under it: 30 x 30 (corners 4, 3 apart, a little faded),
    the current one 40 wide at full strength. Only as many as fit, so none is ever cut at the edge."""

    picked = Signal(int)
    THUMB, WIDE, GAP, AROUND = 30.0, 40.0, 3.0, 7

    def __init__(self, win: "PhotosWindow") -> None:
        super().__init__()
        self.win = win
        self.setFixedHeight(46)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover)
        self._hits: list[tuple[QRectF, int]] = []
        self._hover: QPointF | None = None

    def span(self) -> tuple[int, int]:
        """The first and last photo shown: up to AROUND either side, fewer when the strip is narrow."""
        items, cur = self.win.viewer_items, self.win.viewer_row
        room = max(0.0, self.width() - 24 - self.WIDE)
        around = min(self.AROUND, int(room / (self.THUMB + self.GAP) / 2))
        return max(0, cur - around), min(len(items) - 1, cur + around)

    def paintEvent(self, _e) -> None:  # noqa: N802
        win = self.win
        items, cur = win.viewer_items, win.viewer_row
        self._hits = []
        if not items:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        lo, hi = self.span()
        total = (hi - lo) * (self.THUMB + self.GAP) + self.WIDE
        x = (self.width() - total) / 2
        y = (self.height() - self.THUMB) / 2
        dpr = self.devicePixelRatioF()
        for k in range(lo, hi + 1):
            w = self.WIDE if k == cur else self.THUMB
            rr = QRectF(x, y, w, self.THUMB)
            x += w + self.GAP
            pm = win.thumbs.get(items[k], 160 if k != cur else max(160.0, self.WIDE * dpr))
            path = QPainterPath()
            path.addRoundedRect(rr, 4, 4)
            p.save()
            p.setClipPath(path)
            on = k == cur or self._hover is not None and rr.contains(self._hover)
            p.setOpacity(1.0 if on else 0.6)
            if pm is None:
                p.fillRect(rr, QColor(win.c["tile"]))
            else:
                _cover(p, pm, rr)
            p.restore()
            self._hits.append((rr, k))

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        self._hover = e.position()
        self.update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = None
        self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        for rr, k in self._hits:
            if rr.adjusted(-1.5, -10, 1.5, 10).contains(e.position()):
                self.picked.emit(k)
                return


class SideDelegate(NavDelegate):
    """Sidebar rows as in the design: 34 px, the icon and the name, the count in mono on the right; the
    chosen one a lighter layer with a 3 px accent bar; section titles 600 12 px in the quiet colour."""

    def __init__(self, win: "PhotosWindow") -> None:
        super().__init__(win)
        self.win = win

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(200, 32 if index.data(Qt.UserRole) is None else 34)

    def paint(self, p: QPainter, option, index) -> None:
        u = _ui(self.win)
        r = QRectF(option.rect)
        key = index.data(Qt.UserRole)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont(option.font)
        if key is None:  # a section title
            f.setWeight(QFont.DemiBold)
            f.setPixelSize(12)
            p.setFont(f)
            p.setPen(u["text3"])
            p.drawText(r.adjusted(10, 0, -8, -6), Qt.AlignLeft | Qt.AlignBottom, index.data(Qt.DisplayRole))
            p.restore()
            return
        sel = option.state & QStyle.State_Selected
        hover = option.state & QStyle.State_MouseOver
        if sel or hover:
            _round(p, r, u["layer2"] if sel else u["hover"], None, 6)
        if sel:
            p.setPen(Qt.NoPen)
            p.setBrush(u["accent"])
            p.drawRoundedRect(QRectF(r.left(), r.top() + 9, 3, r.height() - 18), 1.5, 1.5)
        icon = index.data(Qt.DecorationRole)
        if isinstance(icon, QIcon):
            box = QRect(int(r.left()) + 10, int(r.center().y() - 9 + 0.5), 18, 18)
            icon.paint(p, box, Qt.AlignCenter)
        count = index.data(Qt.UserRole + 1) or ""
        cf = count_font(f)
        p.setFont(cf)
        cw = QFontMetricsF(cf).horizontalAdvance(count)
        p.setPen(u["text3"])
        p.drawText(r.adjusted(0, 0, -10, 0), Qt.AlignRight | Qt.AlignVCenter, count)
        p.setFont(f)
        p.setPen(u["text"])
        text_box = r.adjusted(38, 0, -(cw + 18), 0)
        label = QFontMetricsF(f).elidedText(index.data(Qt.DisplayRole) or "", Qt.ElideRight, text_box.width())
        p.drawText(text_box, Qt.AlignLeft | Qt.AlignVCenter, label)
        if option.state & QStyle.State_HasFocus and self.win.side.hasFocus() and self.win.keyboard_used:
            p.setPen(QPen(u["focus"], 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 6, 6)
        p.restore()


# --- pictures kept a while after the window closes -----------------------------------------------
# Opening the window again soon shows the photos at once. Kept only for the same photos folder,
# dropped after WARM_FOR, and at once when the account signs out (PhotosWindow.clear).
WARM_FOR = 10 * 60 * 1000  # ms
_WARM: dict = {}


def _keep_warm(root: str, thumbs: "Thumbs") -> None:
    drop_warm()
    _WARM.update(root=root, cache=thumbs.cache, used=thumbs.used)
    thumbs.cache = {False: OrderedDict(), True: OrderedDict()}
    thumbs.used = {False: 0, True: 0}
    app = QApplication.instance()
    if app is not None:
        timer = QTimer(app)
        timer.setSingleShot(True)
        timer.timeout.connect(drop_warm)
        timer.start(WARM_FOR)
        _WARM["timer"] = timer


def _take_warm(root: str, thumbs: "Thumbs") -> None:
    if _WARM.get("root") == root:
        thumbs.cache, thumbs.used = _WARM["cache"], _WARM["used"]
        _WARM.pop("cache", None)
    drop_warm()


def drop_warm() -> None:
    timer = _WARM.pop("timer", None)
    if timer is not None and isValid(timer):
        timer.stop()
        timer.deleteLater()
    _WARM.clear()


# --- the window ------------------------------------------------------------------------------------
class _Bridge(QObject):
    """Results of work done off the window's thread, delivered on it."""

    library = Signal(object, object)  # fingerprint, items (None: couldn't be read)
    deleted = Signal(object, object)  # deleted asset ids, [(asset id, why not)]


class PhotosWindow(LibraryDialog):
    """source() gives (photos database, photos folder), or None when nobody is signed in."""

    favoriteRequested = Signal(list, bool)  # asset ids, favourite
    RELOAD_GAP = 10.0  # seconds at least between two reads of the library while the sync adds photos

    # kept while Skyfold runs
    zoom_level = ZOOM_DEFAULT
    sidebar_open = True

    def __init__(self, palette: dict[str, str], source, settings) -> None:
        super().__init__(t("Photos") + " – Skyfold")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)
        self.setMinimumSize(520, 420)  # (narrower than the sidebar needs: it then steps aside, see NARROW)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.c = palette
        self.source = source
        self.settings = settings  # callable: Skyfold's current settings
        self.items: list[lib.PhotoItem] = []
        self.by_id: dict[str, lib.PhotoItem] = {}
        self.current: list[lib.PhotoItem] = []
        self.set_key = "library"
        self.view = "all"
        self.query = ""
        self.selecting = False
        self.keyboard_used = False
        self.tile = 120
        self.viewer_items: list[lib.PhotoItem] = []
        self.viewer_row = 0
        self.viewer_message = ""
        self._full: dict[str, QPixmap] = {}
        self._fingerprint = None
        self._loading = False
        self._loaded_at = 0.0
        self._pending_fav: dict[str, bool] = {}  # hearts iCloud hasn't confirmed yet
        self._fav_confirmed: dict[str, bool] = {}  # meanwhile: what iCloud has (a failure goes back to it)
        self._deleting = False
        self._gone: set[str] = set()  # deleted here: kept out of a list read before the delete finished
        self._gone_paths: dict[str, list] = {}  # their files (put back from the Recycle Bin: shown again)
        self._deleted_at = 0.0
        self._load_started = 0.0
        self._forward: list[tuple] = []  # what the mouse's Forward button goes back into
        self.closed = False
        self._mouse = MouseNav(self)
        self._bridge = _Bridge(self)
        self._bridge.library.connect(self._library_loaded)
        self._bridge.deleted.connect(self._deleted)

        self.loader = ImageLoader(self, threads=max(3, min(6, (os.cpu_count() or 4) // 2)))
        self.loader.loaded.connect(self._loaded)
        self.thumbs = Thumbs(self.loader)
        self._signed_out = False
        try:
            src = source()
        except Exception:  # noqa: BLE001
            src = None
        self._root = str(src[1]) if src else None
        if self._root:
            _take_warm(self._root, self.thumbs)
        self._repaint = QTimer(self, singleShot=True, interval=30)
        self._repaint.timeout.connect(self._repaint_now)

        self._build()
        self.apply_palette(palette)
        self.resize(1280, 820)
        self._poll = QTimer(self, interval=3000)
        self._poll.timeout.connect(self.reload)
        self._poll.start()
        QTimer.singleShot(0, self.reload)

    # --- building ------------------------------------------------------------------------------
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 4, 8, 8)
        root.setSpacing(0)

        self.side = QListWidget()
        self.side.setObjectName("PhotoSide")
        self.side.setIconSize(QSize(18, 18))
        self.side.setFixedWidth(224)
        self.side.setFrameShape(QFrame.NoFrame)
        self.side.setAccessibleName(t("Collections"))
        self.side.setItemDelegate(SideDelegate(self))
        self.side.setMouseTracking(True)
        self.side.itemClicked.connect(self._side_clicked)
        self.side.currentItemChanged.connect(lambda cur, _old: cur and self._side_clicked(cur))
        root.addWidget(self.side)
        self.side.installEventFilter(self)

        self.pages = QStackedWidget()
        pane = self.pane = SoftCard()
        pane.setObjectName("LibraryPane")
        layer = QVBoxLayout(pane)
        layer.setContentsMargins(1, 1, 1, 2)  # the photos reach the pane's hairline (the corners: _Corners)
        layer.addWidget(self.pages)
        root.addWidget(pane, 1)

        # browse page: grid or cards, with the title band and the switch floating over them
        self.browse = QWidget()
        self.pages.addWidget(self.browse)
        self.inner = QStackedWidget(self.browse)
        self.model = PhotoModel()
        self.grid = Grid(self)
        self.grid.setModel(self.model)
        self.grid.setItemDelegate(TileDelegate(self))
        self.grid.zoomed.connect(lambda d, pos: self.set_zoom(PhotosWindow.zoom_level - d, pos))
        self.grid.selectionModel().selectionChanged.connect(lambda *_: self._selection_changed())
        self.grid.verticalScrollBar().valueChanged.connect(lambda *_: self._update_header())
        self.inner.addWidget(self.grid)
        self.cards_area = QScrollArea()
        self.cards_area.setWidgetResizable(True)
        self.cards_area.setFrameShape(QFrame.NoFrame)
        self.cards_area.verticalScrollBar().setSingleStep(40)
        self.cards_area.verticalScrollBar().valueChanged.connect(lambda *_: self._update_header())
        self.cards_smooth = SmoothScroll(self.cards_area, step=60)
        self.inner.addWidget(self.cards_area)
        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.inner.addWidget(self.empty)
        self.grid_bar = _OverlayBar(self.grid, self.browse)
        self.cards_bar = _OverlayBar(self.cards_area, self.browse)
        self.inner.currentChanged.connect(lambda *_: self._place_overlays())

        self.header = _HeaderBand(self, self.browse)
        self.fold = _QuietButton(t("Show or hide the sidebar") + " (Ctrl+B)")
        self.fold.setParent(self.header)
        self.fold.clicked.connect(self.toggle_sidebar)
        self.title = QLabel(t("Library"), self.header)
        self.subtitle = QLabel(self.header)
        self.title.setObjectName("PhotosTitle")
        self.subtitle.setObjectName("PhotosSubtitle")
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Search"))
        self.search.setClearButtonEnabled(True)
        self.search_field = SearchField(self.search, self.c)
        self.search_field.setParent(self.header)
        self.search.setAccessibleName(t("Search photos"))
        self.search.textChanged.connect(self._search_changed)
        self._search_timer = QTimer(self, singleShot=True, interval=220)
        self._search_timer.timeout.connect(self._apply_search)
        self.search.returnPressed.connect(lambda: (self._search_timer.stop(), self._apply_search()))
        self.select_btn = _SoftButton(t("Select"), height=34, radius=6)
        self.select_btn.setParent(self.header)
        self.select_btn.clicked.connect(lambda: self.set_selecting(not self.selecting))

        self.pill = _Pill(self.browse)
        pl = QHBoxLayout(self.pill)
        pl.setContentsMargins(4, 4, 4, 4)
        pl.setSpacing(0)
        self.view_buttons = {}
        for key, label in (("years", t("Years")), ("months", t("Months")), ("all", t("All Photos"))):
            b = _PillButton(label)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.set_view(k))
            pl.addWidget(b)
            self.view_buttons[key] = b
        # the photos' size: − 100% + (the same bar as the text's size in Notes)
        self.zoom_pill = ZoomPill(self.browse, t("Smaller"), t("Larger"))
        self.zoom_out, self.zoom_in = self.zoom_pill.minus, self.zoom_pill.plus
        self.zoom_pill.stepped.connect(lambda step: self.set_zoom(PhotosWindow.zoom_level - step))
        self.zoom_pill.reset.connect(lambda: self.set_zoom(ZOOM_DEFAULT))
        self._zoom_shown()

        self.selbar = _Pill(self.browse)
        sl = QHBoxLayout(self.selbar)
        sl.setContentsMargins(14, 4, 4, 4)
        sl.setSpacing(2)
        self.sel_count = QLabel()
        self.sel_count.setObjectName("PhotosSelCount")
        sl.addWidget(self.sel_count)
        sl.addSpacing(6)
        self._sel_extra = []
        for label, fn in ((t("Select all"), self.select_all), (t("Show in Explorer"), self.show_selected),
                          (t("Copy"), self.copy_selected), (t("Favourite"), self.favorite_selected)):
            b = _PillButton(label)
            b.clicked.connect(fn)
            sl.addWidget(b)
            if fn in (self.show_selected, self.copy_selected):
                self._sel_extra.append(b)
        self.del_btn = _PillButton(t("Delete"), danger=True)
        self.del_btn.clicked.connect(lambda: self.delete_items(self.chosen_items()))
        sl.addWidget(self.del_btn)
        done = _PillButton(t("Done"))
        done.clicked.connect(lambda: self.set_selecting(False))
        sl.addWidget(done)
        self.selbar.hide()

        self.toast = _Toast(self)
        self.toast.setObjectName("PhotosToast")
        self.toast.setWordWrap(True)
        self.toast.setAccessibleName(t("Message"))
        self.toast.hide()
        self._toast_timer = QTimer(self, singleShot=True, interval=4500)
        self._toast_timer.timeout.connect(self.toast.hide)

        # viewer page: on the same see-through material as the rest (every theme), the photo on top
        self.viewer = QWidget()
        self.viewer.setObjectName("PhotosViewer")
        self.pages.addWidget(self.viewer)
        vl = QVBoxLayout(self.viewer)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)
        self.vtop = QWidget()
        self.vtop.setObjectName("PhotosViewerBar")
        tb = QGridLayout(self.vtop)
        tb.setContentsMargins(12, 10, 12, 6)
        tb.setHorizontalSpacing(10)
        for column, stretch in enumerate((1, 2, 1)):
            tb.setColumnStretch(column, stretch)
        self.back = _SoftButton()
        self.back.clicked.connect(self.close_viewer)
        tb.addWidget(self.back, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)
        wl = QVBoxLayout()
        wl.setSpacing(1)
        self.v_date = QLabel()
        self.v_time = QLabel()
        self.v_date.setObjectName("PhotosDate")
        self.v_time.setObjectName("PhotosDateSub")
        for label in (self.v_date, self.v_time):
            label.setAlignment(Qt.AlignCenter)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        wl.addWidget(self.v_date)
        wl.addWidget(self.v_time)
        tb.addLayout(wl, 0, 1)
        self.v_play = _SoftButton(t("Play"), height=34)
        self.v_play.clicked.connect(self.play_current)
        self.v_fav = _SquareButton(t("Favourite") + " (F)")
        self.v_fav.clicked.connect(self.toggle_favorite_current)
        self.v_info = _SquareButton(t("Info") + " (I)")
        self.v_info.setCheckable(True)
        self.v_info.toggled.connect(self._show_info)
        self.v_del = _SquareButton(t("Delete") + " (Del)")
        self.v_del.clicked.connect(lambda: self.delete_items([self.viewer_items[self.viewer_row]]))
        self.v_show = _SoftButton(t("Show in folder"), height=34)
        self.v_show.clicked.connect(lambda: self.show_in_explorer([self.viewer_items[self.viewer_row]]))
        self.v_open = _SoftButton(t("Open"), height=34)
        self.v_open.clicked.connect(lambda: self.open_file(self.viewer_items[self.viewer_row].path))
        self.v_more = _SquareButton(t("More"))
        self.v_more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.v_more)
        menu.addAction(self.v_open.text(), self.v_open.click)
        menu.addAction(self.v_show.text(), self.v_show.click)
        self.v_more.setMenu(menu)
        commands = QHBoxLayout()
        commands.setSpacing(6)
        for w in (self.v_play, self.v_open, self.v_show, self.v_fav, self.v_info, self.v_del, self.v_more):
            commands.addWidget(w)
        tb.addLayout(commands, 0, 2, Qt.AlignRight | Qt.AlignVCenter)
        vl.addWidget(self.vtop)
        mid = QHBoxLayout()
        mid.setSpacing(0)
        self.picture = Picture(self)
        self.picture.tapped.connect(self._toggle_bare)
        mid.addWidget(self.picture, 1)
        self.info = QLabel()
        self.info.setObjectName("PhotosInfo")
        self.info.setWordWrap(True)
        self.info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setFixedWidth(300)
        self.info.hide()
        mid.addWidget(self.info)
        vl.addLayout(mid, 1)
        self.prev = _icon_button(t("Previous") + " (←)", 40, _NavButton)
        self.next = _icon_button(t("Next") + " (→)", 40, _NavButton)
        for b in (self.prev, self.next):
            b.setParent(self.picture)
            b.setObjectName("PhotosNav")
        self.prev.clicked.connect(lambda: self.step(-1))
        self.next.clicked.connect(lambda: self.step(1))
        self.strip = FilmStrip(self)
        self.strip.picked.connect(self.show_row)
        vl.addWidget(self.strip)
        self.corners = _Corners(pane)  # (last: over everything in the pane)

        # keys
        def key(seq, fn):
            s = QShortcut(QKeySequence(seq), self)
            s.activated.connect(fn)
            return s

        key("Ctrl+F", lambda: (self.close_viewer(), self.search.setFocus(), self.search.selectAll()))
        key("Ctrl+B", self.toggle_sidebar)
        key(QKeySequence.ZoomIn, lambda: self._zoom_key(-1))
        key("Ctrl+=", lambda: self._zoom_key(-1))
        key(QKeySequence.ZoomOut, lambda: self._zoom_key(1))
        key("Ctrl+0", lambda: self._zoom_key(0))
        key("Ctrl+A", self.select_all)
        key(QKeySequence.Copy, self.copy_selected)
        key("Alt+Left", self.go_back)

    def _zoom_key(self, d: int) -> None:
        if self.pages.currentWidget() is self.viewer:
            if (d < 0) != self.picture.zoom or d == 0 and self.picture.zoom:
                self.picture.toggle_zoom()
            return
        self.set_zoom(ZOOM_DEFAULT if d == 0 else PhotosWindow.zoom_level + d)

    def _zoom_shown(self) -> None:
        """The bar's percentage: a photo's side against the normal size. A photo is as wide as the
        window divided by the columns, so it is the normal number of columns over the number now
        (3, 5, 7, 9, 13 columns: 167, 100, 71, 56, 38 %)."""
        level = max(0, min(len(COLUMNS) - 1, PhotosWindow.zoom_level))
        self.zoom_pill.show_zoom(round(100 * COLUMNS[ZOOM_DEFAULT] / COLUMNS[level]),
                                 smaller=level < len(COLUMNS) - 1, larger=level > 0)

    # --- look ----------------------------------------------------------------------------------
    def apply_palette(self, palette: dict[str, str]) -> None:
        c = self.c = palette
        u = self._ui = _tokens(c, self.glass)
        mono = "font-family:'Cascadia Mono','Consolas',monospace"
        self.setStyleSheet(self.frame_style() +
            f"QListWidget#PhotoSide {{background:transparent;border:none;padding:4px 8px;outline:0}}"
            f"#PhotoSide::item, #PhotoSide::item:selected, #PhotoSide::item:hover "
            f"{{border:none;background:transparent;color:{c['text']};padding:0}}"
            f"QListView, QScrollArea, QScrollArea > QWidget > QWidget {{background:transparent;border:none}}"
            # the search over the photos: the material, as the floating bars
            f"SoftCard#LibrarySearch {{qproperty-fill:{_css(u['mica'])};qproperty-stroke:{_css(u['stroke2'])}}}"
            f"QPushButton#PhotoButton, QPushButton#PhotoPillButton {{background:transparent;border:none;"
            f"padding:0;min-height:0;font-weight:600}}"
            f"QPushButton#PhotoButton {{font-size:9.375pt}}"  # (12.5 px; the app's buttons are 12 px)
            f"QPushButton#PhotoPillButton {{font-size:13px}}"
            f"QToolButton#PhotoSquare, QToolButton#PhotoQuiet, QToolButton#PhotosNav {{background:transparent;"
            f"border:none;padding:0}}"
            # (a style sheet's sizes win over the widget's own: these keep theirs)
            f"QToolButton#PhotoSquare {{min-width:34px;max-width:34px;min-height:34px;max-height:34px}}"
            f"QToolButton#PhotoQuiet {{min-width:30px;max-width:30px;min-height:30px;max-height:30px}}"
            f"QToolButton#PhotosNav {{min-width:40px;max-width:40px;min-height:40px;max-height:40px}}"
            f"QToolButton#PhotoSquare::menu-indicator {{image:none;width:0}}"
            f"#PhotosToast{{background:transparent;border:none;padding:10px 16px;color:{c['text']}}}"
            f"#PhotosViewer, #PhotosViewerBar {{background:transparent}}"
            f"#PhotosViewer QLabel{{color:{c['text']};background:transparent}}"
            f"QLabel#PhotosTitle {{color:{c['text']};background:transparent;font-family:'Segoe UI Variable Display',"
            f"'Segoe UI';font-size:24px;font-weight:600}}"
            f"QLabel#PhotosSubtitle {{color:{_css(u['text2'])};background:transparent;font-size:9.375pt}}"
            f"QLabel#PhotosSelCount {{color:{_css(u['text2'])};background:transparent;font-size:13px;font-weight:600}}"
            f"QLabel#PhotosDate {{font-family:'Segoe UI Variable Display','Segoe UI';font-size:14px;font-weight:600}}"
            f"QLabel#PhotosDateSub {{{mono};font-size:12px;color:{_css(u['text3'])}}}"
            f"QLabel#PhotosInfo{{background:{_css(u['layer'])};border:none;border-left:1px solid {_css(u['stroke'])};"
            f"padding:16px}}"
            f"QScrollBar#PhotoOverlayBar {{background:transparent;width:14px;margin:2px 0}}"
            f"QScrollBar#PhotoOverlayBar::handle:vertical {{background:transparent;min-height:30px}}")
        self.empty.setStyleSheet(f"color:{_css(u['text2'])};font-size:15px;background:transparent")
        self.search_field.apply_palette(c)
        self.hearts = (glyph("heart", c["text"], 16), _favorite_icon(service_color(c, "photo")))
        for button, name, colour, size in ((self.fold, "layout", u["text"], 16), (self.back, "back", u["text"], 14),
                                           (self.v_info, "info", u["text"], 16), (self.v_del, "trash", u["text"], 16),
                                           (self.v_more, "more", u["text"], 16)):
            button.setIcon(glyph(name, colour.name(), size))
        for button, name in ((self.prev, "back"), (self.next, "chevron")):
            button.setIcon(glyph(name, c["text"], 18))
        if self.viewer_items:
            self._update_viewer_bar()
            self._fill_info(self.viewer_items[self.viewer_row])
        if self.glass and self.isVisible():
            self._material()
        self._fill_side()
        if hasattr(self, "_cards_built"):
            self._show_set(keep=True)
        self.corners.refresh()
        self.update()

    def viewer_fg(self) -> str:
        return self.c["text"] if _is_light(self.c) else "#ffffff"

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)

    def eventFilter(self, obj, e):  # noqa: N802
        if obj is getattr(self, "side", None) and e.type() in (QEvent.Show, QEvent.Hide):
            # without the sidebar the pane keeps the same 8 px of material on its left as on its right
            self._base_margins = QMargins(0 if not self.side.isHidden() else 8, 4, 8, 8)
            self._fit_maximized()  # (maximized: a gap under the title bar as well)
        return super().eventFilter(obj, e)

    NARROW = 820  # below this the sidebar steps aside by itself (the ☰ button still opens it)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        narrow = self.width() < self.NARROW
        if narrow != getattr(self, "_narrow", None) and self.isVisible():
            self._narrow = narrow
            self.side.setVisible(PhotosWindow.sidebar_open and not narrow)
            QTimer.singleShot(0, self, lambda: (self._place_overlays(), self._layout_grid()))  # (the photos take its room)
        self._place_overlays()
        self._layout_grid()
        built = getattr(self, "_cards_for", None)
        if (built and self.inner.currentWidget() is self.cards_area
                and self._card_cols(built[0]) != built[1]):
            sb = self.cards_area.verticalScrollBar()
            share = sb.value() / sb.maximum() if sb.maximum() > 0 else 1.0
            self._build_cards(built[0], self.current, None, keep_share=share)

    def _place_overlays(self) -> None:
        b = self.browse
        W, H = b.width(), b.height()
        self.inner.setGeometry(0, 0, W, H)  # (the title band and the bars float over the photos)
        self.header.setGeometry(0, 0, W, HEAD)
        # the title band: the sidebar switch, the title and its line, then Select and the search
        self.fold.move(10, 15)
        right = W - 18
        sw = max(120, min(220, W - 360))
        self.search_field.setFixedWidth(sw)
        self.search_field.setGeometry(right - sw, 14, sw, 34)
        right -= sw + 8
        if not self.select_btn.isHidden():
            bw = self.select_btn.sizeHint().width()
            self.select_btn.setGeometry(right - bw, 14, bw, 34)
            right -= bw + 8
        x0 = 46
        self.title.setGeometry(x0, 9, max(40, right - x0), 32)
        self.subtitle.setGeometry(x0, 36, max(40, right - x0), 20)
        self.header.raise_()
        for bar, area in ((self.grid_bar, self.grid), (self.cards_bar, self.cards_area)):
            bar.setGeometry(W - bar.WIDTH, HEAD - 6, bar.WIDTH, max(0, H - HEAD + 2))
            bar.setVisible(self.inner.currentWidget() is area)
            bar.raise_()
        roomy = W >= 700
        for btn in self._sel_extra:  # Show in Explorer and Copy stay in the right-click menu
            btn.setVisible(roomy)
        self.sel_count.setVisible(True)
        for w in (self.pill, self.zoom_pill, self.selbar):
            w.layout().invalidate()  # (buttons just shown or hidden count at once)
            w.layout().activate()
            w.adjustSize()
        if self.selbar.width() > W - 24:  # still too wide: the count goes, the buttons stay
            self.sel_count.setVisible(False)
            self.selbar.layout().invalidate()
            self.selbar.layout().activate()
            self.selbar.adjustSize()
        y = H - self.pill.height() - 14
        if not self.pill.isHidden():
            x = (W - self.pill.width()) // 2  # the switch in the middle, − 100% + beside it
            zw, zy = self.zoom_pill.width(), y
            zx = x + self.pill.width() + 8
            if zx + zw > W - 8:
                # No room beside the switch in the middle. The size bar keeps the right edge (as under
                # a note) and never lies over the switch (it covered "All Photos" in a narrow window):
                # the switch moves left for it or, when the two are wider than the photos together,
                # stays in the middle with the size bar on a row of its own above it.
                zx = max(8, W - 8 - zw)
                if self.zoom_pill.isHidden():  # (Years, Months: the switch alone, in the middle)
                    pass
                elif self.pill.width() + 8 + zw <= W - 16:
                    x = zx - 8 - self.pill.width()
                else:
                    zy = y - self.zoom_pill.height() - 8
            self.pill.move(max(8, x), y)
            self.zoom_pill.move(zx, zy)
        else:
            self.zoom_pill.move((W - self.zoom_pill.width()) // 2, y)
        self.selbar.move((W - self.selbar.width()) // 2, H - self.selbar.height() - 14)
        for w in (self.pill, self.zoom_pill, self.selbar):
            w.shadow.follow()
            w.raise_()
        wide_viewer = self.pages.width() >= 1000
        self.v_show.setVisible(wide_viewer)
        self.v_open.setVisible(wide_viewer)
        self.v_more.setVisible(not wide_viewer)
        pr = self.picture.rect()
        room = self.picture._room() if self.vtop.isVisible() else QRectF(pr)
        cy = int(room.center().y()) - self.prev.height() // 2
        self.prev.move(max(4, int(room.left()) - self.prev.width() - 8), cy)
        self.next.move(min(pr.width() - self.next.width() - 4, int(room.right()) + 8), cy)
        self.corners.place()
        if self.toast.isVisible():
            self._place_toast()

    def _place_toast(self) -> None:
        self.toast.setMaximumWidth(min(620, self.width() - 80))
        self.toast.adjustSize()
        self.toast.move((self.width() - self.toast.width()) // 2 + (self.side.width() // 2 if self.side.isVisible()
                                                                    else 0) // 2,
                        self.height() - self.toast.height() - 80)
        self.toast.raise_()

    def message(self, text: str) -> None:
        self.toast.setText(text)
        self.toast.show()
        self._place_toast()
        self._toast_timer.start()

    # --- data ----------------------------------------------------------------------------------
    def reload(self, now: bool = False) -> None:
        """Read the library again if it changed: off this thread (a big library takes a moment), and
        while the sync is adding photos at most every RELOAD_GAP seconds."""
        if self.closed:
            return
        src = self.source()
        if src is None:
            self.items, self.by_id = [], {}
            self._fingerprint = None
            self._show_set()
            return
        state, root = src
        try:
            fp = (str(root), lib.fingerprint(state))
        except Exception:  # noqa: BLE001
            log.warning("The Photos window couldn't read the photo list", exc_info=True)
            return
        if self._gone_paths and not self._loading:  # deleted here, then put back from the Recycle Bin?
            back = {aid for aid, paths in self._gone_paths.items() if any(p.exists() for p in paths)}
            if back:
                for aid in back:
                    self._gone_paths.pop(aid, None)
                    self._gone.discard(aid)
                self._fingerprint = (self._fingerprint or (None, None))[:1] + ("restored",)
        if fp == self._fingerprint or self._loading:
            return
        if self._fingerprint is not None and not now and time.monotonic() - self._loaded_at < self.RELOAD_GAP:
            return  # (the poll comes back)
        self._loading = True
        self._load_started = time.monotonic()
        bridge = self._bridge

        def work() -> None:
            try:
                items = lib.load(state, root)
            except Exception:  # noqa: BLE001
                log.warning("The Photos window couldn't read the photo list", exc_info=True)
                items = None
            try:
                bridge.library.emit(fp, items)
            except RuntimeError:
                pass  # the window is gone

        threading.Thread(target=work, name="photos-library", daemon=True).start()

    def _library_loaded(self, fp, items) -> None:
        self._loading = False
        self._loaded_at = time.monotonic()
        if self.closed or items is None:
            return
        src = self.source()
        if src is None or str(src[1]) != fp[0]:
            return  # signed out or another folder meanwhile
        if self._load_started < self._deleted_at and self._gone:
            items = [it for it in items if it.asset_id not in self._gone]  # read before they went
        first = self._fingerprint is None
        self._fingerprint = fp
        self.items = items
        self.by_id = {it.asset_id: it for it in items}
        for aid, value in self._pending_fav.items():  # what was asked wins until iCloud answers
            it = self.by_id.get(aid)
            if it is not None:
                it.favorite = value
        self.thumbs.retry_missing()
        self._fill_side()
        self._show_set(keep=not first)
        if self.pages.currentWidget() is self.viewer:
            self._refresh_viewer_list()

    def _set_items(self) -> list[lib.PhotoItem]:
        k = self.set_key
        items = self.items
        if k == "recent":
            got = lib.recent(items)
        elif k == "fav":
            got = [i for i in items if i.favorite]
        elif k == "videos":
            got = [i for i in items if i.is_video]
        elif k == "live":
            got = [i for i in items if i.live]
        elif k == "shots":
            got = [i for i in items if i.screenshot]
        elif k.startswith("album:"):
            name = k[6:]
            got = [i for i in items if name in i.albums]
        else:
            got = items
        if self.query:
            got = [i for i in got if lib.matches(i, self.query)]
        return got

    def _set_name(self) -> str:
        k = self.set_key
        return {"library": t("Library"), "recent": t("Recently added"), "fav": t("Favourites"),
                "videos": t("Videos"), "live": t("Live Photos"), "shots": t("Screenshots")}.get(
            k, k[6:] if k.startswith("album:") else t("Library"))

    def _fill_side(self) -> None:
        side = self.side
        keep = self.set_key
        side.blockSignals(True)
        side.clear()
        items = self.items
        counts = {"library": len(items), "recent": len(lib.recent(items)), "fav": sum(i.favorite for i in items),
                  "videos": sum(i.is_video for i in items), "live": sum(i.live for i in items),
                  "shots": sum(i.screenshot for i in items)}

        def header(text: str) -> None:
            it = QListWidgetItem(text)
            it.setFlags(Qt.NoItemFlags)
            f = QFont(side.font())
            f.setBold(True)
            f.setPixelSize(12)
            it.setFont(f)
            it.setForeground(QColor(self.c["muted"]))
            side.addItem(it)

        names = {"library": "photo", "recent": "clock", "fav": "heart", "videos": "play",
                 "live": "photo", "shots": "device"}
        icons = {name: glyph(name, self.c["muted"], 18) for name in set(names.values()) | {"folder"}}
        library_icon = glyph("photo", service_color(self.c, "photo"), 18)

        def row(key: str, label: str, n: int) -> None:
            it = QListWidgetItem(label)
            it.setIcon(library_icon if key == "library" else icons[names.get(key, "folder")])
            it.setData(Qt.UserRole, key)
            it.setData(Qt.UserRole + 1, _num(n))
            it.setData(Qt.AccessibleTextRole, f"{label}, {n}")
            side.addItem(it)
            if key == keep:
                side.setCurrentItem(it)

        row("library", t("Library"), counts["library"])
        row("recent", t("Recently added"), counts["recent"])
        row("fav", t("Favourites"), counts["fav"])
        header(t("Media types"))
        row("videos", t("Videos"), counts["videos"])
        row("live", t("Live Photos"), counts["live"])
        row("shots", t("Screenshots"), counts["shots"])
        albums = lib.albums(items)
        if albums:
            header(t("My albums"))
            for name, its in albums.items():
                row("album:" + name, name, len(its))
        side.blockSignals(False)

    def _side_clicked(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.UserRole)
        if not key or key == self.set_key and self.pages.currentWidget() is not self.viewer:
            return
        self.set_key = key
        self.set_selecting(False)
        self.close_viewer(scroll=False)
        self._show_set()

    def _search_changed(self, text: str) -> None:
        self._search_timer.start()  # (a pause in typing: a big library isn't searched on every key)

    def _apply_search(self) -> None:
        text = self.search.text().strip()
        if text == self.query:
            return
        self.query = text
        self.set_selecting(False)
        self._show_set()

    # --- showing a set -------------------------------------------------------------------------
    def _show_set(self, keep: bool = False) -> None:
        self._cards_built = True
        got = self._set_items()
        self.current = got
        library = self.set_key == "library" and not self.query
        view = self.view if library else "all"
        self.title.setText(t("Search results") if self.query else self._set_name() if view == "all" else
                           t("Years") if view == "years" else t("Months"))
        for k, b in self.view_buttons.items():
            b.setChecked(k == self.view)
        self.pill.setVisible(library)
        self.zoom_pill.setVisible(view == "all" and bool(got))
        self.select_btn.setVisible(view == "all" and bool(got))
        if not got:
            self.empty.setText(t("Nothing found") if self.query else
                               t("No photos here yet. They appear as soon as Skyfold has synced them.")
                               if self.set_key == "library" else t("Nothing here"))
            self.inner.setCurrentWidget(self.empty)
            self.model.set_items([])
            self.subtitle.setText("")
            self._place_overlays()
            return
        if view == "all":
            anchor = self._anchor() if keep else None
            at_end = keep and self._at_end()
            selected = {i.asset_id for i in self.chosen_items()} if keep else set()
            self.model.set_items(got)
            self.inner.setCurrentWidget(self.grid)
            self._layout_grid()
            if selected:
                self._select_rows([row for row, it in enumerate(got) if it.asset_id in selected])
            top_first = self.set_key == "recent" or self.query and not library  # (these read from the top)
            if keep and anchor is not None and (not at_end or top_first):
                self._restore_anchor(anchor)
            elif top_first:
                self.grid.scrollToTop()
            else:
                QTimer.singleShot(0, self.grid.scrollToBottom)  # the newest at the bottom, as on iPhone
                self.grid.scrollToBottom()
        else:
            place = (self.cards_area.verticalScrollBar().value()
                     if keep and self.inner.currentWidget() is self.cards_area else None)
            self._build_cards(view, got, keep_scroll=place)
        self._place_overlays()
        self._update_header()

    def _select_rows(self, rows: list[int]) -> None:
        """Select these rows at once (one change, not one per photo: thousands stay quick)."""
        sel = QItemSelection()
        start = prev = None
        for row in rows + [None]:
            if row is not None and prev is not None and row == prev + 1:
                prev = row
                continue
            if start is not None:
                sel.select(self.model.index(start), self.model.index(prev))
            start = prev = row
        sm = self.grid.selectionModel()
        sm.blockSignals(True)
        sm.select(sel, QItemSelectionModel.Select)
        sm.blockSignals(False)
        self.grid.viewport().update()
        self._selection_changed()

    def _layout_grid(self) -> None:
        g = self.grid
        # The width Qt lays the photos out in: all of it (the scroll bar floats over the photos, see
        # _OverlayBar, so no column is kept for it). The last photo of each row reaches the edge.
        try:
            w = g.maximumViewportSize().width()
        except (AttributeError, RuntimeError):
            w = g.viewport().width()
        if w <= 0:
            return
        cols = COLUMNS[max(0, min(len(COLUMNS) - 1, PhotosWindow.zoom_level))]
        cols = min(cols, max(2, w // 60))  # (fewer in a narrow window)
        size = max(40, (w - 4) // cols)  # (Qt wraps a few pixels before the end)
        if size != self.tile or self.grid.gridSize().width() != size:
            self.tile = size
            self.grid.setGridSize(QSize(size, size))

    def _anchor(self):
        """The photo at the top of the grid and how far it sits from the top (to keep the place)."""
        idx = self.grid.indexAt(QPoint(4, HEAD + 6))
        if not idx.isValid():
            return None
        return idx.data(ITEM).asset_id, self.grid.visualRect(idx).top()

    def _restore_anchor(self, anchor) -> None:
        aid, top = anchor
        for row, it in enumerate(self.model.items):
            if it.asset_id == aid:
                idx = self.model.index(row)
                self.grid.scrollTo(idx, QAbstractItemView.PositionAtTop)
                sb = self.grid.verticalScrollBar()
                sb.setValue(sb.value() + self.grid.visualRect(idx).top() - top)
                return

    def _at_end(self) -> bool:
        sb = self.grid.verticalScrollBar()
        return sb.value() >= sb.maximum() - 4

    def set_zoom(self, level: int, pos: QPoint | None = None) -> None:
        level = max(0, min(len(COLUMNS) - 1, level))
        if level == PhotosWindow.zoom_level or self.inner.currentWidget() is not self.grid:
            return
        vp = self.grid.viewport()
        at = pos if pos is not None else QPoint(vp.width() // 2, vp.height() // 2)
        idx = self.grid.indexAt(at)
        if not idx.isValid():
            idx = self.grid.indexAt(QPoint(4, at.y()))
        end = pos is None and self._at_end()
        PhotosWindow.zoom_level = level
        self._zoom_shown()
        self._layout_grid()
        self.grid.doItemsLayout()
        if end:
            self.grid.scrollToBottom()
        elif idx.isValid():  # the photo under the mouse stays under the mouse
            r = self.grid.visualRect(idx)
            sb = self.grid.verticalScrollBar()
            sb.setValue(sb.value() + r.center().y() - at.y())
        self._update_header()

    def _update_header(self) -> None:
        if not self.current:
            return
        if self.inner.currentWidget() is self.grid:
            vp = self.grid.viewport()
            a = self.grid.indexAt(QPoint(4, HEAD + 6))
            b = self.grid.indexAt(QPoint(vp.width() - 6, vp.height() - 6))
            if not a.isValid():
                a = self.model.index(0)
            if not b.isValid():
                b = self.model.index(len(self.model.items) - 1)
            d1, d2 = a.data(ITEM).date, b.data(ITEM).date
            if self.set_key == "recent":
                text = t("Added in the last 30 days, newest first")
            else:
                if d1 > d2:
                    d1, d2 = d2, d1
                text = self._range(d1, d2, PhotosWindow.zoom_level < 1)  # (days only at the largest size)
            if self.query:
                text = t("{n} results", n=_num(len(self.current))) + " · " + text
            else:
                text += " · " + t("{n} items", n=_num(len(self.current)))
            self.subtitle.setText(text)
            self._repaint.start()
        else:
            years = getattr(self, "_years", None)
            if years is None or years[0] is not self.current:
                ys = sorted({i.date.year for i in self.current})
                years = self._years = (self.current, str(ys[0]) if len(ys) == 1 else f"{ys[0]} – {ys[-1]}")
            self.subtitle.setText(years[1])

    @staticmethod
    def _range(d1: datetime, d2: datetime, days: bool) -> str:
        if days:
            if d1.date() == d2.date():
                return _day(d1)
            if (d1.year, d1.month) == (d2.year, d2.month):
                return (f"{d1.day}.–{d2.day}. {_month(d1.month)} {d1.year}" if language() == "bs" else
                        f"{d1.day}–{d2.day} {_month(d1.month)} {d1.year}")
            return f"{_day(d1)} – {_day(d2)}"
        if (d1.year, d1.month) == (d2.year, d2.month):
            return f"{_month(d1.month)} {d1.year}"
        if d1.year == d2.year:
            return f"{_month(d1.month)} – {_month(d2.month)} {d1.year}"
        return f"{_month(d1.month)} {d1.year} – {_month(d2.month)} {d2.year}"

    def set_view(self, view: str) -> None:
        self.view = view
        self.set_selecting(False)
        self._show_set()

    def _build_cards(self, view: str, items: list[lib.PhotoItem], focus_year: int | None = None,
                     keep_scroll: int | None = None, keep_share: float | None = None) -> None:
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(18, HEAD + 6, 18, 80)  # (from under the title band, as in the design)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        cols = self._card_cols(view)
        self._cards_for = (view, cols, focus_year)
        row = 0
        target = None
        if view == "years":
            years: dict[int, list] = {}
            for it in items:
                years.setdefault(it.date.year, []).append(it)
            for k, (y, its) in enumerate(sorted(years.items())):
                months = len({i.date.month for i in its})
                cap = t("{n} items", n=len(its)) + " · " + (t("1 month") if months == 1 else
                                                                  t("{n} months", n=months))
                card = Card(self, str(y), cap, _key_photos(its, 1), mosaic=False)
                card.clicked.connect(lambda y=y: self._open_year(y))
                grid.addWidget(card, k // cols, k % cols)
        else:
            months: dict[tuple[int, int], list] = {}
            for it in items:
                months.setdefault((it.date.year, it.date.month), []).append(it)
            year = None
            col = 0
            for (y, m), its in sorted(months.items()):
                if y != year:
                    if col:
                        row += 1
                    col = 0
                    year = y
                    lbl = QLabel(str(y))
                    lbl.setStyleSheet(f"color:{self.c['text']};background:transparent;font-family:'Segoe UI Variable Display',"
                                      f"'Segoe UI';font-size:20px;font-weight:600;padding-top:6px")
                    grid.addWidget(lbl, row, 0, 1, cols)
                    if focus_year == y:
                        target = lbl
                    row += 1
                days = len({i.date.day for i in its})
                cap = t("{n} items", n=len(its)) + " · " + (t("1 day") if days == 1 else t("{n} days", n=days))
                card = Card(self, _month(m), cap, _key_photos(its, 3), mosaic=len(its) >= 3)
                card.clicked.connect(lambda its=its: self._open_month(its[0]))
                grid.addWidget(card, row, col)
                col += 1
                if col == cols:
                    col = 0
                    row += 1
        for k in range(cols):
            grid.setColumnStretch(k, 1)
        grid.setRowStretch(grid.rowCount(), 1)
        self.cards_area.setWidget(holder)
        self.inner.setCurrentWidget(self.cards_area)

        def scroll() -> None:
            sb = self.cards_area.verticalScrollBar()
            if target is not None:
                sb.setValue(target.y() - HEAD - 10)
            elif keep_scroll is not None:
                sb.setValue(keep_scroll)  # (new photos arrived: the place stays)
            elif keep_share is not None:
                sb.setValue(round(sb.maximum() * keep_share))  # (the window changed size)
            else:
                sb.setValue(sb.maximum())

        QTimer.singleShot(0, holder, scroll)  # (rebuilt meanwhile: these go with the old cards)
        QTimer.singleShot(60, holder, scroll)

    def _card_cols(self, view: str) -> int:
        width = max(400, self.browse.width() - 44)
        return max(1, min(4, width // (400 if view == "months" else 340)))

    def _open_year(self, year: int) -> None:
        self.view = "months"
        self._show_set()
        self._build_cards("months", self.current, focus_year=year)
        self._place_overlays()
        self._update_header()

    def _open_month(self, first: lib.PhotoItem) -> None:
        self.view = "all"
        self._show_set()

        def go() -> None:
            for row, it in enumerate(self.model.items):
                if it.asset_id == first.asset_id:
                    idx = self.model.index(row)
                    self.grid.scrollTo(idx, QAbstractItemView.PositionAtTop)
                    sb = self.grid.verticalScrollBar()
                    sb.setValue(max(0, sb.value() - 80))
                    self.grid.selectionModel().setCurrentIndex(idx, QItemSelectionModel.NoUpdate)
                    return

        QTimer.singleShot(0, self, go)

    def _nav_state(self) -> tuple:
        return (self.pages.currentWidget() is self.viewer, self.set_key, self.query, self.view)

    def _scroll_bar(self):
        return (self.grid if self.inner.currentWidget() is self.grid else self.cards_area).verticalScrollBar()

    def go_back(self) -> None:
        """The mouse's Back button (and the window's own Back): from a photo to the photos, from All
        Photos to Months, from Months to Years. Forward then goes the same way back in."""
        if self.pages.currentWidget() is self.viewer:
            it = self.viewer_items[self.viewer_row] if self.viewer_items else None
            self.close_viewer()
            if it is not None:
                self._forward.append((self._nav_state(), "photo", it.asset_id))
            return
        to = {"all": "months", "months": "years"}.get(self.view)
        if self.set_key == "library" and not self.query and to:
            came = (self.view, self._scroll_bar().value())
            self.set_view(to)
            self._forward.append((self._nav_state(), "view", came))

    def go_forward(self) -> None:
        """Undo the last Back, if nothing else was done since (as in a web browser)."""
        if not self._forward:
            return
        state, kind, what = self._forward.pop()
        if state != self._nav_state():
            self._forward.clear()  # the window went elsewhere meanwhile: nothing to go forward to
            return
        if kind == "photo":
            for row, x in enumerate(self.model.items):
                if x.asset_id == what and self.inner.currentWidget() is self.grid:
                    self.open_viewer(row)
                    return
            self._forward.clear()
            return
        view, place = what
        self.set_view(view)
        QTimer.singleShot(90, self, lambda: self._scroll_bar().setValue(place))  # after the view placed itself

    def _mouse_nav(self, forward: bool) -> None:
        (self.go_forward if forward else self.go_back)()

    def toggle_sidebar(self) -> None:
        PhotosWindow.sidebar_open = not self.side.isVisible()
        self.side.setVisible(PhotosWindow.sidebar_open)
        if (PhotosWindow.sidebar_open and self.info.isVisible()
                and self.width() - self.side.width() - self.info.width() < 360):
            self.v_info.setChecked(False)  # a narrow window: the photo keeps its room
        QTimer.singleShot(0, self, lambda: (self._layout_grid(), self._place_overlays()))

    # --- pictures arriving ---------------------------------------------------------------------
    def _loaded(self, key: str, size: int, full: bool, img: QImage) -> None:
        if self.closed:
            return
        if full:
            if img.isNull():
                cur = self.viewer_items[self.viewer_row] if self.viewer_items else None
                if cur is not None and cur.key == key:
                    heic = (cur.path.suffix.lower() in (".heic", ".heif") and len(str(cur.path)) < 260
                            and cur.path.is_file())
                    self.viewer_message = t(
                        "Windows can't show HEIC photos yet. Get Microsoft's free “HEIF Image Extensions” "
                        "from the Microsoft Store, then open the photo again.") if heic else t(
                        "This photo couldn't be opened. Is it still in the photos folder?")
                    self.picture.update()
                return
            pm = QPixmap.fromImage(img)
            self._full[key] = pm
            while len(self._full) > 6:
                self._full.pop(next(iter(self._full)))
            if self.viewer_items and self.viewer_items[self.viewer_row].key == key:
                self.picture.set_picture(pm, True)
            return
        self.thumbs.put(key, size, img)
        self._repaint.start()

    def _prefetch(self) -> None:
        """Ask for the pictures a screen above and below too, after the ones on screen, so they are
        ready before you scroll to them."""
        items = self.model.items
        if (not items or self.inner.currentWidget() is not self.grid or self.pages.currentWidget() is not self.browse
                or self.tile <= 0):
            return
        vp = self.grid.viewport()
        cols = max(1, vp.width() // self.tile)
        rows = vp.height() // self.tile + 2
        first = (self.grid.verticalScrollBar().value() // self.tile) * cols
        screen = rows * cols
        px = (self.tile - GAP) * self.devicePixelRatioF()  # (as TileDelegate)
        for r in range(max(0, first - screen), min(len(items), first + 2 * screen)):
            self.thumbs.get(items[r], px, ahead=True)

    def _repaint_now(self) -> None:
        if self.closed:
            return
        self._prefetch()
        seen = self.thumbs.seen
        self.thumbs.seen = set()
        self.loader.keep_only(seen | ({self.viewer_items[self.viewer_row].key}
                                      if self.viewer_items else set()))
        self.grid.viewport().update()
        w = self.cards_area.widget()
        if w is not None and self.inner.currentWidget() is self.cards_area:
            w.update()
            for card in w.findChildren(Card):
                card.update()
        self.strip.update()
        if self.pages.currentWidget() is self.viewer and self.picture.pm is None and self.viewer_items:
            it = self.viewer_items[self.viewer_row]
            pm = self.thumbs.get(it, 640)
            if pm is not None:
                self.picture.set_picture(pm, False)

    # --- selection -----------------------------------------------------------------------------
    def set_selecting(self, on: bool) -> None:
        if not on:
            self.grid.clearSelection()
        self.selecting = on
        self.select_btn.setText(t("Done") if on else t("Select"))
        self._selection_changed()

    def _selection_changed(self) -> None:
        n = len(self.grid.selectionModel().selectedIndexes())
        if n and not self.selecting:
            self.selecting = True
            self.select_btn.setText(t("Done"))
        self.sel_count.setText(t("1 selected") if n == 1 else t("{n} selected", n=n) if n else
                               t("Click photos to select them"))
        show = self.selecting and self.inner.currentWidget() is self.grid
        self.selbar.setVisible(show)
        self.pill.setVisible(not show and self.set_key == "library" and not self.query)
        self.zoom_pill.setVisible(not show and self.inner.currentWidget() is self.grid and bool(self.current))
        self._place_overlays()
        self.grid.viewport().update()

    def chosen_items(self) -> list[lib.PhotoItem]:
        rows = sorted(i.row() for i in self.grid.selectionModel().selectedIndexes())
        return [self.model.items[r] for r in rows if r < len(self.model.items)]

    def select_all(self) -> None:
        if self.pages.currentWidget() is self.viewer or self.inner.currentWidget() is not self.grid:
            return
        if not self.selecting:
            self.set_selecting(True)
        self.grid.selectAll()
        self._selection_changed()

    # --- actions -------------------------------------------------------------------------------
    def context_menu(self, items: list[lib.PhotoItem], pos: QPoint) -> None:
        m = QMenu(self)
        one = len(items) == 1
        if one:
            m.addAction(t("Open"), lambda: self.open_file(items[0].path))
        m.addAction(t("Show in Explorer"), lambda: self.show_in_explorer(items))
        m.addAction(t("Copy"), lambda: self.copy_items(items))
        fav = not all(i.favorite for i in items)
        m.addAction(t("Favourite") if fav else t("Remove from Favourites"), lambda: self.set_favorite(items, fav))
        m.addSeparator()
        m.addAction(t("Delete"), lambda: self.delete_items(items))
        m.exec(pos)
        m.deleteLater()  # (it held the chosen photos)

    def open_file(self, path: Path) -> None:
        from ..engine import MEDIA_EXTS

        if Path(path).suffix.lower() not in MEDIA_EXTS or Path(path).suffix.lower() == ".aae":
            # (only photos and videos open in their app; anything else is shown in File Explorer)
            self._select_in_explorer(Path(path))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _select_in_explorer(self, path: Path) -> None:
        try:
            subprocess.Popen([os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe"), "/select,",
                              str(path)])
        except OSError as exc:
            self.message(str(exc))

    def show_in_explorer(self, items: list[lib.PhotoItem]) -> None:
        if not items:
            self.message(t("Select some photos first"))
            return
        try:
            subprocess.Popen([os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe"), "/select,",
                              str(items[0].path)])  # (by its full path: never an explorer.exe planted elsewhere)
        except OSError as exc:
            self.message(str(exc))

    def show_selected(self) -> None:
        self.show_in_explorer(self.chosen_items())

    def copy_items(self, items: list[lib.PhotoItem]) -> None:
        if not items:
            return
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(p)) for it in items for p in it.files])
        QGuiApplication.clipboard().setMimeData(data)
        n = sum(len(it.files) for it in items)
        self.message(t("Copied 1 file: paste it into any folder") if n == 1 else
                     t("Copied {n} files: paste them into any folder", n=n))

    def copy_selected(self) -> None:
        if self.pages.currentWidget() is self.viewer and self.viewer_items:
            self.copy_items([self.viewer_items[self.viewer_row]])
        else:
            self.copy_items(self.chosen_items())

    def favorite_selected(self) -> None:
        items = self.chosen_items()
        if not items:
            self.message(t("Select some photos first"))
            return
        self.set_favorite(items, not all(i.favorite for i in items))

    def set_favorite(self, items: list[lib.PhotoItem], value: bool) -> None:
        """At once on screen; iCloud is told through the sync's thread in one go (and whatever it says no
        to is undone). Until it answers, the heart stays as asked even when the library is read again."""
        changed = [it for it in items if it.favorite != value]
        for it in changed:
            if it.asset_id not in self._pending_fav:
                self._fav_confirmed[it.asset_id] = it.favorite  # what iCloud has, before this click
            it.favorite = value
            self._pending_fav[it.asset_id] = value
        if changed:
            self.favoriteRequested.emit([it.asset_id for it in changed], value)
        self._fill_side()
        self.grid.viewport().update()
        self._update_viewer_bar()
        if len(items) > 1 or self.pages.currentWidget() is not self.viewer:
            self.message(t("Added to Favourites") if value else t("Removed from Favourites"))

    def favorite_result(self, ids: list, value: bool, failed: list, error: str) -> None:
        """iCloud's answer to one set_favorite: ids were asked to become value; failed ones stay as they were."""
        if self.closed:
            return
        failed_set = set(failed)
        changed = False
        for aid in ids:
            if aid not in failed_set:
                self._fav_confirmed[aid] = value
            if self._pending_fav.get(aid) != value:
                continue  # clicked again meanwhile: that later answer decides
            self._pending_fav.pop(aid, None)
            confirmed = self._fav_confirmed.pop(aid, not value)
            it = self.by_id.get(aid)
            if it is not None and it.favorite != confirmed:
                it.favorite = confirmed
                changed = True
            if aid not in failed_set:
                changed = True
        if changed and self.set_key == "fav" and self.pages.currentWidget() is not self.viewer:
            self._show_set(keep=True)  # Favourites shows only favourites (the viewer keeps its list)
        if failed and error:
            self.message(t("Couldn't change the favourite in iCloud: {error}", error=error))
        self._fill_side()
        self.grid.viewport().update()
        self._update_viewer_bar()

    def delete_items(self, items: list[lib.PhotoItem]) -> None:
        if not items:
            self.message(t("Select some photos first"))
            return
        if self._deleting:
            self.message(t("Still deleting the previous photos"))
            return
        s = self.settings()
        to_cloud = bool(getattr(s, "delete_pc_to_cloud", True))
        n = len(items)
        box = QMessageBox(self)
        box.setTextFormat(Qt.PlainText)  # (file names from iCloud: never HTML)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(t("Delete"))
        box.setText(t("Delete {name}?", name=items[0].name) if n == 1 else t("Delete {n} items?", n=n))
        box.setInformativeText(
            t("They go to the Recycle Bin on this PC and to Recently Deleted in iCloud, where they stay for "
              "30 days.") if to_cloud else
            t("They go to the Recycle Bin on this PC. They stay in iCloud (Settings → Photos)."))
        delete = box.addButton(t("Delete"), QMessageBox.DestructiveRole)
        cancel = box.addButton(t("Cancel"), QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        cancel.setFocus()
        box.exec()
        if box.clickedButton() is not delete:
            return
        src = self.source()
        if src is None:
            return
        state, root = src
        ids = [it.asset_id for it in items]
        self._deleting = True
        if n > 20:
            self.message(t("Deleting {n} items…", n=n))
        bridge = self._bridge

        def work() -> None:
            from ..engine import delete_on_pc

            try:
                done, failed = delete_on_pc(state, root, ids)
            except Exception as exc:  # noqa: BLE001
                log.warning("The Photos window couldn't delete", exc_info=True)
                done, failed = [], [(ids[0], str(exc))]
            try:
                bridge.deleted.emit(done, failed)
            except RuntimeError:
                pass  # the window is gone

        threading.Thread(target=work, name="photos-delete", daemon=True).start()

    def _deleted(self, done: list, failed: list) -> None:
        """Off-thread Delete finished: the deleted photos leave the window (the sync does the rest)."""
        self._deleting = False
        if self.closed:
            return
        gone = set(done)
        self._gone |= gone
        self._deleted_at = time.monotonic()
        for aid in gone:
            it = self.by_id.get(aid)
            if it is not None:
                self._gone_paths[aid] = list(it.files)
        if gone:
            to_cloud = bool(getattr(self.settings(), "delete_pc_to_cloud", True))
            viewing = self.pages.currentWidget() is self.viewer
            self.items = [i for i in self.items if i.asset_id not in gone]
            self.by_id = {i.asset_id: i for i in self.items}
            self.set_selecting(False)
            self._fill_side()
            self._show_set(keep=True)
            if viewing:
                self._refresh_viewer_list(removed=gone)
            self.message((t("1 item moved to the Recycle Bin") if len(done) == 1 else
                          t("{n} items moved to the Recycle Bin", n=len(done)))
                         + (" " + t("iCloud gets the deletion at the next sync.") if to_cloud else ""))
        if failed:
            aid, why = failed[0]
            it = self.by_id.get(aid)
            self.message(t("Couldn't delete {name}: {error}", name=it.name if it else aid, error=why)
                         if len(failed) == 1 else t("{n} items couldn't be deleted: {error}", n=len(failed),
                                                    error=why))

    # --- viewer --------------------------------------------------------------------------------
    def open_viewer(self, row: int) -> None:
        if not self.model.items:
            return
        self.viewer_items = list(self.model.items)
        self._viewer_opened_at = time.monotonic()
        label = t("Search results") if self.query else self._set_name()
        self.back.setText(self.back.fontMetrics().elidedText(label, Qt.ElideRight, 132))
        from .safe_text import plain_tip

        self.back.setToolTip(plain_tip(label) if self.back.text() != label else "")
        self.back.setAccessibleName(label)
        self.pages.setCurrentWidget(self.viewer)
        self._set_bare(False)
        self.show_row(row)
        self.picture.setFocus()

    def _refresh_viewer_list(self, removed: set[str] | None = None) -> None:
        if not self.viewer_items:
            return
        cur = self.viewer_items[self.viewer_row]
        if removed is None:  # the library was read again: the same photos, fresher (an unhearted
            items = [self.by_id[i.asset_id] for i in self.viewer_items if i.asset_id in self.by_id]  # one stays)
        else:
            items = list(self.model.items) if self.inner.currentWidget() is self.grid else self._set_items()
        if not items:
            self.close_viewer()
            return
        ids = [i.asset_id for i in items]
        if cur.asset_id in ids:
            row = ids.index(cur.asset_id)
        else:
            row = min(self.viewer_row, len(items) - 1)
        self.viewer_items = items
        self.show_row(row)

    def show_row(self, row: int) -> None:
        if not self.viewer_items:
            return
        self.viewer_row = max(0, min(len(self.viewer_items) - 1, row))
        it = self.viewer_items[self.viewer_row]
        self.viewer_message = ""
        pm = self._full.get(it.key)
        if pm is not None:
            self.picture.set_picture(pm, True)
        else:
            self.picture.set_picture(self.thumbs.get(it, 640), False)
            self.want_full()
        self.v_date.setText(f"{_weekday(it.date)}, {_day(it.date)}")
        self.v_time.setText(f"{it.date:%H:%M} · {it.name} · "
                            + t("{a} of {b}", a=_num(self.viewer_row + 1), b=_num(len(self.viewer_items))))
        self.prev.setEnabled(self.viewer_row > 0)
        self.next.setEnabled(self.viewer_row < len(self.viewer_items) - 1)
        self._update_viewer_bar()
        self._fill_info(it)
        self.strip.update()
        self._place_overlays()

    def want_full(self) -> None:
        if not self.viewer_items:
            return
        it = self.viewer_items[self.viewer_row]
        if it.key not in self._full and not it.is_video:
            self.loader.request(it.key, it.path, 0, full=True)
        for k in (self.viewer_row - 1, self.viewer_row + 1):  # the neighbours, ready to step to
            if 0 <= k < len(self.viewer_items):
                self.thumbs.get(self.viewer_items[k], 640)

    def _update_viewer_bar(self) -> None:
        if not self.viewer_items:
            return
        it = self.viewer_items[self.viewer_row]
        tip = (t("Remove from Favourites") if it.favorite else t("Favourite")) + " (F)"
        self.v_fav.setAccessibleName(tip)
        self.v_fav.setToolTip(tip)
        self.v_fav.setIcon(self.hearts[bool(it.favorite)])
        self.v_play.setVisible(it.is_video or it.live)
        self.v_play.setText("▶  " + (t("Play video") if it.is_video else t("Play Live Photo")))

    def _fill_info(self, it: lib.PhotoItem) -> None:
        muted = self.c["muted"]
        kind = (t("Video") + (f", {_duration(it.duration)}" if it.duration else "") if it.is_video else
                t("Live Photo (photo and 3-second video)") if it.live else
                t("Screenshot") if it.screenshot else t("Photo"))
        rows = [(t("Taken"), f"{_weekday(it.date)}, {_day(it.date)} {it.date:%H:%M}"), (t("Kind"), kind)]
        if it.added and abs((it.added - it.date).total_seconds()) > 86400:
            rows.append((t("Added to iCloud"), f"{_day(it.added)} {it.added:%H:%M}"))
        if it.width and it.height:
            rows.append((t("Size"), f"{_num(it.width)} × {_num(it.height)}"))
        if it.albums:
            rows.append((t("Albums"), ", ".join(it.albums)))
        rows.append((t("Favourite"), t("Yes") if it.favorite else t("No")))
        html = f"<div style='font-size:15px;font-weight:700;margin-bottom:10px'>{_esc(it.name)}</div>"
        for label, value in rows:
            html += (f"<div style='color:{muted};font-size:12px;margin-top:8px'>{_esc(label)}</div>"
                     f"<div>{_esc(value)}</div>")
        html += f"<div style='margin-top:16px;font-weight:700'>{_esc(t('On this PC'))}</div>"
        for p in it.files:
            try:
                size = _size(p.stat().st_size)
            except OSError:
                size = ""
            html += (f"<div style='margin-top:6px;font-family:Cascadia Mono,Consolas,monospace;font-size:11px'>"
                     f"{_esc(str(p)).replace(chr(92), chr(92) + chr(8203))}</div>"
                     f"<div style='color:{muted};font-size:11px'>{size}</div>")
        self.info.setText(html)

    def _show_info(self, on: bool) -> None:
        if on and self.side.isVisible() and self.width() - self.side.width() - self.info.width() < 360:
            self.side.hide()  # a narrow window: the photo keeps its room
        self.info.setVisible(on)
        QTimer.singleShot(0, self._place_overlays)

    def step(self, d: int) -> None:
        if self.viewer_items:
            self.show_row(self.viewer_row + d)

    def play_current(self) -> None:
        it = self.viewer_items[self.viewer_row]
        target = it.live_video if (it.live and not it.is_video) else it.path
        if target is not None:
            self.open_file(target)

    def toggle_favorite_current(self) -> None:
        if self.viewer_items:
            it = self.viewer_items[self.viewer_row]
            self.set_favorite([it], not it.favorite)
            self._fill_info(it)

    def _toggle_bare(self) -> None:
        self._set_bare(self.vtop.isVisible())

    def _set_bare(self, bare: bool) -> None:
        """Only the photo (as tapping a photo on the iPad), still on the window's see-through material."""
        self.vtop.setVisible(not bare)
        self.strip.setVisible(not bare)
        self.info.setVisible(not bare and self.v_info.isChecked())
        self.picture.update()
        QTimer.singleShot(0, self._place_overlays)

    def close_viewer(self, scroll: bool = True) -> None:
        if self.pages.currentWidget() is not self.viewer:
            return
        it = self.viewer_items[self.viewer_row] if self.viewer_items else None
        self.pages.setCurrentWidget(self.browse)
        self.picture.set_picture(None, False)
        if scroll and it is not None and self.inner.currentWidget() is self.grid:
            for row, x in enumerate(self.model.items):
                if x.asset_id == it.asset_id:
                    idx = self.model.index(row)
                    self.grid.selectionModel().setCurrentIndex(idx, QItemSelectionModel.NoUpdate)
                    self.grid.scrollTo(idx, QAbstractItemView.EnsureVisible)
                    break
        self.grid.setFocus()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        k = e.key()
        if self.pages.currentWidget() is self.viewer:
            if k == Qt.Key_Escape:
                if not self.vtop.isVisible():
                    self._set_bare(False)
                else:
                    self.close_viewer()
                return
            if k == Qt.Key_Left:
                self.step(-1)
                return
            if k == Qt.Key_Right:
                self.step(1)
                return
            if k == Qt.Key_Home:
                self.show_row(0)
                return
            if k == Qt.Key_End:
                self.show_row(len(self.viewer_items) - 1)
                return
            if k == Qt.Key_Space and self.viewer_items and (self.viewer_items[self.viewer_row].is_video
                                                            or self.viewer_items[self.viewer_row].live):
                self.play_current()
                return
            if not e.modifiers():
                if k == Qt.Key_F:
                    self.toggle_favorite_current()
                    return
                if k == Qt.Key_I:
                    self.v_info.toggle()
                    return
            if k == Qt.Key_Delete:
                self.v_del.click()
                return
            return
        if k == Qt.Key_Escape:
            if self.search.hasFocus() or self.query:
                self.search.clear()
                self.grid.setFocus()
            elif self.selecting:
                self.set_selecting(False)
            return
        if k == Qt.Key_Delete and self.chosen_items():
            self.delete_items(self.chosen_items())
            return
        if k in (Qt.Key_Home, Qt.Key_End) and self.inner.currentWidget() is self.grid:
            (self.grid.scrollToTop if k == Qt.Key_Home else self.grid.scrollToBottom)()
            return
        super().keyPressEvent(e)

    # --- closing -------------------------------------------------------------------------------
    def clear(self) -> None:
        """Signed out or another Apple ID: nothing of the old library stays on screen or in memory."""
        self._signed_out = True
        drop_warm()
        self._forget()
        self.close()

    def _forget(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._mouse.stop()
        self._forward.clear()
        self._poll.stop()
        self.loader.keep_only(set())
        if self._root and not self._signed_out:
            _keep_warm(self._root, self.thumbs)  # back at once if opened again soon
        self.thumbs.clear()
        self._full.clear()
        self.items, self.by_id, self.current, self.viewer_items = [], {}, [], []
        self.model.set_items([])

    def closeEvent(self, e) -> None:  # noqa: N802
        self._forget()
        self.loader.stop()
        super().closeEvent(e)

    def done(self, r: int) -> None:
        self._forget()
        super().done(r)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        for b in self.findChildren(QPushButton):  # (a dialog presses its default button on Enter)
            b.setAutoDefault(False)
            b.setDefault(False)
        for b in self.viewer.findChildren(QAbstractButton):  # a click leaves ← → to the photos
            b.setFocusPolicy(Qt.TabFocus)
        self._narrow = self.width() < self.NARROW
        self.side.setVisible(PhotosWindow.sidebar_open and not self._narrow)
        QTimer.singleShot(0, self, lambda: (self._place_overlays(), self._layout_grid()))


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _weekday(d: datetime) -> str:
    return t(WEEKDAYS[d.weekday()])


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
