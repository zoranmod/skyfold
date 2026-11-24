"""What the Notes window shows: the list of notes (from the Notes database) and each note's text
(from the page Skyfold exported), cleaned up for Qt's rich-text viewer.

Which notes exist, their titles, folders and whether they are locked come from the database; the
page on disk only supplies what is read. A locked note is never read from disk, even when an older
readable copy of it is still there (Skyfold couldn't remove it yet). Pictures are only ever loaded
from the Notes folder's own _assets folder (where Skyfold saves them): nothing from the internet,
another drive, a network share or elsewhere on the PC. The viewer never sees a file name at all:
pictures get generated ids, mapped to files that were checked first.
"""

from __future__ import annotations

import html
import os
import posixpath
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from .i18n import t
from .notes_engine import ASSETS, _is_junction

# checklist boxes as text (read out by screen readers, found by search), in Segoe UI Symbol so the
# ticked one isn't drawn as a colour emoji
CHECK_ON = '<span class="sf-box sf-done">☑</span> '
CHECK_OFF = '<span class="sf-box">☐</span> '
SCAN_PAGES = 60  # pages of a scanned document shown at first (pictures load as you scroll); "show more": all
MAX_PAGE_BYTES = 8_000_000  # a note page bigger than this is shown from its browser copy only
IMAGE_SCHEME = "sfimg"


@dataclass(frozen=True)
class Row:
    id: str
    rel: str | None  # the page, relative to the Notes folder; None for a locked note
    title: str
    folder: str  # "Folder/Subfolder"
    modified: str | None
    locked: bool


def rows_from(state) -> list[Row]:
    """All notes, newest first. The lock flag outranks a readable path."""
    out = []
    for r in state.all().values():
        locked = bool(r[5])
        out.append(Row(str(r[0]), None if locked else (r[1] or None), r[2] or "", r[3] or "", r[4], locked))
    out.sort(key=lambda r: (r.modified or "", r.title.lower()), reverse=True)
    return out


def page_path(root: Path, row: Row) -> Path | None:
    """The note's page, only when it lies inside the Notes folder."""
    if row.locked or not row.rel:
        return None
    try:
        root_r = root.resolve()
        p = (root_r / row.rel).resolve()
        p.relative_to(root_r)
    except (OSError, ValueError, RuntimeError):
        return None
    return p if p.suffix.lower() == ".html" else None


_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*:", re.IGNORECASE)


def resolve_asset(root: Path, page: Path, ref: str) -> Path | None:
    """A relative link or picture of a note page as a file in the Notes folder's _assets, or None.

    The reference is decoded once, then everything that isn't a plain relative path is refused
    before the disk is looked at (web addresses, drive letters, network and device paths, NUL).
    Relative "../" parts are normal for notes in subfolders; where the path ends up must be inside
    <Notes folder>/_assets, first by its text and then once more after following links/junctions."""
    if not ref:
        return None
    ref = ref.split("#", 1)[0].split("?", 1)[0]
    ref = urllib.parse.unquote(ref).replace("\\", "/")
    if not ref or ref.startswith("/") or ":" in ref or "\x00" in ref:
        return None
    try:
        root_r = root.resolve()
        page_dir = page.parent.resolve().relative_to(root_r).as_posix()
    except (OSError, ValueError, RuntimeError):
        return None
    rel = posixpath.normpath(posixpath.join("" if page_dir == "." else page_dir, ref))
    parts = rel.split("/")
    if len(parts) < 2 or parts[0] != ASSETS or ".." in parts:
        return None
    try:
        top = root_r / ASSETS
        # _assets itself must be a real folder in the Notes folder, not a link or junction to
        # somewhere else (it is the boundary everything below is checked against)
        if os.path.islink(top) or _is_junction(top) or os.path.normcase(top.resolve()) != os.path.normcase(top):
            return None
        target = (root_r / rel).resolve()
        target.relative_to(top)
        target.relative_to(root_r)
    except (OSError, ValueError, RuntimeError):
        return None
    return target


# --- cleaning a page for the viewer ------------------------------------------------------------

_KEEP = {"p", "br", "h1", "h2", "h3", "h4", "h5", "h6", "b", "strong", "i", "em", "u", "s", "strike",
         "del", "sup", "sub", "ul", "ol", "li", "blockquote", "pre", "code", "table", "thead", "tbody",
         "tr", "td", "th", "a", "img", "hr", "span", "div"}
_VOID = {"br", "img", "hr", "input", "source", "embed", "meta", "link", "wbr", "col", "param", "area", "base"}
_DROP = {"script", "style", "head", "title", "button", "svg", "noscript", "template", "select",
         "textarea", "form", "iframe", "object", "audio", "video", "math", "canvas"}
_BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "tr", "br",
          "table", "ul", "ol", "hr"}
_HL = re.compile(r"var\(--hl([1-5])-bg\)")
_ALIGN = re.compile(r"text-align\s*:\s*(left|right|center|justify)", re.IGNORECASE)
_MEDIA_TEXT = {"audio": "Audio recording", "video": "Video", "object": "Open the PDF",
               "embed": "Open the PDF", "iframe": "Open the attachment"}
_WRAPPER = re.compile(r"""<div\b[^>]*\bclass\s*=\s*(?:(["'])(?:[^"']*\s)?note-content(?:\s[^"']*)?\1"""
                      r"""|note-content(?=[\s/>]))""", re.IGNORECASE)  # quoted, or unquoted (class=note-content)
LABEL = "⁣"  # (invisible) starts a line that Skyfold added, such as an attachment link: never a first line


@dataclass
class Page:
    body: str  # clean HTML; pictures as sfimg:<n>
    images: list[str] = field(default_factory=list)  # n -> the page's own reference (checked when loaded)
    hidden: int = 0  # scanned pages not shown yet


class _Clean(HTMLParser):
    """The note's own content (inside pyicloud's div.note-content, or the body without Skyfold's
    navigation bar), with only what Qt's viewer shows well and nothing that loads or runs."""

    def __init__(self, shown: dict[int, int] | None, all_pages: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.shown = shown or {}
        self.all_pages = all_pages
        self.out: list[str] = []
        self.images: list[str] = []
        self.has_wrapper = False
        self.inside = False  # inside the part that is shown
        self.skip = 0  # >0 while inside a dropped element
        self.skip_tag = ""
        self.open: list[str] = []  # elements opened inside ("" for a wrapper that isn't kept)
        self.media: tuple[str, str] | None = None  # (tag, src) of an audio/video/object being skipped
        self.scan = -1  # number of the scanned document (div.sf-pdf) being read, -1 = none
        self.scan_level = 0
        self.scans = 0
        self.scan_pages = 0
        self.hidden = 0

    def run(self, text: str) -> Page:
        self.has_wrapper = bool(_WRAPPER.search(text))
        if not self.has_wrapper:
            self.inside = True  # older pages: the whole body, without the navigation bar
        self.feed(text)
        self.close()
        if self.scan >= 0:
            self._end_scan()
        for tag in reversed(self.open):
            if tag:
                self.out.append(f"</{tag}>")
        # a checklist shows its boxes, not bullets as well
        body = re.sub("<ul>(?=<li>" + re.escape('<span class="sf-box') + ")", '<ul style="list-style-type:none">',
                      "".join(self.out))
        return Page(body, self.images, self.hidden)

    # ----
    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        classes = a.get("class", "").split()
        if not self.inside:
            if tag == "div" and "note-content" in classes:
                self.inside = True
            return
        if self.skip:
            if tag == self.skip_tag and tag not in _VOID:
                self.skip += 1
            if tag == "source" and self.media and not self.media[1]:
                self.media = (self.media[0], a.get("src", ""))
            return
        if tag in ("body", "html"):
            return
        if tag == "div" and "sf-nav" in classes:
            self._skip(tag)
            return
        if tag in ("audio", "video", "object", "iframe"):
            self.media = (tag, a.get("src") or a.get("data") or "")
            self._skip(tag)
            return
        if tag == "embed":
            self._attachment("embed", a.get("src", ""))
            return
        if tag in _DROP:
            self._skip(tag)
            return
        if tag == "input":
            if a.get("type", "").lower() == "checkbox":
                self.out.append(CHECK_ON if "checked" in a else CHECK_OFF)
            return
        if tag not in _KEEP:
            if tag not in _VOID:
                self.open.append("")  # a wrapper that isn't kept: its end is ignored too
            return
        if tag == "img":
            self._img(a, classes)
            return
        if tag in ("br", "hr"):
            self.out.append(f"<{tag}>")
            return
        if tag == "div" and "sf-pdf" in classes and self.scan < 0:
            self.scan, self.scan_level, self.scan_pages = self.scans, len(self.open), 0
            self.scans += 1
        self.open.append(tag)
        self.out.append("<" + tag + self._attrs(tag, a) + ">")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and tag != "input":
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.inside:
            return
        if self.skip:
            if tag == self.skip_tag:
                self.skip -= 1
                if not self.skip and self.media:
                    kind, src = self.media
                    self.media = None
                    self._attachment(kind, src)
            return
        if tag in _VOID:
            return
        want = tag if tag in _KEEP else ""
        if want not in self.open:
            if tag == "div" and self.has_wrapper and not any(self.open):
                self.inside = False  # the end of div.note-content
            return  # a stray end tag: ignored
        while self.open:
            top = self.open.pop()
            if top:
                self.out.append(f"</{top}>")
            if top == want:
                break
        if self.scan >= 0 and len(self.open) <= self.scan_level:
            self._end_scan()

    def handle_data(self, data):
        if self.inside and not self.skip and data:
            self.out.append(html.escape(data, quote=False))

    # ----
    def _skip(self, tag: str) -> None:
        self.skip, self.skip_tag = 1, tag

    def _attachment(self, kind: str, src: str) -> None:
        label = t(_MEDIA_TEXT.get(kind, "Open the attachment"))
        if not src or _SCHEME.match(src):
            self.out.append(f'<p class="sf-att"><i>{html.escape(t("Attachment unavailable on this PC"))}</i></p>')
            return
        name = urllib.parse.unquote(src.split("#", 1)[0].split("?", 1)[0]).replace("\\", "/").rsplit("/", 1)[-1]
        self.out.append(f'<p class="sf-att">\U0001F4CE <a href="{html.escape(src, quote=True)}">{html.escape(label)}</a>'
                        f' <span class="sf-file">{html.escape(name)}</span></p>')

    def _img(self, a: dict, classes: list[str]) -> None:
        src = a.get("src", "")
        if not src or _SCHEME.match(src):
            return  # pictures from the internet (or inline data) are not loaded
        if "sf-page" in classes and self.scan >= 0:
            self.scan_pages += 1
            if not self.all_pages and self.scan_pages > self.shown.get(self.scan, SCAN_PAGES):
                self.hidden += 1
                return
        self.images.append(src)
        alt = html.escape(a.get("alt", ""), quote=True)
        self.out.append(f'<img src="{IMAGE_SCHEME}:{len(self.images) - 1}" alt="{alt}"><br>')

    def _end_scan(self) -> None:
        limit = self.shown.get(self.scan, SCAN_PAGES)
        hidden = 0 if self.all_pages else max(0, self.scan_pages - limit)
        if hidden:
            self.out.append(f'<p><a href="skyfold:more:{self.scan}">'
                            f'{html.escape(t("Show {n} more pages", n=hidden))}</a></p>')
        self.scan = -1
        self.scan_pages = 0

    @staticmethod
    def _attrs(tag: str, a: dict) -> str:
        out = []
        if tag == "a":
            href = a.get("href", "")
            if href and not href.startswith("#") and (not _SCHEME.match(href)
                                                     or href.lower().startswith(("http:", "https:", "mailto:"))):
                out.append(f'href="{html.escape(href, quote=True)}"')
        elif tag in ("td", "th"):
            for k in ("colspan", "rowspan"):
                if a.get(k, "").isdigit():
                    out.append(f'{k}="{min(int(a[k]), 100)}"')
        elif tag == "ol" and a.get("start", "").strip().isdigit():
            out.append(f'start="{int(a["start"].strip())}"')
        elif tag == "table":
            out.append('border="1" cellspacing="0" cellpadding="6"')
        style = a.get("style", "")
        css = []
        m = _ALIGN.search(style)
        if m and tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th"):
            css.append(f"text-align:{m.group(1).lower()}")
        if tag == "span":
            low = style.lower().replace(" ", "")
            if "font-weight:bold" in low or "font-weight:700" in low:
                css.append("font-weight:bold")
            if "font-style:italic" in low:
                css.append("font-style:italic")
            if "underline" in low:
                css.append("text-decoration:underline")
            if "line-through" in low:
                css.append("text-decoration:line-through")
            hl = _HL.search(low)
            if hl:
                out.append(f'class="hl{hl.group(1)}"')
        if css:
            out.append(f'style="{";".join(css)}"')
        return (" " + " ".join(out)) if out else ""


def note_html(page_text: str, shown: dict[int, int] | None = None, all_pages: bool = False) -> Page:
    """The note's content as clean HTML. shown: pages to show of each scanned document (by its
    number in the note) beyond the first few; all_pages: every page (for search)."""
    try:
        return _Clean(shown, all_pages).run(page_text)
    except Exception:  # noqa: BLE001 - a page that can't be parsed shows as plain text
        return Page("<pre>" + html.escape(plain_text(page_text)) + "</pre>")


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
        elif tag == "p" and ("class", "sf-att") in attrs:
            self.parts.append("\n" + LABEL)
        elif tag in _BLOCK or tag in ("td", "th"):
            self.parts.append("\n" if tag not in ("td", "th") else " ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def plain_text(clean_html: str) -> str:
    """Visible text with one line per paragraph, list item or table row."""
    p = _Text()
    try:
        p.feed(clean_html)
        p.close()
    except Exception:  # noqa: BLE001
        return ""
    lines = (" ".join(line.split()) for line in "".join(p.parts).splitlines())
    return "\n".join(line for line in lines if line)


# --- search --------------------------------------------------------------------------------------

def _fold1(ch: str) -> str:
    """One character in lower case without accents ("" for a combining accent on its own)."""
    if ch in "đĐ":
        return "d"
    if unicodedata.combining(ch):
        return ""
    base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
    return (base.casefold() or ch.casefold() or ch)[:1] or ch


class _FoldTable(dict):
    def __missing__(self, code: int) -> str:
        v = self[code] = _fold1(chr(code))
        return v


_TABLE = _FoldTable()


def fold(text: str) -> str:
    """Lower case without accents: "Čaj", "caj" and a decomposed "Čaj" all become "caj";
    "đak" becomes "dak". Fast (str.translate with a growing table)."""
    return text.translate(_TABLE)


def fold_map(text: str) -> tuple[str, list[int]]:
    """fold(text) plus, for each folded character, where it starts in text counted in UTF-16 units
    (as Qt's text cursor counts: an emoji is two), and one more entry for the end."""
    out: list[str] = []
    pos: list[int] = []
    at = 0
    for ch in text:
        f = _TABLE[ord(ch)]
        if f:
            out.append(f)
            pos.append(at)
        at += 2 if ord(ch) > 0xFFFF else 1
    pos.append(at)
    return "".join(out), pos


def fold_positions(text: str, wanted: list[int]) -> list[int]:
    """Where the folded characters at the given positions (in fold(text)) start in text, counted in
    UTF-16 units as Qt counts them; len(fold(text)) gives the end. Walks the text once without
    building a table for all of it (a long paragraph stays cheap)."""
    order = sorted(set(wanted))
    found: dict[int, int] = {}
    k = 0  # folded characters so far
    at = 0  # UTF-16 units so far
    j = 0
    for ch in text:
        if j >= len(order):
            break
        if _TABLE[ord(ch)]:
            while j < len(order) and order[j] == k:
                found[order[j]] = at
                j += 1
            k += 1
        at += 2 if ord(ch) > 0xFFFF else 1
    while j < len(order):
        found[order[j]] = at
        j += 1
    return [found[w] for w in wanted]


def _source_index(text: str, folded_i: int) -> int:
    """Index in text (Python characters) of the folded character folded_i."""
    k = 0
    for i, ch in enumerate(text):
        if _TABLE[ord(ch)]:
            if k == folded_i:
                return i
            k += 1
    return len(text)


def snippet(text: str, folded: str, title: str, query: str = "", width: int = 120) -> str:
    """The first line of the note that isn't its title, or the passage that matches the search.
    folded is fold(text), kept from the index (folding again for every search would be slow)."""
    if query:
        q = fold(query.strip())
        i = folded.find(q) if q else -1
        if i >= 0:
            i = _source_index(text, i)  # lone accents make the two strings differ in length
            start = max(0, i - 30)
            cut = text[start:start + width].replace(LABEL, "").replace("\n", " · ")
            return ("…" if start else "") + cut
    head = fold(title.strip())
    labels = {fold(t("Open the original PDF"))}
    for line in text.splitlines()[:20]:
        line = line.strip()
        if line and not line.startswith(LABEL) and fold(line) != head and fold(line) not in labels:
            return line[:width]
    return ""


def matches(row: Row, body_folded: str | None, terms: list[str]) -> int:
    """0 = no match; otherwise a rank: 3 title, 2 folder, 1 text. Every word must be found somewhere."""
    if not terms:
        return 3
    title, folder = fold(row.title), fold(row.folder)
    rank = 3
    for w in terms:
        if w in title:
            continue
        if w in folder:
            rank = min(rank, 2)
        elif body_folded is not None and w in body_folded:
            rank = min(rank, 1)
        else:
            return 0
    return rank
