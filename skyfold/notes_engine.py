"""One-way Apple Notes export: every note becomes an HTML file on this PC, kept up to date.

Layout:  <notes folder>/<Notes folder path>/<Note title>.html, attachments under _assets/,
plus Notes.html – an index of all notes with search, to browse them in any web browser.
Only notes changed since the last run are fetched (Notes' change feed). Notes deleted on the
phone move to the Recycle Bin here. Locked (password-protected) notes can't be read and are
only listed in the index.
"""

from __future__ import annotations

import html
import json
import logging
import os
import tempfile
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .engine import Callbacks, Cancelled, SyncError, _is_link, inside_root, now_iso
from .i18n import t
from .safelog import brief
from .remote import AuthRequired, RemoteUnavailable, _translate

log = logging.getLogger(__name__)

MARKER = ".skyfold-notes"
FORMAT = "7"  # bump to re-export all notes when the page format improves
ASSETS = "_assets"
INDEX = "Notes.html"
UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id        TEXT PRIMARY KEY,
    rel_path  TEXT,             -- NULL for locked notes (listed in the index only)
    title     TEXT,
    folder    TEXT,
    modified  TEXT,
    locked    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notes_meta (key TEXT PRIMARY KEY, value TEXT);
"""


@dataclass(slots=True)
class NoteInfo:
    id: str
    title: str
    folder_id: str | None
    modified: datetime | None
    deleted: bool
    locked: bool


class NotesRemote(Protocol):
    def cursor(self) -> str: ...
    def changes(self, since: str | None) -> list[NoteInfo]: ...
    def folders(self) -> dict[str, tuple[str, str | None]]: ...  # id -> (name, parent id)
    def export(self, note_id: str, out_dir: Path, filename: str, assets_dir: Path) -> None: ...


class ICloudNotes:
    def __init__(self, api: Any) -> None:
        self._api = api
        self._svc: Any = None

    @property
    def svc(self) -> Any:
        if self._svc is None:
            try:
                self._svc = self._api.notes
            except Exception as exc:  # noqa: BLE001
                raise _translate(exc) from exc
        return self._svc

    def _call(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except (AuthRequired, RemoteUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    def cursor(self) -> str:
        return self._call(lambda: self.svc.sync_cursor())

    def changes(self, since: str | None) -> list[NoteInfo]:
        def info(n: Any, deleted: bool = False) -> NoteInfo:
            return NoteInfo(n.id, n.title or "", n.folder_id, n.modified_at, deleted or bool(n.is_deleted),
                            bool(n.is_locked))

        def run() -> list[NoteInfo]:
            if since is None:
                return [info(n) for n in self.svc.iter_all()]
            return [info(ev.note, ev.type == "deleted") for ev in self.svc.iter_changes(since=since)]
        return self._call(run)

    def folders(self) -> dict[str, tuple[str, str | None]]:
        return self._call(lambda: {f.id: (f.name or "", f.parent_id) for f in self.svc.folders()})

    def export(self, note_id: str, out_dir: Path, filename: str, assets_dir: Path) -> None:
        _prefer_sharpest_previews()

        def run() -> None:
            from pyicloud.services.notes.rendering.exporter import NoteExporter
            from pyicloud.services.notes.rendering.options import ExportConfig

            record = self.svc._lookup_note_record(note_id)
            exporter = NoteExporter(self.svc._raw, config=ExportConfig(assets_dir=str(assets_dir), full_page=True))
            _SKETCH_FALLBACKS.clear()
            _SKETCH_INFO.clear()
            if not filename.startswith(STAGE_PREFIX):
                _CURRENT_NOTE[0] = str(out_dir.name + "/" + filename)
            if not exporter.export(record, output_dir=str(out_dir), filename=filename):
                raise SyncError(t("Note has no content"))
            if _SKETCH_FALLBACKS:
                page = out_dir / (filename if filename.lower().endswith(".html") else filename + ".html")
                if page.exists():
                    fallbacks = dict(_SKETCH_FALLBACKS)
                    upgrade_sketches(page, fallbacks, lambda url, folder: self.svc._raw.download_asset_to(url, str(folder)))
        self._call(run)


_PATCHED = False


def _prefer_sharpest_previews() -> None:
    """Drawings made with a pen come as several preview images; pyicloud takes the first (small)
    one. Pick the largest of the matching appearance instead, so handwriting stays sharp."""
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True
    try:
        from pyicloud.services.notes.rendering import ck_datasource as cds

        for cls in vars(cds).values():
            if isinstance(cls, type) and hasattr(cls, "_match_preview_appearance"):
                original = cls._match_preview_appearance

                def largest(self, tokens, apps, pref_code, asset_url, _orig=original):
                    try:
                        def size(tok):
                            return int(getattr(tok, "size", None) or (tok.get("size") if isinstance(tok, dict) else 0) or 0)

                        idx = list(range(len(tokens)))
                        if isinstance(apps, (list, tuple)) and len(apps) == len(tokens):
                            same = [i for i in idx if str(apps[i]) == str(pref_code)]
                            idx = same or idx
                        best = max(idx, key=lambda i: size(tokens[i]))
                        url = asset_url(tokens[best])
                        if url:
                            return url
                    except Exception:  # noqa: BLE001 - fall back to the library's choice
                        pass
                    return _orig(self, tokens, apps, pref_code, asset_url)

                cls._match_preview_appearance = largest
    except Exception as exc:  # noqa: BLE001
        log.info("Could not tune note previews: %s", exc)
    _prefer_full_images()
    _prefer_vector_sketches()


# Photos the browser can show as they are; for these the full image beats any preview.
FULL_IMAGE_UTIS = {"public.jpeg", "public.png", "public.gif", "public.webp", "public.bmp"}


def _prefer_full_images() -> None:
    """pyicloud only swaps a photo's preview for the full image when the preview it chose is the
    very first one; with the sharpest preview chosen instead it kept a ~384 px thumbnail. Always use
    the full image for photos a browser can display (HEIC can't be shown, so its preview stays)."""
    try:
        from pyicloud.services.notes.rendering import exporter as ex

        original = ex._wire_media_to_parent

        def wire(ds, media_map, mrec, url, config, _orig=original):
            _orig(ds, media_map, mrec, url, config)
            parent = media_map.get(mrec.recordName)
            try:
                uti = (ds.get_attachment_uti(parent) or "").lower() if parent else ""
            except Exception:  # noqa: BLE001
                uti = ""
            if parent and url and uti in FULL_IMAGE_UTIS:
                ds.set_primary_asset_url(parent, url)

        ex._wire_media_to_parent = wire
    except Exception as exc:  # noqa: BLE001
        log.info("Could not prefer full note images: %s", exc)


# Pen drawings (com.apple.paper): attachment id -> download URL of the full "fallback" bitmap.
# Filled while a note is read; used right after its export (see ICloudNotes.export).
_SKETCH_FALLBACKS: dict[str, str] = {}
_SKETCH_INFO: dict[str, str] = {}
_CURRENT_NOTE = [""]  # file name of the note being exported (for the log)


def _prefer_vector_sketches() -> None:
    """Pen drawings only come with small square previews (768 px, sometimes cropped). Most also carry
    a full-size bitmap of the whole drawing (FallbackImage); remember it so the export can swap it in.
    (Their "FallbackPDF" is an empty placeholder page, so that one is not used.)"""
    try:
        from pyicloud.services.notes.rendering import ck_datasource as cds

        cls = cds.CloudKitNoteDataSource
        original = cls.add_attachment_record

        def add(self, rec, _orig=original):
            _orig(self, rec)
            try:
                fields = rec.fields
                uti = fields.get_value("UTI") or fields.get_value("AttachmentUTI")
                if not uti:
                    enc = fields.get_value("UTIEncrypted")
                    uti = enc.decode("utf-8", "replace") if isinstance(enc, (bytes, bytearray)) else ""
                if str(uti).lower() != "com.apple.paper":
                    return
                try:  # for the log when a drawing stays small: which images does Apple keep for it?
                    def is_asset(x):
                        return hasattr(x, "downloadURL") or (isinstance(x, dict) and "downloadURL" in x)

                    def nbytes(x):
                        return str(x.get("size", "?") if isinstance(x, dict) else getattr(x, "size", "?"))

                    parts = []
                    for k in fields:
                        v = getattr(fields.get_field(k), "value", None)
                        if isinstance(v, (list, tuple)) and v and is_asset(v[0]):
                            parts.append(f"{k}[{','.join(nbytes(x) for x in v)} B]")
                        elif is_asset(v):
                            parts.append(f"{k}[{nbytes(v)} B]")
                        elif k.startswith(("Fallback", "Preview", "Paper", "Image", "Size", "Orientation")):
                            parts.append(k if isinstance(v, (bytes, bytearray)) else f"{k}={v!r}"[:60])
                    _SKETCH_INFO[rec.recordName] = "; ".join(parts)
                except Exception:  # noqa: BLE001
                    pass
                fld = fields.get_field("FallbackImage")
                url = getattr(getattr(fld, "value", None), "downloadURL", None) if fld else None
                if isinstance(url, str) and url:
                    keys = [rec.recordName] + [getattr(fields.get_field(k) or (), "value", None)
                                               for k in ("AttachmentIdentifier", "attachmentIdentifier", "Identifier")]
                    for key in keys:
                        if isinstance(key, str) and key:
                            _SKETCH_FALLBACKS[key] = url
                else:
                    log.info("Drawing in %s has no full-size image in iCloud, only: %s",
                             _CURRENT_NOTE[0], _SKETCH_INFO.get(rec.recordName, "?"))
            except Exception as exc:  # noqa: BLE001 - keep the preview
                log.debug("Sketch fallback not used: %s", exc)

        cls.add_attachment_record = add
    except Exception as exc:  # noqa: BLE001
        log.info("Could not prefer vector sketches: %s", exc)


# Light/dark choice shared by the index and every note page (remembered in the browser).
THEME_SCRIPT = """<script>(function(){var k='skyfold-theme',r=document.documentElement;
try{var v=localStorage.getItem(k);if(v)r.dataset.theme=v}catch(e){}
window.sfToggleTheme=function(){var dark=r.dataset.theme?r.dataset.theme==='dark'
:matchMedia('(prefers-color-scheme: dark)').matches;r.dataset.theme=dark?'light':'dark';
try{localStorage.setItem(k,r.dataset.theme)}catch(e){}}})();</script>"""

THEME_BUTTON = ('<button class="sf-theme" type="button" onclick="sfToggleTheme()" title="{label}" '
                'aria-label="{label}"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" '
                'stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="8.5"/>'
                '<path d="M12 3.5a8.5 8.5 0 0 1 0 17z" fill="currentColor"/></svg></button>')

THEME_VARS = """
:root{--bg:#f5f7fa;--card:#fff;--text:#0f172a;--muted:#64748b;--border:#e2e8f0;--accent:#2563eb;--hover:#eff6ff}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#000;--card:#0b0b0d;--text:#f3f4f6;
--muted:#9ca3af;--border:#232327;--accent:#60a5fa;--hover:#0d1a30}}
:root[data-theme=dark]{--bg:#000;--card:#0b0b0d;--text:#f3f4f6;--muted:#9ca3af;--border:#232327;--accent:#60a5fa;
--hover:#0d1a30}
.sf-theme{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;padding:0;
border:1px solid var(--border);border-radius:9px;background:var(--card);color:var(--text);cursor:pointer}
.sf-theme:hover{border-color:var(--accent);color:var(--accent)}
"""

NOTE_CSS = """
<style id="skyfold">""" + THEME_VARS + """
html{background:var(--bg)}
body{max-width:760px;margin:0 auto!important;padding:20px 20px 60px!important;background:var(--bg)!important;
color:var(--text)!important;font:17px/1.6 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif!important}
.sf-nav{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:12px;padding:10px 0 12px;
margin-bottom:8px;background:var(--bg);border-bottom:1px solid var(--border)}
.sf-nav a{color:var(--accent);text-decoration:none;font-weight:600}
.sf-nav a:hover{text-decoration:underline}
.sf-nav span{color:var(--muted);font-size:14px;margin-left:auto}
@media print{.sf-theme{display:none}}
h1{font-size:28px;line-height:1.25;margin:12px 0 16px}
img{max-width:100%;height:auto;border-radius:10px}
img[src*="_assets"]{background:#fff}
.sf-pdf{margin:12px 0 20px}
.sf-page{display:block;width:100%;margin:0 0 10px;border:1px solid var(--border);border-radius:10px;background:#fff}
.sf-sketch{display:block;width:100%;height:auto;margin:8px 0;border-radius:10px;background:#fff}
.sf-open{font-size:14px}
object,embed,iframe{width:100%!important;height:85vh!important;border:1px solid var(--border);border-radius:10px;background:#fff}
table{border-collapse:collapse;max-width:100%} td,th{border:1px solid var(--border);padding:6px 10px}
a{color:var(--accent)}
@media print{.sf-nav{display:none}}
</style>
"""


def polish_note_html(page: str, back_href: str, back_text: str, updated: str,
                     theme_text: str = "Light / dark") -> str:
    """Make an exported note pleasant to read: our styles, a sticky "back to all notes" bar with a
    light/dark switch, and PDFs shown plainly (no viewer toolbar with Draw/Highlight tools)."""
    if 'id="skyfold"' in page:
        return page
    page = re.sub(r'((?:data|src)="[^"#]+\.pdf)(")', r'\1#toolbar=0&navpanes=0&view=FitH\2', page,
                  flags=re.IGNORECASE)
    head = NOTE_CSS + THEME_SCRIPT
    head_end = page.lower().find("</head>")
    page = page[:head_end] + head + page[head_end:] if head_end >= 0 else head + page
    nav = (f'<div class="sf-nav"><a href="{html.escape(back_href)}">&larr; {html.escape(back_text)}</a>'
           f'<span>{html.escape(updated)}</span>'
           f'{THEME_BUTTON.format(label=html.escape(theme_text, quote=True))}</div>')
    m = re.search(r"<body[^>]*>", page, re.IGNORECASE)
    return page[:m.end()] + nav + page[m.end():] if m else nav + page


PDF_TAG = re.compile(
    r'<object\b[^>]*?\sdata="([^"#]+\.pdf)[^"]*"[^>]*>.*?</object>'  # with its fallback content
    r'|<(?:embed|iframe)\b[^>]*?\ssrc="([^"#]+\.pdf)[^"]*"[^>]*>(?:\s*</iframe>)?',
    re.IGNORECASE | re.DOTALL)


def render_pdf_pages(pdf: Path, width: int = 1500) -> list[Path]:
    """Render each page of a PDF to a sharp PNG next to it (name.pdf.p1.png, …). Returns the images,
    or [] if the PDF can't be read. Uses Qt's PDF module, so no extra dependency."""
    try:
        from PySide6.QtCore import QSize
        from PySide6.QtPdf import QPdfDocument
    except ImportError:
        return []
    doc = QPdfDocument()
    if doc.load(str(pdf)) != QPdfDocument.Error.None_ or doc.pageCount() == 0:
        return []
    out: list[Path] = []
    for i in range(min(doc.pageCount(), 50)):
        pts = doc.pagePointSize(i)
        if pts.width() <= 0:
            continue
        size = QSize(width, max(1, int(width * pts.height() / pts.width())))
        img = doc.render(i, size)
        if not img.isNull():
            img = _on_white(img)  # drawings have no background: dark ink would vanish in dark mode
        target = pdf.with_name(f"{pdf.name}.p{i + 1}.png")
        if img.isNull() or not img.save(str(target)):
            continue
        out.append(target)
    doc.close()
    return out


def local_datetime(value) -> str:
    """Date and time in Windows' regional format (Settings › Time & language › Region)."""
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    from PySide6.QtCore import QDateTime, QLocale

    if value.tzinfo is not None:
        value = value.astimezone()
    return QLocale.system().toString(QDateTime(value), QLocale.FormatType.ShortFormat)


def _on_white(img):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter

    out = QImage(img.size(), QImage.Format.Format_RGB32)
    out.fill(Qt.GlobalColor.white)
    p = QPainter(out)
    p.drawImage(0, 0, img)
    p.end()
    return out


IMG_TAG = re.compile(r'<img\b[^>]*?\ssrc="([^"]+)"[^>]*>', re.IGNORECASE)


def upgrade_sketches(page: Path, fallbacks: dict[str, str], download) -> int:
    """Swap each pen drawing's small preview for its full-size bitmap, when that one is bigger and a
    browser can show it. download(url, folder) saves the file and returns its path. Returns the
    number of drawings upgraded; on any problem the preview simply stays."""
    import urllib.parse

    from PySide6.QtGui import QImageReader

    note_dir = page.parent
    text = page.read_text(encoding="utf-8", errors="replace")
    count = 0

    def swap(m: re.Match) -> str:
        nonlocal count
        tag, src = m.group(0), m.group(1)
        ident = re.search(r'\sdata-id="([^"]+)"', tag)
        url = fallbacks.get(ident.group(1)) if ident and 'com.apple.paper' in tag else None
        if not url or src.startswith(("http:", "https:", "data:")):
            return tag
        current = (note_dir / urllib.parse.unquote(src).replace("\\", "/")).resolve()
        if ASSETS not in current.parts:
            return tag  # only our own attachment files are ever replaced
        new: Path | None = None
        try:
            new = Path(download(url, current.parent))
            reader = QImageReader(str(new))
            size, fmt, ok = reader.size(), bytes(reader.format().data()).decode() or "png", reader.canRead()
            reader.setFileName("")  # release the file (Windows keeps it locked otherwise)
            del reader
            preview_w = QImageReader(str(current)).size().width()
            if not ok or size.width() <= preview_w:
                log.info("Drawing in %s: full image not used (readable=%s, %sx%s, format=%r, %d bytes; "
                         "preview %s px)", page.name, ok, size.width(), size.height(), fmt, new.stat().st_size,
                         preview_w)
                new.unlink(missing_ok=True)
                return tag
            ext = ".jpg" if fmt == "jpeg" else "." + fmt
            if new.suffix.lower() != ext:
                renamed = new.with_name(new.name + ext)
                os.replace(new, renamed)
                new = renamed
        except Exception as exc:  # noqa: BLE001 - keep the preview
            log.info("Full drawing not used: %s", exc)
            if new is not None:
                new.unlink(missing_ok=True)
            return tag
        count += 1
        try:
            current.unlink()
        except OSError:
            pass
        rel = urllib.parse.quote(os.path.relpath(new, note_dir).replace("\\", "/"))
        return tag.replace(f'src="{src}"', f'src="{rel}"', 1)

    text = IMG_TAG.sub(swap, text)
    if count:
        page.write_text(text, encoding="utf-8")
    return count


def pdfs_to_images(page: str, note_dir: Path, open_text: str) -> str:
    """Replace embedded PDF viewers with the pages as images (plain, sharp, nothing to click),
    plus a small link to the original PDF."""
    import urllib.parse

    def swap(m: re.Match) -> str:
        src = m.group(1) or m.group(2)
        pdf = (note_dir / urllib.parse.unquote(src)).resolve()
        if ASSETS not in pdf.parts or not pdf.exists():
            return m.group(0)
        images = render_pdf_pages(pdf)
        if not images:
            return m.group(0)
        rel = lambda p: urllib.parse.quote(os.path.relpath(p, note_dir).replace("\\", "/"))  # noqa: E731
        pages = "".join(f'<img class="sf-page" src="{rel(p)}" alt="">' for p in images)
        return f'<div class="sf-pdf">{pages}<a class="sf-open" href="{src}">{html.escape(open_text)}</a></div>'

    return PDF_TAG.sub(swap, page)


def _safe(name: str, fallback: str) -> str:
    name = UNSAFE.sub("_", name).strip(" .")
    return (name or fallback)[:120].rstrip(" .") or fallback  # Windows drops a trailing space or dot


class NotesState:
    def __init__(self, db_path: Path) -> None:
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def all(self) -> dict[str, tuple]:
        with self._lock:
            return {r[0]: r for r in self._db.execute(
                "SELECT id, rel_path, title, folder, modified, locked FROM notes")}

    def put(self, nid: str, rel: str | None, title: str, folder: str, modified: str | None, locked: bool) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO notes VALUES(?,?,?,?,?,?)",
                             (nid, rel, title, folder, modified, int(locked)))

    def drop(self, nid: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM notes WHERE id=?", (nid,))

    def get(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM notes_meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str | None) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO notes_meta VALUES(?,?)", (key, value))

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]

    def reset(self) -> None:
        with self._lock:
            self._db.executescript("DELETE FROM notes; DELETE FROM notes_meta;")


@dataclass
class NotesResult:
    exported: int = 0
    removed: int = 0
    errors: int = 0
    full: bool = False
    more: bool = False  # stopped at the per-run limit; the next run continues
    held: bool = False  # a note failed again (reported before): the cursor waits so it is tried again


def _is_our_index(p: Path) -> bool:
    """Ours = the owner mark near the top (since 1.0.7), or an index from an earlier Skyfold (its exact
    script line and style block, anywhere in the file)."""
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return False
    if INDEX_MARK in text[:4096]:
        return True
    return "const KEY='skyfold-collapsed'" in text and "skyfold-theme" in text


STAGE_PREFIX = ".skyfold-tmp-note-"
# pyicloud leaves an attachment's iCloud web address in the page when its download failed
_MISSING_ATTACHMENT = re.compile(r"""(?i)\b(?:src|data|poster)\s*=\s*["']?https?://[^"'\s>]*icloud-content\.com""")


def _digest(p: Path) -> str:
    import hashlib

    return hashlib.sha256(p.read_bytes()).hexdigest()


def _is_our_page(p: Path) -> bool:
    """Only pages Skyfold wrote (they carry its style block) are ever removed: an .html file the
    user keeps in the same folder is left alone."""
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            return 'id="skyfold"' in fh.read(200_000)
    except OSError:
        return False


class NotesSyncer:
    MAX_PER_RUN = 15  # exports take ~10 s each; keep runs short so photo syncing isn't held up

    def __init__(self, state: NotesState, remote: NotesRemote, root: Path, cb: Callbacks | None = None) -> None:
        self.state = state
        self.remote = remote
        self.root = root
        self.cb = cb or Callbacks()
        self.cancel_flag = threading.Event()

    def _check_root(self) -> None:
        has = self.state.count() > 0
        if not self.root.exists():
            if has:
                raise SyncError(t("Notes folder is not available: {path}", path=self.root))
            self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / MARKER
        if not marker.exists():
            if has:
                raise SyncError(t("The Notes folder doesn't look like before (its sync marker is missing). "
                                  "Syncing has stopped so nothing gets deleted by mistake."))
            marker.write_text(t("This folder is synced by Skyfold. Don't delete this file.\n"), encoding="utf-8")
            if os.name == "nt":
                import ctypes

                ctypes.windll.kernel32.SetFileAttributesW(str(marker), 0x2)

    def _folder_paths(self) -> dict[str, str]:
        raw = self.remote.folders()
        out: dict[str, str] = {}

        def path_of(fid: str, depth: int = 0) -> str:
            if fid in out:
                return out[fid]
            name, parent = raw.get(fid, ("", None))
            own = _safe(name, "Notes")
            p = own if not parent or parent not in raw or depth > 20 else f"{path_of(parent, depth + 1)}/{own}"
            out[fid] = p
            return p

        for fid in raw:
            path_of(fid)
        return out

    def _trash(self, p: Path) -> None:
        from .engine import recycle

        recycle(p)

    def _tidy_leftovers(self) -> None:
        """Private files left by a crash: unfinished exports are removed, a moved-aside page goes to the
        Recycle Bin, and a moved-aside index comes back if Notes.html is missing."""
        cutoff = time.time() - 3600
        for dirpath, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d != ASSETS and not _is_link(Path(dirpath) / d)]
            for f in files:
                if not (f.startswith(STAGE_PREFIX) or (f.startswith(".skyfold-index-") and f.endswith(".tmp"))):
                    continue
                p = Path(dirpath) / f
                try:
                    if p.stat().st_mtime > cutoff or not inside_root(self.root, p):
                        continue  # may belong to a run that is still going
                    if f.startswith(STAGE_PREFIX + "index-") and not (self.root / INDEX).exists():
                        os.rename(p, self.root / INDEX)
                    elif f.startswith((STAGE_PREFIX + "old-", STAGE_PREFIX + "index-")):
                        self._trash(p)
                    else:
                        p.unlink()
                except OSError as exc:
                    log.info("Notes: couldn't tidy %s: %s", f, exc)

    def run(self, full: bool = False) -> NotesResult:
        self.cancel_flag.clear()
        res = NotesResult()
        last_page = INDEX
        self._check_root()
        self._tidy_leftovers()
        if self.state.get("format") != FORMAT:  # nicer pages / sharper drawings: export all again
            for nid, row in self.state.all().items():
                self.state.put(nid, row[1], row[2], row[3], None, bool(row[5]))
            self.state.set("cursor", None)
            self.state.set("format", FORMAT)
        stored = self.state.get("cursor")
        cursor = self.remote.cursor()
        if stored == cursor and not full:
            if self.state.get("orphan_sweep") != "1":  # one-time tidy-up, works offline from the database
                self._remove_leftover_pages(res)
                self.state.set("orphan_sweep", "1")
                self.write_index()
            self.state.set("last_sync", now_iso())  # checked, nothing changed: still a check
            return res
        res.full = full or not stored
        self.cb.status(t("Reading Apple Notes…"))
        notes = self.remote.changes(None if res.full else stored)
        folders = self._folder_paths() if notes or res.full else {}
        known = self.state.all()
        seen: set[str] = set()
        taken = {r[1].lower() for r in known.values() if r[1]}

        for i, n in enumerate(notes, 1):
            if self.cancel_flag.is_set():
                raise Cancelled()
            seen.add(n.id)
            old = known.get(n.id)
            if n.deleted:
                if old and self._remove(old, res):
                    self.state.drop(n.id)
                continue
            if not res.full and self.state.get(f"missing:{n.id}"):
                self.state.set(f"missing:{n.id}", None)  # seen alive: an earlier absence doesn't count
            folder = folders.get(n.folder_id or "", "Notes")
            modified = n.modified.isoformat() if n.modified else None
            if n.locked:
                keep = None
                if old and old[1] and not self._remove(old, res):
                    keep = old[1]  # couldn't remove the readable copy yet: remember it, try again next time
                self.state.put(n.id, keep, n.title, folder, None if keep else modified, True)
                continue
            if old and old[4] == modified and old[1] and (self.root / old[1]).exists() and old[3] == folder \
                    and old[2] == n.title:
                continue  # unchanged
            if res.exported >= self.MAX_PER_RUN:
                res.more = True  # the rest follows next run (the cursor isn't advanced)
                continue
            self.cb.progress(i, len(notes), t("Downloading note {i} of {n}", i=i, n=len(notes)))
            rel = self._path_for(n, folder, old, taken)
            try:
                out_dir = (self.root / rel).parent
                if not inside_root(self.root, out_dir):
                    raise SyncError(t("{name} leads outside the notes folder; left alone", name=out_dir))
                out_dir.mkdir(parents=True, exist_ok=True)
                # Exported under a private name first, then put in place without overwriting anything.
                staged_rel = f"{PurePosixPath(rel).parent.as_posix()}/{STAGE_PREFIX}{uuid.uuid4().hex}.html"
                try:
                    _CURRENT_NOTE[0] = rel
                    self.remote.export(n.id, out_dir, PurePosixPath(staged_rel).name, self.root / ASSETS)
                    if _MISSING_ATTACHMENT.search((self.root / staged_rel).read_text(encoding="utf-8",
                                                                                     errors="replace")):
                        raise SyncError(t("an attachment couldn't be downloaded; tried again next time"))
                    self._polish(staged_rel, n)
                    if n.modified:
                        ts = n.modified.timestamp()
                        os.utime(self.root / staged_rel, (ts, ts))
                    self._publish_page(self.root / staged_rel, self.root / rel, n.id, old, n.modified)
                finally:
                    (self.root / staged_rel).unlink(missing_ok=True)
            except (Cancelled, AuthRequired, RemoteUnavailable):
                raise
            except Exception as exc:  # noqa: BLE001
                key = f"failed:{n.id}"
                if self.state.get(key) == (modified or "?") and not full:
                    res.held = True  # reported already; the cursor doesn't move, so it is tried again
                    continue
                self.state.set(key, modified or "?")
                res.errors += 1
                self.cb.activity("error", t("Couldn't export note ({name}): {error}", name=n.title or n.id, error=brief(exc)))
                continue
            self.state.set(f"failed:{n.id}", None)
            if old and old[1] and old[1].lower() != rel.lower() and (self.root / old[1]).exists():
                self._remove(old, res, count=False)  # renamed or moved: drop the old file
            self.state.put(n.id, rel, n.title, folder, modified, False)
            taken.add(rel.lower())
            res.exported += 1
            last_page = rel

        if res.full and not res.more:  # notes that vanished entirely (e.g. purged) since the last full read
            for nid in seen:
                self.state.set(f"missing:{nid}", None)
            missing_now = [nid for nid in known if nid not in seen]
            gone = [nid for nid in missing_now if self.state.get(f"missing:{nid}")]  # missing twice in a row
            for nid in missing_now:
                self.state.set(f"missing:{nid}", "1")
            if gone and (not seen or len(gone) > max(10, len(known) // 2)):
                log.warning("Notes: a full read misses %d of %d notes; not removing them (looks incomplete)",
                            len(gone), len(known))
                res.errors += 1  # the cursor isn't advanced; the next full read decides again
            else:
                for nid in gone:
                    if self._remove(known[nid], res):
                        self.state.drop(nid)
                        self.state.set(f"missing:{nid}", None)
        if not res.more and not res.errors and (res.full or self.state.get("orphan_sweep") != "1"):
            self._remove_leftover_pages(res)
            self.state.set("orphan_sweep", "1")
        self.write_index()
        if not res.errors and not res.more and not res.held:
            self.state.set("cursor", cursor)
        self.state.set("last_sync", now_iso())
        if res.exported:
            # click: the note itself when it's just one, otherwise the list of all notes
            target = self.root / (last_page if res.exported == 1 else INDEX)
            text = (t("Notes: downloaded {name}", name=Path(last_page).stem) if res.exported == 1
                    else t("Notes: downloaded {n}", n=res.exported))
            self.cb.activity("download", text, str(target))
        return res

    def _remove_leftover_pages(self, res: NotesResult) -> None:  # noqa: C901
        """Note pages on disk that no note uses any more (e.g. "Title (3).html" left behind when
        same-titled notes were numbered differently) go to the Recycle Bin. Only .html pages inside
        the notes folder are touched, and never while the database looks empty or incomplete."""
        rows = self.state.all()
        tracked = {r[1].lower() for r in rows.values() if r[1]}
        if not tracked or any(r[4] is None and not r[5] for r in rows.values()):
            return  # nothing known yet, or notes still waiting to be downloaded
        note_dirs = {str(PurePosixPath(r).parent).lower() for r in tracked}
        leftovers = []
        for dirpath, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d != ASSETS and not _is_link(Path(dirpath) / d)]  # never through a link
            rel_dir = Path(dirpath).relative_to(self.root).as_posix()
            if rel_dir.lower() not in note_dirs:
                continue  # only the notes' own folders: copies kept elsewhere are the user's
            for f in files:
                if not f.lower().endswith(".html") or f == INDEX:
                    continue
                p = Path(dirpath) / f
                rel = p.relative_to(self.root).as_posix()
                if rel.lower() not in tracked and inside_root(self.root, p) and _is_our_page(p):
                    leftovers.append(p)
        if len(leftovers) > max(10, len(tracked) // 2):
            log.warning("Not removing %d untracked note pages: too many, looks wrong", len(leftovers))
            return
        for p in leftovers:
            try:
                self._trash(p)
                res.removed += 1
                self.cb.activity("trash", t("Notes: removed {name}", name=p.relative_to(self.root).as_posix()))
            except OSError as exc:
                log.warning("Could not remove leftover note page %s: %s", p, exc)

    def _polish(self, rel: str, n: NoteInfo) -> None:
        p = self.root / rel
        depth = rel.count("/")
        back = "../" * depth + INDEX
        updated = local_datetime(n.modified)
        page = p.read_text(encoding="utf-8", errors="replace")
        page = pdfs_to_images(page, p.parent, t("Open the original PDF"))
        p.write_text(polish_note_html(page, back, t("All notes"), updated, t("Light / dark")), encoding="utf-8")

    def _publish_page(self, staged: Path, target: Path, nid: str, old: tuple | None, modified=None) -> None:
        """Put a freshly exported page in place. A file there is first moved aside (so nothing saved at
        this moment is overwritten); someone else's file is given back, and our previous page is only
        deleted when it is exactly what Skyfold wrote, otherwise it goes to the Recycle Bin."""
        digest = _digest(staged)
        aside = None
        if target.exists():
            aside = target.with_name(f"{STAGE_PREFIX}old-{uuid.uuid4().hex}.html")
            os.rename(target, aside)
            if not _is_our_page(aside):  # someone's own file appeared there: give it back untouched
                os.rename(aside, target)
                raise SyncError(t("{name} isn't a page Skyfold wrote; the note gets another name next time",
                                  name=target.name))
        try:
            os.rename(staged, target)  # refuses if a file was saved there meanwhile
        except FileExistsError:
            if aside is not None:
                self._let_go(aside, target, nid, old, modified)
            raise SyncError(t("{name} changed while it was downloading; tried again next time",
                              name=target.name)) from None
        except BaseException:
            if aside is not None:
                try:
                    os.rename(aside, target)
                except OSError:
                    self._let_go(aside, target, nid, old, modified)
            raise
        if aside is not None:
            self._let_go(aside, target, nid, old, modified)
        self.state.set(f"page:{nid}", digest)

    def _let_go(self, aside: Path, target: Path, nid: str, old: tuple | None, modified=None) -> None:
        """Our previous page: deleted when unchanged since Skyfold wrote it, else to the Recycle Bin."""
        recorded = self.state.get(f"page:{nid}")
        try:
            if recorded:
                unchanged = _digest(aside) == recorded
            else:  # written before 1.0.6: it carries the note's modified time (as last exported), edits change that
                times = [datetime.fromisoformat(old[4]).timestamp()] if old and old[4] else []
                if modified is not None:
                    times.append(modified.timestamp())
                mtime = aside.stat().st_mtime
                unchanged = any(abs(mtime - w) < 2 for w in times)
        except (OSError, ValueError):
            unchanged = False
        if unchanged:
            aside.unlink(missing_ok=True)
            return
        named = target.with_name(f"{target.stem} ({t('previous version')}).html")
        try:
            if not named.exists():
                os.rename(aside, named)
                aside = named
            self._trash(aside)
        except OSError as exc:
            log.warning("Notes: couldn't move the previous %s to the Recycle Bin: %s", target.name, exc)
            if aside != named and not named.exists():
                try:
                    os.rename(aside, named)  # at least visible, never left under a hidden name
                except OSError:
                    pass

    def _path_for(self, n: NoteInfo, folder: str, old: tuple | None, taken: set[str]) -> str:
        base = f"{folder}/{_safe(n.title, t('Untitled'))}"
        cand = f"{base}.html"
        here = self.root / cand
        if old and old[1] and old[1].lower() == cand.lower() and (not here.exists() or _is_our_page(here)):
            return cand
        k = 2
        while cand.lower() in taken or ((self.root / cand).exists() and not _is_our_page(self.root / cand)):
            cand = f"{base} ({k}).html"
            k += 1
        return cand

    def _remove(self, row: tuple, res: NotesResult, count: bool = True) -> bool:
        """Page to the Recycle Bin. False when it couldn't be removed (the note is remembered and tried again)."""
        if not row[1]:
            return True
        p = self.root / row[1]
        if not inside_root(self.root, p.parent):
            log.warning("Notes: %s leads outside the notes folder; not removed", row[1])
            return True  # nothing of ours to remove there
        try:
            if p.exists() and p.suffix.lower() == ".html" and not _is_our_page(p):
                log.info("Notes: %s isn't a page Skyfold wrote; left alone", row[1])
                return True  # someone else's file now: forget the note's old path, keep the file
            if p.exists():
                self._trash(p)
            if count:
                res.removed += 1
                self.cb.activity("trash", t("Notes: removed {name}", name=row[2] or row[1]))
            return True
        except OSError as exc:
            res.errors += 1
            self.cb.activity("error", t("Couldn't delete {name}: {error}", name=row[1], error=brief(exc)))
            return False

    # index page ------------------------------------------------------------------------
    def write_index(self) -> None:
        rows = sorted(self.state.all().values(), key=lambda r: (r[3] or "", (r[2] or "").lower()))
        data = [{"t": r[2] or t("Untitled"), "f": r[3] or "", "p": r[1], "m": local_datetime(r[4]),
                 "l": bool(r[5])} for r in rows]
        page = INDEX_TEMPLATE.format(
            title=html.escape(t("Apple Notes")),
            search=html.escape(t("Search notes…")),
            locked=html.escape(t("locked – open it on your iPhone")),
            count=html.escape(t("{n} notes", n=len(data))),
            updated=html.escape(t("Updated {when}", when=local_datetime(datetime.now()))),
            theme_script=THEME_SCRIPT, theme_vars=THEME_VARS,
            theme_button=THEME_BUTTON.format(label=html.escape(t("Light / dark"), quote=True)),
            collapse_all=json.dumps(t("Collapse all"), ensure_ascii=False),
            expand_all=json.dumps(t("Expand all"), ensure_ascii=False),
            data=json.dumps(data, ensure_ascii=False).replace("</", "<\\/"),
        )
        target = self.root / INDEX
        not_ours = SyncError(t("The Notes folder already has a Notes.html that isn't from Skyfold. Choose an empty "
                               "folder for Notes in Settings."))
        if target.exists() and not _is_our_index(target):
            raise not_ours
        fd, tmp = tempfile.mkstemp(prefix=".skyfold-index-", suffix=".tmp", dir=self.root)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(page)
        written = _digest(Path(tmp))  # what Skyfold wrote, known before the file is public
        aside = None
        try:
            if target.exists():  # moved aside and checked again: never overwrite what was saved meanwhile
                aside = target.with_name(f"{STAGE_PREFIX}index-{uuid.uuid4().hex}.html")
                os.rename(target, aside)
                if not _is_our_index(aside):
                    os.rename(aside, target)
                    aside = None
                    raise not_ours
            os.rename(tmp, target)  # refuses if a file was saved there meanwhile
        except BaseException:
            if aside is not None:
                try:
                    os.rename(aside, target)
                except OSError:
                    self._trash(aside)  # ours, but recoverable all the same
            raise
        finally:
            Path(tmp).unlink(missing_ok=True)
        if aside is not None:
            recorded = self.state.get("index_digest")
            if recorded and _digest(aside) == recorded:
                aside.unlink(missing_ok=True)  # exactly what Skyfold wrote
            else:  # edited, or unknown (no fingerprint saved, e.g. after a crash): recoverable
                self._trash(aside)
        self.state.set("index_digest", written)


INDEX_MARK = '<meta name="generator" content="Skyfold notes index">'
INDEX_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">""" + INDEX_MARK + """<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
{theme_script}
<style>{theme_vars}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif}}
.wrap{{max-width:880px;margin:0 auto;padding:28px 16px 60px}}
.top{{display:flex;align-items:flex-start;gap:8px}} .top>div:first-child{{flex:1;min-width:0}}
h1{{font-size:26px;margin:0 0 4px}} .sub{{color:var(--muted);font-size:13px;margin-bottom:18px}}
.tb{{height:34px;padding:0 12px;border:1px solid var(--border);border-radius:9px;background:var(--card);color:var(--text);font-size:13px;font-family:inherit;cursor:pointer}}
.tb:hover{{border-color:var(--accent);color:var(--accent)}}
input{{width:100%;padding:11px 14px;border:1px solid var(--border);border-radius:10px;background:var(--card);color:var(--text);font:inherit;outline:none}}
input:focus{{border-color:var(--accent)}}
.fh{{display:flex;align-items:center;gap:8px;width:100%;margin:22px 0 8px;padding:4px;border:0;background:none;cursor:pointer;
font-family:inherit;font-size:12px;font-weight:600;letter-spacing:.6px;text-transform:uppercase;color:var(--muted);text-align:left}}
.fh:hover{{color:var(--text)}} .fh .c{{margin-left:auto;font-weight:400;letter-spacing:0;text-transform:none}}
.fh svg{{transition:transform .15s}} .shut>.fh svg{{transform:rotate(-90deg)}} .shut>.body{{display:none}}
.sub{{margin-left:14px;padding-left:12px;border-left:2px solid var(--border)}} .sub .fh{{margin-top:14px}}
.body>.card+.sub{{margin-top:4px}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden}}
a.row,div.row{{display:flex;justify-content:space-between;gap:12px;padding:11px 14px;border-top:1px solid var(--border);color:var(--text);text-decoration:none}}
.card>.row:first-child{{border-top:none}} a.row:hover{{background:var(--hover)}}
.m{{color:var(--muted);font-size:13px;white-space:nowrap}} .lk{{color:var(--muted)}}
</style></head><body><div class="wrap">
<div class="top"><div><h1>{title}</h1><div class="sub">{count} · {updated}</div></div>
<button class="tb" id="all" type="button"></button>{theme_button}</div>
<input id="q" placeholder="{search}" autofocus>
<div id="list"></div></div>
<script>
const notes={data};
const T={{collapse:{collapse_all},expand:{expand_all}}};
const list=document.getElementById('list'),q=document.getElementById('q'),all=document.getElementById('all');
const KEY='skyfold-collapsed';let shut=new Set();
try{{shut=new Set(JSON.parse(localStorage.getItem(KEY)||'[]'))}}catch(e){{}}
function save(){{try{{localStorage.setItem(KEY,JSON.stringify([...shut]))}}catch(e){{}}}}
const folders=[...new Set(notes.flatMap(n=>{{const p=(n.f||'Notes').split('/');return p.map((_,i)=>p.slice(0,i+1).join('/'))}}))];
function fmt(s){{return s||''}}  // already in Windows' regional format
function esc(s){{return s.replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}})[c])}}
const chev='<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6l4 4 4-4"/></svg>';
// Folders nest like on iPhone: "Work/Offers" is shown inside "Work".
function tree(items){{const root={{kids:new Map(),notes:[]}};
for(const n of items){{let node=root,path='';for(const part of (n.f||'Notes').split('/')){{path=path?path+'/'+part:part;
if(!node.kids.has(part))node.kids.set(part,{{name:part,path,kids:new Map(),notes:[]}});node=node.kids.get(part)}}
node.notes.push(n)}}return root}}
function total(node){{let c=node.notes.length;for(const k of node.kids.values())c+=total(k);return c}}
function row(n){{return n.l?'<div class="row lk"><span>🔒 '+esc(n.t)+'</span><span class="m">{locked}</span></div>'
:'<a class="row" href="'+encodeURI(n.p).replace(/#/g,'%23')+'"><span>'+esc(n.t)+'</span><span class="m">'+esc(fmt(n.m))+'</span></a>'}}
function render(node,s){{let html='';for(const k of node.kids.values()){{const closed=!s&&shut.has(k.path);
html+='<section class="'+(closed?'shut':'')+'" data-f="'+esc(k.path)+'"><button class="fh" type="button">'+chev
+'<span>'+esc(k.name)+'</span><span class="c">'+total(k)+'</span></button><div class="body">';
if(k.notes.length)html+='<div class="card">'+k.notes.map(row).join('')+'</div>';
if(k.kids.size)html+='<div class="sub">'+render(k,s)+'</div>';
html+='</div></section>'}}return html}}
function draw(){{const s=q.value.trim().toLowerCase();
const items=notes.filter(n=>!s||n.t.toLowerCase().includes(s)||n.f.toLowerCase().includes(s));
list.innerHTML=render(tree(items),s);all.textContent=shut.size>=folders.length?T.expand:T.collapse;all.style.display=s?'none':''}}
list.addEventListener('click',e=>{{const b=e.target.closest('.fh');if(!b||q.value.trim())return;
const f=b.parentElement.dataset.f;shut.has(f)?shut.delete(f):shut.add(f);save();draw()}});
all.addEventListener('click',()=>{{shut=shut.size>=folders.length?new Set():new Set(folders);save();draw()}});
q.addEventListener('input',draw);draw();
</script></body></html>
"""
