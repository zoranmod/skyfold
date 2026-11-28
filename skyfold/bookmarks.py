"""Safari bookmarks from iCloud, as a bookmarks file Firefox and Chrome can import. Read-only.

Safari keeps its bookmarks in CloudKit (container com.apple.SafariShared.WBSCloudBookmarksStore, zone
"Bookmarks"), the same service iCloud.com's own "Recover bookmarks" page reads with a web session.
Skyfold only READS them - nothing is ever written to iCloud - and writes one file on this PC, in the
standard bookmarks format both browsers import (Bookmarks → Import), under a "Safari Bookmarks" folder.

The file is replaced only after everything was read and makes sense, and only when it is Skyfold's
own (a file of the same name that Skyfold didn't write is left alone).
"""
from __future__ import annotations

import base64
import html
import json
import logging
import threading
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import save_text
from .engine import SyncError
from .i18n import t

log = logging.getLogger("skyfold.bookmarks")

CONTAINER = "com.apple.SafariShared.WBSCloudBookmarksStore"
ZONE = "Bookmarks"
FILE_NAME = "Safari Bookmarks.html"
MARK = "<!-- Skyfold: Safari bookmarks from iCloud -->"
MAX_PAGES = 50
SAFE_SCHEMES = ("http://", "https://", "ftp://", "mailto:", "file:")
SAFARI_FOLDERS = {"BookmarksBar": "Favorites", "BookmarksMenu": "Bookmarks Menu"}  # internal name -> shown


class BookmarksUnavailable(SyncError):
    pass


class NotCurrent(Exception):
    """The account the bookmarks were read for signed out (or Skyfold is quitting): nothing written."""


@dataclass
class Item:
    name: str  # record name
    is_folder: bool
    title: str
    url: str = ""
    parent: str = ""
    order: tuple = ()
    added: int = 0  # ms since 1970
    children: list = field(default_factory=list)


def _text(field_: dict | None) -> str:
    """Title/URL come as ENCRYPTED_BYTES: base64 of the plain text (decrypted by Apple's server)."""
    if not isinstance(field_, dict):
        return ""
    v = field_.get("value")
    if not isinstance(v, str):
        return ""
    if field_.get("type") in ("ENCRYPTED_BYTES", "BYTES"):
        try:
            return base64.b64decode(v).decode("utf-8", "replace")
        except (ValueError, TypeError):
            return ""
    return v


def _order(field_: dict | None, added: int, title: str) -> tuple:
    """Safari's position: base64 of compressed JSON with "sortValues" (the latest change counts).
    When it can't be read, by date added, then title."""
    try:
        raw = base64.b64decode(field_["value"])
        for wbits in (47, -15):  # zlib/gzip, then raw deflate
            try:
                raw = zlib.decompress(raw, wbits)
                break
            except zlib.error:
                continue
        data = json.loads(raw)
        values = (data.get("default") or data).get("sortValues") or []
        best = max(values, key=lambda v: v.get("changeID", 0) if isinstance(v, dict) else 0)
        sv = best.get("sortValue")
        if isinstance(sv, (int, float)):
            return (0, float(sv), "", added, title.lower())
        if isinstance(sv, str):
            return (0, 0.0, sv, added, title.lower())
    except Exception:  # noqa: BLE001 - not readable: fall back
        pass
    return (1, 0.0, "", added, title.lower())


def fetch(api) -> list[dict]:
    """Every record of the bookmarks zone (all pages), or an error. Read-only requests only."""
    from .remote import _translate

    base = api.get_webservice_url("ckdatabasews")
    url = f"{base}/database/1/{CONTAINER}/production/private/changes/zone"
    body: dict = {"zones": [{"zoneID": {"zoneName": ZONE}}]}
    records: list[dict] = []
    seen_tokens: set[str] = set()
    incomplete = t("Safari bookmarks can't be read right now ({code}).", code="INCOMPLETE_RESPONSE")
    for _ in range(MAX_PAGES):
        try:
            r = api.session.post(url, params=api.params, json=body, timeout=60, allow_redirects=False)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc
        if r.status_code in (401, 403, 421):
            raise BookmarksUnavailable(t("Safari bookmarks can't be read: iCloud didn't allow it ({code}).",
                                         code=r.status_code))
        if r.status_code != 200:
            raise BookmarksUnavailable(t("Safari bookmarks can't be read right now ({code}).", code=r.status_code))
        reply = r.json()
        zones = reply.get("zones") if isinstance(reply, dict) and not reply.get("serverErrorCode") else None
        if not isinstance(zones, list) or len(zones) != 1 or not isinstance(zones[0], dict):
            raise BookmarksUnavailable(incomplete)
        zone = zones[0]
        zone_id = zone.get("zoneID")
        if zone_id is not None and (not isinstance(zone_id, dict) or zone_id.get("zoneName") != ZONE):
            raise BookmarksUnavailable(incomplete)
        code = zone.get("serverErrorCode")
        if code == "ZONE_NOT_FOUND" and not seen_tokens:
            return []  # Safari bookmarks aren't in iCloud (turned off on the devices)
        if code:
            raise BookmarksUnavailable(t("Safari bookmarks can't be read: iCloud answered {code}.", code=code))
        batch = zone.get("records", [])
        if not isinstance(batch, list) or (zone_id is None and "records" not in zone):
            raise BookmarksUnavailable(incomplete)
        records.extend(batch)
        if not zone.get("moreComing"):
            return records
        token = zone.get("syncToken")
        if not isinstance(token, str) or not token or token in seen_tokens:
            raise BookmarksUnavailable(incomplete)
        seen_tokens.add(token)
        body = {"zones": [{"zoneID": {"zoneName": ZONE}, "syncToken": token}]}
    raise BookmarksUnavailable(t("Safari bookmarks can't be read: too many pages."))


def build(records: list[dict]) -> list[Item]:
    """The bookmark tree: the top level's folders and bookmarks, each with its children in order.
    Deleted items, tab groups and the Reading List are left out."""
    items: dict[str, Item] = {}
    skip: set[str] = set()
    for rec in records:
        if rec.get("deleted"):
            continue
        kind = rec.get("recordType")
        if kind not in ("BookmarkLeaf", "BookmarkList"):
            continue
        f = rec.get("fields") or {}
        name = rec.get("recordName") or ""
        if (f.get("Deleted") or {}).get("value"):
            continue
        title = _text(f.get("Title")).strip()
        added = (f.get("DateAdded") or {}).get("value") or 0
        parent = ((f.get("ParentFolder") or {}).get("value") or {}).get("recordName") or ""
        item = Item(name, kind == "BookmarkList", title, _text(f.get("URL")).strip(), parent,
                    _order(f.get("Position"), added if isinstance(added, int) else 0, title),
                    added if isinstance(added, int) else 0)
        plain = title.lower().replace(" ", "")
        if item.is_folder and ((f.get("IsTabGroup") or {}).get("value") or "readinglist" in name.lower()
                               or "readinglist" in plain):
            skip.add(name)  # Safari's Reading List (titled "com.apple.ReadingList") and tab groups
        if item.is_folder and title in SAFARI_FOLDERS:  # Safari's own folders, by the names Safari shows
            item.title = t(SAFARI_FOLDERS[title])
        items[name] = item
    top: list[Item] = []
    for item in items.values():
        if item.parent in items:
            items[item.parent].children.append(item)
        else:
            top.append(item)  # directly under Safari's root
    # leave out skipped folders with everything below them, and anything in a parent loop
    def keep(item: Item, seen: frozenset) -> bool:
        return item.name not in skip and item.name not in seen

    def tidy(nodes: list[Item], seen: frozenset) -> list[Item]:
        out = []
        for n in sorted(nodes, key=lambda i: i.order):
            if not keep(n, seen):
                continue
            if n.is_folder:
                n.children = tidy(n.children, seen | {n.name})
            elif not n.url.lower().startswith(SAFE_SCHEMES):
                continue
            out.append(n)
        return out

    return tidy(top, frozenset())


def count(nodes: list[Item]) -> int:
    return sum(count(n.children) if n.is_folder else 1 for n in nodes)


def render(nodes: list[Item], folder_title: str) -> str:
    """The Netscape bookmarks format that Firefox, Chrome and Edge import."""
    lines = ["<!DOCTYPE NETSCAPE-Bookmark-file-1>", MARK,
             '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
             "<TITLE>Bookmarks</TITLE>", "<H1>Bookmarks</H1>", "<DL><p>",
             f"    <DT><H3>{html.escape(folder_title)}</H3>", "    <DL><p>"]

    def add(n: Item, depth: int) -> None:
        pad = "    " * depth
        when = f' ADD_DATE="{n.added // 1000}"' if n.added else ""
        if n.is_folder:
            lines.append(f"{pad}<DT><H3{when}>{html.escape(n.title or t('Untitled'))}</H3>")
            lines.append(f"{pad}<DL><p>")
            for c in n.children:
                add(c, depth + 1)
            lines.append(f"{pad}</DL><p>")
        else:
            lines.append(f'{pad}<DT><A HREF="{html.escape(n.url, quote=True)}"{when}>'
                         f"{html.escape(n.title or n.url)}</A>")

    for n in nodes:
        add(n, 2)
    lines += ["    </DL><p>", "</DL><p>", ""]
    return "\n".join(lines)


def as_json(nodes: list[Item]) -> list[dict]:
    """The tree for the browser extension: folders {"t", "c"}, bookmarks {"t", "u"}."""
    return [{"t": n.title or t("Untitled"), "c": as_json(n.children)} if n.is_folder
            else {"t": n.title or n.url, "u": n.url} for n in nodes]


# The cache is written (worker) and removed (Sign out, UI thread) one at a time: a read that finishes
# just as you sign out either writes before the removal or sees that the account is gone.
CACHE_LOCK = threading.Lock()


def save_cache(nodes: list[Item], current: Callable[[], bool] | None = None, *, apple_id: str = "") -> None:
    """What the extension gets (see native_host): written after every complete read.
    current: is the account this was read for still signed in? (No: NotCurrent, nothing written.)
    apple_id: whose bookmarks these are. Only its fingerprint is kept, and the extension gets the copy
    only while that Apple ID is the one signed in (without it, nobody gets it)."""
    from datetime import datetime, timezone

    from .native_host import cache_file, cache_owner

    path = cache_file()
    data = json.dumps({"owner": cache_owner(apple_id),
                       "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "tree": as_json(nodes)}, ensure_ascii=False)
    with CACHE_LOCK:
        if current is not None and not current():
            raise NotCurrent()  # signed out (or quitting) while the bookmarks were on their way
        path.parent.mkdir(parents=True, exist_ok=True)
        save_text(path, data)


def remove_cache() -> None:
    """Sign out, or another Apple ID: the extension's copy goes (never while a read is writing it)."""
    from .native_host import cache_file

    with CACHE_LOCK:
        cache_file().unlink(missing_ok=True)


def export(api, folder: Path, current: Callable[[], bool] | None = None) -> tuple[Path, int, bool]:
    """Read the bookmarks and write the file. Returns (file, number of bookmarks, changed?).
    current: see save_cache; checked again before the file is written."""
    nodes = build(fetch(api))
    # (the owner is the session these were read with, not the settings: they may have moved on meanwhile)
    save_cache(nodes, current, apple_id=str(getattr(api, "account_name", "") or ""))
    page = render(nodes, t("Safari Bookmarks"))
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / FILE_NAME
    if target.exists():
        try:
            head = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise SyncError(t("Couldn't read {name}: {error}", name=str(target), error=str(exc))) from exc
        if MARK not in head[:400]:
            raise SyncError(t("{name} wasn't written by Skyfold; it is left as it is. Move it or choose another "
                              "folder for Safari bookmarks.", name=str(target)))
        if head == page:
            return target, count(nodes), False
    if current is not None and not current():
        raise NotCurrent()
    save_text(target, page)
    return target, count(nodes), True
