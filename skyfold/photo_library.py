"""The synced photos as the Photos window shows them: one item per photo or video (a Live Photo is
one item with two files), read from the sync database. Nothing here touches iCloud."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from .engine import ALBUMS_DIR

RECENT_DAYS = 30


@dataclass(slots=True)
class PhotoItem:
    asset_id: str
    path: Path  # the photo or video itself
    date: datetime  # when it was taken, local time
    is_video: bool = False
    live_video: Path | None = None  # a Live Photo's 3-second video
    size: int = 0  # bytes, both files of a Live Photo together
    favorite: bool = False
    added: datetime | None = None  # when it reached iCloud, local time
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    screenshot: bool = False
    albums: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def live(self) -> bool:
        return self.live_video is not None

    @property
    def files(self) -> list[Path]:
        return [self.path] + ([self.live_video] if self.live_video else [])


def _local(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone()


def load(state, root: Path) -> list[PhotoItem]:
    """Every synced photo and video, oldest first. Files that are no longer on this PC are left out."""
    items: dict[str, PhotoItem] = {}
    videos: dict[str, tuple[Path, int]] = {}
    for (aid, key, rel, size, is_video, date, fav, added, w, h, dur, shot) in state.library():
        path = root / PurePosixPath(rel)
        if key == "original_video":
            videos[aid] = (path, size or 0)
            continue
        if key != "original" or aid in items:
            continue
        taken = _local(date) or datetime.fromtimestamp(0).astimezone()
        items[aid] = PhotoItem(
            asset_id=aid, path=path, date=taken, is_video=bool(is_video), size=size or 0,
            favorite=bool(fav), added=_local(added), width=w, height=h, duration=dur,
            screenshot=bool(shot),
        )
    for aid, (path, size) in videos.items():
        it = items.get(aid)
        if it is not None and not it.is_video:
            it.live_video = path
            it.size += size
    for rel, aid in state.album_links().items():
        it = items.get(aid)
        parts = PurePosixPath(rel).parts
        if it is not None and len(parts) >= 3 and parts[0] == ALBUMS_DIR:
            album = "/".join(parts[1:-1])
            if album not in it.albums:
                it.albums.append(album)
    out = [it for it in items.values() if it.path.is_file()]
    out.sort(key=lambda it: (it.date, it.asset_id))
    return out


def fingerprint(state) -> tuple:
    """Changes when the window has something new to show (cheap: one query)."""
    return state.library_fingerprint()


def recent(items: list[PhotoItem], now: datetime | None = None) -> list[PhotoItem]:
    """Added to iCloud in the last 30 days, newest first: an old photo scanned today is here too."""
    now = now or datetime.now().astimezone()
    since = now - timedelta(days=RECENT_DAYS)
    got = [it for it in items if (it.added or it.date) >= since]
    got.sort(key=lambda it: (it.added or it.date, it.asset_id), reverse=True)
    return got


def albums(items: list[PhotoItem]) -> dict[str, list[PhotoItem]]:
    out: dict[str, list[PhotoItem]] = {}
    for it in items:
        for a in it.albums:
            out.setdefault(a, []).append(it)
    return dict(sorted(out.items(), key=lambda kv: kv[0].lower()))


MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december")


def matches(it: PhotoItem, query: str) -> bool:
    """Search: file name, year, "2025-03", month name, album, or a kind (video, live, screenshot,
    favourite)."""
    q = query.strip().lower()
    if not q:
        return True
    d = it.date
    words = [it.name.lower(), str(d.year), f"{d.year}-{d.month:02d}", MONTHS[d.month - 1]]
    words += [a.lower() for a in it.albums]
    if it.is_video:
        words.append("video")
    if it.live:
        words.append("live")
    if it.screenshot:
        words.append("screenshot")
    if it.favorite:
        words += ["favourite", "favorite"]
    return all(any(part in w for w in words) for part in q.split())
