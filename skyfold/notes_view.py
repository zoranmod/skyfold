"""What the Notes window shows: the list of notes (from the Notes database) and each note's text
(from the page Skyfold exported), cleaned up for Qt's rich-text viewer.

Which notes exist, their titles, folders and whether they are locked come from the database; the
page on disk only supplies what is read. A locked note is never read from disk, even when an older
readable copy of it is still there (Skyfold couldn't remove it yet). Only pictures inside the Notes
folder's _assets are ever loaded: nothing from the internet, nothing from elsewhere on the PC.
"""

from __future__ import annotations

import html
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from .i18n import t
from .notes_engine import ASSETS

# checklist boxes as text (read out by screen readers, found by search), in Segoe UI Symbol so the
# ticked one isn't drawn as a colour emoji
CHECK_ON = '<span class="sf-box sf-done">\u2611</span>\u00a0'
CHECK_OFF = '<span class="sf-box">\u2610</span>\u00a0'
SCAN_PAGES = 3  # pages of a scanned document shown at first; the rest on request
MAX_PAGE_BYTES = 8_000_000  # a note page bigger than this is shown from its browser copy only


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
    """A relative link or picture of a note page as a file in the Notes folder's _assets, or None
    (a web address, or anything outside _assets). Relative "../" parts are normal for notes in
    subfolders; it is where the link ends up that counts."""
    if not ref or ref.startswith(("#", "/", "\\")) or _SCHEME.match(ref):
        return None
    ref = ref.split("#", 1)[0].split("?", 1)[0]
    if not ref:
        return None
    try:
        root_r = root.resolve()
        p = (page.parent / urllib.parse.unquote(ref).replace("\\", "/")).resolve()
        rel = p.relative_to(root_r)
    except (OSError, ValueError, RuntimeError):
        return None
    if ASSETS not in rel.parts[:-1]:
        return None
    return p


# --- cleaning a page for the viewer ------------------------------------------------------------

_KEEP = {"p", "br", "h1", "h2", "h3", "h4", "h5", "h6", "b", "strong", "i", "em", "u", "s", "strike",
         "del", "sup", "sub", "ul", "ol", "li", "blockquote", "pre", "code", "table", "thead", "tbody",
         "tr", "td", "th", "a", "img", "hr", "span", "div"}
_VOID = {"br", "img", "hr", "input", "source", "embed", "meta", "link", "wbr", "col", "param"}
_DROP = {"script", "style", "head", "title", "button", "svg", "noscript", "template", "select",
         "textarea", "form", "iframe", "object", "audio", "video", "math", "canvas"}
_BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "tr", "br",
          "table", "ul", "ol", "hr"}
_HL = re.compile(r"var\(--hl([1-5])-bg\)")
_ALIGN = re.compile(r"text-align\s*:\s*(left|right|center|justify)", re.IGNORECASE)
_MEDIA_TEXT = {"audio": "Audio recording", "video": "Video", "object": "Open the PDF",
               "embed": "Open the PDF", "iframe": "Open the attachment"}


class _Clean(HTMLParser):
    """The note's own content (inside pyicloud's div.note-content, or the body without Skyfold's
    navigation bar), with only what Qt's viewer shows well and nothing that loads or runs."""

    def __init__(self, all_pages: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.all_pages = all_pages
        self.out: list[str] = []
        self.has_content_div = False
        self.inside = False  # inside the part that is shown
        self.depth = 0  # open elements inside it (to know where it ends)
        self.skip = 0  # >0 while inside a dropped element
        self.skip_tag = ""
        self.open: list[str] = []  # tags opened inside, to close what the page left open
        self.media: tuple[str, str] | None = None  # (tag, src) of an audio/video/object being skipped
        self.scan_depth = -1  # depth of a scanned document's div.sf-pdf
        self.scan_pages = 0
        self.hidden_pages = 0

    def feed_page(self, text: str) -> str:
        self.has_content_div = 'class="note-content"' in text
        if not self.has_content_div:
            self.inside = True  # older pages: the whole body, without the navigation bar
        self.feed(text)
        self.close()
        for tag in reversed(self.open):
            if tag:
                self.out.append(f"</{tag}>")
        # a checklist shows its boxes, not bullets as well
        return re.sub("<ul>(?=<li>" + re.escape('<span class="sf-box') + ")", '<ul style="list-style-type:none">',
                      "".join(self.out))

    # ----
    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        classes = a.get("class", "").split()
        if not self.inside:
            if tag == "div" and "note-content" in classes:
                self.inside = True
                self.depth = 0
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
                done = "checked" in a
                self.out.append(CHECK_ON if done else CHECK_OFF)
            return
        if tag not in _KEEP:
            if tag not in _VOID:
                self.depth += 1
                self.open.append("")  # a wrapper that isn't kept: its end is ignored too
            return
        if tag == "img":
            self._img(a, classes)
            return
        if tag in ("br", "hr"):
            self.out.append(f"<{tag}>")
            return
        if tag == "div" and "sf-pdf" in classes:
            self.scan_depth = self.depth
            self.scan_pages = 0
        self.depth += 1
        self.open.append(tag)
        self.out.append("<" + tag + self._attrs(tag, a) + ">")

    def handle_startendtag(self, tag, attrs):
        if tag in _VOID or tag == "input":
            self.handle_starttag(tag, attrs)
        else:
            self.handle_starttag(tag, attrs)
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
        if not self.open:
            if self.has_content_div:
                self.inside = False  # the end of div.note-content
            return
        # close up to the matching tag (pages are generated, but be tolerant)
        if tag in self.open or (tag not in _KEEP and "" in self.open):
            want = tag if tag in _KEEP else ""
            while self.open:
                top = self.open.pop()
                self.depth -= 1
                if top:
                    self.out.append(f"</{top}>")
                if top == want:
                    break
            if self.scan_depth >= 0 and self.depth <= self.scan_depth:
                self._end_scan()

    def handle_data(self, data):
        if self.inside and not self.skip and data:
            self.out.append(html.escape(data, quote=False))

    # ----
    def _skip(self, tag: str) -> None:
        self.skip, self.skip_tag = 1, tag

    def _attachment(self, kind: str, src: str) -> None:
        label = t(_MEDIA_TEXT.get(kind, "Open the attachment"))
        if not src:
            self.out.append(f"<p><i>{html.escape(t('Attachment unavailable on this PC'))}</i></p>")
            return
        name = urllib.parse.unquote(src.split("#", 1)[0].split("?", 1)[0]).replace("\\", "/").rsplit("/", 1)[-1]
        self.out.append(f'<p>\U0001F4CE <a href="{html.escape(src, quote=True)}">{html.escape(label)}</a>'
                        f' <span class="sf-file">{html.escape(name)}</span></p>')

    def _img(self, a: dict, classes: list[str]) -> None:
        src = a.get("src", "")
        if not src or _SCHEME.match(src):
            return  # pictures from the internet (or inline data) are not loaded
        if "sf-page" in classes and self.scan_depth >= 0:
            self.scan_pages += 1
            if not self.all_pages and self.scan_pages > SCAN_PAGES:
                self.hidden_pages += 1
                return
        alt = html.escape(a.get("alt", ""), quote=True)
        self.out.append(f'<img src="{html.escape(src, quote=True)}" alt="{alt}"><br>')

    def _end_scan(self) -> None:
        hidden = max(0, self.scan_pages - SCAN_PAGES) if not self.all_pages else 0
        if hidden:
            self.out.append(f'<p><a href="skyfold:all-pages">{html.escape(t("Show {n} more pages", n=hidden))}</a></p>')
        self.scan_depth = -1
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
                    out.append(f'{k}="{a[k]}"')
        elif tag == "ol" and a.get("start", "").isdigit():
            out.append(f'start="{a["start"]}"')
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


def note_html(page_text: str, all_pages: bool = False) -> tuple[str, int]:
    """(clean HTML of the note's content, number of scanned pages left out)."""
    c = _Clean(all_pages)
    try:
        body = c.feed_page(page_text)
    except Exception:  # noqa: BLE001 - a page that can't be parsed shows as plain text
        return "<pre>" + html.escape(plain_text(page_text)) + "</pre>", 0
    return body, c.hidden_pages


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
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
    if ch in "đĐ":
        return "d"
    base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
    return (base.casefold() or ch.casefold() or ch)[:1] or ch


def fold(text: str) -> str:
    """Lower case without accents, one character for each character (so positions match):
    "Čaj" and "caj" are found by "caj", "đak" by "dak"."""
    return "".join(_fold1(c) for c in text)


def snippet(text: str, title: str, query: str = "", width: int = 120) -> str:
    """The first line of the note that isn't its title, or the passage that matches the search."""
    if query:
        q = fold(query.strip())
        i = fold(text).find(q) if q else -1
        if i >= 0:
            start = max(0, i - 30)
            cut = text[start:start + width].replace("\n", " · ")
            return ("…" if start else "") + cut
    head = fold(title.strip())
    for line in text.splitlines():
        line = line.strip()
        if line and fold(line) != head:
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
