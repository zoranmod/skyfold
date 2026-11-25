"""The Photos window: your synced photos and videos as on an iPad (Years, Months, All Photos), with
Favourites, Recently added, videos, Live Photos, screenshots and albums, and a viewer.

Everything shown comes from the sync database and the files in the photos folder; pictures come
from Windows (see photo_images). Deleting moves the files to the Recycle Bin, exactly as deleting
them in the folder would, so the sync then does what it always does with a deleted photo. Only the
heart talks to iCloud, through the sync's own thread."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (QAbstractListModel, QEvent, QItemSelection, QItemSelectionModel, QObject, QMimeData, QModelIndex, QPoint, QRect,
                            QRectF, QSize, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QGuiApplication, QImage, QKeySequence, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListView, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea,
                               QSizePolicy, QStackedWidget, QStyle, QStyledItemDelegate, QToolButton, QVBoxLayout,
                               QWidget)
from shiboken6 import isValid

from .. import photo_library as lib
from ..i18n import t
from .dialogs import _Dialog
from .photo_images import ImageLoader

log = logging.getLogger(__name__)

ICON_FONT = "Segoe Fluent Icons, Segoe MDL2 Assets"
I_BACK, I_HEART, I_HEART_FILL, I_INFO, I_DELETE, I_FOLDER, I_OPEN, I_PLAY = (
    "", "", "", "", "", "", "", "")
I_PREV, I_NEXT, I_SIDEBAR, I_SEARCH = "‹", "›", "", ""
COLUMNS = (3, 5, 7, 9, 13)  # the five grid sizes, as pinching on an iPad
GAP = 2
ITEM = Qt.UserRole + 1
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December")


def _month(m: int) -> str:
    return t(MONTH_NAMES[m - 1])


def _day(d: datetime) -> str:
    return f"{d.day}. {_month(d.month)} {d.year}"


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


def _is_light(c: dict) -> bool:
    return QColor(c["bg"]).lightness() > 128


def _icon_button(text: str, tip: str, size: int = 34) -> QToolButton:
    b = QToolButton()
    b.setText(text)
    b.setToolTip(tip)
    b.setAccessibleName(tip)
    b.setFont(QFont(ICON_FONT.split(",")[0], 11))
    b.setFixedSize(size, size)
    b.setCursor(Qt.PointingHandCursor)
    return b


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
            return f"{kind}, {_day(it.date)} {it.date:%H:%M}, {it.name}"
        return None


class Thumbs:
    """Pictures of the photos that are (or were just) on screen, kept in memory a while."""

    BUDGET = 220 * 1024 * 1024  # bytes of pictures kept (the oldest go first)

    def __init__(self, loader: ImageLoader) -> None:
        self.loader = loader
        self.cache: OrderedDict[tuple[str, int], QPixmap] = OrderedDict()
        self.used = 0
        self.missing: set[tuple[str, int]] = set()
        self.seen: set[str] = set()

    @staticmethod
    def bucket(px: float) -> int:
        return 160 if px <= 160 else 320 if px <= 320 else 640

    def get(self, it: lib.PhotoItem, px: float) -> QPixmap | None:
        b = self.bucket(px)
        key = (it.asset_id, b)
        self.seen.add(it.asset_id)
        pm = self.cache.get(key)
        if pm is not None:
            self.cache.move_to_end(key)
            return pm
        for other in (640, 320, 160):  # a sharper or rougher one meanwhile
            if other != b and (it.asset_id, other) in self.cache:
                pm = self.cache[(it.asset_id, other)]
                break
        if key not in self.missing:
            self.loader.request(it.asset_id, it.path, b)
        return pm

    def put(self, key: str, size: int, img: QImage) -> None:
        if img.isNull():
            self.missing.add((key, size))
            return
        short = min(img.width(), img.height())
        if short > size * 1.25:
            img = img.scaled(img.width() * size // short, img.height() * size // short,
                             Qt.KeepAspectRatio, Qt.SmoothTransformation)
        old = self.cache.pop((key, size), None)
        if old is not None:
            self.used -= self._bytes(old)
        pm = QPixmap.fromImage(img)
        self.cache[(key, size)] = pm
        self.used += self._bytes(pm)
        while self.used > self.BUDGET and len(self.cache) > 1:
            _k, gone = self.cache.popitem(last=False)
            self.used -= self._bytes(gone)

    @staticmethod
    def _bytes(pm: QPixmap) -> int:
        return pm.width() * pm.height() * 4

    def retry_missing(self) -> None:
        """Pictures that couldn't be made are tried again (the file may be there now)."""
        self.missing.clear()

    def clear(self) -> None:
        self.cache.clear()
        self.used = 0
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
        if it.is_video and not (win.selecting and selected) and not small:
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


class Grid(QListView):
    zoomed = Signal(int, QPoint)  # +1 larger / -1 smaller, mouse position

    def __init__(self, win: "PhotosWindow") -> None:
        super().__init__()
        self.win = win
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setLayoutMode(QListView.Batched)
        self.setBatchSize(2000)
        self.setSpacing(0)
        self.setFrameShape(QFrame.NoFrame)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.verticalScrollBar().setSingleStep(40)
        self.setAccessibleName(t("Photos"))
        self._wheel = 0

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
        if e.button() == Qt.LeftButton and idx.isValid() and not self.win.selecting and not (
                mods & (Qt.ControlModifier | Qt.ShiftModifier)):
            self._open_on_release = idx
            return
        self._open_on_release = None
        if e.button() == Qt.LeftButton and idx.isValid() and self.win.selecting and not (
                mods & (Qt.ControlModifier | Qt.ShiftModifier)):
            self.selectionModel().select(idx, QItemSelectionModel.Toggle)
            self.selectionModel().setCurrentIndex(idx, QItemSelectionModel.NoUpdate)
            self.win._selection_changed()
            return
        super().mousePressEvent(e)
        if mods & (Qt.ControlModifier | Qt.ShiftModifier) and not self.win.selecting:
            self.win.set_selecting(True)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        idx = getattr(self, "_open_on_release", None)
        self._open_on_release = None
        if idx is not None and idx == self.indexAt(e.position().toPoint()):
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
        if not self.selectionModel().isSelected(idx):
            self.selectionModel().select(idx, QItemSelectionModel.ClearAndSelect)
        self.win.context_menu(self.win.chosen_items() or [idx.data(ITEM)], e.globalPos())


# --- Years and Months --------------------------------------------------------------------------------
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
        clip.addRoundedRect(r, 14, 14)
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
        g = QLinearGradient(0, r.height() * 0.45, 0, r.height())
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(1, QColor(0, 0, 0, 160))
        p.fillRect(r, g)
        p.setPen(QColor("#ffffff"))
        f = QFont(self.font())
        f.setPixelSize(34 if not self.mosaic else 22)
        f.setBold(True)
        p.setFont(f)
        p.drawText(r.adjusted(16, 0, -16, -34), Qt.AlignLeft | Qt.AlignBottom, self.title)
        f.setPixelSize(13)
        f.setBold(False)
        p.setFont(f)
        p.drawText(r.adjusted(16, 0, -16, -14), Qt.AlignLeft | Qt.AlignBottom, self.caption)
        if self.hasFocus():
            p.setClipping(False)
            p.setPen(QPen(QColor(c["focus_ring"]), 3))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), 13, 13)


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

    def _target(self) -> QRectF:
        r = QRectF(self.rect())
        pm = self.pm
        if pm is None or pm.isNull():
            return r
        dpr = pm.devicePixelRatio() or 1
        w, h = pm.width() / dpr, pm.height() / dpr
        if self.zoom:
            scale = 1.0 / self.devicePixelRatioF() * dpr * 1.0
            tw, th = w * scale * self.devicePixelRatioF(), h * scale * self.devicePixelRatioF()
            tw, th = pm.width() / self.devicePixelRatioF(), pm.height() / self.devicePixelRatioF()
        else:
            scale = min(r.width() / w, r.height() / h)
            tw, th = w * scale, h * scale
        x = r.center().x() - tw / 2 + self.offset.x()
        y = r.center().y() - th / 2 + self.offset.y()
        return QRectF(x, y, tw, th)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.pm is not None and not self.pm.isNull():
            p.drawPixmap(self._target(), self.pm, QRectF(self.pm.rect()))
        elif self.win.viewer_message:
            p.setPen(QColor(self.win.viewer_fg()))
            p.drawText(QRectF(self.rect()).adjusted(40, 0, -40, 0), Qt.AlignCenter | Qt.TextWordWrap,
                       self.win.viewer_message)

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
        if e.button() == Qt.LeftButton and not self._moved:
            QTimer.singleShot(QApplication.doubleClickInterval(), self._single)
            self._pending_single = True
        self._drag = None

    def _single(self) -> None:
        if getattr(self, "_pending_single", False):
            self._pending_single = False
            self.tapped.emit()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        self._pending_single = False
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
    picked = Signal(int)

    def __init__(self, win: "PhotosWindow") -> None:
        super().__init__()
        self.win = win
        self.setFixedHeight(64)
        self.setMouseTracking(True)
        self._hits: list[tuple[QRectF, int]] = []

    def paintEvent(self, _e) -> None:  # noqa: N802
        win = self.win
        items, cur = win.viewer_items, win.viewer_row
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        self._hits = []
        if not items:
            return
        h = 48.0
        thin, wide = 30.0, 72.0
        y = (self.height() - h) / 2
        cx = self.width() / 2
        dpr = self.devicePixelRatioF()
        n = int(self.width() / (thin + 2) / 2) + 1
        for k in range(max(0, cur - n), min(len(items), cur + n + 1)):
            if k == cur:
                rr = QRectF(cx - wide / 2, y, wide, h)
            elif k < cur:
                rr = QRectF(cx - wide / 2 - 6 - (cur - k) * (thin + 2), y, thin, h)
            else:
                rr = QRectF(cx + wide / 2 + 6 + (k - cur - 1) * (thin + 2) + 2, y, thin, h)
            if rr.right() < 0 or rr.left() > self.width():
                continue
            pm = win.thumbs.get(items[k], 160 if k != cur else wide * dpr)
            p.setOpacity(1.0 if k == cur else 0.6)
            if pm is None:
                p.fillRect(rr, QColor(win.c["tile"]))
            else:
                _cover(p, pm, rr)
            self._hits.append((rr, k))
        p.setOpacity(1.0)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        for rr, k in self._hits:
            if rr.contains(e.position()):
                self.picked.emit(k)
                return


class Header(QWidget):
    """Title and date over the top of the photos, on a fade from the window colour (as on the iPad)."""

    def __init__(self, win: "PhotosWindow", parent: QWidget) -> None:
        super().__init__(parent)
        self.win = win

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        bg = QColor(self.win.c["bg"])
        g = QLinearGradient(0, 0, 0, self.height())
        top = QColor(bg)
        top.setAlpha(235)
        mid = QColor(bg)
        mid.setAlpha(160)
        end = QColor(bg)
        end.setAlpha(0)
        g.setColorAt(0, top)
        g.setColorAt(0.6, mid)
        g.setColorAt(1, end)
        p.fillRect(self.rect(), g)


class SideDelegate(QStyledItemDelegate):
    """Sidebar rows: section titles, and collections with their count on the right."""

    def __init__(self, win: "PhotosWindow") -> None:
        super().__init__(win)
        self.win = win

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(200, 38 if index.data(Qt.UserRole) is None else 34)

    def paint(self, p: QPainter, option, index) -> None:
        c = self.win.c
        r = QRectF(option.rect)
        key = index.data(Qt.UserRole)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont(option.font)
        if key is None:  # a section title
            f.setBold(True)
            f.setPixelSize(13)
            p.setFont(f)
            p.setPen(QColor(c["text"]))
            p.drawText(r.adjusted(10, 12, -8, 0), Qt.AlignLeft | Qt.AlignVCenter, index.data(Qt.DisplayRole))
            p.restore()
            return
        sel = option.state & QStyle.State_Selected
        hover = option.state & QStyle.State_MouseOver
        box = r.adjusted(2, 1, -2, -1)
        if sel or hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["accent_soft"] if sel else c["hover"]))
            p.drawRoundedRect(box, 8, 8)
        if option.state & QStyle.State_HasFocus and self.win.side.hasFocus():
            p.setPen(QPen(QColor(c["focus_ring"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(box.adjusted(1, 1, -1, -1), 8, 8)
        f.setPixelSize(14)
        p.setFont(f)
        p.setPen(QColor(c["text"]))
        count = index.data(Qt.UserRole + 1) or ""
        cw = p.fontMetrics().horizontalAdvance(count) + 12
        p.drawText(box.adjusted(12, 0, -cw - 6, 0), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(index.data(Qt.DisplayRole), Qt.ElideRight, int(box.width() - cw - 24)))
        p.setPen(QColor(c["muted"]))
        f.setPixelSize(12)
        p.setFont(f)
        p.drawText(box.adjusted(0, 0, -12, 0), Qt.AlignRight | Qt.AlignVCenter, count)
        p.restore()


# --- the window ------------------------------------------------------------------------------------
class _Bridge(QObject):
    """Results of work done off the window's thread, delivered on it."""

    library = Signal(object, object)  # fingerprint, items (None: couldn't be read)
    deleted = Signal(object, object)  # deleted asset ids, [(asset id, why not)]


class PhotosWindow(_Dialog):
    """source() gives (photos database, photos folder), or None when nobody is signed in."""

    favoriteRequested = Signal(list, bool)  # asset ids, favourite
    RELOAD_GAP = 10.0  # seconds at least between two reads of the library while the sync adds photos

    # kept while Skyfold runs
    zoom_level = 2
    sidebar_open = True

    def __init__(self, palette: dict[str, str], source, settings) -> None:
        super().__init__(t("Photos") + " – Skyfold")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)
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
        self._deleting = False
        self.closed = False
        self._bridge = _Bridge(self)
        self._bridge.library.connect(self._library_loaded)
        self._bridge.deleted.connect(self._deleted)

        self.loader = ImageLoader(self)
        self.loader.loaded.connect(self._loaded)
        self.thumbs = Thumbs(self.loader)
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
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.side = QListWidget()
        self.side.setFixedWidth(236)
        self.side.setFrameShape(QFrame.NoFrame)
        self.side.setAccessibleName(t("Collections"))
        self.side.setItemDelegate(SideDelegate(self))
        self.side.setMouseTracking(True)
        self.side.itemClicked.connect(self._side_clicked)
        self.side.currentItemChanged.connect(lambda cur, _old: cur and self._side_clicked(cur))
        root.addWidget(self.side)

        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)

        # browse page: grid or cards, with the header and the switch floating over them
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
        self.cards_area.verticalScrollBar().valueChanged.connect(lambda *_: self._update_header())
        self.inner.addWidget(self.cards_area)
        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.inner.addWidget(self.empty)

        self.header = Header(self, self.browse)
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(16, 10, 16, 18)
        self.fold = _icon_button(I_SIDEBAR, t("Show or hide the sidebar") + " (Ctrl+B)")
        self.fold.clicked.connect(self.toggle_sidebar)
        hl.addWidget(self.fold, 0, Qt.AlignTop)
        tl = QVBoxLayout()
        tl.setSpacing(0)
        self.title = QLabel(t("Library"))
        self.subtitle = QLabel()
        tl.addWidget(self.title)
        tl.addWidget(self.subtitle)
        hl.addLayout(tl, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Search"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self.search.setAccessibleName(t("Search photos"))
        self.search.textChanged.connect(self._search_changed)
        hl.addWidget(self.search, 0, Qt.AlignTop)
        self.select_btn = QPushButton(t("Select"))
        self.select_btn.setCursor(Qt.PointingHandCursor)
        self.select_btn.clicked.connect(lambda: self.set_selecting(not self.selecting))
        hl.addWidget(self.select_btn, 0, Qt.AlignTop)

        self.pill = QWidget(self.browse)
        self.pill.setObjectName("PhotosPill")
        pl = QHBoxLayout(self.pill)
        pl.setContentsMargins(4, 4, 4, 4)
        pl.setSpacing(2)
        self.view_buttons = {}
        for key, label in (("years", t("Years")), ("months", t("Months")), ("all", t("All Photos"))):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.set_view(k))
            pl.addWidget(b)
            self.view_buttons[key] = b
        self.zoom_pill = QWidget(self.browse)
        self.zoom_pill.setObjectName("PhotosPill")
        zl = QHBoxLayout(self.zoom_pill)
        zl.setContentsMargins(4, 4, 4, 4)
        zl.setSpacing(2)
        self.zoom_out = QPushButton("−")
        self.zoom_out.setToolTip(t("Smaller") + " (Ctrl −)")
        self.zoom_out.setAccessibleName(t("Smaller"))
        self.zoom_out.clicked.connect(lambda: self.set_zoom(PhotosWindow.zoom_level + 1))
        self.zoom_in = QPushButton("+")
        self.zoom_in.setToolTip(t("Larger") + " (Ctrl +)")
        self.zoom_in.setAccessibleName(t("Larger"))
        self.zoom_in.clicked.connect(lambda: self.set_zoom(PhotosWindow.zoom_level - 1))
        for b in (self.zoom_out, self.zoom_in):
            b.setFixedWidth(36)
            b.setCursor(Qt.PointingHandCursor)
            zl.addWidget(b)

        self.selbar = QWidget(self.browse)
        self.selbar.setObjectName("PhotosPill")
        sl = QHBoxLayout(self.selbar)
        sl.setContentsMargins(14, 4, 4, 4)
        sl.setSpacing(4)
        self.sel_count = QLabel()
        sl.addWidget(self.sel_count)
        for label, fn in ((t("Select all"), self.select_all), (t("Show in Explorer"), self.show_selected),
                          (t("Copy"), self.copy_selected), (t("Favourite"), self.favorite_selected)):
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            sl.addWidget(b)
        self.del_btn = QPushButton(t("Delete"))
        self.del_btn.setObjectName("Danger")
        self.del_btn.setCursor(Qt.PointingHandCursor)
        self.del_btn.clicked.connect(lambda: self.delete_items(self.chosen_items()))
        sl.addWidget(self.del_btn)
        done = QPushButton(t("Done"))
        done.setCursor(Qt.PointingHandCursor)
        done.clicked.connect(lambda: self.set_selecting(False))
        sl.addWidget(done)
        self.selbar.hide()

        self.toast = QLabel(self)
        self.toast.setObjectName("PhotosToast")
        self.toast.setWordWrap(True)
        self.toast.setAccessibleName(t("Message"))
        self.toast.hide()
        self._toast_timer = QTimer(self, singleShot=True, interval=4500)
        self._toast_timer.timeout.connect(self.toast.hide)

        # viewer page
        self.viewer = QWidget()
        self.viewer.setObjectName("PhotosViewer")
        self.pages.addWidget(self.viewer)
        vl = QVBoxLayout(self.viewer)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)
        self.vtop = QWidget()
        tb = QHBoxLayout(self.vtop)
        tb.setContentsMargins(12, 8, 12, 8)
        self.back = QPushButton()
        self.back.setCursor(Qt.PointingHandCursor)
        self.back.clicked.connect(self.close_viewer)
        tb.addWidget(self.back)
        wl = QVBoxLayout()
        wl.setSpacing(0)
        self.v_date = QLabel()
        self.v_time = QLabel()
        wl.addWidget(self.v_date)
        wl.addWidget(self.v_time)
        tb.addLayout(wl, 1)
        self.v_play = QPushButton(t("Play"))
        self.v_play.setCursor(Qt.PointingHandCursor)
        self.v_play.clicked.connect(self.play_current)
        self.v_fav = _icon_button(I_HEART, t("Favourite") + " (F)")
        self.v_fav.clicked.connect(self.toggle_favorite_current)
        self.v_info = _icon_button(I_INFO, t("Info") + " (I)")
        self.v_info.setCheckable(True)
        self.v_info.toggled.connect(self._show_info)
        self.v_del = _icon_button(I_DELETE, t("Delete") + " (Del)")
        self.v_del.clicked.connect(lambda: self.delete_items([self.viewer_items[self.viewer_row]]))
        self.v_show = QPushButton(t("Show in folder"))
        self.v_show.setCursor(Qt.PointingHandCursor)
        self.v_show.clicked.connect(lambda: self.show_in_explorer([self.viewer_items[self.viewer_row]]))
        self.v_open = QPushButton(t("Open"))
        self.v_open.setCursor(Qt.PointingHandCursor)
        self.v_open.clicked.connect(lambda: self.open_file(self.viewer_items[self.viewer_row].path))
        for w in (self.v_play, self.v_fav, self.v_info, self.v_del, self.v_show, self.v_open):
            tb.addWidget(w)
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
        self.prev = _icon_button(I_PREV, t("Previous") + " (←)", 44)
        self.next = _icon_button(I_NEXT, t("Next") + " (→)", 44)
        for b in (self.prev, self.next):
            b.setFont(QFont(self.font().family(), 22))
            b.setParent(self.picture)
            b.setObjectName("PhotosNav")
        self.prev.clicked.connect(lambda: self.step(-1))
        self.next.clicked.connect(lambda: self.step(1))
        self.strip = FilmStrip(self)
        self.strip.picked.connect(self.show_row)
        vl.addWidget(self.strip)

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
        self.set_zoom(2 if d == 0 else PhotosWindow.zoom_level + d)

    # --- look ----------------------------------------------------------------------------------
    def apply_palette(self, palette: dict[str, str]) -> None:
        c = self.c = palette
        light = _is_light(c)
        raised = c["surface"] if light else ("#1c1c1e" if c["bg"] == "#000000" else c["surface2"])
        ring = c["border"] if light else "rgba(255,255,255,0.12)"
        vbg = "#ffffff" if light else "#000000"
        vfg = c["text"] if light else "#ffffff"
        vbtn = "rgba(0,0,0,0.06)" if light else "rgba(255,255,255,0.12)"
        self.setStyleSheet(
            f"PhotosWindow{{background:{c['bg']}}}"
            f"QListWidget{{background:{c['bg'] if c['bg'] == '#000000' else c['group']};border:none;"
            f"border-right:1px solid {c['border']};padding:10px 8px;outline:0}}"
            f"QListWidget::item{{border:none;background:transparent}}"
            f"QListView{{background:{c['bg']};border:none}}"
            f"QScrollArea, QScrollArea > QWidget > QWidget{{background:{c['bg']}}}"
            f"#PhotosPill{{background:{raised};border:1px solid {ring};border-radius:19px}}"
            f"#PhotosPill QPushButton{{background:transparent;border:none;border-radius:15px;padding:6px 14px;"
            f"color:{c['text']};font-weight:600}}"
            f"#PhotosPill QPushButton:hover{{background:{c['hover']}}}"
            f"#PhotosPill QPushButton:checked{{background:{c['hover']}}}"
            f"#PhotosPill QPushButton#Danger{{background:#c42b1c;color:#ffffff}}"
            f"#PhotosToast{{background:{raised};border:1px solid {ring};border-radius:12px;padding:10px 16px;"
            f"color:{c['text']}}}"
            f"#PhotosViewer{{background:{vbg}}}"
            f"#PhotosViewer QLabel{{color:{vfg};background:transparent}}"
            f"#PhotosViewer QPushButton, #PhotosViewer QToolButton{{background:{vbtn};color:{vfg};border:none;"
            f"border-radius:8px;padding:6px 12px;min-height:22px}}"
            f"#PhotosViewer QToolButton:checked{{background:{c['accent']};color:{c['accent_text']}}}"
            f"#PhotosNav{{border-radius:22px}}"
            f"#PhotosInfo{{background:{c['surface'] if light else '#000000'};color:{vfg};"
            f"border-left:1px solid {c['border'] if light else 'rgba(255,255,255,0.10)'};padding:16px}}"
        )
        self.title.setStyleSheet(f"color:{c['text']};background:transparent;font-size:28px;font-weight:700")
        self.subtitle.setStyleSheet(f"color:{c['muted']};background:transparent;font-weight:600")
        self.empty.setStyleSheet(f"color:{c['muted']};font-size:15px")
        self.header.setAutoFillBackground(False)
        self.v_date.setStyleSheet("font-weight:700;font-size:15px")
        self._fill_side()
        if hasattr(self, "_cards_built"):
            self._show_set(keep=True)
        self.update()

    def viewer_fg(self) -> str:
        return self.c["text"] if _is_light(self.c) else "#ffffff"

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)

    def eventFilter(self, obj, e):  # noqa: N802
        return super().eventFilter(obj, e)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._place_overlays()
        self._layout_grid()
        built = getattr(self, "_cards_for", None)
        if (built and self.inner.currentWidget() is self.cards_area
                and self._card_cols(built[0]) != built[1]):
            self._build_cards(built[0], self.current, built[2])

    def _place_overlays(self) -> None:
        b = self.browse
        self.inner.setGeometry(0, 0, b.width(), b.height())
        self.header.setGeometry(0, 0, b.width(), 76)
        self.header.raise_()
        for w in (self.pill, self.zoom_pill, self.selbar):
            w.adjustSize()
        y = b.height() - self.pill.height() - 18
        gap = 10 if self.zoom_pill.isVisible() else 0
        total = self.pill.width() + (self.zoom_pill.width() + gap if self.zoom_pill.isVisible() else 0)
        x = (b.width() - total) // 2
        self.pill.move(x, y)
        self.zoom_pill.move(x + self.pill.width() + gap, y)
        self.selbar.move((b.width() - self.selbar.width()) // 2, b.height() - self.selbar.height() - 18)
        for w in (self.pill, self.zoom_pill, self.selbar):
            w.raise_()
        pr = self.picture.rect()
        self.prev.move(16, pr.height() // 2 - 22)
        self.next.move(pr.width() - 60, pr.height() // 2 - 22)
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
        if fp == self._fingerprint or self._loading:
            return
        if self._fingerprint is not None and not now and time.monotonic() - self._loaded_at < self.RELOAD_GAP:
            return  # (the poll comes back)
        self._loading = True
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

        def row(key: str, label: str, n: int) -> None:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, key)
            it.setData(Qt.UserRole + 1, f"{n:,}".replace(",", "."))
            it.setData(Qt.AccessibleTextRole, f"{label}, {n}")
            side.addItem(it)
            if key == keep:
                side.setCurrentItem(it)

        header(t("Photos"))
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
        self.query = text.strip()
        self.set_selecting(False)
        self._show_set()

    # --- showing a set -------------------------------------------------------------------------
    def _show_set(self, keep: bool = False) -> None:
        self._cards_built = True
        got = self._set_items()
        self.current = got
        library = self.set_key == "library" and not self.query
        view = self.view if library else "all"
        self.title.setText(t("Search results") if self.query else self._set_name())
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
            if keep and anchor is not None and not at_end:
                self._restore_anchor(anchor)
            elif self.set_key == "recent" or self.query and not library:
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
        w = self.grid.viewport().width()
        if w <= 0:
            return
        cols = COLUMNS[max(0, min(len(COLUMNS) - 1, PhotosWindow.zoom_level))]
        cols = min(cols, max(2, w // 60))  # (fewer in a narrow window)
        size = max(40, w // cols)
        if size != self.tile or self.grid.gridSize().width() != size:
            self.tile = size
            self.grid.setGridSize(QSize(size, size))

    def _anchor(self):
        """The photo at the top of the grid and how far it sits from the top (to keep the place)."""
        idx = self.grid.indexAt(QPoint(4, 84))
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
            a = self.grid.indexAt(QPoint(4, 84))
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
                text = self._range(d1, d2, PhotosWindow.zoom_level <= 1)
            if self.query:
                text = t("{n} results", n=len(self.current)) + " · " + text
            self.subtitle.setText(text)
            self._repaint.start()
        else:
            years = sorted({i.date.year for i in self.current})
            self.subtitle.setText(str(years[0]) if len(years) == 1 else f"{years[0]} – {years[-1]}")

    @staticmethod
    def _range(d1: datetime, d2: datetime, days: bool) -> str:
        if days:
            if d1.date() == d2.date():
                return _day(d1)
            if (d1.year, d1.month) == (d2.year, d2.month):
                return f"{d1.day}.–{d2.day}. {_month(d1.month)} {d1.year}"
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
                     keep_scroll: int | None = None) -> None:
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(22, 84, 22, 90)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)
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
                    lbl.setStyleSheet(f"color:{self.c['text']};font-size:22px;font-weight:700;padding-top:8px")
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
                sb.setValue(target.y() - 84)
            elif keep_scroll is not None:
                sb.setValue(keep_scroll)  # (new photos arrived: the place stays)
            else:
                sb.setValue(sb.maximum())

        QTimer.singleShot(0, scroll)
        QTimer.singleShot(60, scroll)

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

        QTimer.singleShot(0, go)

    def go_back(self) -> None:
        if self.pages.currentWidget() is self.viewer:
            self.close_viewer()
        elif self.set_key == "library" and not self.query and self.view == "all":
            self.set_view("months")
        elif self.set_key == "library" and not self.query and self.view == "months":
            self.set_view("years")

    def toggle_sidebar(self) -> None:
        PhotosWindow.sidebar_open = not self.side.isVisible()
        self.side.setVisible(PhotosWindow.sidebar_open)
        QTimer.singleShot(0, lambda: (self._layout_grid(), self._place_overlays()))

    # --- pictures arriving ---------------------------------------------------------------------
    def _loaded(self, key: str, size: int, full: bool, img: QImage) -> None:
        if self.closed:
            return
        if full:
            if img.isNull():
                cur = self.viewer_items[self.viewer_row] if self.viewer_items else None
                if cur is not None and cur.asset_id == key:
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
            if self.viewer_items and self.viewer_items[self.viewer_row].asset_id == key:
                self.picture.set_picture(pm, True)
            return
        self.thumbs.put(key, size, img)
        self._repaint.start()

    def _repaint_now(self) -> None:
        if self.closed:
            return
        seen = self.thumbs.seen
        self.thumbs.seen = set()
        self.loader.keep_only(seen | ({self.viewer_items[self.viewer_row].asset_id}
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

    def open_file(self, path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def show_in_explorer(self, items: list[lib.PhotoItem]) -> None:
        if not items:
            self.message(t("Select some photos first"))
            return
        try:
            subprocess.Popen(["explorer.exe", "/select,", str(items[0].path)])
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
        for aid in ids:
            if self._pending_fav.get(aid) != value:
                continue  # clicked again meanwhile: that later answer decides
            self._pending_fav.pop(aid, None)
            if aid in failed_set:
                it = self.by_id.get(aid)
                if it is not None:
                    it.favorite = not value
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
        self.back.setText("‹  " + (t("Search results") if self.query else self._set_name()))
        self.pages.setCurrentWidget(self.viewer)
        self._set_bare(False)
        self.show_row(row)
        self.picture.setFocus()

    def _refresh_viewer_list(self, removed: set[str] | None = None) -> None:
        if not self.viewer_items:
            return
        cur = self.viewer_items[self.viewer_row]
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
        pm = self._full.get(it.asset_id)
        if pm is not None:
            self.picture.set_picture(pm, True)
        else:
            self.picture.set_picture(self.thumbs.get(it, 640), False)
            self.want_full()
        self.v_date.setText(f"{_weekday(it.date)}, {_day(it.date)}")
        self.v_time.setText(f"{it.date:%H:%M} · {it.name} · "
                            + t("{a} of {b}", a=f"{self.viewer_row + 1:,}".replace(",", "."),
                                b=f"{len(self.viewer_items):,}".replace(",", ".")))
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
        if it.asset_id not in self._full and not it.is_video:
            self.loader.request(it.asset_id, it.path, 0, full=True)
        for k in (self.viewer_row - 1, self.viewer_row + 1):  # the neighbours, ready to step to
            if 0 <= k < len(self.viewer_items):
                self.thumbs.get(self.viewer_items[k], 640)

    def _update_viewer_bar(self) -> None:
        if not self.viewer_items:
            return
        it = self.viewer_items[self.viewer_row]
        self.v_fav.setText(I_HEART_FILL if it.favorite else I_HEART)
        self.v_fav.setStyleSheet("color:#ff375f" if it.favorite else "")
        self.v_fav.setToolTip((t("Remove from Favourites") if it.favorite else t("Favourite")) + " (F)")
        self.v_play.setVisible(it.is_video or it.live)
        self.v_play.setText("▶  " + (t("Play video") if it.is_video else t("Play Live Photo")))

    def _fill_info(self, it: lib.PhotoItem) -> None:
        muted = "#59636f" if _is_light(self.c) else "#a0a6ae"
        kind = (t("Video") + (f", {_duration(it.duration)}" if it.duration else "") if it.is_video else
                t("Live Photo (photo and 3-second video)") if it.live else
                t("Screenshot") if it.screenshot else t("Photo"))
        rows = [(t("Taken"), f"{_weekday(it.date)}, {_day(it.date)} {it.date:%H:%M}"), (t("Kind"), kind)]
        if it.added and abs((it.added - it.date).total_seconds()) > 86400:
            rows.append((t("Added to iCloud"), f"{_day(it.added)} {it.added:%H:%M}"))
        if it.width and it.height:
            rows.append((t("Size"), f"{it.width:,} × {it.height:,}".replace(",", ".")))
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
        """Only the photo, on black (as tapping a photo on the iPad)."""
        self.vtop.setVisible(not bare)
        self.strip.setVisible(not bare)
        self.info.setVisible(not bare and self.v_info.isChecked())
        self.viewer.setStyleSheet("#PhotosViewer{background:#000000}" if bare else "")
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

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.BackButton:
            self.go_back()
            return
        super().mousePressEvent(e)

    # --- closing -------------------------------------------------------------------------------
    def clear(self) -> None:
        """Signed out or another Apple ID: nothing of the old library stays on screen or in memory."""
        self._forget()
        self.close()

    def _forget(self) -> None:
        self.closed = True
        self._poll.stop()
        self.loader.keep_only(set())
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
        self.side.setVisible(PhotosWindow.sidebar_open)
        QTimer.singleShot(0, lambda: (self._place_overlays(), self._layout_grid()))


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _weekday(d: datetime) -> str:
    return t(WEEKDAYS[d.weekday()])


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
