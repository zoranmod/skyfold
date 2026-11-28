"""The Notes window: read the exported Apple Notes inside Skyfold, like a notes app.

Folders on the left, the notes in the middle (newest first, with date and first line), the note
on the right; search covers titles, folders and the text of the notes. Read-only. The notes come
from the Notes database (which notes exist, and which are locked) and the exported pages (what is
read); see notes_view. The window looks again every two seconds while it is open, so a note that
gets locked, deleted, renamed or moved during a sync is updated (or cleared) within moments.

Private text is held only while the window is open: however it is closed (Esc, the close button,
signing out, another Apple ID), the index, the shown note and its pictures are dropped at once.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (QDateTime, QEvent, QLocale, QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, QUrl,
                            Signal)
from PySide6.QtGui import (QAction, QActionGroup, QColor, QCursor, QDesktopServices, QFont, QFontMetricsF, QImage,
                           QImageIOHandler, QImageReader, QKeySequence, QPainter, QPainterPath, QPalette, QPen,
                           QShortcut, QTextCharFormat, QTextCursor, QTextBlockFormat, QTextDocument,
                           QTextFormat, QTextFrameFormat, QTextLayout, QTextTable)
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QSizePolicy, QSplitter, QStyle, QStyledItemDelegate, QTextBrowser, QTextEdit,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from shiboken6 import isValid

from ..i18n import t
from ..notes_engine import local_datetime
from .. import notes_view as nv
from .dialogs import Combo
from .library_frame import LibraryDialog, PaneCorners, SearchField, count_font
from .panel_parts import SoftCard
from .pills import PILL_SHEET, ZoomPill, _tokens
from .settings_parts import ElidedLabel
from .icons import glyph
from .theme import service_color

log = logging.getLogger(__name__)

ROW_ID = Qt.UserRole
ROW_SNIPPET = Qt.UserRole + 1
FOLDER_PATH = Qt.UserRole
OPENABLE = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".heic", ".heif", ".tif", ".tiff", ".webp", ".bmp",
            ".m4a", ".mp3", ".wav", ".aac", ".caf", ".mov", ".mp4", ".m4v", ".txt", ".csv",
            ".docx", ".xlsx", ".pptx", ".pages", ".numbers", ".key", ".vcf"}  # (no macro-capable .doc/.xls/.ppt/.rtf)
HIGHLIGHTS = ("#ba55d3", "#ff5252", "#ffb74d", "#80cbc4", "#64b5f6")  # Apple's five highlight colours
MAX_MARKS = 300  # search words marked in one note
IMAGE_BUDGET = 400_000_000  # bytes of decoded pictures for the open note (then less sharp, then a note)
SHARP_SHARE = 0.4  # pictures are decoded for this screen's sharpness until this share of it is used
RETRY_INDEX = 30.0  # seconds before pages that couldn't be read are tried again
RETRY_NOTE = 5.0
DOC_SIDE = 40  # the note's padding left and right (the design: 26 above, 40 at the sides)
DOC_LEFT = DOC_SIDE - 4  # (on the left, 4 px of it are the room to grab the line between list and note)
ZOOM_BOTTOM, ZOOM_RIGHT = 14, 24  # the size bar over the note: from its foot, and from its right (the scroll bar)
DOC_BOTTOM = 26 + 36  # under the note's last line: the design's 26, and the size bar's height
HEAD_MARK = "#010203"  # a table's head cells carry this background from the style sheet until polished
MAX_TABLES = 200  # tables of one note drawn as cards (any further ones: text only)


def _blend(a: str, b: str, f: float) -> str:
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() * (1 - f) + cb.red() * f), round(ca.green() * (1 - f) + cb.green() * f),
                  round(ca.blue() * (1 - f) + cb.blue() * f)).name()


def high_contrast() -> bool:
    try:
        hints = QApplication.styleHints()  # kept in a variable: a temporary would be deleted too soon
        acc = hints.accessibility()
        return acc.contrastPreference() == Qt.ContrastPreference.HighContrast
    except Exception:  # noqa: BLE001 - older Qt
        return False


def contrast_colors() -> dict[str, QColor] | None:
    """The Windows contrast theme's own colours (window, text, highlight, highlighted text), or
    None when no contrast theme is on. Read from Windows itself: Skyfold's palette replaces Qt's."""
    if not high_contrast():
        return None
    pal = QApplication.style().standardPalette()
    out = {"window": pal.color(QPalette.Base), "text": pal.color(QPalette.Text),
           "high": pal.color(QPalette.Highlight), "high_text": pal.color(QPalette.HighlightedText)}
    if sys.platform == "win32":
        try:
            import ctypes

            get = ctypes.windll.user32.GetSysColor

            def col(i: int) -> QColor:
                v = get(i)
                return QColor(v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)

            out = {"window": col(5), "text": col(8), "high": col(13), "high_text": col(14)}
        except Exception:  # noqa: BLE001
            pass
    return out


def _alpha(colour: str, a: float) -> QColor:
    q = QColor(colour)
    q.setAlphaF(a)
    return q


def _rgba(q: QColor) -> str:
    return f"rgba({q.red()}, {q.green()}, {q.blue()}, {q.alpha()})"


def _layers(c: dict[str, str]) -> dict[str, QColor]:
    """The design's layers (1.1 mockup: layer-2, hover, stroke, stroke-2, accent-soft, text-2/3).
    Partly see-through, the same over the window's material and over a plain colour, so the
    selected rows and fields never cover the material with a solid colour (OLED included)."""
    if QColor(c["bg"]).lightness() < 128:
        return {"layer2": QColor(255, 255, 255, 20), "hover": QColor(255, 255, 255, 18),
                "stroke": QColor(255, 255, 255, 18), "stroke2": QColor(255, 255, 255, 31),
                "sel": _alpha(c["accent"], 0.16), "sel_hover": _alpha(c["accent"], 0.22),
                "text": QColor(c["text"]), "text2": QColor(c["muted"]), "text3": QColor(c["muted2"])}
    return {"layer2": QColor(255, 255, 255, 235), "hover": QColor(0, 0, 0, 10),
            "stroke": QColor(0, 0, 0, 15), "stroke2": QColor(0, 0, 0, 28),
            "sel": _alpha(c["accent"], 0.10), "sel_hover": _alpha(c["accent"], 0.14),
            "text": QColor(c["text"]), "text2": QColor(c["muted"]), "text3": QColor(c["muted2"])}


def _px_font(base: QFont, px: float, weight=None, families: list[str] | None = None) -> QFont:
    """A font of the design's size in pixels (fractions kept: 12.5, 13.5)."""
    f = QFont(base)
    if families:
        f.setFamilies(families)
    f.setPointSizeF(px * 0.75)
    if weight is not None:
        f.setWeight(weight)
    return f


MONO = ["Cascadia Mono", "Consolas"]
DISPLAY = ["Segoe UI Variable Display", "Segoe UI"]


def _row_colors(c: dict[str, str]) -> dict:
    """Colours of a row in the folder and note lists. With a contrast theme: its own colours, and a
    highlighted row (hovered or chosen) always pairs the highlight with the highlighted text."""
    hc = contrast_colors()
    if hc is not None:
        return {"bg": hc["window"], "text": hc["text"], "muted": hc["text"], "sel": hc["high"],
                "sel_hover": hc["high"], "hover": hc["high"], "on": hc["high_text"], "focus": hc["high_text"],
                "layer2": hc["high"], "bar": hc["high_text"]}
    k = _layers(c)
    return {"bg": None, "text": k["text"], "muted": k["text3"], "sel": k["sel"], "sel_hover": k["sel_hover"],
            "hover": k["hover"], "on": None, "focus": QColor(c["focus_ring"]), "layer2": k["layer2"],
            "bar": QColor(c["accent"])}


def _long_when(iso: str) -> str:
    """'24 November 2026, 07:15' (the note's meta line, as in the design)."""
    from PySide6.QtCore import QDateTime

    stamp = QDateTime.fromString(iso, Qt.ISODate)
    if not stamp.isValid():
        return local_datetime(iso)
    loc = QLocale(QLocale.English, QLocale.UnitedKingdom)
    return loc.toString(stamp.toLocalTime(), "d MMMM yyyy, HH:mm")


def _key(r: nv.Row) -> tuple:
    return (r.rel, r.modified)



BOX_SCHEME = "sfbox"
BOX, BOX_GAP = 18, 10  # the design's checklist: an 18 px box, 10 px before the text
# (one picture: the box and the gap after it, so the text starts at the same place on every line)
def _box_img(on: bool, zoom: float = 1.0) -> str:
    return (f'<img src="{BOX_SCHEME}:{"on" if on else "off"}" width="{round((BOX + BOX_GAP) * zoom)}"'
            f' height="{round(BOX * zoom)}" align="middle">')


CHECK_ON_IMG, CHECK_OFF_IMG = _box_img(True), _box_img(False)


def _check_box(c: dict, on: bool, dpr: float, zoom: float = 1.0):
    """A checklist box as in the design: 18 px, corners of 5, a 1.5 px quiet border; when done,
    filled with the accent and a tick. Drawn at the note's zoom (the text grows, the box too)."""
    size, gap = BOX * zoom, BOX_GAP * zoom
    img = QImage(max(1, round((size + gap) * dpr)), max(1, round(size * dpr)), QImage.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(zoom, zoom)
    box = QRectF(0.75, 0.75, BOX - 1.5, BOX - 1.5)
    if on:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["btn"]))
        p.drawRoundedRect(QRectF(0, 0, BOX, BOX), 5, 5)
        tick = QPainterPath(QPointF(5.0, 9.4))
        tick.lineTo(QPointF(7.8, 12.1))
        tick.lineTo(QPointF(13.0, 6.3))
        p.setPen(QPen(QColor(c["accent_text"]), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(tick)
    else:
        p.setPen(QPen(QColor(c["muted2"]), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(box, 4.4, 4.4)
    p.end()
    return img

class _Indexer(QObject):
    """Reads the note pages in the background for search and the first lines in the list. Results
    carry a generation: anything from an earlier one (the list changed since) is thrown away."""

    batch = Signal(int, object)  # generation, {id: (key, text, folded)}
    done = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.gen = 0
        self._stop = threading.Event()

    def start(self, root: Path, rows: list, known: dict) -> int:
        self.stop()
        gen, stop = self.gen, threading.Event()
        self._stop = stop
        todo = [r for r in rows if not r.locked and r.rel and known.get(r.id, (None,))[0] != _key(r)]

        def run() -> None:
            out: dict = {}
            for r in todo:
                if stop.is_set():
                    return
                p = nv.page_path(root, r)
                try:
                    if p is None or p.stat().st_size > nv.MAX_PAGE_BYTES:
                        text = ""  # nothing to read (or too big): known, not tried again
                    else:
                        text = nv.plain_text(nv.note_html(p.read_text(encoding="utf-8", errors="replace"),
                                                          all_pages=True).body)
                except OSError:
                    continue  # can't be read right now: tried again later
                out[r.id] = (_key(r), text, nv.fold(text))
                if len(out) >= 40:
                    self.batch.emit(gen, out)
                    out = {}
            if not stop.is_set():
                if out:
                    self.batch.emit(gen, out)
                self.done.emit(gen)

        threading.Thread(target=run, name="notes-index", daemon=True).start()
        return gen

    def stop(self) -> None:
        """Results still on their way are ignored from now on."""
        self._stop.set()
        self.gen += 1


def _list_date(value: str, now: datetime | None = None) -> str:
    """The date in the list, short as in the design: the time today, the day this week ("Sat"),
    the day and month this year ("18 Nov"), else the date. Names in English, as the app is; the time
    and the full date as Windows is set."""
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return value
    if when.tzinfo is not None:
        when = when.astimezone()
    today = (now or datetime.now()).date()
    stamp = QDateTime(when)
    system = QLocale.system()
    names = QLocale(QLocale.English, QLocale.UnitedStates)  # "Sep", not "Sept"
    days = (today - when.date()).days
    if days == 0:
        return system.toString(stamp.time(), QLocale.ShortFormat)
    if 0 < days < 6:  # (the design: "Sat" two days ago, "18 Nov" six days ago)
        return names.toString(stamp.date(), "ddd")
    if when.year == today.year:
        return names.toString(stamp.date(), "d MMM")
    return system.toString(stamp.date(), QLocale.ShortFormat)


def _match_ranges(text: str, terms: list[str], limit: int) -> list[tuple[int, int]]:
    """Qt text offsets for accent-insensitive matches, including supplementary characters."""
    folded = nv.fold(text)
    spans = []
    for term in terms:
        i = folded.find(term)
        while i >= 0 and len(spans) < limit:
            spans.append((i, i + len(term)))
            i = folded.find(term, i + len(term))
    offsets = nv.fold_positions(text, [n for span in spans for n in span]) if spans else []
    return list(zip(offsets[::2], offsets[1::2]))


def _list_line(p: QPainter, rect: QRectF, text: str, terms: list[str], mark: QTextCharFormat | None) -> None:
    text = p.fontMetrics().elidedText(text, Qt.ElideRight, max(0, int(rect.width())))
    spans = _match_ranges(text, terms, MAX_MARKS) if terms else []
    if not spans:
        p.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
        return
    layout = QTextLayout(text, p.font(), p.device())
    ranges = []
    for start, end in spans:
        fmt = QTextLayout.FormatRange()
        fmt.start, fmt.length, fmt.format = start, end - start, mark
        ranges.append(fmt)
    layout.setFormats(ranges)
    layout.beginLayout()
    line = layout.createLine()
    line.setLineWidth(rect.width())
    layout.endLayout()
    layout.draw(p, QPointF(rect.left(), rect.center().y() - line.height() / 2))


ROW_COMPACT = Qt.UserRole + 2  # a note known to have no text after its title: a one-line row
NOTE_PAD_V, NOTE_PAD_H, NOTE_GAP = 10, 12, 2  # the design's note row: padding 10/12, 2 px between rows


class _NoteDelegate(QStyledItemDelegate):
    """A note in the list as in the design: the title (600) with the date on the right (mono, quiet),
    the first line of the text under it (12.5 px, quiet); the chosen note on a soft accent layer."""

    def __init__(self, win: "NotesWindow") -> None:
        super().__init__(win)
        self.win = win
        self._fonts_for: tuple | None = None

    def fonts(self, base: QFont) -> tuple[QFont, QFont, QFont, float, float]:
        # Asked for every row at every layout (thousands of times while the notes are being read):
        # comparing with the font kept from last time costs a fraction of spelling the font out.
        kept = self._fonts_for
        if kept is None or kept[1] != NotesWindow.zoom or kept[0] != base:
            title = _px_font(base, 14, QFont.DemiBold)
            snip = _px_font(base, 12.5)
            date = _px_font(base, 11.5, families=MONO)
            line = max(QFontMetricsF(title).height(), 14 * 1.45)  # the design's line: 1.45 of 14 px
            self._fonts = (title, snip, date, line, max(QFontMetricsF(snip).height(), line))
            self._fonts_for = (QFont(base), NotesWindow.zoom)
        return self._fonts

    def sizeHint(self, opt, idx) -> QSize:  # noqa: N802
        _t, _s, _d, h1, h2 = self.fonts(opt.font)
        body = h1 if idx.data(ROW_COMPACT) else h1 + h2
        return QSize(160, round(2 * NOTE_PAD_V + body + NOTE_GAP))

    def paint(self, p: QPainter, opt, idx) -> None:
        row: nv.Row | None = self.win.row_by_id.get(idx.data(ROW_ID))
        if row is None:
            return
        k = _row_colors(self.win.c)
        text, muted, focus = k["text"], k["muted"], k["focus"]
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        if k["bg"] is not None:
            p.fillRect(opt.rect, k["bg"])
        r = QRectF(opt.rect).adjusted(0, 0, 0, -NOTE_GAP)
        selected = bool(opt.state & QStyle.State_Selected)
        hovered = bool(opt.state & QStyle.State_MouseOver)
        if selected or hovered:
            p.setPen(Qt.NoPen)
            p.setBrush((k["sel_hover"] if hovered else k["sel"]) if selected else k["hover"])
            p.drawRoundedRect(r, 6, 6)
        if (selected or hovered) and k["on"] is not None:
            text = muted = k["on"]
        if selected and self.win.notes.hasFocus() and getattr(self.win, "keyboard_used", False):
            p.setPen(QPen(focus, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 6, 6)
        title_f, snip_f, date_f, h1, h2 = self.fonts(opt.font)
        x, w = r.left() + NOTE_PAD_H, r.width() - 2 * NOTE_PAD_H
        top = r.top() + NOTE_PAD_V
        when = _list_date(row.modified) if row.modified else ""
        p.setFont(date_f)
        dw = min(p.fontMetrics().horizontalAdvance(when) + 10, int(w * 0.46)) if when else 0
        if when:
            p.setPen(muted)
            p.drawText(QRectF(x + w - dw, top, dw, h1), Qt.AlignRight | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(when, Qt.ElideRight, max(0, dw - 4)))
        p.setFont(title_f)
        p.setPen(text)
        title = row.title or t("Untitled")
        terms = self.win._terms()
        mark = self.win._match_format() if terms else None
        _list_line(p, QRectF(x, top, max(0, w - dw), h1), title, terms, mark)
        second = ("\U0001F512 " + t("Locked note")) if row.locked else (idx.data(ROW_SNIPPET) or "")
        if second and not idx.data(ROW_COMPACT):
            p.setFont(snip_f)
            p.setPen(muted)
            _list_line(p, QRectF(x, top + h1, w, h2), second, terms, mark)
        p.restore()


FOLDER_COUNT = Qt.UserRole + 1
CHEVRON = 26  # px at the right end of a folder row that has subfolders: click to fold it


FOLDER_ROW = 34  # the design's sidebar row
GROUP_LABEL = 32  # room for the "iCloud" label above the folders (margin 14, 12 px text, margin 6)


def _labelled(idx) -> bool:
    """The first folder after "All notes" carries the "iCloud" label above it."""
    return not idx.parent().isValid() and idx.row() == 1


class _FolderTree(QTreeWidget):
    """Folders as in Apple Notes: one rounded highlight over the whole row (no separate box for
    the indent), the count on the right, a chevron on the right for folders with subfolders."""

    def _on_label(self, it, y: float) -> bool:
        if it is None:
            return False
        idx = self.indexFromItem(it)
        return _labelled(idx) and y < self.visualRect(idx).top() + GROUP_LABEL

    def mousePressEvent(self, e) -> None:  # noqa: N802
        it = self.itemAt(e.position().toPoint())
        if self._on_label(it, e.position().y()):
            e.accept()  # the label is not a part of the folder under it
            return
        if (it is not None and it.childCount() and e.button() == Qt.LeftButton
                and e.position().x() >= self.viewport().width() - CHEVRON - 6):
            it.setExpanded(not it.isExpanded())
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if self._on_label(self.itemAt(e.position().toPoint()), e.position().y()):
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        super().mouseMoveEvent(e)
        self.viewport().update()  # (the hover highlight leaves the label's room at once)


class _FolderDelegate(QStyledItemDelegate):
    """A sidebar row as in the design: 34 px, the Notes-amber icon, the name, the count in mono on
    the right; the chosen folder on a lighter layer with a 3 px accent bar."""

    def __init__(self, win: "NotesWindow") -> None:
        super().__init__(win)
        self.win = win

    def sizeHint(self, opt, idx) -> QSize:  # noqa: N802
        row = max(FOLDER_ROW, round(QFontMetricsF(opt.font).height()) + 14)
        return QSize(120, row + (GROUP_LABEL if _labelled(idx) else 0))

    def paint(self, p: QPainter, opt, idx) -> None:
        tree = self.win.folders
        k = _row_colors(self.win.c)
        text, muted, hover, focus = k["text"], k["muted"], k["hover"], k["focus"]
        depth, parent = 0, idx.parent()
        while parent.isValid():
            depth, parent = depth + 1, parent.parent()
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        full = QRectF(0, opt.rect.top(), tree.viewport().width(), opt.rect.height())
        if k["bg"] is not None:
            p.fillRect(full, k["bg"])
        r = QRectF(full)
        if _labelled(idx):
            label = QRectF(full.left() + 10, full.top() + 10, full.width() - 20, 20)
            p.setFont(_px_font(opt.font, 12, QFont.DemiBold))
            p.setPen(k["muted"])
            p.drawText(label, Qt.AlignLeft | Qt.AlignVCenter, "iCloud")
            r.setTop(full.top() + GROUP_LABEL)
        selected = bool(opt.state & QStyle.State_Selected)
        hovered = bool(opt.state & QStyle.State_MouseOver)
        if hovered and _labelled(idx):
            hovered = tree.viewport().mapFromGlobal(QCursor.pos()).y() >= r.top()
        lit = selected or hovered
        if lit:
            p.setPen(Qt.NoPen)
            p.setBrush(k["layer2"] if selected else hover)
            p.drawRoundedRect(r, 6, 6)
        if selected:
            p.setPen(Qt.NoPen)
            p.setBrush(k["bar"])
            p.drawRoundedRect(QRectF(r.left(), r.top() + 9, 3, r.height() - 18), 1.5, 1.5)
        if lit and k["on"] is not None:
            text = muted = k["on"]
        if selected and tree.hasFocus() and getattr(self.win, "keyboard_used", False):
            p.setPen(QPen(focus, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 6, 6)
        fm = opt.fontMetrics
        right = r.right() - 10
        room_for_indent = max(0.0, r.width() - 10 - 10 - CHEVRON - 28 - 70)  # a name keeps at least ~70 px
        x = r.left() + 10 + min(depth * 18, room_for_indent)
        has_kids = idx.model().hasChildren(idx)
        if has_kids:  # chevron: down when open, right when folded
            cx, cy = right - CHEVRON / 2 + 4, r.center().y()
            pen = QPen(muted, 1.6)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            if tree.isExpanded(idx):
                p.drawPolyline([QPointF(cx - 4, cy - 2), QPointF(cx, cy + 2), QPointF(cx + 4, cy - 2)])
            else:
                p.drawPolyline([QPointF(cx - 2, cy - 4), QPointF(cx + 2, cy), QPointF(cx - 2, cy + 4)])
            right -= CHEVRON
        icon = idx.data(Qt.DecorationRole)
        if icon is not None:
            icon.paint(p, int(x), int(r.center().y() - 9), 18, 18)
            x += 28
        count = idx.data(FOLDER_COUNT)
        cw = 0
        if count is not None:
            label = str(count)
            p.setFont(count_font(opt.font))
            cw = p.fontMetrics().horizontalAdvance(label) + 6
            p.setPen(muted)
            p.drawText(QRectF(right - cw, r.top(), cw, r.height()), Qt.AlignRight | Qt.AlignVCenter, label)
        p.setPen(text)
        p.setFont(opt.font)
        room = max(0, right - cw - 6 - x)
        p.drawText(QRectF(x, r.top(), room, r.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(idx.data(Qt.DisplayRole) or "", Qt.ElideRight, int(room)))
        p.restore()


def _restyle(doc: QTextDocument, block, fmt: QTextCharFormat) -> None:
    """Give a block's text these properties. Qt's own size step for h1-h6 (a size adjustment)
    would win over any size given, so it is taken off first, piece by piece."""
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        if frag.isValid():
            cf = frag.charFormat()
            cf.clearProperty(QTextFormat.FontSizeAdjustment)
            cf.merge(fmt)
            cur = QTextCursor(doc)
            cur.setPosition(frag.position())
            cur.setPosition(frag.position() + frag.length(), QTextCursor.KeepAnchor)
            cur.setCharFormat(cf)
        it += 1


class _Reader(QTextBrowser):
    """The note itself. Pictures arrive as sfimg:<n> ids only (never file names), each mapped to a
    file checked to be in the Notes folder's _assets; decoded at the size shown, on white so
    drawings and scans stay readable in dark themes. Links open only on a click."""

    def __init__(self, win: "NotesWindow") -> None:
        super().__init__()
        self.win = win
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.page: Path | None = None
        self.images: list[str] = []
        # decoded pictures by (file, its size and time, width decoded at, zoom): a picture id can
        # point to another file after "show more", so ids are never the key
        self._cache: dict[tuple, QImage] = {}
        self._bytes = 0
        self._geometry: tuple = ()

    def _drop_cache(self) -> None:
        self._cache.clear()
        self._bytes = 0

    def _new_document(self, css: str = "", font: QFont | None = None, body: str = "", title: str = "") -> None:
        """Every note gets a document of its own. The old one is never emptied in place: Qt may still
        be laying it out, painting it or answering a screen reader about it, so it is let go and
        deleted once Qt is done with whatever it was doing (emptying it in place crashed Qt)."""
        self.setExtraSelections([])
        self.table_heads = {}
        doc = QTextDocument(self)  # its pictures are asked from this viewer (loadResource)
        if css:
            doc.setDefaultStyleSheet(css)
        if font is not None:
            doc.setDefaultFont(font)
        zoom = NotesWindow.zoom
        doc.setHtml(body.replace(nv.CHECK_ON, _box_img(True, zoom)).replace(nv.CHECK_OFF, _box_img(False, zoom)))
        if title and font is not None:
            cur = QTextCursor(doc)
            if cur.block().text().strip() != title.strip():
                # Some exports begin with the body instead of the note title. Insert literal text
                # while keeping the first original block's formatting and its links intact.
                original = cur.blockFormat()
                cur.insertText(title, QTextCharFormat())
                cur.insertBlock(original)
                cur.movePosition(QTextCursor.Start)
                cur.setBlockFormat(QTextBlockFormat())
            heading = QTextCharFormat()  # the design's title: 600, 26 px, Segoe UI Variable Display
            heading.setFontFamilies(DISPLAY)
            heading.setFontPointSize(font.pointSizeF() * 26 / 14)
            heading.setFontWeight(QFont.DemiBold)
            heading.setForeground(QColor(self.win.c["text"]))
            _restyle(doc, cur.block(), heading)
            block = cur.blockFormat()
            block.setHeadingLevel(1)
            block.setTopMargin(6 * zoom)
            block.setBottomMargin(12 * zoom)
            block.setLineHeight(92, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)  # (26 px, 1.2)
            cur.setBlockFormat(block)
        if css:
            self._polish(doc, zoom, bool(title and font is not None))
        old = self.document()
        self.setDocument(doc)
        if old is not None and old is not doc and isValid(old) and old.parent() is self:
            old.deleteLater()  # (Qt itself deletes the first, built-in one)

    def _polish(self, doc: QTextDocument, zoom: float, title: bool) -> None:
        """What CSS can't say to Qt: the note's padding, checklist rows (ticked ones struck through
        and dimmed) and tables as the design's cards (only lines between rows, the head row on a
        lighter layer; the rounded card itself is painted by paintEvent)."""
        k = _layers(self.win.c)
        root = doc.rootFrame()
        fmt = root.frameFormat()
        fmt.setLeftMargin(DOC_LEFT)
        fmt.setRightMargin(DOC_SIDE)
        fmt.setTopMargin(0)
        fmt.setBottomMargin(DOC_BOTTOM)  # (the end of a note scrolls clear of the size bar)
        root.setFrameFormat(fmt)
        # checklists: 28 px rows (8 px between items), a ticked item struck through in the quiet colour
        done = QTextCharFormat()
        done.setFontStrikeOut(True)
        done.setForeground(k["text3"])
        item = QTextCharFormat()
        item.setForeground(k["text"])
        sizes = {1: 20, 2: 17}  # the note's own headings (its title is the first block, set apart)
        block = doc.begin().next() if title else doc.begin()
        while block.isValid():
            level = block.blockFormat().headingLevel()
            if level:  # Qt adds its own size step to h1-h6: the design's sizes, set here
                head = QTextCharFormat()
                head.setFontPointSize(sizes.get(level, 14) * 0.75 * zoom)
                head.setFontWeight(QFont.DemiBold)
                _restyle(doc, block, head)
            block = block.next()
        block = doc.begin()
        while block.isValid():
            text = block.text()
            if text[:1] == "\ufffc":  # (a picture first: a checklist box?)
                it = block.begin()
                first = it.fragment() if not it.atEnd() else None
                name = first.charFormat().toImageFormat().name() if first is not None and first.isValid() else ""
                if name.startswith(BOX_SCHEME + ":"):
                    cur = QTextCursor(block)
                    bf = cur.blockFormat()
                    bf.setTopMargin(4 * zoom)
                    bf.setBottomMargin(4 * zoom)
                    cur.setBlockFormat(bf)
                    cur.setPosition(block.position() + 1)
                    cur.setPosition(block.position() + block.length() - 1, QTextCursor.KeepAnchor)
                    cur.mergeCharFormat(done if name == BOX_SCHEME + ":on" else item)
            block = block.next()
        for table in self._tables(doc):
            tf = table.format()
            tf.setBorder(0)
            tf.setBorderStyle(QTextFrameFormat.BorderStyle_None)
            tf.setCellSpacing(0)
            tf.setCellPadding(0)
            tf.setMargin(0)
            tf.setTopMargin(2 * zoom)
            tf.setBottomMargin(14 * zoom)
            tf.setLeftMargin(1)
            table.setFormat(tf)
            heads = 0
            for r in range(table.rows()):
                head_row = True
                for col in range(table.columns()):
                    cell = table.cellAt(r, col)
                    cf = cell.format().toTableCellFormat()
                    is_head = cf.background().color() == QColor(HEAD_MARK)
                    if cell.row() == r and cell.column() == col:
                        if is_head:
                            self._clear_marks(doc, cell)
                        cf.clearBackground()
                        cf.setBorder(0)
                        cf.setTopPadding(7 * zoom)
                        cf.setBottomPadding(7 * zoom)
                        cf.setLeftPadding(14 * zoom)
                        cf.setRightPadding(14 * zoom)
                        cf.setVerticalAlignment(QTextCharFormat.AlignTop)
                        cell.setFormat(cf)
                    head_row = head_row and is_head
                if head_row and heads == r:
                    heads += 1
            self.table_heads[table.firstPosition()] = heads

    @staticmethod
    def _clear_marks(doc: QTextDocument, cell) -> None:
        """The head cell's marker colour reached its text too: taken off again."""
        start, end = cell.firstCursorPosition().position(), cell.lastCursorPosition().position()
        block = doc.findBlock(start)
        while block.isValid() and block.position() <= end:
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid() and frag.charFormat().background().color() == QColor(HEAD_MARK):
                    fmt = frag.charFormat()
                    fmt.clearBackground()
                    cur = QTextCursor(doc)
                    cur.setPosition(frag.position())
                    cur.setPosition(frag.position() + frag.length(), QTextCursor.KeepAnchor)
                    cur.setCharFormat(fmt)
                it += 1
            block = block.next()

    @staticmethod
    def _tables(doc: QTextDocument, limit: int | None = None) -> list[QTextTable]:
        out, stack = [], [doc.rootFrame()]
        while stack and (limit is None or len(out) < limit):
            frame = stack.pop()
            for child in frame.childFrames():
                if isinstance(child, QTextTable):
                    out.append(child)
                stack.append(child)
        return out

    def _table_cards(self) -> list[tuple[QRectF, list[float], float]]:
        """Each table on screen: its box, where the lines between rows go, and where its head row
        ends (in viewport coordinates)."""
        doc = self.document()
        heads = getattr(self, "table_heads", {})
        if not heads:
            return []
        layout = doc.documentLayout()
        dx, dy = -self.horizontalScrollBar().value(), -self.verticalScrollBar().value()
        view = QRectF(self.viewport().rect())
        out = []
        for table in self._tables(doc, MAX_TABLES):
            if table.firstPosition() not in heads or not table.rows() or not table.columns():
                continue
            box = layout.frameBoundingRect(table)
            tops = []
            for r in range(table.rows()):
                ys = []
                for col in range(table.columns()):
                    cell = table.cellAt(r, col)
                    if cell.row() == r and cell.column() == col:
                        b = layout.blockBoundingRect(cell.firstCursorPosition().block())
                        ys.append(b.top() - cell.format().toTableCellFormat().topPadding())
                tops.append(min(ys) if ys else None)
            first = table.cellAt(0, 0)
            b0 = layout.blockBoundingRect(first.firstCursorPosition().block())
            left = b0.left() - first.format().toTableCellFormat().leftPadding()
            top = tops[0] if tops[0] is not None else box.top()
            bottom = top
            for r in range(table.rows()):
                for col in range(table.columns()):
                    cell = table.cellAt(r, col)
                    if cell.row() == r and cell.column() == col:
                        b = layout.blockBoundingRect(cell.lastCursorPosition().block())
                        bottom = max(bottom, b.bottom() + cell.format().toTableCellFormat().bottomPadding())
            # (Qt places the box at the first cell's text: its width is right, its place is not)
            rect = QRectF(left, top, box.width(), bottom - top).translated(dx, dy)
            if not rect.intersects(view):
                continue
            lines = [y + dy for y in tops[1:] if y is not None]
            n = heads[table.firstPosition()]
            head = (tops[n] + dy) if 0 < n < len(tops) and tops[n] is not None else (rect.bottom() if n else rect.top())
            out.append((rect, lines, head))
        return out

    def paintEvent(self, e) -> None:  # noqa: N802
        cards = self._table_cards()
        if cards:  # the head rows' layer, under the text
            k = _layers(self.win.c)
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.Antialiasing)
            for rect, _lines, head in cards:
                if head > rect.top():
                    path = QPainterPath()
                    path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
                    band = QPainterPath()
                    band.addRect(QRectF(rect.left(), rect.top(), rect.width(), head - rect.top()))
                    p.fillPath(path.intersected(band), k["layer2"])
            p.end()
        super().paintEvent(e)
        if cards:  # the card's hairline and the lines between rows, over it
            k = _layers(self.win.c)
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.Antialiasing)
            for rect, lines, _head in cards:
                p.setPen(QPen(k["stroke"], 1))
                for y in lines:
                    yy = round(y) - 0.5
                    p.drawLine(QPointF(rect.left() + 1, yy), QPointF(rect.right() - 1, yy))
                p.setPen(QPen(k["stroke2"], 1))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(QRectF(round(rect.left()) + 0.5, round(rect.top()) + 0.5,
                                         round(rect.width()) - 1, round(rect.height()) - 1), 8, 8)
            p.end()

    def show_page(self, page: Path, clean: nv.Page, keep_pictures: bool, css: str = "",
                  font: QFont | None = None, title: str = "") -> None:
        if self.page != page or not keep_pictures:
            self._drop_cache()
        self.page = page
        self.images = list(clean.images)
        self._new_document(css, font, clean.body, title)

    def clear_note(self) -> None:
        self.page = None
        self.images = []
        self._drop_cache()
        self._new_document()

    def wheelEvent(self, e) -> None:  # noqa: N802
        if e.modifiers() & Qt.ControlModifier:  # Ctrl + wheel: zoom, as in a browser
            if e.angleDelta().y():
                step = 1 if e.angleDelta().y() > 0 else -1
                QTimer.singleShot(0, lambda: self.win.set_zoom(step))  # after Qt is done with this event
            e.accept()
            return
        super().wheelEvent(e)

    def loadResource(self, kind: int, url: QUrl):  # noqa: N802
        if kind == QTextDocument.ImageResource and url.scheme() == BOX_SCHEME:
            return _check_box(self.win.c, url.path() == "on", self.devicePixelRatioF() or 1.0, NotesWindow.zoom)
        if kind != QTextDocument.ImageResource or self.page is None or self.win.root is None:
            return None
        if url.scheme() != nv.IMAGE_SCHEME or not url.path().isdigit():
            return None
        n = int(url.path())
        if n >= len(self.images):
            return None
        dpr = self.devicePixelRatioF() or 1.0
        room = max(200, self.viewport().width() - DOC_LEFT - DOC_SIDE)
        zoom = NotesWindow.zoom
        geometry = (int(room * dpr), zoom)
        if geometry != self._geometry:  # pictures decoded for another width or zoom aren't shown again
            self._drop_cache()
            self._geometry = geometry
        f = nv.resolve_asset(self.win.root, self.page, self.images[n])
        try:
            st = f.stat() if f is not None else None
        except OSError:
            st = None
        if st is None or not f.is_file():
            return None
        key = (str(f), st.st_size, st.st_mtime_ns)
        if key in self._cache:
            return self._cache[key]
        reader = QImageReader(str(f))
        reader.setAutoTransform(True)
        if bytes(reader.format()).lower() in (b"svg", b"svgz"):
            return None  # (a drawing that can load other things: only real pictures are shown)
        raw = reader.size()  # as stored (a photo taken upright may be stored sideways)
        disp = raw.transposed() if reader.transformation() & QImageIOHandler.TransformationRotate90 else raw
        if raw.isValid() and disp.width() > 0:
            # as wide as a browser shows it (one image pixel per screen point), times the zoom,
            # never wider than the note; decoded at exactly that size for this screen
            shown = min(room, disp.width() * zoom)
            scale = dpr
            free = IMAGE_BUDGET - self._bytes
            need = lambda s: 4 * (shown * s / disp.width()) ** 2 * raw.width() * raw.height()  # noqa: E731
            if self._bytes + need(scale) > IMAGE_BUDGET * SHARP_SHARE:
                scale = 1.0  # a note with very many big pictures: the rest less sharp, not missing
            if need(scale) > free:
                return self._placeholder(shown, shown * disp.height() / disp.width())
            k = shown * scale / disp.width()
            reader.setScaledSize(QSize(max(1, round(raw.width() * k)), max(1, round(raw.height() * k))))
        elif raw.isValid() or 4 * 4000 * 4000 > IMAGE_BUDGET - self._bytes:
            return None
        img = reader.read()
        if img.isNull():
            return None  # not cached: tried again next time the note is shown
        if img.hasAlphaChannel():
            base = QImage(img.size(), QImage.Format_RGB32)
            base.fill(QColor("#ffffff"))
            q = QPainter(base)
            q.drawImage(0, 0, img)
            q.end()
            img = base
        if raw.isValid() and disp.width() > 0:
            img.setDevicePixelRatio(img.width() / min(room, disp.width() * zoom))
        else:
            img.setDevicePixelRatio(dpr)
        self._bytes += img.sizeInBytes()
        self._cache[key] = img
        return img

    def _placeholder(self, width: float, height: float) -> QImage:
        """Shown instead of a picture that doesn't fit in memory any more (not kept), as big as the
        picture would be, drawn at half sharpness to stay small."""
        w, h = max(120, int(width)), max(64, int(height))
        img = QImage(max(1, w // 2), max(1, h // 2), QImage.Format_RGB32)
        img.setDevicePixelRatio(0.5)
        img.fill(QColor(self.win.c["hover"]))
        p = QPainter(img)
        p.setPen(QColor(self.win.c["muted"]))
        p.drawRect(0, 0, w - 1, h - 1)
        p.drawText(QRectF(12, 0, w - 24, h), Qt.AlignVCenter | Qt.TextWordWrap,
                   t("Too many large pictures to show here. Open the note in your browser to see this one."))
        p.end()
        return img


class _Pane(SoftCard):
    """The one raised layer holding the list and the note: corners of 8, the bottom right one 12
    (it follows the window's own corner), a hairline around it."""

    def card_path(self) -> QPainterPath:
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -1.5)
        tl = tr = bl = 8.0
        br = 12.0
        path = QPainterPath(QPointF(r.left() + tl, r.top()))
        path.lineTo(r.right() - tr, r.top())
        path.arcTo(QRectF(r.right() - 2 * tr, r.top(), 2 * tr, 2 * tr), 90, -90)
        path.lineTo(r.right(), r.bottom() - br)
        path.arcTo(QRectF(r.right() - 2 * br, r.bottom() - 2 * br, 2 * br, 2 * br), 0, -90)
        path.lineTo(r.left() + bl, r.bottom())
        path.arcTo(QRectF(r.left(), r.bottom() - 2 * bl, 2 * bl, 2 * bl), 270, -90)
        path.lineTo(r.left(), r.top() + tl)
        path.arcTo(QRectF(r.left(), r.top(), 2 * tl, 2 * tl), 180, -90)
        path.closeSubpath()
        return path


def _focus_ring(p: QPainter, rect: QRectF, colour: QColor, radius: float = 6) -> None:
    p.setPen(QPen(colour, 2))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)


def _by_keyboard(e) -> bool:
    return e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)


class _QuietButton(QToolButton):
    """The "more" button over the note: only its icon in the quiet colour, a soft rounded layer
    under the pointer (painted smoothly; a style sheet's rounded corners are jagged)."""

    def __init__(self, win: "NotesWindow") -> None:
        super().__init__()
        self.win = win
        self.setFixedSize(32, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)
        self.setFocusPolicy(Qt.TabFocus)
        self._keyboard = False

    def focusInEvent(self, e) -> None:  # noqa: N802
        self._keyboard = _by_keyboard(e)
        super().focusInEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        k = _layers(self.win.c)
        hc = contrast_colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        on = self.isDown() or self.underMouse()
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(hc["high"] if hc else k["layer2"] if self.isDown() else k["hover"])
            p.drawRoundedRect(r, 6, 6)
        colour = (hc["high_text"] if on else hc["text"]) if hc else (k["text"] if on else k["text2"])
        glyph("more", colour.name(), 20).paint(p, self.rect().adjusted(6, 6, -6, -6))
        if self.hasFocus() and self._keyboard:
            _focus_ring(p, r, QColor(self.win.c["focus_ring"]))
        p.end()


class _QuietCombo(Combo):
    """The order of the list, at its foot: quiet text with a small chevron, a soft layer under
    the pointer; the same list of choices as any Skyfold combo box."""

    def __init__(self, win: "NotesWindow") -> None:
        super().__init__()
        self.win = win
        self.setFixedHeight(28)
        self.setAttribute(Qt.WA_Hover)
        self.setFocusPolicy(Qt.StrongFocus)
        self._keyboard = False

    def _font(self) -> QFont:
        return _px_font(self.font(), 12)

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetricsF(self._font())
        widest = max((fm.horizontalAdvance(self.itemText(i)) for i in range(self.count())), default=60)
        return QSize(round(widest) + 8 + 6 + 10 + 8, 28)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def focusInEvent(self, e) -> None:  # noqa: N802
        self._keyboard = _by_keyboard(e)
        super().focusInEvent(e)

    def showPopup(self) -> None:  # noqa: N802
        super().showPopup()
        pop = self.view().window()
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None and pop.geometry().bottom() > screen.bottom():  # at the foot: open upwards
            pop.move(pop.x(), self.mapToGlobal(QPoint(0, 0)).y() - pop.height() - 4)

    def paintEvent(self, _e) -> None:  # noqa: N802
        k = _layers(self.win.c)
        hc = contrast_colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        on = self.underMouse() or self.view().isVisible()
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(hc["high"] if hc else k["hover"])
            p.drawRoundedRect(r, 6, 6)
        colour = (hc["high_text"] if on else hc["text"]) if hc else (k["text2"] if on else k["text3"])
        p.setFont(self._font())
        p.setPen(colour)
        text_rect = r.adjusted(8, 0, -24, 0)
        p.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.currentText(), Qt.ElideRight, int(text_rect.width())))
        cx, cy = r.right() - 14, r.center().y()
        pen = QPen(colour, 1.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawPolyline([QPointF(cx - 3.5, cy - 1.75), QPointF(cx, cy + 1.75), QPointF(cx + 3.5, cy - 1.75)])
        if self.hasFocus() and self._keyboard:
            _focus_ring(p, r, QColor(self.win.c["focus_ring"]))
        p.end()


class _StatusLabel(ElidedLabel):
    """How many notes were found, quiet at the right of the list's foot."""

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setFont(self.font())
        group = QPalette.Active if self.isEnabled() else QPalette.Disabled
        p.setPen(self.palette().color(group, QPalette.WindowText))
        r = self.contentsRect()
        p.drawText(r, Qt.AlignRight | Qt.AlignVCenter, self.fontMetrics().elidedText(self.text(), self.mode, r.width()))


class NotesWindow(LibraryDialog):
    """source() gives (Notes database, Notes folder), or None when Notes isn't set up or nobody
    is signed in."""

    # kept while Skyfold runs (for every Notes window)
    zoom = 1.0  # text size of the note
    sort = "new"  # new | old | title
    own_theme: str | None = None  # None: as Skyfold; or light | dark | oled for this window only
    THEMES = (None, "light", "dark", "oled")
    resume: dict | None = None  # folder, note and scroll position when the window was last closed
    ZOOMS = (0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.4, 1.6, 1.8, 2.0, 2.4)

    def __init__(self, palette: dict[str, str], source) -> None:
        super().__init__(t("Notes") + " \u2013 Skyfold")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)  # can fill the screen
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.c = palette
        self.app_c = palette  # Skyfold's own theme (followed unless the window has its own)
        self.source = source
        self.root: Path | None = None
        self.rows: list[nv.Row] = []
        self.row_by_id: dict[str, nv.Row] = {}
        self.texts: dict[str, tuple] = {}  # id -> (key, text, folded)
        self.indexing = False
        self.off = False
        self.closed = False
        self.current: str | None = None  # id of the note shown
        self.current_key: tuple | None = None
        self.shown_pages: dict[int, int] = {}  # scanned document -> pages shown (open note)
        self._fingerprint = None
        self._read_at: tuple | None = None  # (database, its change count, folder) when the list was last read
        self._index_started = 0.0
        self._note_failed = 0.0  # when the open note couldn't be read (0 = it could)
        self.folder = ""  # "" = all notes
        self._hl_query = ""
        self._resume_id: str | None = None
        self._resume_pos: int | None = None
        self._pending_anchor: int | None = None
        r = NotesWindow.resume
        if r:  # back where you were
            self.folder, self.current = r.get("folder") or "", r.get("id")
            self._resume_id, self._resume_pos = r.get("id"), r.get("pos")

        self.indexer = _Indexer()
        self.indexer.batch.connect(self._on_batch)
        self.indexer.done.connect(self._on_index_done)

        # the design's frame: the sidebar on the window's material, the content on one raised layer
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 8, 8)
        lay.setSpacing(0)
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Search notes"))
        self.search.setToolTip(t("Search notes (Ctrl+K)"))
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName(t("Search notes"))
        self.search.setMinimumWidth(120)
        self.search.returnPressed.connect(lambda: self.notes.setFocus())
        self.search_field = SearchField(self.search, palette)
        self.status = _StatusLabel("", Qt.ElideRight)
        self.status.setObjectName("NoteStatus")
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.status.setMinimumWidth(0)

        self.split = QSplitter(Qt.Horizontal)
        self.split.setHandleWidth(4)
        self.folders = _FolderTree()
        self.folders.setProperty("tipForClippedItems", True)
        self.folders.setHeaderHidden(True)
        self.folders.setIndentation(0)  # the delegate indents; the highlight spans the whole row
        self.folders.setRootIsDecorated(False)
        self.folders.setItemDelegate(_FolderDelegate(self))
        self.folders.setMouseTracking(True)
        self.folders.itemExpanded.connect(lambda _it: self.folders.viewport().update())
        self.folders.itemCollapsed.connect(self._on_fold)
        self.folders.setAccessibleName(t("Folders"))
        self.folders.setMinimumWidth(110)
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(8, 4, 4, 0)  # (with the splitter's 4 px: the design's 8 px either side)
        side.setSpacing(0)
        side.addWidget(self.folders, 1)
        self.notes = QListWidget()
        self.notes.setAccessibleName(t("Notes"))
        self.notes.setItemDelegate(_NoteDelegate(self))
        self.notes.setMouseTracking(True)
        self.notes.setUniformItemSizes(False)  # a note without text gets a one-line row
        self.notes.setVerticalScrollMode(QListWidget.ScrollPerPixel)

        middle = QWidget()
        middle.setMinimumWidth(190)
        ml = QVBoxLayout(middle)
        ml.setContentsMargins(10, 10, 10, 6)
        ml.setSpacing(10)
        ml.addWidget(self.search_field)
        self.sorter = _QuietCombo(self)
        self.sorter.setAccessibleName(t("Sort notes"))
        self.sorter.setToolTip(t("Sort notes"))
        for key, label in (("new", t("Newest first")), ("old", t("Oldest first")), ("title", t("Title A–Z"))):
            self.sorter.addItem(label, key)
        self.sorter.setCurrentIndex(max(0, self.sorter.findData(NotesWindow.sort)))
        self.sorter.currentIndexChanged.connect(self._on_sort)
        ml.addWidget(self.notes, 1)
        foot = QHBoxLayout()  # quiet: the order of the list, and how many were found
        foot.setContentsMargins(0, 0, 2, 0)
        foot.setSpacing(8)
        foot.addWidget(self.sorter)
        foot.addWidget(self.status, 1)
        ml.addLayout(foot)

        right = QWidget()
        right.setMinimumWidth(260)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(DOC_LEFT, 19, 10, 0)  # the meta line 26 px from the top, as the design
        bar.setSpacing(8)
        self.meta = ElidedLabel("", Qt.ElideMiddle)
        self.meta.setTextFormat(Qt.PlainText)
        self.meta.setObjectName("NoteMeta")
        self.meta.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # long folder paths never widen the window
        bar.addWidget(self.meta, 1)
        # the rest in one menu, so the window fits small screens and the note stays clean
        self.more = _QuietButton(self)
        self.more.setToolTip(t("More"))
        self.more.setAccessibleName(t("More"))
        self.more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.more)
        self.act_browser = menu.addAction(t("Open in browser"), self._open_in_browser)
        menu.addSeparator()
        self.act_full = menu.addAction(t("Full screen"), self.toggle_full_screen)
        self.act_full.setShortcut(QKeySequence("F11"))
        self.act_full.setShortcutContext(Qt.WidgetShortcut)  # F11 itself is handled below (once)
        themes = menu.addMenu(t("Theme of this window"))
        group = QActionGroup(self)
        self.theme_actions: dict = {}
        for key in NotesWindow.THEMES:
            act = QAction(self._theme_name(key), group, checkable=True)
            act.triggered.connect(lambda _=False, k=key: self.set_theme(k))
            themes.addAction(act)
            self.theme_actions[key] = act
        self.theme_actions[NotesWindow.own_theme].setChecked(True)
        self.more.setMenu(menu)
        bar.addWidget(self.more)
        rl.addLayout(bar)
        self.reader = _Reader(self)
        self.reader.setAccessibleName(t("Note"))
        self.reader.anchorClicked.connect(self._on_link)
        rl.addWidget(self.reader, 1)
        # the text size: − 100% +, the same bar as in Photos, floating at the note's foot on the right
        # (beside the scroll bar, where lines end early; it shows and hides with the note). Also
        # Ctrl + wheel and Ctrl + / − / 0.
        self.zoom_pill = ZoomPill(self.reader, t("Smaller text"), t("Larger text"))
        self.zoom_pill.stepped.connect(self.set_zoom)
        self.zoom_pill.reset.connect(lambda: self.set_zoom(0))
        self.reader.installEventFilter(self)  # (the bar follows the note's size)
        self._zoom_shown()
        self.empty = QLabel("")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setContentsMargins(DOC_LEFT, 0, DOC_SIDE, 0)
        self.empty.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        rl.addWidget(self.empty, 1)

        self.split.addWidget(sidebar)
        pane = _Pane()
        pane.setObjectName("LibraryPane")
        content = QHBoxLayout(pane)
        content.setContentsMargins(1, 1, 1, 2)
        columns = QSplitter(Qt.Horizontal)
        columns.setObjectName("NoteColumns")
        columns.setChildrenCollapsible(False)
        columns.setHandleWidth(5)  # (a hairline at its left; a little room to grab it)
        columns.addWidget(middle)
        columns.addWidget(right)
        columns.setStretchFactor(0, 0)
        columns.setStretchFactor(1, 1)
        columns.setSizes([289, 590])
        content.addWidget(columns)
        # what reaches the pane's edge (a scroll bar, the selection, the size bar) follows its rounded
        # corners, smoothly, as the photos do in Photos (16: the bottom right corner's 12 and a little)
        self.pane, self.corners = pane, PaneCorners(pane, 16)
        self.split.addWidget(pane)
        self.split.setCollapsible(0, True)
        self.split.setCollapsible(1, False)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([220, 884])
        QWidget.setTabOrder(self.folders, self.search)
        QWidget.setTabOrder(self.search, self.notes)
        QWidget.setTabOrder(self.notes, self.sorter)
        lay.addWidget(self.split, 1)
        self.resize(1120, 720)

        self.folders.currentItemChanged.connect(self._on_folder)
        self.notes.currentItemChanged.connect(lambda *_: self._show_current())
        self.notes.itemActivated.connect(lambda *_: self._focus_reader())
        self.keyboard_used = False  # the focus ring shows once the keyboard is used, not on a click
        self._watched = (self.notes, self.folders, self.notes.viewport(), self.folders.viewport())
        for w in self._watched:  # (clicks arrive at the viewports)
            w.installEventFilter(self)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(180)
        self._debounce.timeout.connect(self._fill_list)
        self.search.textChanged.connect(lambda *_: self._debounce.start())
        for keys in ("Ctrl+K", "Ctrl+F"):
            QShortcut(QKeySequence(keys), self, activated=self._focus_search)
        for keys, step in (("Ctrl++", 1), ("Ctrl+=", 1), ("Ctrl+-", -1), ("Ctrl+0", 0)):
            QShortcut(QKeySequence(keys), self, activated=lambda s=step: self.set_zoom(s))
        QShortcut(QKeySequence("F11"), self, activated=self.toggle_full_screen)
        QShortcut(QKeySequence("F6"), self, activated=lambda: self._cycle(1))
        QShortcut(QKeySequence("Shift+F6"), self, activated=lambda: self._cycle(-1))
        self._refit = QTimer(self)  # pictures fitted again after the window got wider or narrower
        self._refit.setSingleShot(True)
        self._refit.setInterval(250)
        self._refit.timeout.connect(self._refit_pictures)
        self._fitted_width = 0

        self.poll = QTimer(self)
        self.poll.setInterval(2000)
        self.poll.timeout.connect(self.refresh)
        self.apply_palette(palette)
        self.refresh()
        self.poll.start()
        self.notes.setFocus()  # arrow keys move through the notes right away

    # --- data ---------------------------------------------------------------------------------
    def refresh(self) -> None:
        """Read the list of notes again; change only what changed."""
        if self.closed:
            return
        try:
            src = self.source()
        except Exception:  # noqa: BLE001 - e.g. the database is being replaced right now
            log.info("Notes window: the notes list couldn't be read now", exc_info=True)
            return
        self.off = src is None
        read_at = None
        if src is None:
            rows, root = [], None
        else:
            state, root = src
            # Asked every two seconds: while nothing was written to the database since the list was
            # last read, it isn't read and compared again (thousands of rows, on the window's thread).
            changes = getattr(state, "changes", None)
            if changes is not None:
                read_at = (state, changes(), root)  # taken before reading: a write meanwhile is seen next time
                seen = self._read_at
                if (self._fingerprint is not None and seen is not None and seen[0] is state
                        and seen[1:] == read_at[1:]):
                    self._retry()
                    return
            try:
                rows = nv.rows_from(state)
            except Exception:  # noqa: BLE001
                log.info("Notes window: the notes list couldn't be read now", exc_info=True)
                return
        fingerprint = (str(root), tuple(rows))
        self._read_at = read_at
        if fingerprint == self._fingerprint:
            self._retry()
            return
        self.indexer.stop()  # whatever it was reading belongs to the old list
        if root != self.root:
            self.texts.clear()
        self._fingerprint = fingerprint
        self.root = root
        self.rows = rows
        self.row_by_id = {r.id: r for r in rows}
        # a note that got locked, deleted, changed or moved takes its old text with it, also out of search
        self.texts = {k: v for k, v in self.texts.items()
                      if (r := self.row_by_id.get(k)) is not None and not r.locked and v[0] == _key(r)}
        self._start_index()
        self._fill_folders()
        self._fill_list()

    def _start_index(self) -> None:
        todo = self.root is not None and any(
            not r.locked and r.rel and r.id not in self.texts for r in self.rows)
        self.indexing = bool(todo)
        if todo:
            self._index_started = time.monotonic()
            self.indexer.start(self.root, self.rows, self.texts)

    def _retry(self) -> None:
        """Pages that couldn't be read (a folder briefly unavailable) are tried again now and then."""
        now = time.monotonic()
        if not self.indexing and now - self._index_started > RETRY_INDEX:
            self._start_index()
        if self._note_failed and now - self._note_failed > RETRY_NOTE:
            self.current_key = None
            self._show_current()

    def _on_batch(self, gen: int, out: dict) -> None:
        if gen != self.indexer.gen or self.closed:
            return
        for nid, entry in out.items():
            r = self.row_by_id.get(nid)
            if r is not None and not r.locked and entry[0] == _key(r):  # still the same note, still readable
                self.texts[nid] = entry
        self._refresh_snippets(out)
        if self.search.text().strip():
            self._debounce.start()

    def _on_index_done(self, gen: int) -> None:
        if gen != self.indexer.gen or self.closed:
            return
        self.indexing = False
        self._fill_list()

    # --- folders ------------------------------------------------------------------------------
    def _fill_folders(self) -> None:
        counts: dict[str, int] = {}
        for r in self.rows:
            parts = [p for p in r.folder.split("/") if p] or ["Notes"]
            for i in range(1, len(parts) + 1):
                counts.setdefault("/".join(parts[:i]), 0)
            counts["/".join(parts)] += 1
        keep = self.folder
        folded = {it.data(0, FOLDER_PATH) for it in self._folder_items() if it.childCount() and not it.isExpanded()}
        self.folders.blockSignals(True)
        self.folders.clear()
        yellow = service_color(self.c, "note")
        top = QTreeWidgetItem([t("All notes")])
        top.setData(0, FOLDER_PATH, "")
        top.setData(0, FOLDER_COUNT, len(self.rows))
        top.setData(0, Qt.AccessibleTextRole, t("All notes in every folder, {n} notes", n=len(self.rows)))
        top.setIcon(0, glyph("note", yellow, 18))
        self.folders.addTopLevelItem(top)
        items = {"": top}
        folder_icon = glyph("folder", yellow, 18)

        def order(path: str) -> tuple:  # as in Apple Notes: Notes first, Recently Deleted last
            first = path.split("/")[0]
            return (first == "Recently Deleted", first != "Notes", path.lower())

        for path in sorted(counts, key=order):
            parent_path, _, name = path.rpartition("/")
            it = QTreeWidgetItem([name])
            it.setData(0, FOLDER_PATH, path)
            it.setData(0, FOLDER_COUNT, counts[path])
            it.setIcon(0, folder_icon)
            full = path.replace("/", " › ")
            from .safe_text import plain_tip

            if parent_path:
                it.setData(0, Qt.ToolTipRole, plain_tip(full) if "<" in full else full)
            it.setData(0, Qt.AccessibleDescriptionRole, full)
            it.setData(0, Qt.AccessibleTextRole, t("{name}, {n} notes", name=name, n=counts[path]))
            parent = items.get(parent_path) if parent_path else None
            if parent is not None:
                parent.addChild(it)
            else:
                self.folders.addTopLevelItem(it)
            items[path] = it
        self.folders.expandAll()
        for path in folded:
            if path in items:
                items[path].setExpanded(False)
        chosen = items.get(keep) or top
        while chosen is not top and chosen.parent() is not None and not chosen.parent().isExpanded():
            chosen.parent().setExpanded(True)
        if keep in items or self.rows:  # (nothing loaded yet: the folder to come back to is kept)
            self.folder = chosen.data(0, FOLDER_PATH)
        self.folders.setCurrentItem(chosen)
        self.folders.blockSignals(False)

    def _folder_items(self) -> list:
        out, stack = [], [self.folders.topLevelItem(i) for i in range(self.folders.topLevelItemCount())]
        while stack:
            it = stack.pop()
            out.append(it)
            stack.extend(it.child(i) for i in range(it.childCount()))
        return out

    def _on_fold(self, item) -> None:
        """A folded folder takes over the choice of a subfolder hidden inside it."""
        cur = self.folders.currentItem()
        while cur is not None and cur is not item:
            cur = cur.parent()
        if cur is item and self.folders.currentItem() is not item:
            self.folders.setCurrentItem(item)
        self.folders.viewport().update()

    def _on_folder(self, item, _prev=None) -> None:
        if item is None:
            return
        self.folder = item.data(0, FOLDER_PATH) or ""
        self._fill_list()

    # --- list ---------------------------------------------------------------------------------
    def _terms(self) -> list[str]:
        return [w for w in (nv.fold(x) for x in self.search.text().split()) if w]

    def _visible_rows(self) -> list[tuple[int, nv.Row]]:
        terms = self._terms()
        out = []
        for r in self.rows:
            if not terms and self.folder:
                f = "/".join(p for p in r.folder.split("/") if p) or "Notes"
                if f != self.folder:
                    continue
            entry = self.texts.get(r.id)
            rank = nv.matches(r, entry[2] if entry else None, terms)
            if rank:
                out.append((rank, r))
        if NotesWindow.sort == "old":
            out.sort(key=lambda x: (x[1].modified or "", x[1].title.lower()))
        elif NotesWindow.sort == "title":
            out.sort(key=lambda x: (nv.fold(x[1].title or t("Untitled")), x[1].title))
        if terms:
            out.sort(key=lambda x: -x[0])  # stable: in the chosen order within a rank
        return out

    def _snippet(self, r: nv.Row) -> str:
        entry = self.texts.get(r.id)
        if r.locked or entry is None:
            return ""
        return nv.snippet(entry[1], entry[2], r.title, self.search.text())

    def _fill_list(self) -> None:
        if self.closed:
            return
        keep = self.current or self._resume_id
        shown = self._visible_rows()
        self.notes.blockSignals(True)
        self.notes.clear()
        select = None
        for _, r in shown:
            it = QListWidgetItem()
            it.setData(ROW_ID, r.id)
            snip = self._snippet(r)
            it.setData(ROW_SNIPPET, snip)
            it.setData(ROW_COMPACT, self._compact(r, snip))
            when = local_datetime(r.modified) if r.modified else ""
            folder = r.folder.replace("/", " \u203a ")
            text = ", ".join(x for x in (r.title or t("Untitled"), when, folder,
                                          t("Locked note") if r.locked else "") if x)
            it.setData(Qt.AccessibleTextRole, text)
            it.setData(Qt.AccessibleDescriptionRole, snip)
            from .safe_text import plain_tip

            it.setToolTip(plain_tip(text))  # (a shared note's title is anyone's text: never HTML)
            self.notes.addItem(it)
            if r.id == keep:
                select = it
        if select is None and self.notes.count():
            select = self.notes.item(0)
        if select is not None:
            self.notes.setCurrentItem(select)
        self.notes.blockSignals(False)
        query = self.search.text().strip()
        if query and self.indexing:
            self.status.setText(t("Searching titles now · indexing note text…"))
        elif query:
            self.status.setText(t("{n} found", n=len(shown)))
        else:
            self.status.setText("")
        self._show_current()

    def _compact(self, r: nv.Row, snip: str) -> bool:
        """A note read and found to have nothing after its title: one line in the list, not an
        empty second one (until it is read, a note keeps room for its first line)."""
        return not snip and not r.locked and r.id in self.texts

    def _refresh_snippets(self, only=None) -> None:
        """Give the rows their first line once their note has been read. only: the notes just read
        (the others have nothing new: with thousands of notes, going through every row's text again
        for each batch of 40 kept the window busy for seconds)."""
        changed = False
        for i in range(self.notes.count()):
            it = self.notes.item(i)
            nid = it.data(ROW_ID)
            if only is not None and nid not in only:
                continue
            r = self.row_by_id.get(nid)
            if r is not None and not it.data(ROW_SNIPPET):
                snip = self._snippet(r)
                it.setData(ROW_SNIPPET, snip)
                it.setData(Qt.AccessibleDescriptionRole, snip)
                compact = self._compact(r, snip)
                if bool(it.data(ROW_COMPACT)) != compact:
                    it.setData(ROW_COMPACT, compact)
                    changed = True
        if changed:
            self.notes.doItemsLayout()  # (rows of another height)

    # --- the note -----------------------------------------------------------------------------
    def _show_current(self) -> None:
        if self.closed:
            return
        it = self.notes.currentItem()
        row = self.row_by_id.get(it.data(ROW_ID)) if it is not None else None
        if row is None:
            self.current = self.current_key = None
            self._note_failed = 0.0
            self.reader.clear_note()
            self._empty(t("Notes are turned off in Settings.") if self.off else
                        t("Your notes will appear here after the first sync.") if not self.rows
                        else t("No notes found."))
            return
        key = (row.id, row.rel, row.modified, row.locked, row.title, row.folder)
        if key == self.current_key and self.reader.page is not None:
            self._highlight(jump=self.search.text() != self._hl_query)
            return
        same_note = row.id == self.current
        if not same_note:
            self.shown_pages = {}
        self.current, self.current_key = row.id, key
        when = _long_when(row.modified) if row.modified else ""
        folder = row.folder.replace("/", " \u203a ")
        self.meta.setText(" \u00b7 ".join(x for x in (folder, when) if x))  # "Home \u00b7 24 November 2026, 07:15"
        self._note_failed = 0.0
        if row.locked:
            self.reader.clear_note()
            self._empty("\U0001F512 " + t("This note is locked. Skyfold can't read locked notes; open it on your "
                                         "iPhone, iPad or Mac."))
            return
        page = nv.page_path(self.root, row) if self.root else None
        if page is None:
            self.reader.clear_note()
            self._empty(t("This note isn't on this PC yet. It will be after the next sync."))
            return
        try:
            size = page.stat().st_size
            if size > nv.MAX_PAGE_BYTES:
                self.reader.clear_note()
                self._empty(t("This note is too big to show here. Open it in your browser."), browser=True)
                return
            text = page.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            self._note_failed = time.monotonic()  # it may be back in a moment (a folder briefly away)
            self.reader.clear_note()
            self._empty(t("This note isn't on this PC yet. It will be after the next sync."))
            return
        except OSError:
            self._note_failed = time.monotonic()  # tried again in a moment
            self.reader.clear_note()
            self._empty(t("This note can't be read right now. Skyfold tries again in a moment."))
            return
        clean = nv.note_html(text, self.shown_pages)
        clean.body = self._sized(page, clean).replace(
            '<ul style="list-style-type:none">', '<ul style="list-style-type:none;-qt-list-indent:0">')
        # the same note shown again (zoom, a wider window, more pages): it stays at the same text,
        # not the same number of pixels; a reopened window: back where it was closed
        anchor = self._top_position() if (same_note and self.reader.page == page) else None
        if self._resume_pos is not None and self._resume_id == row.id:
            anchor = self._resume_pos
        self._resume_pos = None
        if self._resume_id == row.id:
            self._resume_id = None
        font = QFont(QApplication.font())  # the design's body text: 14 px Segoe UI Variable
        font.setFamilies(["Segoe UI Variable Text", "Segoe UI"])
        font.setPointSizeF(10.5 * NotesWindow.zoom)
        self.reader.show_page(page, clean, keep_pictures=same_note, css=self._css(), font=font,
                              title=row.title or t("Untitled"))
        self._fitted_width = self.reader.viewport().width()
        self._pending_anchor = anchor if (anchor and not self.isVisible()) else None
        self._scroll_to_position(anchor or 0)
        self.empty.hide()
        self.reader.show()
        self.act_browser.setEnabled(True)
        self._highlight(jump=not same_note or self.search.text() != self._hl_query)

    def _top_position(self) -> int:
        """Where in the note's text the top of the view is (a character, or a picture)."""
        if not self.reader.isVisible() or self.reader.page is None:
            return 0
        return self.reader.cursorForPosition(QPoint(4, 4)).position()

    def _scroll_to_position(self, pos: int) -> None:
        """Put that place of the note at the top of the view at once: the whole note is laid out
        first (pictures have their size already), so it doesn't arrive in steps."""
        doc = self.reader.document()
        layout = doc.documentLayout()
        layout.documentSize()  # lays out the whole note now (and sets the scrollbar's range)
        y = 0.0
        if pos > 0:
            block = doc.findBlock(pos)
            if block.isValid():
                y = layout.blockBoundingRect(block).top()
                lines = block.layout()
                if lines is not None and lines.lineCount():
                    line = lines.lineForTextPosition(max(0, pos - block.position()))
                    if line.isValid():
                        y += line.y()
        self.reader.verticalScrollBar().setValue(int(y))

    def showEvent(self, e) -> None:  # noqa: N802
        """The window has its real size only now, before anything is drawn: the note is fitted to it
        and put at its place here, so it appears there at once (no jump, no scrolling into place)."""
        super().showEvent(e)
        anchor, self._pending_anchor = getattr(self, "_pending_anchor", None), None
        if self.closed or self.reader.page is None:
            return
        if abs(self.reader.viewport().width() - self._fitted_width) > 40:
            if anchor:
                self._resume_id, self._resume_pos = self.current, anchor
            self.current_key = None
            self._show_current()
        elif anchor:
            self._scroll_to_position(anchor)

    def _sized(self, page: Path, clean: nv.Page) -> str:
        """Every picture with its width and height on screen: Qt then lays the note out at once and
        asks for a picture only when it is drawn (a scan with dozens of pages opens quickly)."""
        if not clean.images or self.root is None:
            return clean.body
        room = max(200, self.reader.viewport().width() - DOC_LEFT - DOC_SIDE)
        body = clean.body
        for n, ref in enumerate(clean.images):
            f = nv.resolve_asset(self.root, page, ref)
            if f is None:
                continue
            reader = QImageReader(str(f))
            reader.setAutoTransform(True)
            if bytes(reader.format()).lower() in (b"svg", b"svgz"):
                continue
            raw = reader.size()
            if not raw.isValid() or raw.width() <= 0 or raw.height() <= 0:
                continue
            disp = raw.transposed() if reader.transformation() & QImageIOHandler.TransformationRotate90 else raw
            w = min(room, disp.width() * NotesWindow.zoom)
            h = w * disp.height() / disp.width()
            body = body.replace(f'src="{nv.IMAGE_SCHEME}:{n}"',
                                f'src="{nv.IMAGE_SCHEME}:{n}" width="{round(w)}" height="{round(h)}"', 1)
        return body

    def _empty(self, text: str, browser: bool = False) -> None:
        self.reader.hide()
        self.empty.setText(text)
        self.empty.show()
        self.act_browser.setEnabled(browser)
        if not self.current:
            self.meta.setText("")

    def _match_format(self) -> QTextCharFormat:
        fmt = QTextCharFormat()
        hc = contrast_colors()
        fmt.setBackground(hc["high"] if hc else _alpha(self.c["warn"], 0.35))  # (the design's mark)
        fmt.setForeground(hc["high_text"] if hc else QColor(self.c["text"]))
        if hc:
            fmt.setFontUnderline(True)
        return fmt

    def _highlight(self, jump: bool) -> None:
        """Mark the searched words in the open note (without changing the note); with jump, scroll
        to the first one. Positions are counted the way Qt counts them (an emoji is two)."""
        self._hl_query = self.search.text()
        terms = self._terms()
        sels = []
        fmt = self._match_format()
        first = None
        doc = self.reader.document()
        block = doc.begin()
        while terms and block.isValid() and len(sels) < MAX_MARKS:
            text = block.text()
            for start, end in _match_ranges(text, terms, MAX_MARKS - len(sels)):
                a, b = block.position() + start, block.position() + end
                cur = QTextCursor(doc)
                cur.setPosition(a)
                cur.setPosition(b, QTextCursor.KeepAnchor)
                sel = QTextEdit.ExtraSelection()
                sel.cursor, sel.format = cur, fmt
                sels.append(sel)
                first = a if first is None else min(first, a)
            block = block.next()
        self.reader.setExtraSelections(sels)
        if first is not None and jump:
            cur = QTextCursor(doc)
            cur.setPosition(first)
            self.reader.setTextCursor(cur)
            self.reader.ensureCursorVisible()

    def _css(self) -> str:
        """The note as in the design: body text 14 px in the secondary colour, headings 600 in the
        text colour, paragraphs 14 px apart; sizes follow the zoom."""
        c, z = self.c, NotesWindow.zoom
        k = _layers(c)
        text2, text3 = _rgba(k["text2"]), _rgba(k["text3"])

        def pt(px: float) -> str:
            return f"{px * 0.75 * z:.2f}pt"

        def px(n: float) -> str:
            return f"{round(n * z)}px"

        hl = "".join(f".hl{i + 1}{{background-color:{_blend(c['bg'], col, 0.35)}}}"
                     for i, col in enumerate(HIGHLIGHTS))
        return (f"body{{color:{text2}}} a{{color:{c['accent_fg']}}}"
                f"p{{margin-top:0;margin-bottom:{px(14)}}}"
                f"h1{{font-size:{pt(20)};font-weight:600;color:{c['text']};margin-top:{px(18)};margin-bottom:{px(8)}}}"
                f"h2{{font-size:{pt(17)};font-weight:600;color:{c['text']};margin-top:{px(16)};margin-bottom:{px(6)}}}"
                f"h3,h4,h5,h6{{font-size:{pt(14)};font-weight:600;color:{c['text']};"
                f"margin-top:{px(14)};margin-bottom:{px(4)}}}"
                f"pre,code{{background-color:{_rgba(k['layer2'])};font-family:'Cascadia Mono','Consolas'}}"
                f"table{{border:none}}"
                f"td,th{{font-size:{pt(13)};color:{c['text']};text-align:left}}"
                f"th{{background-color:{HEAD_MARK};font-weight:600}}"
                f"blockquote{{color:{text3}}} .sf-file{{color:{text3}}}"
                f".sf-box{{font-family:'Segoe UI Symbol';font-size:large;color:{text3}}}"
                f".sf-done{{background-color:{c['btn']};color:{c['accent_text']}}}" + hl)

    # --- links --------------------------------------------------------------------------------
    def _on_link(self, url: QUrl) -> None:
        ref = url.toString()
        if ref.startswith("skyfold:more:") and ref[13:].isdigit():
            n = int(ref[13:])
            self.shown_pages[n] = 1_000_000  # all of that scan's pages, as the link says
            self.current_key = None
            QTimer.singleShot(0, self._show_current)  # not inside Qt's own handling of the click
            return
        if url.scheme().lower() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
            return
        f = nv.resolve_asset(self.root, self.reader.page, ref) if self.root and self.reader.page else None
        if f is None or not f.is_file():
            self.status.setText(t("Attachment unavailable on this PC"))
            return
        if f.suffix.lower() in OPENABLE:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(f)))
        else:  # an unfamiliar kind of file is shown in its folder, not run
            subprocess.Popen([os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe"),
                              "/select,", str(f)])

    def _open_in_browser(self) -> None:
        row = self.row_by_id.get(self.current or "")
        page = nv.page_path(self.root, row) if (self.root and row) else None
        if page is not None and page.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(page)))

    # --- order and size -----------------------------------------------------------------------
    def _on_sort(self, *_args) -> None:
        NotesWindow.sort = self.sorter.currentData() or "new"
        self._fill_list()
        it = self.notes.currentItem()
        if it is not None:
            self.notes.scrollToItem(it)

    def _zoom_shown(self) -> None:
        """The bar's percentage: the text's size against the normal one."""
        self.zoom_pill.show_zoom(round(NotesWindow.zoom * 100), smaller=NotesWindow.zoom > NotesWindow.ZOOMS[0],
                                 larger=NotesWindow.zoom < NotesWindow.ZOOMS[-1])

    def _place_zoom(self) -> None:
        pill, r = self.zoom_pill, self.reader
        pill.adjustSize()
        pill.move(max(0, r.width() - ZOOM_RIGHT - pill.width()), max(0, r.height() - ZOOM_BOTTOM - pill.height()))
        pill.shadow.follow()
        pill.raise_()

    def set_zoom(self, step: int) -> None:
        """Text of the note larger (1), smaller (-1) or back to normal (0)."""
        zooms = NotesWindow.ZOOMS
        now = min(range(len(zooms)), key=lambda i: abs(zooms[i] - NotesWindow.zoom))
        NotesWindow.zoom = 1.0 if step == 0 else zooms[max(0, min(len(zooms) - 1, now + step))]
        self._zoom_shown()
        self.current_key = None  # same note: its pictures are kept, only the text changes size
        self._show_current()

    def toggle_full_screen(self) -> None:
        if self.isFullScreen():
            if getattr(self, "_was_maximized", False):
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
        self.act_full.setText(t("Exit full screen") if self.isFullScreen() else t("Full screen"))

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if hasattr(self, "_refit"):
            self._refit.start()

    def _refit_pictures(self) -> None:
        if self.reader.page is not None and abs(self.reader.viewport().width() - self._fitted_width) > 40:
            self.current_key = None
            self._show_current()

    # --- keyboard -----------------------------------------------------------------------------
    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def _focus_reader(self) -> None:
        (self.reader if self.reader.isVisible() else self.notes).setFocus()

    def _cycle(self, step: int) -> None:
        order = [w for w in (self.search, self.folders, self.notes, self.reader)
                 if w.isEnabled() and not w.visibleRegion().isEmpty()]
        now = next((i for i, w in enumerate(order) if w.hasFocus() or w.isAncestorOf(QApplication.focusWidget())),
                   -1)
        if order:
            order[(now + step) % len(order)].setFocus()

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if obj is getattr(self, "reader", None) and e.type() in (QEvent.Resize, QEvent.Show):
            self._place_zoom()
        if e.type() in (QEvent.KeyPress, QEvent.MouseButtonPress) and obj in getattr(self, "_watched", ()):
            used = e.type() == QEvent.KeyPress
            if used != self.keyboard_used:
                self.keyboard_used = used
                self.notes.viewport().update()
                self.folders.viewport().update()
        return super().eventFilter(obj, e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key_Escape and self.search.text():
            self.search.clear()
            return
        if e.key() == Qt.Key_Escape and self.isFullScreen():
            self.toggle_full_screen()
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            return  # never a dialog's default button (Enter on a note opens it: itemActivated)
        super().keyPressEvent(e)

    # --- look ---------------------------------------------------------------------------------
    @staticmethod
    def _theme_name(key) -> str:
        return {None: t("As Skyfold"), "light": t("Light"), "dark": t("Dark"), "oled": "OLED"}[key]

    def set_theme(self, key) -> None:
        NotesWindow.own_theme = key
        self.theme_actions[key].setChecked(True)
        self.apply_palette(self.app_c)

    def _cycle_theme(self) -> None:
        order = NotesWindow.THEMES
        self.set_theme(order[(order.index(NotesWindow.own_theme) + 1) % len(order)])

    def apply_palette(self, palette: dict[str, str]) -> None:
        """Skyfold's theme changed (or this window's own): restyle everything in the window."""
        from . import theme

        self.app_c = palette
        own = NotesWindow.own_theme
        if own is not None:
            palette = theme.palette(own)
        self.c = palette
        self._ui = _tokens(palette, self.glass)  # (the size bar's layers, as in Photos)
        self.setPalette(theme.qpalette(palette) if own else QApplication.palette())
        k = _layers(palette)
        self.setStyleSheet((theme.stylesheet(palette) if own else "") + self.frame_style() + PILL_SHEET +
                           # the design's search field: a lighter layer, a stronger hairline, 13.5 px
                           f"SoftCard#LibrarySearch {{ qproperty-fill:{_rgba(k['layer2'])};"
                           f" qproperty-stroke:{_rgba(k['stroke2'])}; }}"
                           "SoftCard#LibrarySearch QLineEdit { font-size:10.125pt; }"
                           f"QSplitter#NoteColumns::handle {{ border-left:1px solid {_rgba(k['stroke'])};"
                           " background:transparent; }"
                           f"QLabel#NoteMeta {{ font-size:9pt; color:{palette['muted2']}; }}"
                           f"QLabel#NoteStatus {{ font-size:9pt; color:{palette['muted2']}; }}")
        pal = self.search.palette()
        pal.setColor(QPalette.PlaceholderText, QColor(palette["muted2"]))
        self.search.setPalette(pal)
        if self.glass and self.isVisible():
            self._material()
        self._apply(palette)

    def _apply(self, palette: dict[str, str]) -> None:
        c = self.c = palette
        self.search_field.apply_palette(c)
        self.notes.setStyleSheet("QListWidget{background:transparent;border:none;outline:0}"
                                 "QListWidget::item{border:none;background:transparent}")
        self.reader.setStyleSheet(f"QTextBrowser{{background:transparent;border:none;padding:0;"
                                  f"selection-background-color:{c['btn']};selection-color:{c['accent_text']}}}")
        self.folders.setStyleSheet("QTreeView{background:transparent;border:none;padding:0;outline:0;"
                                   "show-decoration-selected:0} QTreeView::item,QTreeView::branch"
                                   "{border:none;background:transparent}")
        for w in (self.more, self.sorter, self.zoom_pill, self.zoom_pill.shadow):
            w.update()
        if not self.closed:
            self._fill_folders()
            self.current_key = None
            self._show_current()
            self.notes.viewport().update()

    # --- closing ------------------------------------------------------------------------------
    def _forget(self, remember: bool = True) -> None:
        """Drop every bit of note text and every picture the window holds (any way of closing).
        Only which folder and note were open (and how far down) is kept, to come back to."""
        if not self.closed and remember and self.current:
            NotesWindow.resume = {"folder": self.folder, "id": self.current, "pos": self._top_position()}
        self.closed = True
        self.poll.stop()
        self._debounce.stop()
        self._refit.stop()
        self.indexer.stop()
        self.indexing = False
        self.texts = {}
        self.rows, self.row_by_id = [], {}
        self.current = self.current_key = None
        self._fingerprint = None  # holds the titles
        self._hl_query = ""
        self.folder = ""
        self.shown_pages = {}
        self.meta.setText("")
        self.meta.setToolTip("")
        self.empty.setText("")
        self.reader.clear_note()
        self.notes.clear()
        self.folders.clear()
        self.search.clear()

    def reject(self) -> None:
        """Esc, and the dialog's other ways of closing: everything is dropped, then the window goes."""
        self._forget()
        super().reject()
        self.deleteLater()

    def closeEvent(self, e) -> None:  # noqa: N802
        self._forget()
        super().closeEvent(e)

    def clear(self) -> None:
        """Signed out or another Apple ID: nothing of the old notes stays on screen or in memory."""
        self._forget(remember=False)
        NotesWindow.resume = None
        self.close()
