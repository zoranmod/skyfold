"""The Notes window: read the exported Apple Notes inside Skyfold, like a notes app.

Folders on the left, the notes in the middle (newest first, with date and first line), the note
on the right; search covers titles, folders and the text of the notes. Read-only. The notes come
from the Notes database (which notes exist, and which are locked) and the exported pages (what is
read); see notes_view. The window looks again every two seconds while it is open, so a note that
gets locked, deleted, renamed or moved during a sync is updated (or cleared) right away.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QImage, QKeySequence, QPainter, QPen, QShortcut,
                           QTextCharFormat, QTextCursor, QTextDocument)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QSplitter, QStyle, QStyledItemDelegate, QTextBrowser, QTextEdit, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ..i18n import t
from ..notes_engine import local_datetime
from .. import notes_view as nv
from .dialogs import _Dialog
from .icons import glyph
from .theme import service_color

log = logging.getLogger(__name__)

ROW_ID = Qt.UserRole
ROW_SNIPPET = Qt.UserRole + 1
FOLDER_PATH = Qt.UserRole
OPENABLE = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".heic", ".heif", ".tif", ".tiff", ".webp", ".bmp",
            ".m4a", ".mp3", ".wav", ".aac", ".caf", ".mov", ".mp4", ".m4v", ".txt", ".csv", ".rtf",
            ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pages", ".numbers", ".key", ".vcf"}
HIGHLIGHTS = ("#ba55d3", "#ff5252", "#ffb74d", "#80cbc4", "#64b5f6")  # Apple's five highlight colours


def _blend(a: str, b: str, f: float) -> str:
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() * (1 - f) + cb.red() * f), round(ca.green() * (1 - f) + cb.green() * f),
                  round(ca.blue() * (1 - f) + cb.blue() * f)).name()


class _Indexer(QObject):
    """Reads the note pages in the background for search and the first lines in the list."""

    batch = Signal(int, object)  # generation, {id: (key, text, folded)}
    done = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.gen = 0
        self._stop = threading.Event()

    def start(self, root: Path, rows: list, known: dict) -> int:
        self.stop()
        self.gen += 1
        gen, stop = self.gen, threading.Event()
        self._stop = stop
        todo = [r for r in rows if not r.locked and r.rel and known.get(r.id, (None,))[0] != _key(r)]

        def run() -> None:
            out: dict = {}
            for r in todo:
                if stop.is_set():
                    return
                text = ""
                p = nv.page_path(root, r)
                try:
                    if p is not None and p.stat().st_size <= nv.MAX_PAGE_BYTES:
                        body, _ = nv.note_html(p.read_text(encoding="utf-8", errors="replace"), all_pages=True)
                        text = nv.plain_text(body)
                except OSError:
                    text = ""
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
        self._stop.set()


def _key(r: nv.Row) -> tuple:
    return (r.rel, r.modified)


class _NoteDelegate(QStyledItemDelegate):
    def __init__(self, win: "NotesWindow") -> None:
        super().__init__(win)
        self.win = win

    def sizeHint(self, opt, idx) -> QSize:  # noqa: N802
        fm = opt.fontMetrics
        return QSize(200, 2 * fm.height() + 24)

    def paint(self, p: QPainter, opt, idx) -> None:
        c = self.win.c
        row: nv.Row | None = self.win.row_by_id.get(idx.data(ROW_ID))
        if row is None:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(opt.rect).adjusted(4, 2, -4, -2)
        selected = bool(opt.state & QStyle.State_Selected)
        if selected:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["accent_soft"]))
            p.drawRoundedRect(r, 8, 8)
        elif opt.state & QStyle.State_MouseOver:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["hover"]))
            p.drawRoundedRect(r, 8, 8)
        if selected and opt.state & QStyle.State_HasFocus and self.win.notes.hasFocus():
            p.setPen(QPen(QColor(c["focus_ring"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 8, 8)
        fm = opt.fontMetrics
        x, w = r.left() + 12, r.width() - 24
        line = fm.height()
        top = r.top() + (r.height() - 2 * line - 4) / 2
        when = local_datetime(row.modified) if row.modified else ""
        f = QFont(opt.font)
        p.setFont(f)
        dw = fm.horizontalAdvance(when) + 8 if when else 0
        p.setPen(QColor(c["muted"]))
        if when:
            p.drawText(QRectF(x + w - dw, top, dw, line), Qt.AlignRight | Qt.AlignVCenter, when)
        bold = QFont(opt.font)
        bold.setWeight(QFont.DemiBold)
        p.setFont(bold)
        p.setPen(QColor(c["text"]))
        title = row.title or t("Untitled")
        p.drawText(QRectF(x, top, w - dw, line), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(title, Qt.ElideRight, int(w - dw)))
        p.setFont(f)
        p.setPen(QColor(c["muted"]))
        second = ("\U0001F512 " + t("Locked note")) if row.locked else (idx.data(ROW_SNIPPET) or "")
        p.drawText(QRectF(x, top + line + 4, w, line), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(second, Qt.ElideRight, int(w)))
        p.restore()


class _Reader(QTextBrowser):
    """The note itself. Loads pictures only from the Notes folder's _assets (fitted to the width,
    on white so drawings and scans stay readable in dark themes); links open only on a click."""

    def __init__(self, win: "NotesWindow") -> None:
        super().__init__()
        self.win = win
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.page: Path | None = None
        self._images: dict[str, QImage] = {}

    def clear_note(self) -> None:
        self.page = None
        self._images.clear()
        self.document().clear()
        self.setHtml("")

    def loadResource(self, kind: int, url: QUrl):  # noqa: N802
        if kind != QTextDocument.ImageResource or self.page is None or self.win.root is None:
            return None
        ref = url.toString()
        if ref in self._images:
            return self._images[ref]
        f = nv.resolve_asset(self.win.root, self.page, ref)
        img = QImage(str(f)) if f is not None and f.is_file() else QImage()
        if img.isNull():
            img = QImage()
        else:
            dpr = self.devicePixelRatioF() or 1.0
            room = max(200, min(900, self.viewport().width() - 48))
            if img.width() > room * dpr:
                img = img.scaledToWidth(int(room * dpr), Qt.SmoothTransformation)
            if img.hasAlphaChannel():
                base = QImage(img.size(), QImage.Format_RGB32)
                base.fill(QColor("#ffffff"))
                q = QPainter(base)
                q.drawImage(0, 0, img)
                q.end()
                img = base
            img.setDevicePixelRatio(dpr)
        self._images[ref] = img
        return img


class NotesWindow(_Dialog):
    """source() gives (Notes database, Notes folder), or None when Notes isn't set up."""

    def __init__(self, palette: dict[str, str], source) -> None:
        super().__init__(t("Notes") + " \u2013 Skyfold")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.c = palette
        self.source = source
        self.root: Path | None = None
        self.rows: list[nv.Row] = []
        self.row_by_id: dict[str, nv.Row] = {}
        self.texts: dict[str, tuple] = {}  # id -> (key, text, folded)
        self.indexing = False
        self.current: str | None = None  # id of the note shown
        self.current_key: tuple | None = None
        self.all_pages = False
        self._fingerprint = None
        self.folder = ""  # "" = all notes
        self._hl_query = ""

        self.indexer = _Indexer()
        self.indexer.batch.connect(self._on_batch)
        self.indexer.done.connect(self._on_index_done)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("Search notes (Ctrl+K)"))
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName(t("Search notes"))
        top.addWidget(self.search, 1)
        self.status = QLabel("")
        self.status.setObjectName("Hint")
        top.addWidget(self.status)
        lay.addLayout(top)

        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(10)
        self.folders = QTreeWidget()
        self.folders.setHeaderHidden(True)
        self.folders.setAccessibleName(t("Folders"))
        self.folders.setMinimumWidth(140)
        self.notes = QListWidget()
        self.notes.setAccessibleName(t("Notes"))
        self.notes.setItemDelegate(_NoteDelegate(self))
        self.notes.setMouseTracking(True)
        self.notes.setUniformItemSizes(True)
        self.notes.setMinimumWidth(220)
        self.notes.setVerticalScrollMode(QListWidget.ScrollPerPixel)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        bar = QHBoxLayout()
        self.meta = QLabel("")
        self.meta.setObjectName("Hint")
        self.meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bar.addWidget(self.meta, 1)
        self.open_browser = QPushButton(t("Open in browser"))
        self.open_browser.setObjectName("Link")
        self.open_browser.setCursor(Qt.PointingHandCursor)
        self.open_browser.clicked.connect(self._open_in_browser)
        bar.addWidget(self.open_browser)
        rl.addLayout(bar)
        self.reader = _Reader(self)
        self.reader.setAccessibleName(t("Note"))
        self.reader.anchorClicked.connect(self._on_link)
        rl.addWidget(self.reader, 1)
        self.empty = QLabel("")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        rl.addWidget(self.empty, 1)

        for w in (self.folders, self.notes, right):
            self.split.addWidget(w)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 0)
        self.split.setStretchFactor(2, 1)
        self.split.setSizes([200, 300, 620])
        lay.addWidget(self.split, 1)
        self.resize(1120, 720)

        self.folders.currentItemChanged.connect(self._on_folder)
        self.notes.currentItemChanged.connect(lambda *_: self._show_current())
        self.notes.itemActivated.connect(lambda *_: self.reader.setFocus())
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(180)
        self._debounce.timeout.connect(self._fill_list)
        self.search.textChanged.connect(lambda *_: self._debounce.start())
        for keys in ("Ctrl+K", "Ctrl+F"):
            QShortcut(QKeySequence(keys), self, activated=self._focus_search)
        QShortcut(QKeySequence("F6"), self, activated=lambda: self._cycle(1))
        QShortcut(QKeySequence("Shift+F6"), self, activated=lambda: self._cycle(-1))

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
        src = None
        try:
            src = self.source()
        except Exception:  # noqa: BLE001 - e.g. the database is being replaced right now
            log.info("Notes window: the notes list couldn't be read now", exc_info=True)
            return
        self.off = src is None
        if src is None:
            rows, root = [], None
        else:
            state, root = src
            try:
                rows = nv.rows_from(state)
            except Exception:  # noqa: BLE001
                log.info("Notes window: the notes list couldn't be read now", exc_info=True)
                return
        fingerprint = (str(root), tuple(rows))
        if fingerprint == self._fingerprint:
            return
        if root != self.root:
            self.texts.clear()
            self.indexer.stop()
        self._fingerprint = fingerprint
        self.root = root
        self.rows = rows
        self.row_by_id = {r.id: r for r in rows}
        # a note that got locked (or deleted) takes its text with it, also out of search
        self.texts = {k: v for k, v in self.texts.items() if k in self.row_by_id and not self.row_by_id[k].locked}
        if root is not None and rows:
            self.indexing = True
            self.indexer.start(root, rows, self.texts)
        self._fill_folders()
        self._fill_list()

    def _on_batch(self, gen: int, out: dict) -> None:
        if gen != self.indexer.gen:
            return
        self.texts.update(out)
        self._refresh_snippets()
        if self.search.text().strip():
            self._debounce.start()

    def _on_index_done(self, gen: int) -> None:
        if gen != self.indexer.gen:
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
        self.folders.blockSignals(True)
        self.folders.clear()
        icon = glyph("note", service_color(self.c, "note"), 16)
        top = QTreeWidgetItem([f"{t('All notes')}  ({len(self.rows)})"])
        top.setData(0, FOLDER_PATH, "")
        top.setIcon(0, icon)
        self.folders.addTopLevelItem(top)
        items = {"": top}
        folder_icon = glyph("folder", self.c["muted"], 16)
        for path in sorted(counts, key=lambda s: s.lower()):
            parent_path, _, name = path.rpartition("/")
            it = QTreeWidgetItem([f"{name}  ({counts[path]})" if counts[path] else name])
            it.setData(0, FOLDER_PATH, path)
            it.setIcon(0, folder_icon)
            it.setData(0, Qt.AccessibleTextRole, t("{name}, {n} notes", name=name, n=counts[path]))
            (items.get(parent_path) or top).addChild(it) if parent_path else self.folders.addTopLevelItem(it)
            items[path] = it
        self.folders.expandAll()
        chosen = items.get(keep) or top
        self.folder = chosen.data(0, FOLDER_PATH)
        self.folders.setCurrentItem(chosen)
        self.folders.blockSignals(False)

    def _on_folder(self, item, _prev=None) -> None:
        if item is None:
            return
        self.folder = item.data(0, FOLDER_PATH) or ""
        self._fill_list()

    # --- list ---------------------------------------------------------------------------------
    def _visible_rows(self) -> list[tuple[int, nv.Row]]:
        terms = [nv.fold(w) for w in self.search.text().split() if w.strip()]
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
        if terms:
            out.sort(key=lambda x: -x[0])  # stable: newest first within a rank
        return out

    def _snippet(self, r: nv.Row) -> str:
        entry = self.texts.get(r.id)
        if r.locked or entry is None:
            return ""
        return nv.snippet(entry[1], r.title, self.search.text())

    def _fill_list(self) -> None:
        keep = self.current
        shown = self._visible_rows()
        self.notes.blockSignals(True)
        self.notes.clear()
        select = None
        for _, r in shown:
            it = QListWidgetItem()
            it.setData(ROW_ID, r.id)
            it.setData(ROW_SNIPPET, self._snippet(r))
            when = local_datetime(r.modified) if r.modified else ""
            folder = r.folder.replace("/", " \u203a ")
            text = ", ".join(x for x in (r.title or t("Untitled"), when, folder,
                                          t("Locked note") if r.locked else "") if x)
            it.setData(Qt.AccessibleTextRole, text)
            it.setToolTip(r.title or t("Untitled"))
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

    def _refresh_snippets(self) -> None:
        for i in range(self.notes.count()):
            it = self.notes.item(i)
            r = self.row_by_id.get(it.data(ROW_ID))
            if r is not None and not it.data(ROW_SNIPPET):
                it.setData(ROW_SNIPPET, self._snippet(r))

    # --- the note -----------------------------------------------------------------------------
    def _show_current(self) -> None:
        it = self.notes.currentItem()
        row = self.row_by_id.get(it.data(ROW_ID)) if it is not None else None
        if row is None:
            self.current = self.current_key = None
            self.reader.clear_note()
            self._empty(t("Notes are turned off in Settings.") if getattr(self, "off", False) else
                        t("Your notes will appear here after the first sync.") if not self.rows
                        else t("No notes found."))
            return
        key = (row.id, row.rel, row.modified, row.locked, row.title, row.folder)
        if key == self.current_key and self.reader.page is not None:
            self._highlight(jump=self.search.text() != self._hl_query)
            return
        if row.id != self.current:
            self.all_pages = False
        self.current, self.current_key = row.id, key
        when = local_datetime(row.modified) if row.modified else ""
        folder = row.folder.replace("/", " \u203a ")
        self.meta.setText("  \u00b7  ".join(x for x in (folder, when) if x))
        if row.locked:
            self.reader.clear_note()
            self._empty("\U0001F512 " + t("This note is locked. Skyfold can't read locked notes; open it on your "
                                         "iPhone, iPad or Mac."))
            return
        page = nv.page_path(self.root, row) if self.root else None
        try:
            if page is None or page.stat().st_size > nv.MAX_PAGE_BYTES:
                raise OSError
            text = page.read_text(encoding="utf-8", errors="replace")
        except OSError:
            self.reader.clear_note()
            self._empty(t("This note isn't on this PC yet. It will be after the next sync.")
                        if page is None or not page.exists() else
                        t("This note is too big to show here. Open it in your browser."),
                        browser=page is not None and page.exists())
            return
        body, _hidden = nv.note_html(text, all_pages=self.all_pages)
        scroll = self.reader.verticalScrollBar().value() if self.reader.page == page else 0
        self.reader.clear_note()
        self.reader.page = page
        self.reader.document().setDefaultStyleSheet(self._css())
        self.reader.setHtml(body)
        self.reader.verticalScrollBar().setValue(scroll)
        self.empty.hide()
        self.reader.show()
        self.open_browser.show()
        self._highlight(jump=True)

    def _empty(self, text: str, browser: bool = False) -> None:
        self.reader.hide()
        self.empty.setText(text)
        self.empty.show()
        self.open_browser.setVisible(browser)
        if not self.current:
            self.meta.setText("")

    def _highlight(self, jump: bool) -> None:
        """Mark the searched words in the open note (without changing the note); with jump, scroll
        to the first one (not again while the list is only being refreshed)."""
        self._hl_query = self.search.text()
        terms = [nv.fold(w) for w in self.search.text().split() if w.strip()]
        sels = []
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(_blend(self.c["bg"], service_color(self.c, "note"), 0.45)))
        fmt.setForeground(QColor(self.c["text"]))
        first = None
        doc = self.reader.document()
        block = doc.begin()
        while terms and block.isValid():
            folded = nv.fold(block.text())
            for w in terms:
                i = folded.find(w)
                while i >= 0:
                    cur = QTextCursor(doc)
                    cur.setPosition(block.position() + i)
                    cur.setPosition(block.position() + i + len(w), QTextCursor.KeepAnchor)
                    sel = QTextEdit.ExtraSelection()
                    sel.cursor, sel.format = cur, fmt
                    sels.append(sel)
                    first = first if first is not None else block.position() + i
                    i = folded.find(w, i + len(w))
            block = block.next()
        self.reader.setExtraSelections(sels)
        if first is not None and jump:
            cur = QTextCursor(doc)
            cur.setPosition(first)
            self.reader.setTextCursor(cur)
            self.reader.ensureCursorVisible()

    def _css(self) -> str:
        c = self.c
        hl = "".join(f".hl{i + 1}{{background-color:{_blend(c['bg'], col, 0.35)}}}"
                     for i, col in enumerate(HIGHLIGHTS))
        return (f"body{{color:{c['text']}}} a{{color:{c['accent_fg']}}}"
                f"h1{{font-size:x-large;font-weight:600}} h2{{font-size:large;font-weight:600}}"
                f"h3{{font-size:medium;font-weight:600}}"
                f"pre,code{{background-color:{c['group']}}}"
                f"th{{background-color:{c['group']}}} table{{border-color:{c['border']}}}"
                f"blockquote{{color:{c['muted']}}} .sf-file{{color:{c['muted']}}}"
                f".sf-box{{font-family:'Segoe UI Symbol';font-size:large;color:{c['muted']}}}"
                f".sf-done{{color:{c['accent_fg']}}}" + hl)

    # --- links --------------------------------------------------------------------------------
    def _on_link(self, url: QUrl) -> None:
        ref = url.toString()
        if ref == "skyfold:all-pages":
            self.all_pages = True
            self.current_key = None
            self._show_current()
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

    # --- keyboard -----------------------------------------------------------------------------
    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def _cycle(self, step: int) -> None:
        order = [self.search, self.folders, self.notes, self.reader if self.reader.isVisible() else self.notes]
        now = next((i for i, w in enumerate(order) if w.hasFocus()), -1)
        order[(now + step) % len(order)].setFocus()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key_Escape and self.search.text():
            self.search.clear()
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self.notes.hasFocus():
            self.reader.setFocus()
            return
        super().keyPressEvent(e)

    # --- look ---------------------------------------------------------------------------------
    def apply_palette(self, palette: dict[str, str]) -> None:
        c = self.c = palette
        self.notes.setStyleSheet(f"QListWidget{{background:{c['surface']};border:1px solid {c['border']};"
                                 f"border-radius:8px;padding:4px;outline:0}}"
                                 f"QListWidget::item{{border:none;background:transparent}}")
        self.reader.setStyleSheet(f"QTextBrowser{{background:{c['surface']};border:1px solid {c['border']};"
                                  f"border-radius:8px;padding:12px 18px;selection-background-color:{c['accent']};"
                                  f"selection-color:{c['accent_text']}}}")
        self.folders.setStyleSheet("QTreeView{show-decoration-selected:0}")
        if hasattr(self, "rows"):
            self._fill_folders()
            self.current_key = None
            self._show_current()
            self.notes.viewport().update()

    def closeEvent(self, e) -> None:  # noqa: N802
        self.poll.stop()
        self.indexer.stop()
        self.reader.clear_note()
        self.texts.clear()
        super().closeEvent(e)

    def clear(self) -> None:
        """Signed out or another account: nothing of the old notes stays on screen."""
        self.indexer.stop()
        self.texts.clear()
        self.rows, self.row_by_id = [], {}
        self.reader.clear_note()
        self.close()
