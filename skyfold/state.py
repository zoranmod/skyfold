"""SQLite record of what has been synchronized, so deletions can be told apart from new files."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .remote import AssetInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    asset_id     TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    rel_path     TEXT NOT NULL,
    size         INTEGER,
    is_video     INTEGER NOT NULL DEFAULT 0,
    asset_date   TEXT,
    synced_at    TEXT NOT NULL,
    PRIMARY KEY (asset_id, resource_key)
);
CREATE INDEX IF NOT EXISTS files_path ON files(rel_path);
-- Assets the user removed on the PC but chose to keep in iCloud: never download again.
CREATE TABLE IF NOT EXISTS excluded (
    asset_id TEXT PRIMARY KEY,
    reason   TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- Hard links that mirror iCloud albums under Albums/ (one-way, recreated from iCloud).
CREATE TABLE IF NOT EXISTS album_links (
    rel_path TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL
);
-- What the Photos window shows besides the files (favourite, when added, size, length,
-- screenshot), as iCloud last said. Not needed for syncing.
CREATE TABLE IF NOT EXISTS asset_info (
    asset_id   TEXT PRIMARY KEY,
    favorite   INTEGER NOT NULL DEFAULT 0,
    added      TEXT,
    width      INTEGER,
    height     INTEGER,
    duration   REAL,
    screenshot INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS activity (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts   TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL
);
"""


@dataclass(slots=True)
class FileRow:
    asset_id: str
    resource_key: str
    rel_path: str
    size: int | None
    is_video: bool
    asset_date: str | None
    hash: str | None = None  # SHA-256 of the file as Skyfold downloaded it (None: from before 1.0.6)


class State:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        if "hash" not in {r[1] for r in self._db.execute("PRAGMA table_info(files)")}:
            self._db.execute("ALTER TABLE files ADD COLUMN hash TEXT")  # databases from before 1.0.6
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(activity)")}
        if "target" not in cols:  # what clicking the entry opens (a file or folder); added in 1.1
            self._db.execute("ALTER TABLE activity ADD COLUMN target TEXT NOT NULL DEFAULT ''")

    # meta -----------------------------------------------------------------
    def get(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str | None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def items(self, prefix: str) -> list[tuple[str, str]]:
        """(key, value) of every entry whose key starts with prefix and that has a value."""
        with self._lock:
            rows = self._db.execute("SELECT key, value FROM meta WHERE substr(key, 1, ?)=? AND value IS NOT NULL",
                                    (len(prefix), prefix)).fetchall()
        return [(r[0], r[1]) for r in rows]

    # files ----------------------------------------------------------------
    def files(self) -> list[FileRow]:
        with self._lock:
            rows = self._db.execute(
                "SELECT asset_id, resource_key, rel_path, size, is_video, asset_date, hash FROM files"
            ).fetchall()
        return [FileRow(r[0], r[1], r[2], r[3], bool(r[4]), r[5], r[6]) for r in rows]

    def asset_ids(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._db.execute("SELECT DISTINCT asset_id FROM files")}

    def paths(self) -> set[str]:
        with self._lock:
            return {r[0].lower() for r in self._db.execute("SELECT rel_path FROM files")}

    def add_file(self, row: FileRow, synced_at: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO files(asset_id, resource_key, rel_path, size, is_video, asset_date, "
                "synced_at, hash) VALUES(?,?,?,?,?,?,?,?)",
                (row.asset_id, row.resource_key, row.rel_path, row.size, int(row.is_video), row.asset_date,
                 synced_at, row.hash),
            )

    def move_file(self, asset_id: str, resource_key: str, new_rel_path: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE files SET rel_path=? WHERE asset_id=? AND resource_key=?",
                (new_rel_path, asset_id, resource_key),
            )

    def remove_asset(self, asset_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM files WHERE asset_id=?", (asset_id,))

    def remove_file(self, asset_id: str, resource_key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM files WHERE asset_id=? AND resource_key=?", (asset_id, resource_key))

    def stats(self) -> dict[str, int]:
        with self._lock:
            photos = self._db.execute(
                "SELECT COUNT(DISTINCT asset_id) FROM files WHERE is_video=0 AND resource_key NOT LIKE '%video'"
            ).fetchone()[0]
            videos = self._db.execute(
                "SELECT COUNT(DISTINCT asset_id) FROM files WHERE is_video=1"
            ).fetchone()[0]
            size = self._db.execute("SELECT COALESCE(SUM(size), 0) FROM files").fetchone()[0]
        return {"photos": photos, "videos": videos, "bytes": size}

    # exclusions -------------------------------------------------------------
    def excluded(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._db.execute("SELECT asset_id FROM excluded")}

    def unexclude_parts(self, asset_id: str) -> None:
        """Forget "don't bring back" marks for an asset's parts (e.g. its Live Photo video)."""
        prefix = asset_id + "#"
        with self._lock:
            self._db.execute("DELETE FROM excluded WHERE substr(asset_id, 1, ?) = ?", (len(prefix), prefix))

    def exclude(self, asset_id: str, reason: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO excluded VALUES(?, ?)", (asset_id, reason))

    def clear_exclusions(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM excluded")

    # activity -------------------------------------------------------------
    def log_activity(self, ts: str, kind: str, text: str, target: str = "") -> None:
        with self._lock:
            self._db.execute("INSERT INTO activity(ts, kind, text, target) VALUES(?,?,?,?)",
                             (ts, kind, text, target))
            self._db.execute(
                "DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT 300)"
            )

    def clear_activity(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM activity")

    def recent_activity(self, limit: int = 100) -> list[tuple[str, str, str, str]]:
        with self._lock:
            return list(
                self._db.execute("SELECT ts, kind, text, target FROM activity ORDER BY id DESC LIMIT ?", (limit,))
            )

    # what the Photos window shows ------------------------------------------
    def set_infos(self, infos: dict[str, "AssetInfo"]) -> None:
        """Keep iCloud's details of these assets (favourite, added, size, length, screenshot)."""
        rows = [(aid, int(i.favorite), i.added.isoformat() if i.added else None, i.width, i.height, i.duration,
                 int(i.screenshot)) for aid, i in infos.items() if i is not None]
        if not rows:
            return
        with self._lock:
            self._db.executemany(
                "INSERT OR REPLACE INTO asset_info(asset_id, favorite, added, width, height, duration, screenshot) "
                "VALUES(?,?,?,?,?,?,?)", rows)

    def drop_infos(self, asset_ids: set[str]) -> None:
        with self._lock:
            self._db.executemany("DELETE FROM asset_info WHERE asset_id=?", [(a,) for a in asset_ids])

    def set_favorite(self, asset_id: str, value: bool) -> None:
        with self._lock:
            self._db.execute("INSERT INTO asset_info(asset_id, favorite) VALUES(?, ?) "
                             "ON CONFLICT(asset_id) DO UPDATE SET favorite=excluded.favorite", (asset_id, int(value)))

    def library(self) -> list[tuple]:
        """Every synced photo and video for the Photos window, oldest first: (asset_id, resource_key,
        rel_path, size, is_video, asset_date, favorite, added, width, height, duration, screenshot).
        One row per file: a Live Photo has its photo and its video."""
        with self._lock:
            return self._db.execute(
                "SELECT f.asset_id, f.resource_key, f.rel_path, f.size, f.is_video, f.asset_date, "
                "COALESCE(i.favorite, 0), i.added, i.width, i.height, i.duration, COALESCE(i.screenshot, 0) "
                "FROM files f LEFT JOIN asset_info i ON i.asset_id = f.asset_id "
                "ORDER BY f.asset_date, f.asset_id").fetchall()

    def library_fingerprint(self) -> tuple:
        """Changes whenever a file, a favourite or an album link changes (for the Photos window)."""
        with self._lock:
            return (
                self._db.execute("SELECT COUNT(*), MAX(synced_at), COALESCE(SUM(LENGTH(rel_path)), 0) "
                                 "FROM files").fetchone()
                + self._db.execute("SELECT COUNT(*), COALESCE(SUM(favorite), 0) FROM asset_info").fetchone()
                + self._db.execute("SELECT COUNT(*) FROM album_links").fetchone()
            )

    # album links -----------------------------------------------------------
    def album_links(self) -> dict[str, str]:
        with self._lock:
            return dict(self._db.execute("SELECT rel_path, asset_id FROM album_links"))

    def add_album_link(self, rel: str, asset_id: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO album_links VALUES(?, ?)", (rel, asset_id))

    def drop_album_link(self, rel: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM album_links WHERE rel_path=?", (rel,))

    def reset(self) -> None:
        """Forget everything (used when switching account or folder)."""
        with self._lock:
            # (what the upload knows about files on this PC stays: it is about those files, not the folder)
            self._db.executescript("DELETE FROM files; DELETE FROM excluded; DELETE FROM album_links; DELETE FROM asset_info; "
                                   "DELETE FROM meta WHERE key NOT LIKE 'drive_%' AND key NOT LIKE 'upload_file:%' "
                                   "AND key NOT LIKE 'upload_legacy:%';")
