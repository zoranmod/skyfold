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
import stat
import tempfile
import re
import sqlite3
import threading
import uuid
from collections.abc import MutableMapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .config import place_new
from .engine import Callbacks, Cancelled, SyncError, _is_link, create_marker, known_missing, inside_root, marker_present, now_iso
from .i18n import t
from .safelog import brief, value
from .remote import AuthRequired, RemoteUnavailable, _translate

log = logging.getLogger(__name__)

MARKER = ".skyfold-notes"
FORMAT = "7"  # bump to re-export all notes when the page format improves
QUALITY = "3"  # bump to re-export notes whose photos, drawings or scans are still shown as small previews
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
            _check_assets_dir(Path(assets_dir) / str(getattr(record, "recordName", "") or note_id))
            raw = self.svc._raw
            made: set[tuple[int, int]] = set()  # the files this export downloaded, by file identity
            dirs: set[Path] = set()  # and the folders it downloaded them into (the note's own)
            calls: dict[str, int] = {}

            class Recording:
                """This export's own view of the client: downloads are written down; the shared
                client itself is never changed (other callers and exports don't pass through here)."""

                def __getattr__(self, name):
                    return getattr(raw, name)

                def download_asset_to(self, url, directory):
                    turn = calls[url] = calls.get(url, -1) + 1  # the n-th attachment fetching this file
                    try:
                        path = raw.download_asset_to(url, directory)
                    except Exception as exc:  # noqa: BLE001
                        previews = _PREVIEW_FOR.get(url) or []
                        preview = previews[min(turn, len(previews) - 1)] if previews else None
                        if not preview:
                            raise
                        log.info("Note image: the full size couldn't be fetched (%s); its preview is used",
                                 brief(exc))
                        path = raw.download_asset_to(preview, directory)
                    ident = _file_identity(Path(path))
                    if ident:
                        made.add(ident)
                        try:
                            dirs.add(Path(directory).resolve())
                        except OSError:
                            pass
                    return path

            exporter = NoteExporter(Recording(), config=ExportConfig(assets_dir=str(assets_dir), full_page=True))
            _SKETCH_FALLBACKS.clear()
            _SKETCH_INFO.clear()
            _HEIC_FULL.clear()
            _ATTACHMENT_KEYS.clear()
            _ALIASES.clear()
            _PREVIEW_FOR.clear()
            if not filename.startswith(STAGE_PREFIX):
                _CURRENT_NOTE[0] = str(out_dir.name + "/" + filename)
            if not exporter.export(record, output_dir=str(out_dir), filename=filename):
                raise SyncError(t("Note has no content"))
            page = out_dir / (filename if filename.lower().endswith(".html") else filename + ".html")
            fetch = lambda url, folder: raw.download_asset_to(url, str(folder))  # noqa: E731
            if _SKETCH_FALLBACKS and page.exists():
                upgrade_sketches(page, dict(_SKETCH_FALLBACKS), fetch, made=made, dirs=dirs)
            if page.exists() and "public.hei" in page.read_text(encoding="utf-8", errors="replace"):
                upgrade_heic(page, dict(_HEIC_FULL), fetch, made=made, dirs=dirs)
            if page.exists():
                browser_images(page, made=made, dirs=dirs)
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

                        idx = [i for i in range(len(tokens)) if asset_url(tokens[i])]  # only ones that can be fetched
                        if isinstance(apps, (list, tuple)) and len(apps) == len(tokens):
                            same = [i for i in idx if str(apps[i]) == str(pref_code)]
                            idx = same or idx
                        if idx:
                            return asset_url(tokens[max(idx, key=lambda i: size(tokens[i]))])
                    except Exception:  # noqa: BLE001 - fall back to the library's choice
                        pass
                    return _orig(tokens, apps, pref_code, asset_url)  # (a static method: no self)

                cls._match_preview_appearance = largest
    except Exception as exc:  # noqa: BLE001
        log.info("Could not tune note previews: %s", exc)
    _prefer_full_images()
    _prefer_vector_sketches()
    _better_attachment_choices()


# Photos the browser can show as they are; for these the full image beats any preview.
FULL_IMAGE_UTIS = {"public.jpeg", "public.jpg", "public.png", "public.gif", "public.webp", "public.bmp"}
HEIC_UTIS = {"public.heic", "public.heif"}  # iPhone photos: shown as a JPEG made from the original


def _prefer_full_images() -> None:
    """pyicloud only swaps a photo's preview for the full image when the preview it chose is the
    very first one; with the sharpest preview chosen instead it kept a ~384 px thumbnail. Always use
    the full image for photos a browser can display (HEIC can't be shown, so its preview stays)."""
    try:
        from pyicloud.services.notes.rendering import exporter as ex

        original = ex._wire_media_to_parent

        def wire(ds, media_map, mrec, url, config, _orig=original):
            parents = getattr(media_map, "parents", {}).get(mrec.recordName) or [media_map.get(mrec.recordName)]
            for parent in [p for p in parents if p]:  # a media record two attachments share: both get it
                try:
                    before = ds.get_primary_asset_url(parent)
                except Exception:  # noqa: BLE001
                    before = None
                _orig(ds, {mrec.recordName: parent}, mrec, url, config)
                try:
                    uti = (ds.get_attachment_uti(parent) or "").lower()
                except Exception:  # noqa: BLE001
                    uti = ""
                if url and uti in FULL_IMAGE_UTIS:
                    if before and before != url:
                        _PREVIEW_FOR.setdefault(url, []).append(before)  # if the full image can't be fetched
                    ds.set_primary_asset_url(parent, url)
                elif url and uti in HEIC_UTIS:  # a browser can't show it: converted after the export
                    if before:
                        ds.set_primary_asset_url(parent, before)  # the preview stays what the page shows
                    for key in _ATTACHMENT_KEYS.get(parent, [parent]):
                        _HEIC_FULL[key] = url

        ex._wire_media_to_parent = wire

        original_media_url = ex._media_field_url

        def media_url(mrec, _orig=original_media_url):
            """The media record's full image first (not whichever asset field comes first)."""
            try:
                found = {}
                for k in mrec.fields:
                    u = getattr(getattr(mrec.fields.get_field(k), "value", None), "downloadURL", None)
                    if isinstance(k, str) and isinstance(u, str) and u:
                        found[k] = u
                for name in ("Image", "FullImage", "Original", "PrimaryAsset", "Asset", "Media"):
                    if name in found:
                        return found[name]
                for k, u in found.items():
                    if not any(w in k.lower() for w in ("thumb", "preview", "small")):
                        return u
            except Exception:  # noqa: BLE001
                pass
            return _orig(mrec)

        ex._media_field_url = media_url

        original_hydrate = ex._hydrate_attachment_records

        def hydrate(ds, resp, config, _orig=original_hydrate):
            media_map = _MediaMap(_orig(ds, resp, config))
            for rec in getattr(resp, "records", None) or []:
                try:
                    rn = getattr(getattr(rec.fields.get_field("Media"), "value", None), "recordName", None)
                except Exception:  # noqa: BLE001
                    rn = None
                if rn and rec.recordName not in media_map.parents.setdefault(rn, []):
                    media_map.parents[rn].append(rec.recordName)
            return media_map

        ex._hydrate_attachment_records = hydrate
    except Exception as exc:  # noqa: BLE001
        log.info("Could not prefer full note images: %s", exc)


class _MediaMap(dict):
    """media record -> its (first) attachment, as pyicloud keeps it; .parents: all attachments using it."""

    def __init__(self, *a) -> None:
        super().__init__(*a)
        self.parents: dict[str, list[str]] = {}


SCAN_UTIS = {"com.apple.paper.doc.scan", "com.apple.notes.gallery"}  # scanned documents: their full PDF
LEGACY_DRAWING_UTIS = {"com.apple.drawing", "com.apple.drawing.2", "com.apple.notes.sketch"}


def _better_attachment_choices() -> None:
    """Choices pyicloud leaves to chance, made on what the attachment record actually holds:
    previews by their real size in pixels (not bytes), a full image kept on the attachment itself,
    a scanned document's full PDF, an older drawing's full bitmap - and an attachment known under two
    names (record and inline id) gets the same file under both."""
    try:
        from pyicloud.services.notes.rendering import attachments as at
        from pyicloud.services.notes.rendering import ck_datasource as cds

        cls = cds.CloudKitNoteDataSource
        original_add, original_set = cls.add_attachment_record, cls.set_primary_asset_url

        def set_primary(self, identifier, url, _orig=original_set):
            for key in _ALIASES.get(identifier, [identifier]):  # every name of the attachment
                _orig(self, key, url)

        def url_of(v):
            u = v.get("downloadURL") if isinstance(v, dict) else getattr(v, "downloadURL", None)
            return u if isinstance(u, str) and u else None

        def add(self, rec, _orig=original_add):
            _orig(self, rec)
            try:
                fields = rec.fields
                keys = [k for k in (getattr(fields.get_field(n) or (), "value", None)
                                    for n in ("AttachmentIdentifier", "attachmentIdentifier", "Identifier"))
                        if isinstance(k, str) and k][:1]  # the first one only, as pyicloud does
                keys = list(dict.fromkeys(keys + ([rec.recordName] if rec.recordName else [])))
                if not keys:
                    return
                for k in keys:
                    _ALIASES[k] = keys
                uti = (self._uti.get(keys[0]) or "").lower()
                primary = self._primary_asset_url.get(keys[0])

                def value(name):
                    return getattr(fields.get_field(name), "value", None) if fields.get_field(name) else None

                def choose(url, as_uti=None):
                    for k in keys:
                        self._primary_asset_url[k] = url
                        if as_uti:
                            self._uti[k] = as_uti

                tokens = value("PreviewImages")
                tokens = list(tokens) if isinstance(tokens, (list, tuple)) else []
                preview_urls = [url_of(t) for t in tokens]
                from_preview = primary is None or primary in preview_urls
                image_like = uti.startswith("public.image") or uti in FULL_IMAGE_UTIS | HEIC_UTIS | \
                    {"public.tiff", "com.apple.paper"}
                best_preview = next((u for u in reversed(preview_urls) if u), None)
                if uti in SCAN_UTIS:  # a scan: its full PDF, shown and kept like any PDF in a note
                    pdf = url_of(value("FallbackPDF"))
                    if pdf:
                        if best_preview:
                            _PREVIEW_FOR.setdefault(pdf, []).append(best_preview)
                        choose(pdf, "com.apple.paper.doc.pdf")
                    return
                if uti in LEGACY_DRAWING_UTIS:  # an older drawing: its full bitmap
                    full = url_of(value("FallbackImage"))
                    if full:
                        if best_preview:
                            _PREVIEW_FOR.setdefault(full, []).append(best_preview)
                        choose(full, "public.image")
                    return
                if not image_like or not from_preview:
                    return
                # a full image kept on the attachment itself beats any preview
                for name in ("Image", "Media", "FallbackImage"):
                    full = url_of(value(name)) if uti != "com.apple.paper" else None
                    if not full:
                        continue
                    if uti in HEIC_UTIS:  # can't be shown as it is: converted after the export
                        for k in keys:
                            _HEIC_FULL[k] = full
                        break
                    if primary:
                        _PREVIEW_FOR.setdefault(full, []).append(primary)  # if the full image can't be fetched
                    choose(full)
                    return
                # previews: the largest in pixels of the chosen appearance (bytes only break ties)
                widths, heights, apps = value("PreviewWidths"), value("PreviewHeights"), value("PreviewAppearances")
                n = len(tokens)
                if n < 2 or not isinstance(widths, (list, tuple)) or not isinstance(heights, (list, tuple)) \
                        or len(widths) != n or len(heights) != n:
                    return
                idx = [i for i in range(n) if preview_urls[i]]
                if isinstance(apps, (list, tuple)) and len(apps) == n and primary in preview_urls:
                    shown = apps[preview_urls.index(primary)]
                    idx = [i for i in idx if apps[i] == shown] or idx

                def rank(i):
                    tok = tokens[i]
                    nbytes = tok.get("size") if isinstance(tok, dict) else getattr(tok, "size", 0)
                    try:
                        return (int(widths[i]) * int(heights[i]), int(nbytes or 0))
                    except (TypeError, ValueError):
                        return (0, 0)

                if idx:
                    choose(preview_urls[max(idx, key=rank)])
            except Exception as exc:  # noqa: BLE001 - pyicloud's own choice stays
                log.debug("Attachment choice not improved: %s", exc)

        cls.add_attachment_record = add
        cls.set_primary_asset_url = set_primary
        for u in SCAN_UTIS:  # shown as PDFs (pages as images, the original linked)
            at._EXACT.setdefault(u, at._EXACT["com.apple.paper.doc.pdf"])
        for u in LEGACY_DRAWING_UTIS:  # shown as images
            at._EXACT.setdefault(u, at._EXACT["com.apple.paper"])
    except Exception as exc:  # noqa: BLE001
        log.info("Could not improve note attachment choices: %s", exc)


_IMAGE_FORMATS = ("png", "jpeg", "jpg", "heic", "heif", "gif", "tiff", "bmp", "webp", "pdf", "svg")
_SKETCH_FIELDS = ("FallbackImage", "FallbackPDF", "PreviewImages", "PreviewAppearances", "PreviewScales",
                  "PreviewWidths", "PreviewHeights", "PreviewUpdateDate", "PaperBundle", "PaperAssets", "Image",
                  "Media", "SizeHeight", "SizeWidth", "Orientation")


# Pen drawings (com.apple.paper): attachment id -> download URL of the full "fallback" bitmap.
# Filled while a note is read; used right after its export (see ICloudNotes.export).
class _PerThread(MutableMapping):
    """A dict of its own for each thread: what one export collects is never seen by another."""

    def __init__(self) -> None:
        self._local = threading.local()

    def _d(self) -> dict:
        d = getattr(self._local, "d", None)
        if d is None:
            d = self._local.d = {}
        return d

    def __getitem__(self, k):
        return self._d()[k]

    def __setitem__(self, k, v):
        self._d()[k] = v

    def __delitem__(self, k):
        del self._d()[k]

    def __iter__(self):
        return iter(list(self._d()))

    def __len__(self):
        return len(self._d())


_SKETCH_FALLBACKS: MutableMapping = _PerThread()
_HEIC_FULL: MutableMapping = _PerThread()  # attachment id -> download URL of the full HEIC original
_ATTACHMENT_KEYS: MutableMapping = _PerThread()  # attachment record name -> every id the page may use
_ALIASES: MutableMapping = _PerThread()  # any name of an attachment -> all its names
_PREVIEW_FOR: MutableMapping = _PerThread()  # full image URL -> each attachment's own preview, in order
_SKETCH_INFO: MutableMapping = _PerThread()
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
                if str(uti).lower() in HEIC_UTIS:  # the names the page may use for this photo
                    _ATTACHMENT_KEYS[rec.recordName] = [rec.recordName] + [
                        k for k in (getattr(fields.get_field(n) or (), "value", None)
                                    for n in ("AttachmentIdentifier", "attachmentIdentifier", "Identifier"))
                        if isinstance(k, str) and k]
                if str(uti).lower() != "com.apple.paper":
                    return
                try:  # for the log when a drawing stays small: which images does Apple keep for it?
                    def is_asset(x):
                        return hasattr(x, "downloadURL") or (isinstance(x, dict) and "downloadURL" in x)

                    def nbytes(x):
                        n = x.get("size") if isinstance(x, dict) else getattr(x, "size", None)
                        return str(n) if isinstance(n, int) and not isinstance(n, bool) else "?"

                    parts = []
                    for k in fields:
                        v = getattr(fields.get_field(k), "value", None)
                        # field names and values come from Apple: known names and numbers only
                        name = k if isinstance(k, str) and k in _SKETCH_FIELDS else "other"
                        if isinstance(v, (list, tuple)) and v and is_asset(v[0]):
                            parts.append(f"{name}[{','.join(nbytes(x) for x in v[:20])} B]")
                        elif is_asset(v):
                            parts.append(f"{name}[{nbytes(v)} B]")
                        elif isinstance(k, str) and k.startswith(("Fallback", "Preview", "Paper", "Image", "Size",
                                                                   "Orientation")):
                            parts.append(f"{name}={value(v)}")
                    _SKETCH_INFO[rec.recordName] = "; ".join(parts[:30])
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
        target = None if img.isNull() else _save_image_new(img, pdf.with_name(f"{pdf.name}.p{i + 1}.png"))
        if target is None:
            continue
        out.append(target)
    doc.close()
    return out


def _place_new_name(tmp: Path | str, target: Path) -> Path | None:
    """Put a file of ours in place under target, or under a fresh variant of that name when it is
    taken: an existing file is never replaced. None if neither name was free."""
    for cand in (target, target.with_name(f"{target.stem}-{uuid.uuid4().hex[:8]}{target.suffix}")):
        if place_new(tmp, cand):
            return cand
    return None


def _save_image_new(img, target: Path) -> Path | None:
    """Save an image as a new file at target (or a variant of the name); never over an existing one."""
    fd, tmp = tempfile.mkstemp(prefix=STAGE_PREFIX, suffix=".png", dir=target.parent)
    os.close(fd)
    try:
        if not img.save(tmp, "PNG"):
            return None
        return _place_new_name(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)  # only our private copy (gone once it was put in place)


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


def _file_identity(p: Path) -> tuple[int, int] | None:
    """(volume, file id) of an ordinary file; None when unknown (no id, not a file, can't be read)."""
    try:
        st = os.lstat(p)
    except OSError:
        return None
    return (st.st_dev, st.st_ino) if stat.S_ISREG(st.st_mode) and st.st_ino else None


_HEIC_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationCore
$in = [IO.File]::OpenRead($env:SKYFOLD_HEIC_IN)
try {
  $dec = [Windows.Media.Imaging.BitmapDecoder]::Create($in, [Windows.Media.Imaging.BitmapCreateOptions]::None, [Windows.Media.Imaging.BitmapCacheOption]::OnLoad)
} finally { $in.Close() }
$rgb = New-Object Windows.Media.Imaging.FormatConvertedBitmap($dec.Frames[0], [Windows.Media.PixelFormats]::Bgr24, $null, 0)
$enc = New-Object Windows.Media.Imaging.JpegBitmapEncoder
$enc.QualityLevel = 92
$enc.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($rgb))
$out = [IO.File]::Open($env:SKYFOLD_HEIC_OUT, [IO.FileMode]::Truncate, [IO.FileAccess]::Write)
try { $enc.Save($out) } finally { $out.Close() }
"""


def heic_to_jpeg(src: Path, folder: Path) -> Path | None:
    """A full-size JPEG of a HEIC photo, made by Windows' own HEIF decoder (the "HEIF Image
    Extensions" most PCs have). Written only as a new file in `folder`. None when it can't be made
    (no decoder, damaged photo, not Windows): then the small preview simply stays."""
    import subprocess

    if os.name != "nt":
        return None
    fd, tmp = tempfile.mkstemp(prefix=STAGE_PREFIX, suffix=".jpg", dir=folder)
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-Command", _HEIC_SCRIPT],
                           env={**os.environ, "SKYFOLD_HEIC_IN": str(src), "SKYFOLD_HEIC_OUT": str(tmp_path)},
                           capture_output=True, timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        from PySide6.QtGui import QImageReader

        reader = QImageReader(str(tmp_path))
        ok = r.returncode == 0 and reader.canRead() and reader.size().width() > 0
        reader.setFileName("")
        if not ok:
            log.info("HEIC photo not converted (exit %s)", r.returncode)
            tmp_path.unlink(missing_ok=True)
            return None
        return _place_new_name(tmp_path, folder / (src.stem + ".jpg")) or None
    except Exception as exc:  # noqa: BLE001 - the preview stays
        log.info("HEIC photo not converted: %s", exc)
        tmp_path.unlink(missing_ok=True)
        return None


def upgrade_heic(page: Path, fulls: dict[str, str], download, made: set | None = None,
                 dirs: set | None = None, convert=None) -> int:
    """Show iPhone (HEIC) photos at full size: the original is downloaded and turned into a JPEG,
    which replaces the small preview in the page. On any problem the preview stays. Files are only
    ever written as new ones; the downloaded original and a preview this export made are removed."""
    import urllib.parse

    convert = convert or heic_to_jpeg
    note_dir = page.parent
    text = page.read_text(encoding="utf-8", errors="replace")
    count = 0
    owned_dirs = {Path(d).resolve() for d in (dirs or ())}

    def ours(p: Path) -> bool:
        try:
            return bool(made) and p.parent.resolve() in owned_dirs and _file_identity(p) in made
        except OSError:
            return False

    def swap(m: re.Match) -> str:
        nonlocal count
        tag, src = m.group(0), m.group(1)
        uti = re.search(r'\sdata-uti="([^"]+)"', tag)
        if not uti or uti.group(1).lower() not in HEIC_UTIS or src.startswith(("http:", "https:", "data:")):
            return tag
        current = (note_dir / urllib.parse.unquote(src).replace("\\", "/")).resolve()
        if ASSETS not in current.parts:
            return tag
        original: Path | None = None
        fetched = False
        try:
            if current.suffix.lower() in (".heic", ".heif"):
                original = current  # the original itself is in the page (a browser can't show it)
            else:
                ident = re.search(r'\sdata-id="([^"]+)"', tag)
                url = fulls.get(ident.group(1)) if ident else None
                if not url:
                    return tag
                original = Path(download(url, current.parent))
                fetched = True
            jpg = convert(original, current.parent)
        except Exception as exc:  # noqa: BLE001 - keep the preview
            log.info("Full photo not used: %s", exc)
            jpg = None
        if fetched and original is not None:
            original.unlink(missing_ok=True)  # downloaded just now, only to be converted
        if jpg is None:
            return tag
        count += 1
        if current.suffix.lower() not in (".heic", ".heif") and ours(current):
            try:
                current.unlink()  # the small preview this export downloaded for this page
            except OSError:
                pass
        rel = urllib.parse.quote(os.path.relpath(jpg, note_dir).replace("\\", "/"))
        return tag.replace(f'src="{src}"', f'src="{rel}"', 1)

    text = IMG_TAG.sub(swap, text)
    if count:
        page.write_text(text, encoding="utf-8")
    return count


def upgrade_sketches(page: Path, fallbacks: dict[str, str], download, made: set | None = None,
                     dirs: set | None = None) -> int:
    """Swap each pen drawing's small preview for its full-size bitmap, when that one is bigger and a
    browser can show it. download(url, folder) saves the file and returns its path. Returns the
    number of drawings upgraded; on any problem the preview simply stays. The replaced preview is
    removed only when this very export downloaded it (its file identity is in `made`) into one of
    the folders it downloaded into (`dirs`: the note's own attachment folder); any other file stays."""
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
            preview = QImageReader(str(current)).size()
            preview_w = preview.width()
            # The preview is a small square of the canvas; the full bitmap is the whole drawing, often
            # narrow and tall, or wide and low. It wins when its longer side is longer (or, as long,
            # when it has more pixels); a browser must be able to show it.
            full_side, prev_side = max(size.width(), size.height()), max(preview.width(), preview.height(), 0)
            more = full_side > prev_side or (full_side == prev_side and size.width() * size.height()
                                             > max(preview.width(), 0) * max(preview.height(), 0))
            if ok and more and fmt.lower() not in ("png", "jpeg", "jpg", "gif", "webp"):
                from PySide6.QtGui import QImage

                img = QImage(str(new))  # e.g. TIFF: a browser can't show it; PNG keeps it lossless
                converted = _save_image_new(img, new.with_name(new.name + ".png")) if not img.isNull() else None
                if converted is not None:
                    new.unlink(missing_ok=True)
                    new, fmt = converted, "png"
            if not ok or not more or fmt.lower() not in ("png", "jpeg", "jpg", "gif", "webp"):
                log.info("Drawing in %s: full image not used (readable=%s, %sx%s, format=%s, %d bytes; "
                         "preview %s px)", page.name, ok, size.width(), size.height(), value(fmt, _IMAGE_FORMATS), new.stat().st_size,
                         preview_w)
                new.unlink(missing_ok=True)
                return tag
            ext = ".jpg" if fmt == "jpeg" else "." + fmt
            if new.suffix.lower() != ext:
                renamed = _place_new_name(new, new.with_name(new.name + ext))  # never over another file
                if renamed is None:
                    raise OSError(f"no free name for {new.name}{ext}")
                new = renamed
        except Exception as exc:  # noqa: BLE001 - keep the preview
            log.info("Full drawing not used: %s", exc)
            if new is not None:
                new.unlink(missing_ok=True)
            return tag
        count += 1
        try:
            ours = bool(made) and bool(dirs) and current.parent in {Path(d).resolve() for d in dirs} \
                and _file_identity(current) in made
        except OSError:
            ours = False
        if ours:  # downloaded by this export, for this page: no other page or file uses it
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


def _check_assets_dir(assets: Path) -> None:
    """The note's attachment folder (and _assets above it) must be what it says: no link or
    junction leading somewhere else, so nothing is ever written outside the Notes folder."""
    for d in (assets.parent, assets):
        if not os.path.lexists(d):
            continue
        real = os.path.normcase(os.path.realpath(d))
        if real != os.path.normcase(os.path.abspath(d)) or os.path.islink(d) or _is_junction(d):
            raise SyncError(t("The attachment folder {name} leads outside the Notes folder; nothing is written "
                              "there.", name=str(d)))


def _is_junction(p: Path) -> bool:
    try:
        return bool(os.lstat(p).st_file_attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except (OSError, AttributeError):
        return False


BROWSER_IMAGES = ("png", "jpeg", "jpg", "gif", "webp", "bmp", "svg")


def browser_images(page: Path, made: set | None = None, dirs: set | None = None) -> int:
    """An attachment image a browser can't show (TIFF) is converted to PNG, losslessly; the page then
    shows the PNG. The original is removed only when this export downloaded it (as for drawings).
    When it can't be converted, the page stays as it is."""
    import urllib.parse

    from PySide6.QtGui import QImage, QImageReader

    note_dir = page.parent
    text = page.read_text(encoding="utf-8", errors="replace")
    count = 0

    def swap(m: re.Match) -> str:
        nonlocal count
        tag, src = m.group(0), m.group(1)
        if src.startswith(("http:", "https:", "data:")):
            return tag
        f = (note_dir / urllib.parse.unquote(src).replace("\\", "/")).resolve()
        if ASSETS not in f.parts or not f.is_file():
            return tag
        reader = QImageReader(str(f))
        fmt = bytes(reader.format().data()).decode().lower()
        reader.setFileName("")
        if not fmt or fmt in BROWSER_IMAGES:
            return tag
        img = QImage(str(f))
        new = _save_image_new(img, f.with_name(f.name + ".png")) if not img.isNull() else None
        if new is None:
            log.info("Note image in %s: format %s can't be shown by a browser and couldn't be converted",
                     page.name, value(fmt, _IMAGE_FORMATS))
            return tag
        count += 1
        try:
            if made and dirs and f.parent in {Path(d).resolve() for d in dirs} and _file_identity(f) in made:
                f.unlink()
        except OSError:
            pass
        rel = urllib.parse.quote(os.path.relpath(new, note_dir).replace("\\", "/"))
        return tag.replace(f'src="{src}"', f'src="{rel}"', 1)

    def unembed(m: re.Match) -> str:
        """An embedded "PDF" that is an image (a scan whose full PDF wasn't available: its preview)."""
        nonlocal count
        src = m.group(1)
        if src.lower().endswith(".pdf") or src.startswith(("http:", "https:", "data:")):
            return m.group(0)
        f = (note_dir / urllib.parse.unquote(src).replace(chr(92), "/")).resolve()
        if ASSETS not in f.parts or not f.is_file() or not QImageReader(str(f)).canRead():
            return m.group(0)
        count += 1
        return f'<img src="{src}" alt="">'

    text = OBJECT_TAG.sub(unembed, text)
    text = IMG_TAG.sub(swap, text)
    if count:
        page.write_text(text, encoding="utf-8")
    return count


OBJECT_TAG = re.compile(r'<object\b[^>]*?\sdata="([^"#]+)"[^>]*>.*?</object>', re.IGNORECASE | re.DOTALL)


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
            from PySide6.QtGui import QImageReader

            if QImageReader(str(pdf)).canRead():  # e.g. a scan's preview, its full PDF wasn't available
                return f'<img src="{src}" alt="">'
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

    def items(self, prefix: str) -> list[tuple[str, str]]:
        """(key, value) of every entry whose key starts with prefix and that has a value."""
        with self._lock:
            rows = self._db.execute("SELECT key, value FROM notes_meta WHERE substr(key, 1, ?)=? AND value IS NOT NULL",
                                    (len(prefix), prefix)).fetchall()
        return [(r[0], r[1]) for r in rows]

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


def _legacy_index_of(p: Path, rows: list) -> bool:
    """An index written by Skyfold 1.0.5 or earlier (its older format, without the owner mark that
    1.0.6 writes) that lists at least one note page of this setup: those versions kept no record."""
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return False
    if INDEX_MARK in text[:4096] or not ("const KEY='skyfold-collapsed'" in text and "skyfold-theme" in text):
        return False
    return any(r[1] and json.dumps(r[1], ensure_ascii=False) in text for r in rows)


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


def _is_our_page(p: Path, strict: bool = False) -> bool:
    """Only pages Skyfold wrote (they carry its style block) are ever removed: an .html file the
    user keeps in the same folder is left alone. A page that can't be read is not ours (strict:
    the error is raised instead, as "can't tell" is not "someone else's")."""
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            return 'id="skyfold"' in fh.read(200_000)
    except OSError:
        if strict:
            raise
        return False


class NotesSyncer:
    MAX_PER_RUN = 15  # exports take ~10 s each; keep runs short so photo syncing isn't held up

    def __init__(self, state: NotesState, remote: NotesRemote, root: Path, cb: Callbacks | None = None) -> None:
        self.state = state
        self.remote = remote
        self.root = root
        self.cb = cb or Callbacks()
        self.cancel_flag = threading.Event()  # this pass (cleared when the next one starts)
        self.closing = threading.Event()  # the app is quitting: never cleared

    def _check_root(self) -> None:
        has = self.state.count() > 0
        if not self.root.exists():
            if has:
                raise SyncError(t("Notes folder is not available: {path}", path=self.root))
            self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / MARKER
        if not marker_present(marker):
            if has:
                raise SyncError(t("The Notes folder doesn't look like before (its sync marker is missing). "
                                  "Syncing has stopped so nothing gets deleted by mistake."))
            create_marker(marker, t("This folder is synced by Skyfold. Don't delete this file.\n"))

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

    def _own(self, p: Path, kind: str) -> None:
        """Write down a private file before it is created: only files on this list are ever tidied up."""
        self.state.set("tmp:" + p.relative_to(self.root).as_posix(), kind)

    def _disown(self, p: Path) -> None:
        try:
            self.state.set("tmp:" + p.relative_to(self.root).as_posix(), None)
        except ValueError:
            pass

    def _tidy_leftovers(self) -> None:
        """Private files a crash left behind - only the ones Skyfold wrote down when it made them:
        unfinished exports are removed, a moved-aside page goes to the Recycle Bin, and a moved-aside
        index comes back if Notes.html is missing. A file that merely has such a name is someone's."""
        for key, kind in self.state.items("tmp:"):
            p = self.root / key[len("tmp:"):]
            if known_missing(p):
                self.state.set(key, None)
                continue
            try:
                st = os.lstat(p)
            except OSError:
                continue  # couldn't be looked at: stays written down, tried again next time
            try:
                if stat.S_ISREG(st.st_mode) and not _is_link(p) and inside_root(self.root, p):
                    if kind == "index" and not (self.root / INDEX).exists():
                        os.rename(p, self.root / INDEX)
                    elif kind in ("old", "index"):
                        self._trash(p)
                    else:
                        p.unlink()
            except OSError as exc:
                log.info("Notes: couldn't tidy %s: %s", p.name, exc)
                continue  # stays written down: tried again next time
            self.state.set(key, None)

    def _refresh_for_quality(self) -> None:
        """Once per QUALITY: notes whose page still shows a photo (also iPhone HEIC) or a pen drawing as
        a small preview, or holds a scanned document or an older drawing, are exported again (sharper
        images, full scans); all others are left as they are."""
        import urllib.parse

        from PySide6.QtGui import QImageReader

        def small(page: Path, tag: str) -> bool:
            src = re.search(r'\ssrc="([^"]+)"', tag)
            if not src or src.group(1).startswith(("http:", "https:", "data:")):
                return False
            f = page.parent / urllib.parse.unquote(src.group(1)).replace("\\", "/")
            if 'data-uti="public.hei' in tag:
                return f.suffix.lower() != ".jpg"  # not yet the full-size JPEG
            size = QImageReader(str(f)).size()
            return max(size.width(), size.height()) <= 768  # still the square preview

        for nid, row in self.state.all().items():
            rel = row[1]
            if not rel:
                continue
            page = self.root / rel
            try:
                text = page.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            tags = [m.group(0) for m in IMG_TAG.finditer(text)
                    if 'data-uti="public.' in m.group(0) or 'data-uti="com.apple.paper"' in m.group(0)]
            whole = any(f'data-uti="{u}"' in text for u in SCAN_UTIS | LEGACY_DRAWING_UTIS)
            if whole or any(small(page, tag) for tag in tags):
                self.state.put(nid, row[1], row[2], row[3], None, bool(row[5]))
        self.state.set("cursor", None)
        self.state.set("quality", QUALITY)

    def run(self, full: bool = False) -> NotesResult:
        self.cancel_flag.clear()
        if self.closing.is_set():
            raise Cancelled()  # quitting: this pass never starts
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
            elif self.state.get("quality") != QUALITY:
                self._refresh_for_quality()
                return self.run(full=True)
            elif self.state.get("legacy_index") != "1":
                # once: a Notes.html from Skyfold 1.0.5 or earlier is brought up to date right away,
                # not only after the next change in Notes
                self.state.set("legacy_index", "1")
                if not (self.state.get("index_owned") or self.state.get("index_digest")):
                    self.write_index()
            elif self.state.get("index_layout") != INDEX_LAYOUT and self.state.get("index_owned") == "1":
                self.write_index()  # once after an update with a new Notes.html look (from the database)
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
            if self.cancel_flag.is_set() or self.closing.is_set():
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
                    self._own(self.root / staged_rel, "stage")
                    self.remote.export(n.id, out_dir, PurePosixPath(staged_rel).name, self.root / ASSETS)
                    if _MISSING_ATTACHMENT.search((self.root / staged_rel).read_text(encoding="utf-8",
                                                                                     errors="replace")):
                        raise SyncError(t("an attachment couldn't be downloaded; tried again next time"))
                    self._polish(staged_rel, n)
                    if n.modified:
                        ts = n.modified.timestamp()
                        os.utime(self.root / staged_rel, (ts, ts))
                    if old and old[1] and old[1].lower() != rel.lower():
                        # the note moves to another name: its old page is written down (with its
                        # fingerprint) until it has really been removed
                        self.state.set(f"obsolete:{old[1]}", self.state.get(f"page:{n.id}") or "")
                    self._publish_page(self.root / staged_rel, self.root / rel, n.id, old, n.modified)
                finally:
                    (self.root / staged_rel).unlink(missing_ok=True)
                    self._disown(self.root / staged_rel)
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
            if old and old[1] and old[1].lower() != rel.lower():
                if known_missing(self.root / old[1]) or self._remove(old, res, count=False):
                    self.state.set(f"obsolete:{old[1]}", None)  # renamed or moved: the old file is gone
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

    def _remove_leftover_pages(self, res: NotesResult) -> None:
        """A page Skyfold meant to remove when its note got another name or folder, and couldn't (the
        file was in use, the app was closed in between). Each such path is written down with the
        fingerprint of the page; here it goes to the Recycle Bin if it is still exactly that page and
        no note uses the name. Nothing is ever recognised by its name or its look: a file that isn't
        on that list - any copy the user made - is theirs."""
        tracked = {r[1].lower() for r in self.state.all().values() if r[1]}
        for key, recorded in self.state.items("obsolete:"):
            rel = key[len("obsolete:"):]
            p = self.root / rel
            if not known_missing(p):
                try:
                    st = os.lstat(p)
                except OSError:
                    continue  # couldn't be looked at: stays written down, tried again next time
                if not stat.S_ISREG(st.st_mode):
                    self.state.set(key, None)  # a folder or link there now: not the page written down
                    continue
            try:
                if rel.lower() not in tracked and recorded and not known_missing(p) and not _is_link(p)                         and inside_root(self.root, p) and _digest(p) == recorded:
                    self._trash(p)
                    res.removed += 1
                    self.cb.activity("trash", t("Notes: removed {name}", name=rel))
            except OSError as exc:
                log.warning("Could not remove leftover note page %s: %s", p, exc)
                continue  # stays written down: tried again next time
            self.state.set(key, None)

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
            self._own(aside, "old")
            os.rename(target, aside)
            if not _is_our_page(aside):  # someone's own file appeared there: give it back untouched
                os.rename(aside, target)
                self._disown(aside)
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
            if known_missing(aside):  # still there (the Recycle Bin refused it): tried again later
                self._disown(aside)
        self.state.set(f"page:{nid}", digest)

    def _let_go(self, aside: Path, target: Path, nid: str, old: tuple | None, modified=None) -> None:
        """Our previous page: deleted when unchanged since Skyfold wrote it, else to the Recycle Bin."""
        recorded = self.state.get(f"page:{nid}")
        try:
            # Only a page that is provably Skyfold's is deleted: it has the recorded fingerprint, or it is
            # byte for byte the page just written in its place. Otherwise (e.g. a page from before
            # 1.0.6 without a fingerprint) nothing proves that: it goes to the Recycle Bin.
            had = _digest(aside)
            unchanged = (bool(recorded) and had == recorded) or (target.is_file() and had == _digest(target))
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
        """Where this note's page goes. Its own tracked page is reused; any other name is free only
        when no note has it AND nothing exists there. A file Skyfold doesn't track - whatever it looks
        like, also a copy of one of its own pages - is someone's file and is never written over."""
        base = f"{folder}/{_safe(n.title, t('Untitled'))}"
        own = old[1].lower() if old and old[1] else None

        def free(cand: str) -> bool:
            here = self.root / cand
            if own is not None and cand.lower() == own:
                return not here.exists() or _is_our_page(here)  # this note's page (replaced by the user: not it)
            return cand.lower() not in taken and not os.path.lexists(here)

        cand = f"{base}.html"
        k = 2
        while not free(cand):
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
            if not known_missing(p):
                os.lstat(p)  # can't be looked at: raises, the note is remembered and tried again
                if p.suffix.lower() == ".html" and not _is_our_page(p, strict=True):  # unreadable: tried again
                    log.info("Notes: %s isn't a page Skyfold wrote; left alone", row[1])
                    return True  # someone else's file now: forget the note's old path, keep the file
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
        page = render_index(data, t("Updated {when}", when=local_datetime(datetime.now())))
        target = self.root / INDEX
        not_ours = SyncError(t("The Notes folder already has a Notes.html that isn't from Skyfold. Choose an empty "
                               "folder for Notes in Settings."))
        present = not known_missing(target)  # one that can't be looked at counts as there
        if present and not _is_our_index(target):
            raise not_ours
        claim = self.state.get("index_owned") or ""
        if claim.startswith("pending:"):  # the first index was being put in place when Skyfold stopped
            try:
                landed = present and _digest(target) == claim[len("pending:"):]
            except OSError:
                return  # can't be read now: decided next time
            if landed:  # exactly what Skyfold wrote: it is ours
                self.state.set("index_digest", claim[len("pending:"):])
                self.state.set("index_owned", "1")
            else:
                self.state.set("index_owned", None)  # it never got there: no claim on that name
        was_owned = bool(self.state.get("index_owned") or self.state.get("index_digest"))
        if present and not was_owned and _legacy_index_of(target, rows):
            # Written by Skyfold 1.0.5 or earlier, which kept no record of it: an index in that older
            # format that lists this setup's own note pages. Taken back; the old page goes to the
            # Recycle Bin when the new one is put in place (nothing is lost).
            log.info("Notes: taking back the Notes.html written by an earlier Skyfold")
            self.state.set("index_owned", "1")
            was_owned = True
        if present and not was_owned:
            # It looks like a Skyfold index, but this setup has no record of writing one here (a copy
            # brought into the folder, or the database is new). Looking like one isn't owning it.
            if not getattr(self, "_index_told", False):
                self._index_told = True
                self.cb.activity("info", t("Notes.html in the Notes folder wasn't written by this Skyfold; it is left "
                                           "as it is. Move or delete it to get a new index."))
            return
        fd, tmp = tempfile.mkstemp(prefix=".skyfold-index-", suffix=".tmp", dir=self.root)
        self._own(Path(tmp), "indextmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(page)
        written = _digest(Path(tmp))  # what Skyfold wrote, known before the file is public
        if not was_owned:  # the first one: claimed only if exactly these bytes are found there later
            self.state.set("index_owned", f"pending:{written}")
        aside = None
        try:
            if target.exists():  # moved aside and checked again: never overwrite what was saved meanwhile
                aside = target.with_name(f"{STAGE_PREFIX}index-{uuid.uuid4().hex}.html")
                self._own(aside, "index")
                os.rename(target, aside)
                if not _is_our_index(aside):
                    os.rename(aside, target)
                    self._disown(aside)
                    aside = None
                    raise not_ours
            os.rename(tmp, target)  # refuses if a file was saved there meanwhile
        except BaseException:
            if not was_owned and aside is None:
                self.state.set("index_owned", None)  # nothing was put in place: no claim on that name
            if aside is not None:
                try:
                    os.rename(aside, target)
                except OSError:
                    self._trash(aside)  # ours, but recoverable all the same
            raise
        finally:
            Path(tmp).unlink(missing_ok=True)
            self._disown(Path(tmp))
        if aside is not None:
            recorded = self.state.get("index_digest")
            if recorded and _digest(aside) == recorded:
                aside.unlink(missing_ok=True)  # exactly what Skyfold wrote
            else:  # edited, or unknown (no fingerprint saved, e.g. after a crash): recoverable
                try:
                    self._trash(aside)
                except OSError as exc:  # stays written down: tried again next time
                    log.warning("Notes: couldn't move the previous index to the Recycle Bin: %s", exc)
            if known_missing(aside):
                self._disown(aside)
        self.state.set("index_digest", written)
        self.state.set("index_owned", "1")
        self.state.set("index_layout", INDEX_LAYOUT)


INDEX_MARK = '<meta name="generator" content="Skyfold notes index">'
INDEX_LAYOUT = "2"  # raised when Notes.html looks different: an existing one is rewritten once
# Layout by Codex (design proposal for fdb8e84), in the app's own palette. Placeholders are @@name@@
# (HTML-escaped words) and @@strings@@ / @@data@@ (JSON); no str.format, so CSS/JS braces stay plain.
INDEX_TEMPLATE = """<!doctype html>
<html lang="@@lang@@"><head><meta charset="utf-8">""" + INDEX_MARK + """<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>@@title@@ · Skyfold</title>
@@theme_script@@
<style>
:root{color-scheme:light;--bg:#fff;--group:#f3f5f8;--hover:#e7ebf0;--border:#dce2e9;--text:#20252b;--muted:#59636f;--accent:#075bcd;--accent-soft:#e7f0ff;--accent-text:#fff;--focus:#075bcd}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;--bg:#191e25;--group:#222932;--hover:#303944;--border:#36404d;--text:#f0f3f7;--muted:#b0bac7;--accent:#99c2ff;--accent-soft:#243b59;--accent-text:#10223b;--focus:#99c2ff}}
:root[data-theme=dark]{color-scheme:dark;--bg:#191e25;--group:#222932;--hover:#303944;--border:#36404d;--text:#f0f3f7;--muted:#b0bac7;--accent:#99c2ff;--accent-soft:#243b59;--accent-text:#10223b;--focus:#99c2ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:13px/1.5 "Segoe UI Variable Text","Segoe UI Variable","Segoe UI",system-ui,sans-serif}
button,input{font:inherit;color:inherit}button{cursor:pointer;border:0}a{color:var(--accent)}
button:focus-visible,a:focus-visible,input:focus-visible{outline:2px solid var(--focus);outline-offset:3px}
::selection{background:var(--accent);color:var(--accent-text)}[hidden]{display:none!important}
.icon{width:18px;height:18px;flex:none;fill:none;stroke:currentColor;stroke-width:1.65;stroke-linecap:round;stroke-linejoin:round}
.sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
.skip{position:fixed;top:-100px;left:16px;padding:12px;background:var(--accent);color:var(--accent-text);z-index:20;border-radius:8px}.skip:focus{top:12px}
.shell{max-width:1200px;margin:auto;padding:0 40px}
.topbar{min-height:110px;display:flex;align-items:center;justify-content:space-between;gap:20px}
.brand{display:flex;align-items:center;gap:12px}.brandmark{display:grid;place-items:center;width:44px;height:44px;border-radius:11px;background:var(--accent-soft);color:var(--accent)}.brandmark svg{width:34px;height:34px}
.brand-name{font-size:17px;font-weight:650;letter-spacing:-.3px}.brand-sub{font-size:11px;color:var(--muted);margin-top:1px}
.top-actions{display:flex;align-items:center;gap:20px}.export-info{color:var(--muted);text-align:right;font-size:11px}.export-info strong{font-weight:500;color:var(--text);display:block;font-size:12px}
.icon-button{display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;border-radius:8px;background:var(--group);color:var(--muted)}.icon-button:hover{background:var(--hover);color:var(--text)}
.workspace{display:grid;grid-template-columns:216px minmax(0,1fr);gap:36px;align-items:start;padding-top:20px}
.sidebar{position:sticky;top:20px}.nav-card{background:var(--group);border-radius:11px;padding:12px 8px 10px}
.eyebrow{color:var(--muted);font-size:11px;font-weight:600;letter-spacing:.65px;text-transform:uppercase}.nav-label{padding:4px 12px 12px}
.nav-item{display:flex;align-items:center;gap:10px;width:100%;min-height:44px;padding:10px 12px;border-radius:8px;background:transparent;text-align:left}.nav-item:hover{background:var(--hover)}
.nav-item[aria-current=page]{background:var(--accent-soft);color:var(--accent);font-weight:600}
.nav-item .num{margin-left:auto;font-size:11px;font-variant-numeric:tabular-nums;color:var(--muted)}.nav-item[aria-current=page] .num{color:var(--accent)}
.nav-item span:not(.num){min-width:0;overflow-wrap:anywhere}.nav-divider{height:12px}
.nav-parent{display:flex;align-items:center}.nav-parent>.nav-item{flex:1;min-width:0}
.disclosure{width:32px;min-height:44px;display:grid;place-items:center;border-radius:8px;background:transparent;color:var(--muted)}.disclosure:hover{background:var(--hover)}.disclosure .icon{width:14px;height:14px}.disclosure[aria-expanded=false] svg{transform:rotate(-90deg)}
.subnav{margin:2px 0 4px 19px;padding-left:9px;border-left:1px solid var(--border)}.subnav .nav-item{font-size:12px;padding-left:10px}.subnav .icon{width:16px;height:16px}
.sidebar-note{display:flex;gap:9px;margin:18px 12px;color:var(--muted);font-size:11px;line-height:1.65}.sidebar-note .icon{width:15px;height:15px;margin-top:2px}.sidebar-note p{margin:0}
.mobile-folders{display:none}.main{min-width:0}
.intro{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;margin-bottom:23px}.intro h1{font-size:22px;line-height:1.25;letter-spacing:-.55px;font-weight:650;margin:2px 0 7px}.intro p{margin:0;color:var(--muted);font-size:13px}
.collection-icon{width:44px;height:44px;display:grid;place-items:center;color:var(--muted);background:var(--group);border-radius:11px}.collection-icon .icon{width:23px;height:23px}
.searchbar{display:flex;align-items:center;gap:10px;background:var(--group);border-radius:8px;padding:0 12px;min-height:46px}.searchbar>.icon{color:var(--muted);width:17px;height:17px}
.searchbar:focus-within{outline:2px solid var(--focus);outline-offset:2px}.searchbar input{min-width:0;flex:1;width:100%;padding:13px 0;border:0;background:transparent;outline:none}
.searchbar input:focus-visible{outline:none}.searchbar input::placeholder{color:var(--muted);opacity:1}.searchbar input::-webkit-search-cancel-button{display:none}
.search-clear{width:30px;height:32px;border-radius:8px;display:grid;place-items:center;background:transparent;color:var(--muted)}.search-clear:hover{background:var(--hover)}.search-clear .icon{width:15px;height:15px}
.key{font-size:11px;color:var(--muted);white-space:nowrap}
.collection-bar{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:24px 0 13px}.collection-title{display:flex;align-items:baseline;flex-wrap:wrap;gap:8px;min-width:0}
.collection-title h2{font-size:13px;font-weight:600;margin:0;overflow-wrap:anywhere}.count{font-size:11px;color:var(--muted)}
.text-button{background:transparent;color:var(--accent);border-radius:8px;padding:9px 10px;font-size:12px;white-space:nowrap;min-height:36px}.text-button:hover{background:var(--accent-soft)}
.groups{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.folder-card{background:var(--group);border-radius:11px;min-width:0;padding:4px 8px 8px;align-self:start}
.folder-toggle{width:100%;display:flex;align-items:center;gap:8px;padding:12px 8px;min-height:48px;background:transparent;border-radius:8px;text-align:left;font-size:13px;font-weight:600}
.folder-toggle>.icon{color:var(--muted);width:16px;height:16px}.folder-toggle .folder-count{font-size:11px;color:var(--muted);font-weight:400;margin-left:auto}
.folder-toggle .chevron{width:13px;height:13px;color:var(--muted)}.folder-toggle[aria-expanded=false] .chevron{transform:rotate(-90deg)}.folder-toggle:hover{background:var(--hover)}
.folder-title{min-width:0;overflow-wrap:anywhere}.note-list{list-style:none;margin:0;padding:0}
.note-row{display:flex;align-items:center;gap:10px;min-height:65px;padding:10px 8px;border-radius:8px;text-decoration:none;color:var(--text)}a.note-row:hover{background:var(--hover)}
.note-glyph{width:30px;height:34px;border-radius:8px;display:grid;place-items:center;color:var(--muted);flex:none}.note-glyph .icon{width:19px;height:19px}
.note-copy{min-width:0;flex:1}.note-title{display:block;font-weight:550;font-size:13px;line-height:1.4;overflow-wrap:anywhere}.note-date{display:block;color:var(--muted);font-size:11px;margin-top:4px}
.note-arrow{width:14px;height:14px;color:var(--muted);opacity:0}a.note-row:hover .note-arrow,a.note-row:focus-visible .note-arrow{opacity:1}.locked .note-title{color:var(--muted)}
.parent-heading{grid-column:1/-1;display:flex;align-items:center;gap:8px;color:var(--muted);font-size:12px;font-weight:600;margin:10px 2px -3px}.parent-heading .icon{width:16px;height:16px}
.parent-grid{grid-column:1/-1;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.parent-grid .note-row{min-height:77px;gap:7px}.parent-grid .note-glyph{width:22px}.parent-grid .note-arrow{display:none}
.empty{grid-column:1/-1;background:var(--group);padding:48px 24px;border-radius:11px;text-align:center}.empty>.icon{width:28px;height:28px;color:var(--muted)}.empty h3{font-size:17px;margin:12px 0 5px}.empty p{color:var(--muted);margin:0 0 12px}
.footer{display:flex;justify-content:space-between;gap:16px;margin:30px 0 32px;color:var(--muted);font-size:11px}.footer p{margin:0}.mobile-update{display:none}
@media(max-width:1000px){.shell{padding:0 24px}.workspace{grid-template-columns:190px minmax(0,1fr);gap:24px}.parent-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.parent-grid .note-row{min-height:65px}.parent-grid .note-glyph{width:30px}}
@media(max-width:700px){.shell{padding:0 16px}.topbar{min-height:92px}.brandmark{width:40px;height:40px}.brandmark svg{width:32px;height:32px}.top-actions{gap:0}.export-info{display:none}
.workspace{display:flex;flex-direction:column;gap:0;padding-top:13px}.sidebar{position:static;width:100%;display:none;margin:0 0 24px}.sidebar.mobile-open{display:block}.sidebar-note{display:none}.main{width:100%}
.collection-icon{display:none}.mobile-folders{display:inline-flex;align-items:center;gap:8px;background:var(--group);border-radius:8px;min-height:44px;padding:10px 12px;color:var(--text);font-size:12px}
.mobile-folders[aria-expanded=true]{background:var(--accent-soft);color:var(--accent)}.mobile-folders .icon{width:16px;height:16px}.intro-text{flex:1;min-width:0}.intro p{font-size:12px}
.groups,.parent-grid{grid-template-columns:minmax(0,1fr);gap:14px}.collection-bar{margin-top:20px;margin-bottom:10px}.note-row,.parent-grid .note-row{min-height:65px}.note-arrow{opacity:1}
.footer{flex-direction:column;gap:6px;margin-top:26px}.mobile-update{display:inline}.key{display:none}}
@media(prefers-reduced-motion:no-preference){.icon-button,.note-row,.nav-item{transition:background .12s ease}.disclosure svg,.folder-toggle .chevron{transition:transform .12s ease}}
@media print{.sidebar,.searchbar,.top-actions,.mobile-folders,.text-button,.footer{display:none}.workspace{display:block}.shell{max-width:none;padding:0}.folder-card{break-inside:avoid}.note-list[hidden]{display:block!important}}
</style></head>
<body>
<a class="skip" href="#main">@@skip@@</a>
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<symbol id="mark" viewBox="0 0 32 32"><path d="M6.72640,23.69280 C4.76229,23.69280 3.06492,22.32107 2.65276,20.40069 C2.24061,18.48030 3.22562,16.53288 5.01676,15.72692 C5.50108,15.50899 5.87075,15.09653 6.03455,14.59132 C6.75883,12.35732 8.91326,10.90519 11.25580,11.07212 C11.95126,11.12167 12.61711,10.78260 12.98608,10.19101 C14.57905,7.63687 17.67622,6.45365 20.56641,7.29507 C23.45661,8.13650 25.43467,10.79727 25.40773,13.80733 C25.40054,14.61092 25.90449,15.33037 26.66216,15.59820 C28.57539,16.27449 29.72311,18.23080 29.38002,20.23082 C29.03693,22.23084 27.30284,23.69280 25.27360,23.69280 L6.72640,23.69280 Z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/><path d="M13.31200,12.94080V20.19840M11.29600,18.18240L13.31200,20.19840L15.32800,18.18240" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/><path d="M18.68800,20.19840V12.94080M16.67200,14.95680L18.68800,12.94080L20.70400,14.95680" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="folder" viewBox="0 0 24 24"><path d="M3 7V5.5A1.5 1.5 0 0 1 4.5 4H9l2 3h8.5A1.5 1.5 0 0 1 21 8.5v10a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5V7Z"/></symbol>
<symbol id="note" viewBox="0 0 24 24"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h4"/></symbol>
<symbol id="stack" viewBox="0 0 24 24"><rect x="7" y="3" width="13" height="15" rx="2"/><path d="M4 7v12a2 2 0 0 0 2 2h10M11 7h5M11 11h5"/></symbol>
<symbol id="search" viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></symbol>
<symbol id="chevron" viewBox="0 0 24 24"><path d="m7 10 5 5 5-5"/></symbol>
<symbol id="arrow" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></symbol>
<symbol id="lock" viewBox="0 0 24 24"><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/></symbol>
<symbol id="theme" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M12 3.5a8.5 8.5 0 0 1 0 17Z" fill="currentColor" stroke="none"/></symbol>
<symbol id="close" viewBox="0 0 24 24"><path d="m6 6 12 12M6 18 12-12"/></symbol>
<symbol id="pc" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></symbol>
</defs></svg>
<div class="shell">
<header class="topbar">
 <div class="brand"><span class="brandmark"><svg aria-hidden="true"><use href="#mark"/></svg></span><div><div class="brand-name">Skyfold</div><div class="brand-sub">@@brand_sub@@</div></div></div>
 <div class="top-actions"><div class="export-info"><strong>@@saved@@</strong>@@updated@@</div><button type="button" class="icon-button" id="theme-toggle" title="@@theme@@" aria-label="@@theme@@"><svg class="icon" aria-hidden="true"><use href="#theme"/></svg></button></div>
</header>
<div class="workspace">
 <aside class="sidebar" id="sidebar" aria-label="@@folders@@"><nav class="nav-card" aria-label="@@folders@@"><div class="eyebrow nav-label">@@library@@</div><div id="folder-nav"></div></nav><div class="sidebar-note"><svg class="icon" aria-hidden="true"><use href="#pc"/></svg><p>@@side_note@@</p></div></aside>
 <main class="main" id="main" tabindex="-1">
 <div class="intro"><div class="intro-text"><h1>@@title@@</h1><p>@@intro@@</p></div><div class="collection-icon"><svg class="icon" aria-hidden="true"><use href="#stack"/></svg></div><button id="mobile-folders" class="mobile-folders" type="button" aria-expanded="false" aria-controls="sidebar"><svg class="icon" aria-hidden="true"><use href="#folder"/></svg>@@folders@@</button></div>
 <form role="search" id="search-form"><div class="searchbar"><svg class="icon" aria-hidden="true"><use href="#search"/></svg><label class="sr" for="q">@@search@@</label><input type="search" id="q" placeholder="@@search@@" autocomplete="off"><span class="key" aria-hidden="true">Ctrl K</span><button class="search-clear" id="clear" type="button" title="@@clear@@" aria-label="@@clear@@" hidden><svg class="icon" aria-hidden="true"><use href="#close"/></svg></button></div></form>
 <div class="collection-bar"><div class="collection-title"><h2 id="selection-title">@@all@@</h2><span class="count" id="count"></span></div><button class="text-button" id="collapse-all" type="button"></button></div>
 <div id="result-status" class="sr" role="status" aria-live="polite" aria-atomic="true"></div>
 <div class="groups" id="list"></div>
 <footer class="footer"><p><span id="totals"></span><span class="mobile-update"> · @@updated@@</span></p><p>@@footer@@</p></footer>
 </main>
</div>
</div>
<noscript><p style="padding:24px">@@noscript@@</p></noscript>
<script>
'use strict';
const notes=@@data@@;
const S=@@strings@@;
const $=id=>document.getElementById(id),icon=(name,cls='')=>`<svg class="icon ${cls}" aria-hidden="true"><use href="#${name}"/></svg>`;
for(const n of notes){n.f=n.f||'Notes';n.t=n.t||S.untitled}
function plural(n,forms){const a=n%10,b=n%100;const f=n===1?forms[0]:(S.slavic&&a===1&&b!==11)?forms[0]:(S.slavic&&a>=2&&a<=4&&(b<12||b>14))?forms[1]:forms[2];return f.replace('{n}',n)}
let selected='';const navClosed=new Set(),KEY='skyfold-collapsed';let shut=new Set();
try{const v=JSON.parse(localStorage.getItem(KEY)||'[]');if(Array.isArray(v))shut=new Set(v.filter(x=>typeof x==='string'))}catch(e){}
const folders=[...new Set(notes.map(n=>n.f))];
function persist(){try{localStorage.setItem(KEY,JSON.stringify([...shut]))}catch(e){}}
function text(tag,value,cls){const el=document.createElement(tag);el.textContent=value;if(cls)el.className=cls;return el}
function inFolder(n,path){return !path||n.f===path||n.f.startsWith(path+'/')}
function navButton(label,path,kind){const b=document.createElement('button');b.type='button';b.className='nav-item';b.dataset.folder=path;b.innerHTML=icon(kind||'folder');b.append(text('span',label));b.append(text('span',String(notes.filter(n=>inFolder(n,path)).length),'num'));if(selected===path)b.setAttribute('aria-current','page');b.addEventListener('click',()=>selectFolder(path));return b}
function drawNav(){
 const nav=$('folder-nav');nav.replaceChildren(navButton(S.all,'','stack'));nav.append(text('div','','nav-divider'));
 const root=new Map();for(const folder of folders){let kids=root,path='';for(const part of folder.split('/')){path=path?path+'/'+part:part;if(!kids.has(part))kids.set(part,{name:part,path,kids:new Map()});kids=kids.get(part).kids}}
 let index=0;function branch(nodes,box){for(const node of nodes.values()){
  if(!node.kids.size){box.append(navButton(node.name,node.path));continue}
  const parent=text('div','','nav-parent');parent.append(navButton(node.name,node.path));const toggle=text('button','','disclosure'),id='nav-children-'+index++,open=!navClosed.has(node.path);
  toggle.type='button';toggle.dataset.branch=node.path;toggle.innerHTML=icon('chevron');toggle.setAttribute('aria-label',node.name);toggle.setAttribute('aria-expanded',String(open));toggle.setAttribute('aria-controls',id);
  toggle.onclick=()=>{open?navClosed.add(node.path):navClosed.delete(node.path);drawNav();const b=[...nav.querySelectorAll('.disclosure')].find(x=>x.dataset.branch===node.path);if(b)b.focus()};
  parent.append(toggle);box.append(parent);const sub=text('div','','subnav');sub.id=id;sub.hidden=!open;branch(node.kids,sub);box.append(sub)}}
 branch(root,nav)}
function selectFolder(path){selected=path;drawNav();draw();if(matchMedia('(max-width:700px)').matches){$('sidebar').classList.remove('mobile-open');$('mobile-folders').setAttribute('aria-expanded','false');$('mobile-folders').focus()}else{const b=[...document.querySelectorAll('[data-folder]')].find(el=>el.dataset.folder===path);if(b)b.focus()}}
// Only relative paths inside the Notes folder, each part encoded (a "#" or "?" in a title stays part of the name).
function safePath(p){if(typeof p!=='string'||!p||/^[\\/]|^[a-z][a-z0-9+.-]*:/i.test(p)||p.split(/[\\/]/).includes('..'))return null;return p.split('/').map(encodeURIComponent).join('/')}
function noteRow(n){const href=n.l?null:safePath(n.p),row=document.createElement(href?'a':'div');row.className='note-row'+(n.l?' locked':'');row.innerHTML=`<span class="note-glyph">${icon(n.l?'lock':'note')}</span>`;
 const copy=text('span','','note-copy');copy.append(text('span',n.t,'note-title'));copy.append(text('span',n.l?S.locked:(n.m||''),'note-date'));row.append(copy);
 if(href){row.href=href;row.insertAdjacentHTML('beforeend',icon('arrow','note-arrow'))}return row}
function isShut(path){return [...shut].some(f=>f===path||path.startsWith(f+'/'))}
function setClosed(path,closed){if(closed)shut.add(path);else{for(const f of [...shut])if(f===path||path.startsWith(f+'/'))shut.delete(f)}persist()}
function folderCard(path,items,index){const card=text('section','','folder-card'),id='folder-body-'+index;card.setAttribute('aria-label',path);const b=text('button','','folder-toggle');b.type='button';b.innerHTML=icon('folder');
 b.append(text('span',path.split('/').pop(),'folder-title'));b.append(text('span',String(items.length),'folder-count'));b.insertAdjacentHTML('beforeend',icon('chevron','chevron'));b.setAttribute('aria-controls',id);
 const closed=!$('q').value.trim()&&isShut(path);b.setAttribute('aria-expanded',String(!closed));const ul=text('ul','','note-list');ul.id=id;ul.hidden=closed;
 for(const n of items){const li=document.createElement('li');li.append(noteRow(n));ul.append(li)}
 b.onclick=()=>{ul.hidden=!ul.hidden;b.setAttribute('aria-expanded',String(!ul.hidden));if(!$('q').value.trim())setClosed(path,ul.hidden);updateCollapse()};card.append(b,ul);return card}
function updateCollapse(){const toggles=[...document.querySelectorAll('.folder-toggle')],allClosed=toggles.length&&toggles.every(b=>b.getAttribute('aria-expanded')==='false');$('collapse-all').textContent=allClosed?S.expand:S.collapse;$('collapse-all').dataset.close=allClosed?'':'1';$('collapse-all').hidden=!toggles.length||!!$('q').value.trim()}
function draw(){
 const query=$('q').value.trim().toLocaleLowerCase(),filtered=notes.filter(n=>inFolder(n,selected)&&(!query||n.t.toLocaleLowerCase().includes(query)||n.f.toLocaleLowerCase().includes(query)));
 $('clear').hidden=!query;$('selection-title').textContent=selected?selected.replaceAll('/',' / '):S.all;$('count').textContent=plural(filtered.length,S.notes);$('result-status').textContent=$('count').textContent;
 const list=$('list'),grids=new Map();list.replaceChildren();
 for(const [i,f] of folders.entries()){
  const items=filtered.filter(n=>n.f===f);if(!items.length)continue;const card=folderCard(f,items,i),parent=f.includes('/')?f.slice(0,f.lastIndexOf('/')):'';
  if(parent&&f!==selected){if(!grids.has(parent)){const h=text('div','','parent-heading');h.innerHTML=icon('folder');h.append(text('span',parent.replaceAll('/',' / ')));list.append(h);const g=text('div','','parent-grid');list.append(g);grids.set(parent,g)}grids.get(parent).append(card)}
  else list.append(card)}
 if(!filtered.length){const empty=text('div','','empty');empty.innerHTML=icon('search');empty.append(text('h3',S.none));empty.append(text('p',S.tryOther));const reset=text('button',S.showAll,'text-button');reset.type='button';reset.onclick=()=>{$('q').value='';selected='';drawNav();draw();$('q').focus()};empty.append(reset);list.append(empty)}
 updateCollapse()}
$('totals').textContent=plural(notes.length,S.notes)+' · '+plural(folders.length,S.folderCount);
$('search-form').onsubmit=e=>e.preventDefault();$('q').oninput=draw;$('clear').onclick=()=>{$('q').value='';draw();$('q').focus()};
$('collapse-all').onclick=()=>{const close=!!$('collapse-all').dataset.close;for(const f of folders.filter(p=>notes.some(n=>n.f===p&&inFolder(n,selected))))setClosed(f,close);draw();$('collapse-all').focus()};
$('mobile-folders').onclick=()=>{const open=$('sidebar').classList.toggle('mobile-open');$('mobile-folders').setAttribute('aria-expanded',String(open));if(open)$('folder-nav').querySelector('button').focus()};
const scheme=matchMedia('(prefers-color-scheme:dark)'),root=document.documentElement;
function dark(){return root.dataset.theme==='dark'||root.dataset.theme==='light'?root.dataset.theme==='dark':scheme.matches}
function themeLabel(){const l=dark()?S.toLight:S.toDark;$('theme-toggle').title=l;$('theme-toggle').setAttribute('aria-label',l)}
window.sfToggleTheme=function(){const next=dark()?'light':'dark';root.dataset.theme=next;try{localStorage.setItem('skyfold-theme',next)}catch(e){}themeLabel()};
$('theme-toggle').onclick=window.sfToggleTheme;scheme.addEventListener('change',themeLabel);themeLabel();
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('q').focus()}if(e.key==='Escape'&&document.activeElement===$('q')){$('q').value='';draw()}});
drawNav();draw();
</script>
</body></html>
"""


def render_index(data: list, updated: str) -> str:
    """The Notes.html page: the notes' data (title, folder, page, date, locked) and this UI's words."""
    from .i18n import language

    words = {
        "title": t("Apple Notes"), "brand_sub": t("Notes on this PC"), "saved": t("Saved to this PC"),
        "updated": updated, "theme": t("Light / dark"), "folders": t("Folders"), "library": t("Your library"),
        "side_note": t("Your exported notes, ready to read. Changes in Apple Notes appear after the next sync."),
        "intro": t("All your notes from iCloud, kept on this PC."), "search": t("Search notes and folders"),
        "clear": t("Clear search"), "all": t("All notes"), "skip": t("Skip to notes"),
        "footer": t("Edit your notes on your iPhone, iPad or Mac"),
        "noscript": t("Turn on JavaScript to search and browse this note index."),
    }
    strings = {
        "all": t("All notes"), "untitled": t("Untitled"), "locked": t("locked – open it on your iPhone"),
        "collapse": t("Collapse all"), "expand": t("Expand all"), "none": t("No notes found"),
        "tryOther": t("Try another title or folder name."), "showAll": t("Show all notes"),
        "toDark": t("Switch to dark theme"), "toLight": t("Switch to light theme"),
        "notes": [t("1 note"), t("{n} notes (2-4)"), t("{n} notes")],
        "folderCount": [t("1 folder"), t("{n} folders (2-4)"), t("{n} folders")],
        "slavic": language() == "bs",  # 1 / 2-4 / 5+ forms ("1 bilješka, 3 bilješke, 7 bilješki")
    }
    if not strings["slavic"]:
        strings["notes"][1], strings["folderCount"][1] = strings["notes"][2], strings["folderCount"][2]

    def js(v) -> str:
        return json.dumps(v, ensure_ascii=False).replace("</", "<\\/")

    page = INDEX_TEMPLATE.replace("@@theme_script@@", THEME_SCRIPT).replace("@@lang@@", language() or "en")
    for k, v in words.items():
        page = page.replace(f"@@{k}@@", html.escape(v, quote=True))
    return page.replace("@@strings@@", js(strings)).replace("@@data@@", js(data))
