"""Two-way iCloud Drive sync.

Every run compares three views of each path: iCloud now, this PC now, and the state both
had after the last successful sync (the "base", kept in SQLite). From that it decides:

    changed in iCloud only  -> download          changed on the PC only -> upload
    changed on both sides   -> keep iCloud's version, save the PC's as "name (PC conflict …)"
    new on one side         -> copy to the other  deleted on one side    -> delete on the other

Deletions are never permanent: iCloud items go to "Recently Deleted", PC files to the Recycle
Bin, and more deletions than the safety threshold wait for the user's OK.
Renames and moves are recognised (same content gone in one place, new in another) and done as
renames on the other side. An edit always wins over a deletion or rename on the other side.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import sqlite3
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from .config import save_text
from .drive_remote import UPLOAD_WORKERS, WORKERS, DriveItem, DriveRemote, MacPackage, is_transient
from .engine import (Callbacks, Cancelled, SyncError, THROTTLE, check_free_space, create_marker, known_missing, inside_root,
                     marker_present, now_iso, transfer_text)
from .i18n import t
from .safelog import brief
from .remote import AuthRequired, RemoteUnavailable

log = logging.getLogger(__name__)

MARKER = ".skyfold-drive"
TMP_PREFIX = ".skyfold-tmp-"
SCRUB_BYTES = 64 * 1024 * 1024  # PC files re-read per full pass to catch edits that kept size and time
ASIDE_PREFIX = f"{TMP_PREFIX}old-"  # the PC's file while a download is put in its place
IGNORE = ["desktop.ini", "Thumbs.db", ".DS_Store", "~$*", "*.tmp", "*.crdownload", "*.partial",
          ".~lock.*", f"{TMP_PREFIX}*", MARKER, "Icon\r"]
SETTLE_SECONDS = 5  # a file changed more recently than this is probably still being written

SCHEMA = """
CREATE TABLE IF NOT EXISTS drive_files (
    path    TEXT PRIMARY KEY,   -- posix path relative to the Drive root
    is_dir  INTEGER NOT NULL,
    etag    TEXT,               -- iCloud version at last sync (NULL right after our own upload)
    size    INTEGER,
    lmtime  REAL,               -- PC modification time at last sync
    lsize   INTEGER,
    pending INTEGER NOT NULL DEFAULT 0, -- 1: PC version must be uploaded (iCloud copy was trashed)
    hash    TEXT                -- SHA-256 of the content both sides had at last sync (NULL: not known yet)
);
CREATE TABLE IF NOT EXISTS drive_meta (key TEXT PRIMARY KEY, value TEXT);
"""


@dataclass(slots=True)
class Base:
    path: str
    is_dir: bool
    etag: str | None
    size: int | None
    lmtime: float | None
    lsize: int | None
    pending: bool
    hash: str | None = None  # content both sides had at last sync; None: not known (kept by put() while
    #                          the PC size/time of the row stay the same)


@dataclass
class DriveResult:
    last_down: str = ""  # a file to show in Explorer when the activity entry is clicked
    last_up: str = ""
    downloaded: int = 0
    uploaded: int = 0
    deleted_cloud: int = 0
    deleted_local: int = 0
    conflicts: int = 0
    moved: int = 0  # renamed or moved, on either side, instead of copied again
    errors: int = 0
    pending_to_cloud: list[str] = field(default_factory=list)
    pending_to_pc: list[str] = field(default_factory=list)
    down_files: list[str] = field(default_factory=list)
    up_files: list[str] = field(default_factory=list)
    full: bool = False  # True when the whole Drive was read
    followup: bool = False  # a new folder was created; its contents go up on the next pass


class DriveState:
    def __init__(self, db_path: Path) -> None:
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        if "hash" not in {r[1] for r in self._db.execute("PRAGMA table_info(drive_files)")}:
            self._db.execute("ALTER TABLE drive_files ADD COLUMN hash TEXT")  # databases from before 1.0.6

    def all(self) -> dict[str, Base]:
        with self._lock:
            rows = self._db.execute("SELECT path, is_dir, etag, size, lmtime, lsize, pending, hash FROM drive_files")
            return {r[0]: Base(r[0], bool(r[1]), r[2], r[3], r[4], r[5], bool(r[6]), r[7]) for r in rows}

    def put(self, b: Base) -> None:
        with self._lock:
            # A row written without a fingerprint keeps the one it had while it still describes the
            # same PC version (same size and time); a new PC version starts without one.
            self._db.execute(
                "INSERT OR REPLACE INTO drive_files(path, is_dir, etag, size, lmtime, lsize, pending, hash) "
                "VALUES(?,?,?,?,?,?,?, COALESCE(?, (SELECT hash FROM drive_files "
                "WHERE path=? AND lsize IS ? AND lmtime IS ?)))",
                (b.path, int(b.is_dir), b.etag, b.size, b.lmtime, b.lsize, int(b.pending),
                 b.hash, b.path, b.lsize, b.lmtime))

    def drop(self, path: str) -> None:
        with self._lock:
            prefix = path + "/"
            self._db.execute("DELETE FROM drive_files WHERE path=? OR substr(path, 1, ?)=?",
                             (path, len(prefix), prefix))

    def drop_many(self, paths: list[str]) -> None:
        with self._lock, self._db:
            for path in paths:
                prefix = path + "/"
                self._db.execute("DELETE FROM drive_files WHERE path=? OR substr(path, 1, ?)=?",
                                 (path, len(prefix), prefix))

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM drive_meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str | None) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO drive_meta VALUES(?,?)", (key, value))

    def meta_items(self, prefix: str) -> dict[str, str]:
        """key -> value of every meta entry whose key starts with prefix and that has a value."""
        with self._lock:
            rows = self._db.execute("SELECT key, value FROM drive_meta WHERE substr(key, 1, ?)=? AND value IS NOT NULL",
                                    (len(prefix), prefix)).fetchall()
        return {r[0]: r[1] for r in rows}

    def drop_one(self, path: str) -> None:
        """Forget only this row, not what is below it."""
        with self._lock:
            self._db.execute("DELETE FROM drive_files WHERE path=?", (path,))

    def stats(self) -> dict[str, int]:
        with self._lock:
            files, size = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(lsize), 0) FROM drive_files WHERE is_dir=0").fetchone()
        return {"files": files, "bytes": size}

    def reset(self) -> None:
        """Forget everything about the synced folder, moves written down for it included."""
        with self.transaction():
            self._db.execute("DELETE FROM drive_files")
            self._db.execute("DELETE FROM drive_meta WHERE key=?", (PENDING_MOVE,))
            self._db.execute("DELETE FROM drive_meta WHERE substr(key, 1, 6)='shell:'")  # kept folders of the other one

    @contextmanager
    def transaction(self):
        """Everything inside is written together or not at all (also when the process dies)."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")


SKIPPED_REMOTE = "skipped_remote"  # iCloud items the PC never shows (JSON list of paths)
RECEIPT = "receipt:"  # receipt:<path> = version tag iCloud named when it confirmed an upload
PENDING_MOVE = "move_pending"  # renames/moves in iCloud started and not known to be settled, by item id


def ignored(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in IGNORE)


@dataclass(slots=True)
class Local:
    is_dir: bool
    size: int
    mtime: float


class DriveSyncer:
    def __init__(self, state: DriveState, remote: DriveRemote, root: Path, delete_threshold: int,
                 cb: Callbacks | None = None, delete_pc_to_cloud: bool = True,
                 delete_cloud_to_pc: bool = True, cache_file: Path | None = None) -> None:
        self.state = state
        self.remote = remote
        self.root = root
        self.delete_threshold = delete_threshold
        self.delete_pc_to_cloud = delete_pc_to_cloud
        self.delete_cloud_to_pc = delete_cloud_to_pc
        self.cb = cb or Callbacks()
        self.cancel_flag = threading.Event()  # this pass (cleared when the next one starts)
        self.closing = threading.Event()  # the app is quitting: never cleared
        self._hashes: dict[tuple, str | None] = {}
        self.unsupported: set[str] = set()
        self.folders: list[str] | None = None  # chosen Drive folders; None = all of iCloud Drive
        # Called (from the Drive thread) as soon as a big deletion needs the user's OK, so the
        # question appears right away instead of after a long download.
        self.on_pending: Callable[[list[str], list[str]], None] | None = None
        self._late: tuple[dict, dict, DriveResult] | None = None
        self.mac_packages: set[str] = set()  # left in iCloud: Macs show these folders as one file
        self.approved: set[str] = set()
        # What the last question showed, per side (iCloud: with which items and versions they were).
        # An approval only counts for that side and those very items; a deletion once asked about is
        # never done unanswered, also when a later pass sees fewer items than the limit.
        self._asked_cloud: dict[str, dict | None] = {}
        self._asked_pc: dict[str, dict | None] = {}  # path -> what was on this PC when asked (_pc_shape)
        self._approved_side: dict[str, str] = {}  # path -> "cloud"/"pc", when the answer said which
        self.question_gen = 0  # goes up whenever the question changes: an answer names the one it saw
        self.side_gen = {"cloud": 0, "pc": 0}  # the same, per side of the question
        self.own_paths: dict[str, float] = {}
        self.cache: dict[str, DriveItem] = {}  # iCloud tree as of the last read
        self.account = ""  # short fingerprint of the Apple ID this folder belongs to (set by the worker)
        self._prev: dict[str, DriveItem] = {}
        self.cache_file = cache_file  # kept on disk so quick passes work right after a restart
        self._load_cache()

    def _load_cache(self) -> None:
        if not self.cache_file or not self.cache_file.exists():
            return
        try:
            raw = json.loads(self.cache_file.read_text(encoding="utf-8"))
            if raw.get("root") == str(self.root):
                self.cache = {d["path"]: DriveItem(**d) for d in raw["items"]}
                self._saved_cache = self._cache_shape()
        except (OSError, ValueError, KeyError, TypeError):
            self.cache = {}

    def _cache_shape(self) -> dict[str, tuple]:
        """What the saved file holds, to compare (items are values, never shared with the cache)."""
        return {p: (i.path, i.drivewsid, i.docwsid, i.zone, i.etag, i.is_dir, i.size, i.mtime)
                for p, i in self.cache.items()}

    def _save_cache(self) -> None:
        if not self.cache_file:
            return
        shape = self._cache_shape()
        if shape == getattr(self, "_saved_cache", None):
            return  # nothing changed since it was written (a quick check of a quiet folder)
        try:
            save_text(self.cache_file, json.dumps({"root": str(self.root),
                                                   "items": [asdict(i) for i in self.cache.values()]}))
            self._saved_cache = shape
        except OSError as exc:
            log.warning("Could not save Drive cache: %s", exc)

    # helpers ------------------------------------------------------------------------
    def _abs(self, rel: str) -> Path:
        return self.root / Path(rel)

    def _touch_own(self, p: Path) -> None:
        now = time.monotonic()
        if len(self.own_paths) > 2000:
            self.own_paths = {k: v for k, v in self.own_paths.items() if now - v < 10}
        self.own_paths[str(p).lower()] = now

    def is_own_path(self, p: str) -> bool:
        ts = self.own_paths.get(str(Path(p)).lower())
        return ts is not None and time.monotonic() - ts < 10

    def _stopped(self) -> bool:
        return self.cancel_flag.is_set() or self.closing.is_set()

    def _check_cancel(self) -> None:
        if self._stopped():
            raise Cancelled()

    # private files ----------------------------------------------------------------------
    def _rel(self, p: Path) -> str | None:
        try:
            return Path(p).relative_to(self.root).as_posix()
        except ValueError:
            return None

    def _own(self, p: Path, kind: str, value: str = "1") -> None:
        """Write down a private file (a download in progress, a file moved aside) before it exists:
        only files on this list are ever cleaned up, recovered, recycled or renamed."""
        rel = self._rel(p)
        if rel is not None:
            self.state.set_meta(f"{kind}:{rel}", value)

    @staticmethod
    def _file_id(p: Path) -> str | None:
        """Which file this is (not where it is): the same after a rename, different for another file."""
        try:
            st = os.stat(p, follow_symlinks=False)
        except OSError:
            return None
        return f"{st.st_dev}:{st.st_ino}" if st.st_ino else None

    def _disown(self, p: Path, kind: str) -> None:
        rel = self._rel(p)
        if rel is not None:
            self.state.set_meta(f"{kind}:{rel}", None)

    # content --------------------------------------------------------------------------
    def _cloud_hash(self, r: DriveItem) -> str | None:
        """SHA-256 of what iCloud has for this item now, by downloading it to a private file.
        None: couldn't be checked (then nothing is assumed about it)."""
        # Tagged: that version. Untagged: only this very listing of it, within this pass (a later look,
        # or the next pass, may see other content).
        key = (r.docwsid or r.path, r.etag) if r.etag else ("listing", id(r))
        if key in self._hashes and (r.etag or self._hashes[key][0] is r):
            return self._hashes[key] if r.etag else self._hashes[key][1]
        tmp_path = None
        try:
            self._check_cancel()
            fd, tmp = tempfile.mkstemp(prefix=TMP_PREFIX, dir=self.root)
            os.close(fd)
            tmp_path = Path(tmp)
            self._own(tmp_path, "tmp")

            def on_bytes(k: int) -> None:
                THROTTLE.take(max(k, 0))
                self._check_cancel()

            self.remote.download(r, tmp_path, on_bytes)
            if r.size is None or r.size < 0 or tmp_path.stat().st_size != r.size:
                got = None  # not the whole file (or its size is unknown): proves nothing
            else:
                got = file_hash(tmp_path)
        except (AuthRequired, RemoteUnavailable, Cancelled):
            raise
        except Exception as exc:  # noqa: BLE001
            log.info("Drive: couldn't check the content of %s in iCloud: %s", r.path, brief(exc))
            got = None
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
                self._disown(tmp_path, "tmp")
        self._hashes[key] = got if r.etag else (r, got)  # (the item is kept: its id isn't reused meanwhile)
        return got

    def _pc_has_synced_content(self, b: Base | None, p: Path, strict: bool = False) -> bool:
        """Before a PC file is replaced or sent to the Recycle Bin as "the old synced version": is it?
        Size and time must match the database; when its fingerprint is known, the content must too
        (a file can be changed while keeping size and time). Unreadable: no (strict: the error is
        raised, as "can't tell" is not "changed")."""
        try:
            st = p.stat()
            if b is None or (st.st_size, round(st.st_mtime, 2)) != (b.lsize, round(b.lmtime or 0, 2)):
                return False
            return not b.hash or file_hash(p) == b.hash
        except OSError:
            if strict:
                raise
            return False

    def _scrub(self, remote: dict[str, DriveItem], local: dict[str, "Local"], base: dict[str, Base],
               uploads: list[str]) -> None:
        """A change on this PC that kept the file's size and time is invisible to the normal scan.
        Each full pass therefore re-reads a bounded amount of files that look in sync (in turn, so
        all of them come round) and compares them with their fingerprints; a file that differs is a
        change made on this PC and goes up."""
        cands = sorted(p for p, b in base.items()
                       if not b.is_dir and b.hash and not b.pending and b.etag is not None
                       and p in remote and remote[p].etag == b.etag and p in local and p not in uploads
                       and (local[p].size, round(local[p].mtime, 2)) == (b.lsize, round(b.lmtime or 0, 2)))
        if not cands:
            return
        cursor = self.state.get_meta("scrub_cursor") or ""
        order = [p for p in cands if p > cursor] + [p for p in cands if p <= cursor]
        spent, last = 0, None
        for p in order:
            size = local[p].size or 0
            if last is not None and spent + size > SCRUB_BYTES:
                break
            self._check_cancel()
            try:
                changed = file_hash(self._abs(p)) != base[p].hash
            except OSError:
                continue
            spent, last = spent + size, p
            if changed:
                log.info("Drive: %s changed on this PC without a new size or time; uploading it", p)
                uploads.append(p)
        if last is not None:
            self.state.set_meta("scrub_cursor", last)

    def _is_synced_content(self, b: Base | None, r: DriveItem) -> bool:
        """Is iCloud's copy, whatever its version tag says, exactly the content this PC last synced?
        Decided by content (the stored fingerprint against a fresh download), never by size or time."""
        return bool(b is not None and b.hash and self._cloud_hash(r) == b.hash)

    def _check_root(self, has_base: bool) -> None:
        if not self.root.exists():
            if has_base:
                raise SyncError(t("Drive folder is not available: {path}", path=self.root))
            self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / MARKER
        account_line = f"account: {self.account}" if self.account else ""
        if not marker_present(marker):
            if has_base:
                raise SyncError(t(
                    "The Drive folder doesn't look like before (its sync marker is missing). "
                    "Syncing has stopped so nothing gets deleted by mistake."))
            create_marker(marker, t("This folder is synced by Skyfold. Don't delete this file.\n")
                          + (account_line + "\n" if account_line else ""))
            return
        if not account_line:
            return
        try:
            text = marker.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise SyncError(t("Couldn't read the Drive folder's sync marker: {error}", error=brief(exc))) from exc
        owner = next((ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("account:")), None)
        if owner is None and not has_base:
            # made by an older version, and this sync database never synced it (e.g. another Apple ID
            # signed in since): it can't be told whose files these are
            raise SyncError(t("This Drive folder was used before and it isn't clear by which Apple ID. Choose a "
                              "new, empty folder for iCloud Drive in Settings."))
        if owner is None:  # made by an older version and synced by this database: it is this account's
            try:
                with open(marker, "a", encoding="utf-8") as fh:
                    fh.write(account_line + "\n")
            except OSError:
                pass
        elif owner != self.account:
            raise SyncError(t("This Drive folder belongs to another Apple ID. Choose a new, empty folder for "
                              "iCloud Drive in Settings."))

    def _refuse_unowned_folder(self) -> None:
        """A folder this database never synced: refuse it if its marker says it belongs to someone else,
        or if it has an old marker that can't tell whose it is."""
        marker = self.root / MARKER
        if not marker_present(marker):
            return
        try:
            text = marker.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise SyncError(t("Couldn't read the Drive folder's sync marker: {error}", error=brief(exc))) from exc
        owner = next((ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("account:")), None)
        if owner is None:
            raise SyncError(t("This Drive folder was used before and it isn't clear by which Apple ID. Choose a "
                              "new, empty folder for iCloud Drive in Settings."))
        if self.account and owner != self.account:
            raise SyncError(t("This Drive folder belongs to another Apple ID. Choose a new, empty folder for "
                              "iCloud Drive in Settings."))

    def _unreadable_add(self, full: str | Path) -> None:
        try:
            rel = Path(full).relative_to(self.root).as_posix()
        except ValueError:
            return
        self.unreadable.add("" if rel == "." else rel)

    def scan_local(self) -> dict[str, Local]:
        out: dict[str, Local] = {}
        self.unreadable = set()
        owned: dict[str, str] = {}
        for kind in ("aside:", "tmp:", "prev:"):
            for key, val in self.state.meta_items(kind).items():
                if known_missing(self.root / key[len(kind):]):
                    self.state.set_meta(key, None)  # the file is gone: nothing left to finish
                else:
                    owned[key] = val  # also when it couldn't be looked at: kept, and left alone
        previous = any(k.startswith("prev:") for k in owned)
        # A replaced version on its way to the Recycle Bin is held (nothing done there on either side)
        # until this scan proves what is at that name now; whether or not the walk lists it.
        held = {k[len("prev:"):] for k in owned if k.startswith("prev:")}
        self.unreadable |= held
        prev_key = {k[len("prev:"):].lower(): k for k in owned if k.startswith("prev:")}  # Windows: case-blind
        for dirpath, dirs, files in os.walk(self.root, onerror=lambda e: self._unreadable_add(e.filename or "")):
            dirs[:] = [d for d in dirs if not ignored(d)]
            for d in [d for d in dirs if _is_link(Path(dirpath) / d)]:
                dirs.remove(d)  # a junction or link may point anywhere: never sync through it
                self._unreadable_add(Path(dirpath) / d)
                log.warning("Drive: %s is a link/junction; left alone", Path(dirpath) / d)
            rel_dir = Path(dirpath).relative_to(self.root).as_posix()
            rel_dir = "" if rel_dir == "." else rel_dir
            for d in dirs:
                rel = f"{rel_dir}/{d}" if rel_dir else d
                key = prev_key.get(rel.lower())
                if key:  # a folder at that name now: provably not the file Skyfold wrote down
                    self.state.set_meta(key, None)
                    self.unreadable.discard(key[len("prev:"):])
                out[rel] = Local(True, 0, 0.0)
                if d.startswith("."):
                    hide_like_mac(Path(dirpath) / d)
            for f in files:
                key = prev_key.get(f"{rel_dir}/{f}".lower() if rel_dir else f.lower()) if previous else None
                if key:
                    # A replaced version that was on its way to the Recycle Bin when Skyfold stopped:
                    # it goes there now. It is never taken for a new file while it may still be that file.
                    full = Path(dirpath) / f
                    rel_f = self._rel(full)
                    recorded = key[len("prev:"):]
                    fid = self._file_id(full)
                    if fid and fid == owned[key]:
                        try:
                            self._trash_local(full)
                            self.state.set_meta(key, None)
                            self.unreadable.discard(recorded)  # in the Recycle Bin: the name is free again
                        except Exception as exc:  # noqa: BLE001 - tried again next time
                            log.warning("Drive: couldn't move %s to the Recycle Bin: %s", f, exc)
                            self.unreadable.add(rel_f)  # still ours: left alone on both sides
                        continue
                    if not fid:  # can't tell which file this is now: kept on record, left alone
                        self.unreadable.add(rel_f)  # (under the spelling it has now, too)
                        continue
                    self.state.set_meta(key, None)  # provably another file has that name now: someone's
                    self.unreadable.discard(recorded)
                if f.startswith(TMP_PREFIX):
                    # Skyfold's own leftovers (written down when they were made) are finished off here.
                    # A file that only has such a name is someone's: left alone, and not synced.
                    full = Path(dirpath) / f
                    rel_f = self._rel(full)
                    if f"aside:{rel_f}" in owned:
                        self._recover_aside(full)
                        if known_missing(full):
                            self._disown(full, "aside")
                    elif f"tmp:{rel_f}" in owned:
                        try:
                            os.unlink(full)  # an unfinished download from a crash
                            self._disown(full, "tmp")
                        except OSError:
                            pass
                if ignored(f):
                    continue
                rel = f"{rel_dir}/{f}" if rel_dir else f
                try:
                    st = os.stat(Path(dirpath) / f)
                except OSError:
                    self.unreadable.add(rel)
                    continue
                out[rel] = Local(False, st.st_size, st.st_mtime)
                if f.startswith("."):
                    hide_like_mac(Path(dirpath) / f, getattr(st, "st_file_attributes", None))
        return out

    def _check_inside(self, p: Path) -> None:
        """The real location (links and junctions resolved) must be inside the Drive folder."""
        if not inside_root(self.root, p):
            raise SyncError(t("{name} leads outside the Drive folder; left alone", name=p))

    def _trash_local(self, p: Path) -> None:
        from .engine import recycle

        self._check_inside(p.parent)
        self._touch_own(p)
        recycle(p)

    def _wanted_dir(self, path: str) -> bool:
        return in_scope(path, True, self.folders)

    def _skip_unsupported(self, path: str) -> bool:
        """iCloud items Windows can't store (names with : ? * etc., too-long paths) are left in
        iCloud with one message per app run, instead of failing on every sync."""
        reason = windows_name_problem(path, self.root)
        if not reason:
            return False
        if path not in self.unsupported:
            self.unsupported.add(path)
            self.cb.activity("info", t("Left in iCloud, {reason}: {name}", reason=t(reason), name=path))
        return True

    def _conflict_name(self, rel: str) -> str:
        p = PurePosixPath(rel)
        stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
        name = str(p.with_name(f"{p.stem} (PC conflict {stamp}){p.suffix}"))
        n = 2
        while self._abs(name).exists():
            name = str(p.with_name(f"{p.stem} (PC conflict {stamp} {n}){p.suffix}"))
            n += 1
        return name

    # main ---------------------------------------------------------------------------
    def _scope_folders(self, changed: set[str]) -> list[DriveItem] | None:
        """Folders to re-read for a quick sync after local changes; None means "walk everything"."""
        if not self.cache:
            return None
        wanted: set[str] = set()
        for rel in changed:
            parent = str(PurePosixPath(rel).parent) if "/" in rel else ""
            wanted.add(parent)
            if rel in self.cache and self.cache[rel].is_dir:
                wanted.add(rel)
        folders: dict[str, DriveItem] = {}
        for path in wanted:
            while path not in self.cache or not self.cache[path].is_dir:  # new local folder: use its parent
                if path == "":
                    return None  # nothing known to start from: read everything
                path = str(PurePosixPath(path).parent) if "/" in path else ""
            folders[path] = self.cache[path]
        return list(folders.values())

    def scan_dirs(self, folders: list[str]) -> dict[str, Local]:
        """Direct contents of some folders (quick sync), same shape as scan_local()."""
        out: dict[str, Local] = {}
        self.unreadable = set()
        # A replaced version still on its way to the Recycle Bin (see scan_local) is never a new file:
        # a quick pass leaves it alone on both sides, the next full pass finishes the move.
        private = {k[len("prev:"):] for k in self.state.meta_items("prev:")}
        scanned = {f.lower() for f in folders}
        self.unreadable |= {p for p in private if _parent(p).lower() in scanned}  # also when not listed
        private_key = {p.lower(): p for p in private}  # Windows: letter case doesn't count
        for rel_dir in folders:
            d = self._abs(rel_dir) if rel_dir else self.root
            if rel_dir and (_is_link(d) or not inside_root(self.root, d)):
                self.unreadable.add(rel_dir)  # a link/junction may point anywhere: never sync through it
                continue
            try:
                entries = list(os.scandir(d))
            except FileNotFoundError:
                continue  # the folder itself is gone: its parent's listing says so
            except OSError:
                self.unreadable.add(rel_dir)
                continue
            for e in entries:
                if ignored(e.name):
                    continue
                rel = f"{rel_dir}/{e.name}" if rel_dir else e.name
                recorded = private_key.get(rel.lower())
                if recorded is not None:
                    try:
                        is_dir = e.is_dir(follow_symlinks=False)
                    except OSError:
                        is_dir = False
                    if not is_dir:
                        self.unreadable.add(rel)  # left alone on both sides until a full pass decides
                        continue
                    self.state.set_meta(f"prev:{recorded}", None)  # a folder now: provably not that file
                    self.unreadable.discard(recorded)
                try:
                    if e.is_dir() and _is_link(Path(e.path)):
                        self.unreadable.add(rel)
                        continue
                    if e.is_dir():
                        out[rel] = Local(True, 0, 0.0)
                    else:
                        st = e.stat()
                        out[rel] = Local(False, st.st_size, st.st_mtime)
                except OSError:
                    self.unreadable.add(rel)
                    continue
        return out

    def run(self, changed: set[str] | None = None, _prev: dict | None = None) -> DriveResult:
        """Full sync, or a quick one limited to the folders around `changed` local paths."""
        self.cancel_flag.clear()
        self._check_cancel()  # quitting: this pass never starts
        self._hashes = {}
        self._late = None
        root_id = os.path.normcase(os.path.abspath(self.root))
        synced_root = self.state.get_meta("root")
        if synced_root is None and self.state.all():  # from before 1.0.6: which folder was it?
            cached_root = None
            if self.cache_file and self.cache_file.exists():
                try:
                    cached_root = json.loads(self.cache_file.read_text(encoding="utf-8")).get("root")
                except (OSError, ValueError):
                    cached_root = None
            if not cached_root or os.path.normcase(os.path.abspath(cached_root)) != root_id:
                synced_root = "?"  # can't show it was this folder: start afresh (a marked folder is then refused)
        if synced_root is not None and synced_root != root_id:
            self._refuse_unowned_folder()  # nothing is forgotten when the new folder can't be used anyway
            log.info("Drive: another folder than the one this database synced; starting afresh")
            self.state.reset()  # what it remembers is about the other folder
            self.cache = {}
        self.state.set_meta("root", root_id)
        self._prev = _prev if _prev is not None else dict(self.cache)  # iCloud as of the last read: identities for rename detection
        prev_before = self._prev
        if hasattr(self.remote, "incomplete"):
            self.remote.incomplete = set()
        if hasattr(self.remote, "skipped"):
            self.remote.skipped = set()
        res = DriveResult()
        base = self.state.all()  # (after the folder check above)
        everything_known = dict(base)  # before a quick pass narrows `base` to a few folders
        self._check_root(bool(base))

        pending = self.state.get_meta(PENDING_MOVE)
        scope = self._scope_folders(changed) if changed and not pending and not self._under_local_shell(changed) \
            else None  # (inside a folder kept only on this PC: iCloud has nothing to anchor a quick read)
        if scope is None:
            self.cb.status(t("Reading iCloud Drive…"))
            remote = self.remote.walk(lambda n: self.cb.status(t("Reading iCloud Drive… {n}", n=n)),
                                      want=self._wanted_dir)
            partly = set(getattr(self.remote, "incomplete", set()) or ())
            kept: dict[str, DriveItem] = {}
            for p in sorted(self.cache):  # what partly listed folders (the root too) held last time, all depths
                if p and p not in remote and (_parent(p) in partly or _parent(p) in kept):
                    kept[p] = self.cache[p]
            self.cache = {**kept, **remote}
            if pending:
                if not hasattr(self.remote, "incomplete"):
                    self.remote.incomplete = set()
                self._settle_pending_moves(remote, partly, res)
                base = self.state.all()
                everything_known = dict(base)
            self._check_cancel()
            local = self.scan_local()
            res.full = True
        else:
            self.cb.status(t("Checking iCloud Drive…"))
            dirs = {f.path for f in scope}
            remote = self.remote.walk(folders=scope)
            # A changed folder that wasn't in the cache (just created by us last pass) is now listed
            # by its parent: read it too, so its contents are compared in this same pass.
            for _ in range(8):
                extra = [r for p, r in remote.items() if r.is_dir and p not in dirs and p in changed]
                if not extra:
                    break
                dirs.update(r.path for r in extra)
                remote.update(self.remote.walk(folders=extra))
            partly = set(getattr(self.remote, "incomplete", set()) or ())
            for p in [p for p in self.cache if p and _parent(p) in dirs and p not in remote
                      and _parent(p) not in partly]:  # refresh those folders in the cache (all listed ones)
                del self.cache[p]
            self.cache.update(remote)
            local = self.scan_dirs(sorted(dirs))
            base = {p: b for p, b in base.items() if _parent(p) in dirs}
            remote = {p: r for p, r in remote.items() if p in dirs or _parent(p) in dirs}
        # Names the PC side never looks at (temp files, desktop.ini, …) are left alone in iCloud too:
        # otherwise a downloaded "x.tmp" would look deleted on the PC and be trashed in iCloud.
        self._unsynced_remote = sorted({p for p in remote if _ignored_path(p)}
                                       | self._skipped_remote(scope, remote))  # never deleted with a folder
        remote = {p: r for p, r in remote.items() if not _ignored_path(p)}
        base = {p: b for p, b in base.items() if not _ignored_path(p)}
        if self.folders is not None:  # only the chosen folders: everything else is left alone
            remote = {p: r for p, r in remote.items() if in_scope(p, r.is_dir, self.folders)}
            local = {p: lo for p, lo in local.items() if in_scope(p, lo.is_dir, self.folders)}
            for p in [p for p, b in base.items() if not in_scope(p, b.is_dir, self.folders)]:
                self.state.drop(p)  # e.g. written by a pass that ran while the choice changed
                del base[p]
        local = self._local_as_synced(local, base, remote)
        now = time.time()

        downloads: list[DriveItem] = []
        uploads: list[str] = []
        mkdir_remote: list[str] = []
        to_cloud: list[str] = []  # delete in iCloud
        to_pc: list[str] = []  # delete on PC
        mkdir_local: list[DriveItem] = []  # new folders in iCloud

        twins = self._case_twins({**self.cache, **remote}, everything_known)
        if twins:
            def in_twin(p: str) -> bool:
                return any(p == q or p.startswith(q + "/") for q in twins)

            remote = {p: r for p, r in remote.items() if not in_twin(p)}
            base = {p: b for p, b in base.items() if not in_twin(p)}
            local = {p: lo for p, lo in local.items() if not in_twin(p)}
        partial = set(getattr(self.remote, "incomplete", set()) or ())
        if partial:
            log.warning("Drive: iCloud listed %d folders only partly; no deletions decided there", len(partial))

        def listed_partly(path: str) -> bool:
            q = path
            while "/" in q:
                q = q.rsplit("/", 1)[0]
                if q in partial:
                    return True
            return "" in partial

        if scope is None:
            self._settle_shells(remote, local, partial)

        paths = sorted(set(remote) | set(local) | set(base))
        paths = [p for p in paths if p]  # never the root itself
        if self.unreadable:  # couldn't be read on this PC (access denied, disk hiccup): leave alone
            if "" in self.unreadable:
                raise SyncError(t("Couldn't read the Drive folder on this PC. Syncing has stopped so nothing "
                                  "gets deleted by mistake."))
            bad = sorted(self.unreadable)
            log.warning("Drive: skipped %d unreadable paths on this PC, e.g. %s", len(bad), bad[:3])
            paths = [p for p in paths if not any(p == u or p.startswith(u + "/") for u in bad)]
        # A chosen folder renamed or moved away on this PC is not a deletion: get it back from iCloud.
        if self.folders:
            for f in self.folders:
                if f in base and f in remote and f not in local and known_missing(self._abs(f)):
                    log.info("Drive: chosen folder %s is gone on this PC; downloading it again", f)
                    self.cb.activity("info", t("Folder {name} is missing on this PC, it is downloaded again "
                                               "(it isn't deleted in iCloud).", name=f))
                    for p in [p for p in base if p == f or p.startswith(f + "/")]:
                        self.state.drop(p)
                        del base[p]
        if scope is not None:
            paths = [p for p in paths if _parent(p) in dirs]  # only the re-read folders' contents
        # A file on this PC where iCloud has a folder: nothing is decided below it until you sort it
        # out, so the folder's files in iCloud are never taken for deleted on this PC. (The other way
        # round, a folder's files missing in iCloud are asked about as usual.)
        clashes = {p for p in paths if p in remote and p in local and remote[p].is_dir and not local[p].is_dir}

        def under_clash(p: str) -> bool:
            while "/" in p:
                p = p.rsplit("/", 1)[0]
                if p in clashes:
                    return True
            return False

        for path in paths:
            if clashes and under_clash(path):
                continue
            r, lo, b = remote.get(path), local.get(path), base.get(path)
            if partial and (listed_partly(path) or path in partial):
                continue  # iCloud didn't list this folder completely: nothing is done in it this pass
            if r and not lo and not b and self._skip_unsupported(path):
                continue
            if b and r and b.is_dir != r.is_dir:
                self.state.drop_one(path)  # replaced in iCloud by the other kind: what is there now is new
                # (only this row: files below keep theirs while their deletion may still be waiting)
                base.pop(path, None)
                b = None
            if r and lo and r.is_dir != lo.is_dir:
                res.errors += 1
                self.cb.activity("error", t("File/folder clash, skipped: {name}", name=path))
                continue
            is_dir = (r or lo or b).is_dir  # type: ignore[union-attr]
            if is_dir:
                if r and lo:
                    if not b or b.etag is None:  # new, or created by us: remember iCloud's version
                        self.state.put(Base(path, True, r.etag, None, None, None, False))
                elif r and not lo:
                    if b:
                        to_cloud.append(path)  # folder deleted on PC
                    elif self._shell_held(path, "cloud", remote, everything_known):
                        pass  # deleted on this PC; left in iCloud only for items that are never synced
                    else:
                        mkdir_local.append(r)
                elif lo and not r:
                    if b and b.etag is not None and listed_partly(path):
                        pass  # iCloud's answer for its folder was incomplete: decide next time
                    elif b and b.etag == "":
                        if scope is None:  # made by us, still not listed by a second full read: gone in iCloud
                            to_pc.append(path)  # (files never sent up stay, and the folder is made again)
                    elif b and b.etag is not None:
                        to_pc.append(path)  # folder deleted in iCloud
                    elif not b and self._shell_held(path, "pc", local, everything_known):
                        pass  # deleted in iCloud; kept on this PC only for items that are never synced
                    elif not b:
                        mkdir_remote.append(path)
                    elif scope is None:  # made by us, not listed by this full read: one more full read decides
                        self.state.put(Base(path, True, "", None, None, None, False))
                elif b:
                    self.state.drop(path)
                continue

            settled = lo is not None and not (0 <= now - lo.mtime < SETTLE_SECONDS)
            if lo is not None and not lo.is_dir and not settled:
                res.followup = True  # still being written: looked at again shortly, not in an hour
            if r and lo:
                if b is None:
                    if r.size == lo.size:
                        # maybe the same file on both sides (first sync into a folder that has files):
                        # downloaded and compared; kept as it is when identical, both kept otherwise
                        downloads.append(r)
                    else:
                        self._conflict(path, r, res, downloads)
                    continue
                if b.pending:  # a replacement upload that didn't finish: the PC version still has to go up
                    if b.etag is not None and r.etag != b.etag:
                        # The version this upload replaced went to Recently Deleted; what is there now is
                        # either this upload (its reply got lost) or another device's save. Same size:
                        # downloaded and compared (identical = ours); otherwise both are kept.
                        if r.size == lo.size:
                            # compared as a file met on both sides for the first time: identical =
                            # this upload; different = both kept (the PC edit is never taken for the
                            # version that was synced)
                            self.state.drop(path)
                            base.pop(path, None)
                            downloads.append(r)
                        else:
                            self._conflict(path, r, res, downloads)
                    elif settled:
                        uploads.append(path)
                    continue
                # iCloud's version tag differs from the synced one (or isn't known yet, after our own
                # upload): its content is unknown until it has been looked at. Size and time prove nothing.
                if b.etag is None and not b.pending:
                    told = self.state.get_meta(RECEIPT + path)
                    if told and r.etag == told:
                        # the version iCloud named when it confirmed our upload: synced, no download
                        b = Base(path, False, r.etag, r.size, b.lmtime, b.lsize, False, b.hash)
                        self.state.put(b)
                        base[path] = b
                    if told:
                        self.state.set_meta(RECEIPT + path, None)
                remote_changed = b.etag is None or r.etag != b.etag
                local_changed = (lo.size, round(lo.mtime, 2)) != (b.lsize, round(b.lmtime or 0, 2))
                if remote_changed and local_changed and self._is_synced_content(b, r):
                    # iCloud still holds exactly what was synced (only its tag moved: renamed, moved,
                    # our own upload): remember the tag; the change on this PC goes up as usual
                    b = Base(path, False, r.etag, r.size, b.lmtime, b.lsize, False, b.hash)
                    self.state.put(b)
                    base[path] = b
                    remote_changed = False
                if remote_changed and local_changed:
                    if r.size == lo.size:
                        downloads.append(r)  # compared when downloaded: identical = already ours, else both kept
                    else:
                        self._conflict(path, r, res, downloads)
                elif remote_changed:
                    downloads.append(r)  # (an identical download leaves the PC file as it is)
                elif local_changed and settled:
                    uploads.append(path)
            elif r and not lo:
                if b and not b.pending and (b.etag is None or r.etag != b.etag):
                    if self._is_synced_content(b, r):
                        to_cloud.append(path)  # deleted on PC; iCloud has exactly what was deleted here
                    else:
                        self.state.drop(path)
                        downloads.append(r)  # deleted on PC, but edited in iCloud since: keep the edit
                elif b and not b.pending:
                    to_cloud.append(path)  # deleted on PC
                else:
                    downloads.append(r)  # new in iCloud
            elif lo and not r and b and listed_partly(path):
                pass  # iCloud's answer for its folder was incomplete: never read that as "deleted"
            elif lo and not r:
                pc_same = None  # PC content against the synced fingerprint (None: not looked at)
                if b and not b.pending and b.etag is not None and b.hash and \
                        (lo.size, round(lo.mtime, 2)) == (b.lsize, round(b.lmtime or 0, 2)):
                    try:
                        pc_same = self._pc_has_synced_content(b, self._abs(path), strict=True)
                    except OSError as exc:  # can't be read now: nothing decided, looked at next time
                        log.info("Drive: %s couldn't be read now: %s", path, brief(exc))
                        continue
                if b and b.pending:
                    if settled:
                        uploads.append(path)  # replacement upload that didn't finish: retry
                elif b and b.etag is not None and (lo.size, round(lo.mtime, 2)) != (b.lsize, round(b.lmtime or 0, 2)):
                    self.state.drop(path)  # deleted in iCloud, but edited on this PC since: keep the edit
                    if settled:
                        uploads.append(path)
                elif b and b.etag is not None and pc_same is False:
                    self.state.drop(path)  # deleted in iCloud, but changed here keeping size and time: keep it
                    uploads.append(path)
                elif b and b.etag is not None:
                    to_pc.append(path)  # deleted in iCloud
                elif b and (lo.size, round(lo.mtime, 2)) != (b.lsize, round(b.lmtime or 0, 2)):
                    self.state.drop(path)  # changed on the PC since our upload: the new version goes up
                    if settled:
                        uploads.append(path)
                elif b and scope is None:
                    # uploaded earlier and still not in iCloud on a full read: remember that; if the
                    # next full read doesn't list it either, it was deleted in iCloud
                    self.state.put(Base(path, False, "", b.size, b.lmtime, b.lsize, False))
                elif b:
                    pass  # uploaded last run, iCloud hasn't listed it yet: wait
                elif settled:
                    uploads.append(path)  # new on PC
            elif b:
                self.state.drop(path)

        if scope is None and not partial:
            self._scrub(remote, local, base, uploads)
        if (scope is not None and (to_cloud or to_pc) and self._only_waiting(to_cloud, to_pc)
                and not self._could_be_moved(to_cloud, to_pc, uploads, downloads, local, base)):
            # Only deletions that already wait for your answer (asked, not OK'd yet): nothing new to
            # decide, so no full read for them; they keep waiting and the rest of the pass goes on.
            to_cloud.clear()
            to_pc.clear()
            still_asked = True
        else:
            still_asked = False
        if scope is not None and (to_cloud or to_pc):
            # Something went away on one side. A quick pass sees only a few folders: how many deletions
            # there are in all (the limit for asking you), what is inside a folder, or where it went if
            # it was renamed is only known from a full read. Deletions are only ever decided there.
            log.info("Drive: something was removed or renamed; doing a full read instead of a quick one")
            return self.run(_prev=prev_before)
        self._partial = partial
        # A rename or move looks like "one deleted + one new": do it as a rename instead.
        self._moves(remote, local, base, downloads, uploads, mkdir_remote, mkdir_local, to_cloud, to_pc, res)
        deferred = getattr(self, "_deferred", set())
        if deferred:  # a rename that touches a partly listed folder waits as a whole: nothing half-done
            def held(p: str) -> bool:
                return any(p == d or p.startswith(d + "/") for d in deferred)

            to_cloud = [p for p in to_cloud if not held(p)]
            to_pc = [p for p in to_pc if not held(p)]
            mkdir_remote = [p for p in mkdir_remote if not held(p)]
            uploads = [p for p in uploads if not held(p)]
            mkdir_local = [r for r in mkdir_local if not held(r.path)]
            downloads = [r for r in downloads if not held(r.path)]
        for r in mkdir_local:
            try:
                self._abs(r.path).mkdir(parents=True, exist_ok=True)
                hide_like_mac(self._abs(r.path))
            except OSError as exc:
                res.errors += 1
                self.cb.activity("error", t("Couldn't create folder ({name}): {error}", name=r.path, error=brief(exc)))
                continue
            self.state.put(Base(r.path, True, r.etag, None, None, None, False))

        if partial:
            def touches_partial(p: str) -> bool:
                return any(q == p or q.startswith(p + "/") for q in partial) or listed_partly(p)

            for lst in (to_cloud, to_pc):
                for p in [p for p in lst if base.get(p) is not None and base[p].is_dir and touches_partial(p)]:
                    # iCloud didn't list everything inside: nothing in it is deleted this pass
                    lst[:] = [x for x in lst if not (x == p or x.startswith(p + "/"))]
        if self.folders:
            def leads_to_chosen(p: str) -> bool:
                return any(f.startswith(p + "/") for f in self.folders)

            for lst in (to_cloud, to_pc):
                for p in [p for p in lst if base.get(p) is not None and base[p].is_dir and leads_to_chosen(p)]:
                    lst.remove(p)  # holds folders that aren't synced: never deleted as a whole
                    self.state.drop_one(p)  # it is re-created on the side where it went missing
        # Deleting a folder also covers everything inside it.
        to_cloud = _collapse(to_cloud)
        to_pc = _collapse(to_pc)
        to_pc = self._keep_unsynced_local(to_pc, base)
        to_cloud = self._keep_unsynced_remote(to_cloud, remote, base)
        self._deletions(to_cloud, to_pc, remote, base, res)
        known_now = self.state.all() if res.pending_to_cloud else {}
        ordered = sorted(remote) if res.pending_to_cloud else []
        shapes = {p: self._cloud_shape(p, remote, known_now, ordered=ordered) for p in res.pending_to_cloud}
        pc_shapes = {p: self._pc_shape(p) for p in res.pending_to_pc}
        if scope is None:  # approvals only count for deletions still waiting, on the side and items asked about
            self.approved = {p for p in self.approved
                             if (p in res.pending_to_cloud and p in self._asked_cloud
                                 and shapes[p] is not None and self._asked_cloud[p] == shapes[p])
                             or (p in res.pending_to_pc and p in self._asked_pc
                                 and pc_shapes[p] is not None and self._asked_pc[p] == pc_shapes[p])}
            self._approved_side = {p: v for p, v in self._approved_side.items() if p in self.approved}
            new_cloud, new_pc = shapes, pc_shapes
        elif res.pending_to_cloud or res.pending_to_pc:  # a quick pass sees only part: it adds, never forgets
            new_cloud, new_pc = {**self._asked_cloud, **shapes}, {**self._asked_pc, **pc_shapes}
        else:
            new_cloud, new_pc = self._asked_cloud, self._asked_pc
        if new_cloud != self._asked_cloud or new_pc != self._asked_pc:
            self.question_gen += 1  # another question: answers to the earlier one don't count
            self.side_gen["cloud"] += new_cloud != self._asked_cloud  # per side: answering one side
            self.side_gen["pc"] += new_pc != self._asked_pc  # leaves the other one valid
            self._asked_cloud, self._asked_pc = new_cloud, new_pc
        if res.pending_to_cloud or res.pending_to_pc:
            self._late = (remote, base, res)
            if self.on_pending:
                self.on_pending(list(res.pending_to_cloud), list(res.pending_to_pc))
        elif still_asked:  # the question shown stays as it was (nothing about it was decided here)
            res.pending_to_cloud, res.pending_to_pc = list(self._asked_cloud), list(self._asked_pc)

        # Folders first, then files.
        for path in mkdir_remote:
            parent = remote.get(str(PurePosixPath(path).parent) if "/" in path else "")
            if parent is None:
                continue  # parent is being created this round; next run creates this one
            self._check_cancel()  # (before every change in iCloud)
            try:
                self.remote.mkdir(parent, PurePosixPath(path).name)
                self.state.put(Base(path, True, None, None, None, None, False))
                res.followup = True
                try:
                    made = self.remote.walk(folders=[parent]).get(path)
                except (AuthRequired, RemoteUnavailable):
                    raise
                except Exception:  # noqa: BLE001 - the next pass finds it
                    made = None
                if made is not None and made.is_dir:  # its contents (and sub-folders) follow in this pass
                    remote[path] = made
                    self.cache[path] = made
            except (AuthRequired, RemoteUnavailable):
                raise
            except Exception as exc:  # noqa: BLE001
                res.errors += 1
                self.cb.activity("error", t("Couldn't create folder in iCloud ({name}): {error}", name=path, error=brief(exc)))

        downloads = self._without_case_twins(downloads, local)
        self._downloads(downloads, base, res)
        self.apply_approved_deletions()
        self._uploads(uploads, remote, base, local, res)

        if res.uploaded:
            res.followup = True
        self._save_cache()
        few = len(res.down_files) + len(res.up_files) <= 8
        if res.downloaded and few:  # a few files in this pass: one entry each
            for p in res.down_files:
                self.cb.activity("download", t("Drive: downloaded: {name}", name=p), str(self._abs(p)))
        elif res.downloaded:
            self.cb.activity("download", t("Drive: downloaded {n} files", n=res.downloaded), str(self.root))
        if res.uploaded and few:
            for p in res.up_files:
                self.cb.activity("upload", t("Drive: uploaded: {name}", name=p), str(self._abs(p)))
        elif res.uploaded:
            self.cb.activity("upload", t("Drive: uploaded {n} files", n=res.uploaded), str(self.root))
        self.state_set_last_sync()
        return res

    def _case_twins(self, remote: dict[str, DriveItem], base: dict[str, Base] | None = None) -> list[str]:
        """iCloud paths that Windows would merge with another spelling (Docs/ and docs/): the first
        spelling syncs, the others are left in iCloud with a message."""
        first: dict[str, str] = {}
        twins: list[str] = []
        synced = base or {}
        for p in sorted(remote, key=lambda x: (x.count("/"), x not in synced, x)):  # the synced spelling wins
            if not p or any(p.startswith(q + "/") for q in twins):
                continue
            key = p.lower()
            if key in first and first[key] != p:
                twins.append(p)
                if p not in self.unsupported:
                    self.unsupported.add(p)
                    self.cb.activity("info", t("Left in iCloud, {reason}: {name}",
                                               reason=t("another item has the same name in different letter case"),
                                               name=p))
            else:
                first.setdefault(key, p)
        return twins

    def _without_case_twins(self, downloads: list[DriveItem], local: dict[str, Local]) -> list[DriveItem]:
        """Windows sees "Report.txt" and "report.txt" as one file: download only the first of such
        names, and never one that would land on a differently-spelled file already here."""
        here = {p.lower(): p for p in local}
        seen: dict[str, str] = {}
        out: list[DriveItem] = []
        for r in downloads:
            key = r.path.lower()
            other = seen.get(key) or (here.get(key) if here.get(key) != r.path else None)
            if other:
                if r.path not in self.unsupported:
                    self.unsupported.add(r.path)
                    self.cb.activity("info", t("Left in iCloud, {reason}: {name}",
                                               reason=t("another item has the same name in different letter case"),
                                               name=r.path))
                continue
            seen[key] = r.path
            out.append(r)
        return out

    def _keep_unsynced_local(self, to_pc: list[str], base: dict[str, Base]) -> list[str]:
        """A folder deleted in iCloud that holds new or edited files on this PC: those files stay
        (and go up again); only what is unchanged since the last sync goes to the Recycle Bin."""
        out: list[str] = []
        known = self.state.all() if to_pc else {}  # a quick pass holds only a slice in `base`
        known_low = {k.lower(): v for k, v in known.items()}  # (Windows matches names in any letter case)
        shells = self.state.meta_items("shell:") if to_pc else {}

        def record(rel: str, is_dir: bool = True) -> Base | None:
            b = base.get(rel) or known.get(rel)
            if b is None:
                b = known_low.get(rel.lower())  # (folders spelled differently are the same folders;
                if b is not None and not is_dir and b.path.rsplit("/", 1)[-1] != rel.rsplit("/", 1)[-1]:
                    return None  # a file whose own name differs in letter case is not proven the same)
            return b

        for p in to_pc:
            b = base.get(p)
            if b is None or not b.is_dir:
                out.append(p)
                continue
            dirty, clean = False, []
            edited = False  # dirty because of synced content changed or added here (not only never-synced items)
            drops: list[str] = []
            shell: list[str] = []  # the synced folders below, as they are now
            unreadable: list[str] = []
            for dirpath, _dirs, files in os.walk(self._abs(p), onerror=lambda e: unreadable.append(str(e))):
                rel_dir = Path(dirpath).relative_to(self.root).as_posix()
                for d in list(_dirs):
                    if _is_link(Path(dirpath) / d):
                        unreadable.append(f"link: {Path(dirpath) / d}")
                    elif _ignored_path(f"{rel_dir}/{d}"):
                        dirty = True  # a folder that is never synced (even empty): it and its parents stay
                        _dirs.remove(d)
                    else:
                        shell.append(f"{rel_dir}/{d}")
                        was = record(f"{rel_dir}/{d}")
                        if was is None or not was.is_dir:  # (a folder where a file was synced is new too)
                            dirty = edited = True  # a folder made on this PC since: never deleted with it
                for f in files:
                    rel = f"{rel_dir}/{f}"
                    if _ignored_path(rel):
                        dirty = True  # never synced (a .tmp draft, …): it and its folder stay
                        continue
                    fb = record(rel, is_dir=False)
                    try:
                        st = os.stat(Path(dirpath) / f)
                        same = fb is not None and (st.st_size, round(st.st_mtime, 2)) == \
                            (fb.lsize, round(fb.lmtime or 0, 2)) and self._pc_has_synced_content(
                                fb, Path(dirpath) / f, strict=True)
                    except OSError as exc:  # can't be looked at: the folder is decided another time
                        unreadable.append(str(exc))
                        continue
                    if not same:
                        dirty = edited = True
                        if fb is not None:
                            drops.append(fb.path)
                    else:
                        clean.append(fb.path)  # (under the spelling it was synced with)
            if unreadable:
                log.warning("Drive: %s couldn't be read completely; not deleted (%s)", p, unreadable[:2])
                continue  # can't check what's inside: decide another time (nothing forgotten)
            if not dirty:
                out.append(p)
                continue
            for rel in drops:
                self.state.drop(rel)  # edited here: goes up again as a new file
            if edited:
                log.info("Drive: %s was deleted in iCloud but has changes on this PC; keeping them", p)
                self.cb.activity("info", t("Deleted in iCloud, but changed on this PC: kept {name}", name=p))
                for key in [k for k in shells if k.lower() == f"shell:pc:{p}".lower()]:
                    self.state.set_meta(key, None)
            else:
                # Only items Skyfold never syncs (desktop.ini, an Office lock file, a .tmp draft) keep
                # the folder: it stays on this PC, and it isn't created in iCloud again for them.
                if f"shell:pc:{p}" not in shells:
                    log.info("Drive: %s was deleted in iCloud; kept on this PC only for items that aren't synced", p)
                    self.cb.activity("info", t("Deleted in iCloud; {name} stays on this PC only for files that "
                                               "aren't synced (it isn't created in iCloud again)", name=p))
                self.state.set_meta(f"shell:pc:{p}", json.dumps(sorted(shell)))
                if clean:
                    # Synced files are still in it: they go first (maybe after your confirmation). Every
                    # record stays until they are really gone, so the next pass decides the same again,
                    # never "new on this PC".
                    out.extend(clean)
                    continue
            for q in [q for q in list(base) if q == p or q.startswith(p + "/")]:
                if base[q].is_dir:  # only the folder's own row: files waiting for deletion keep theirs
                    self.state.drop_one(q)  # recreated in iCloud on the next pass (edited), or left here (shell)
            out.extend(clean)
        return out

    def _shell_held(self, path: str, side: str, items: dict, known: dict[str, Base]) -> bool:
        """A folder deleted on one side and kept on the other only for items that are never synced:
        it isn't created again where it was deleted. Something synced added inside it reopens it."""
        low_path = path.lower()  # (a kept folder on this PC is matched the way Windows matches names;
        for key, val in self.state.meta_items(f"shell:{side}:").items():  # iCloud's side is matched exactly)
            top = key[len(f"shell:{side}:"):]
            if side == "pc":
                if low_path != top.lower() and not low_path.startswith(top.lower() + "/"):
                    continue
            elif path != top and not path.startswith(top + "/"):
                continue
            try:
                kept = set(json.loads(val or "[]"))
            except (ValueError, TypeError):
                kept = set()
            if side == "pc":
                low_known = self._lower_known(known)
                kept = {k.lower() for k in kept}
                prefix = top.lower() + "/"
                added = [q for q in items if q.lower().startswith(prefix) and q.lower() not in kept
                         and q.lower() not in low_known]
            else:
                added = [q for q in items if q.startswith(top + "/") and q not in kept and q not in known]
            if added:
                log.info("Drive: %s has new content (%s); synced again", top, added[0])
                self.state.set_meta(key, None)
                return False
            return True
        return False

    def _local_as_synced(self, local: dict[str, "Local"], base: dict[str, Base],
                         remote: dict[str, DriveItem]) -> dict[str, "Local"]:
        """Windows sees TRIP and Trip as one name. A PC path spelled only differently from a synced
        item that iCloud no longer has is that item (its records, and a deletion that may be waiting
        for your OK, follow it): it keeps the synced spelling. Nothing to rename in iCloud there."""
        by_low: dict[str, str] = {}
        for q in base:
            if q not in remote:
                by_low.setdefault(q.lower(), q)
        if not by_low:
            return local
        out: dict[str, Local] = {}
        for p, lo in local.items():
            q = by_low.get(p.lower())
            if q is not None and q != p and p not in base and p not in remote and q not in local:
                if lo.is_dir or q.rsplit("/", 1)[-1] == p.rsplit("/", 1)[-1]:
                    out[q] = lo  # only its folders are spelled differently: the same item
                    continue
                # The file's own name differs in letter case: maybe that file renamed, maybe a new
                # copy (same content proves nothing). While it still has the synced content it is
                # left alone (neither uploaded nor deleted). Changed since, or never synced content:
                # your new work under its new name, which goes up (it doesn't wait forever).
                try:
                    same = self._pc_has_synced_content(base[q], self._abs(p), strict=True)
                except OSError:
                    same = True  # can't be read now: left alone until it can
                if same:
                    self.unreadable |= {p, q}
                out[p] = lo
            else:
                out[p] = lo
        return out

    def _lower_known(self, known: dict[str, Base]) -> set[str]:
        cached = getattr(self, "_known_low", None)
        if cached is None or cached[0] is not known:
            cached = (known, {k.lower() for k in known})
            self._known_low = cached
        return cached[1]

    def _settle_shells(self, remote: dict[str, DriveItem], local: dict[str, "Local"], partial: set[str]) -> None:
        """A kept folder that is back on the side it was deleted from, or gone where it was kept:
        nothing left to hold (full passes only). Subfolders gone where it was kept are struck from its
        list (one made again later under that name is new). Outside the chosen folders nothing is
        decided: not listed there is not gone."""
        local_low = {p.lower() for p in local}
        remote_low = {p.lower() for p in remote}
        for key, val in self.state.meta_items("shell:").items():
            side, top = key[len("shell:"):].split(":", 1)
            if self.folders is not None and not in_scope(top, True, self.folders):
                continue
            if side == "pc":
                done = top.lower() in remote_low or (top.lower() not in local_low and known_missing(self._abs(top)))
                here = local_low
            else:
                if partial:
                    continue  # iCloud's answer was incomplete: nothing decided about kept folders
                done = top.lower() in local_low or top not in remote
                here = {p.lower() for p in remote}
            if done:
                self.state.set_meta(key, None)
                continue
            try:
                kept = json.loads(val or "[]")
            except (ValueError, TypeError):
                kept = []
            if side == "pc":  # (one that can't be looked at now is kept)
                still = [k for k in kept if k.lower() in here or not known_missing(self._abs(k))]
            else:
                still = [k for k in kept if k.lower() in here]
            if still != kept:
                self.state.set_meta(key, json.dumps(still))

    def _under_local_shell(self, paths: set[str]) -> bool:
        tops = [k[len("shell:pc:"):].lower() for k in self.state.meta_items("shell:pc:")]
        return any(p.lower() == t or p.lower().startswith(t + "/") for p in paths for t in tops)

    def _skipped_remote(self, scope: list[DriveItem] | None, remote: dict[str, DriveItem]) -> set[str]:
        """iCloud items in listed folders that the PC never shows (hidden ones, names with a slash).
        Remembered between passes: a quick pass that only knows a folder by its rows still keeps it."""
        try:
            before = set(json.loads(self.state.get_meta(SKIPPED_REMOTE) or "[]"))
        except (ValueError, TypeError):
            before = set()
        known = before
        now = set(getattr(self.remote, "skipped", set()) or ())
        if scope is None:
            partly = set(getattr(self.remote, "incomplete", set()) or ())
            known = {p for p in known if _parent(p) in partly} | now
        else:
            dirs = {f.path for f in scope} | {p for p, r in remote.items() if r.is_dir}
            known = {p for p in known if _parent(p) not in dirs} | now
        if known != before:
            self.state.set_meta(SKIPPED_REMOTE, json.dumps(sorted(known)) if known else None)
        return known

    def _keep_unsynced_remote(self, to_cloud: list[str], remote: dict[str, DriveItem],
                              base: dict[str, Base]) -> list[str]:
        """A folder deleted on this PC that holds new or edited files in iCloud: those come back
        down; only what is unchanged since the last sync goes to Recently Deleted."""
        import bisect

        out: list[str] = []
        known = self.state.all() if to_cloud else {}
        shells = self.state.meta_items("shell:") if to_cloud else {}
        ordered = sorted(remote)

        def below(p: str) -> list[str]:
            lo = bisect.bisect_left(ordered, p + "/")
            hi = bisect.bisect_left(ordered, p + "0")  # "0" sorts right after "/"
            return ordered[lo:hi]

        for p in to_cloud:
            b = base.get(p)
            if b is None or not b.is_dir:
                out.append(p)
                continue
            all_below = below(p)
            inside = [q for q in all_below if not remote[q].is_dir]
            unsynced = [q for q in getattr(self, "_unsynced_remote", ()) if q.startswith(p + "/")]
            if not all_below and not unsynced:
                out.append(p)  # nothing inside listed (e.g. a quick pass): judged by its files' rows
                continue
            changed = [q for q in inside if known.get(q) is None
                       or ((known[q].etag is None or remote[q].etag is None or known[q].etag != remote[q].etag)
                           and not self._is_synced_content(known[q], remote[q]))]
            if not changed and not unsynced:  # (never-synced items, a .tmp draft, … keep the folder)
                out.append(p)
                continue
            unchanged = [q for q in inside if q not in changed]
            if changed:
                log.info("Drive: %s was deleted on this PC but has changes in iCloud; keeping them", p)
                self.cb.activity("info", t("Deleted on this PC, but changed in iCloud: kept {name}", name=p))
                if f"shell:cloud:{p}" in shells:
                    self.state.set_meta(f"shell:cloud:{p}", None)
            else:
                # Only items Skyfold never syncs keep the folder in iCloud: it isn't made on this PC again.
                if f"shell:cloud:{p}" not in shells:
                    log.info("Drive: %s was deleted on this PC; kept in iCloud only for items that aren't synced", p)
                    self.cb.activity("info", t("Deleted on this PC; {name} stays in iCloud only for files that "
                                               "aren't synced (it isn't created on this PC again)", name=p))
                self.state.set_meta(f"shell:cloud:{p}",
                                    json.dumps(sorted(q for q in all_below if remote[q].is_dir)))
                if unchanged:
                    # Synced files are still in it: they go first (maybe after your confirmation). Every
                    # record stays until they are really gone, so the next pass decides the same again,
                    # never "new in iCloud".
                    out.extend(unchanged)
                    continue
            for q in [q for q in list(base) if q == p or q.startswith(p + "/")]:
                if q not in unchanged:  # (an unchanged file keeps its record until it is really deleted)
                    self.state.drop_one(q)  # comes back down on the next pass
            out.extend(unchanged)
        return out

    def state_set_last_sync(self) -> None:
        self.last_sync = now_iso()

    # steps ---------------------------------------------------------------------------
    def _conflict(self, path: str, r: DriveItem, res: DriveResult, downloads: list[DriveItem]) -> None:
        """Both sides changed: iCloud's copy keeps the name; the PC copy is renamed and uploaded later."""
        src = self._abs(path)
        for _ in range(20):
            dst_rel = self._conflict_name(path)
            try:
                os.rename(src, self._abs(dst_rel))  # refuses a name taken meanwhile (never overwrites)
                break
            except FileExistsError:
                continue  # another program saved a file there just now: pick another name
            except OSError as exc:
                res.errors += 1
                self.cb.activity("error", t("Couldn't resolve conflict ({name}): {error}", name=path, error=brief(exc)))
                return
        else:
            res.errors += 1
            self.cb.activity("error", t("Couldn't resolve conflict ({name}): {error}", name=path,
                                        error="no free name"))
            return
        self.state.drop(path)  # iCloud's copy is now simply "new in iCloud" until it is downloaded
        res.conflicts += 1
        self.cb.activity("info", t("Changed on both sides, kept both: {name}", name=dst_rel))
        downloads.append(r)

    # renames and moves -------------------------------------------------------------------
    def _moves(self, remote: dict[str, DriveItem], local: dict[str, Local], base: dict[str, Base],
               downloads: list[DriveItem], uploads: list[str], mkdir_remote: list[str],
               mkdir_local: list[DriveItem], to_cloud: list[str], to_pc: list[str], res: DriveResult) -> None:
        """Pair "gone here" with "new there" by content (folders: same files and sizes inside;
        files: same size and time) and rename/move instead of uploading or downloading again and
        deleting. Only unambiguous one-to-one pairs are used; anything else syncs as before."""

        def under(paths, top):
            return [p for p in paths if p.startswith(top + "/")]

        partial = getattr(self, "_partial", set()) or set()
        self._deferred: set[str] = set()

        def touches_partial(*tops: str) -> bool:
            return any(_overlaps(q, top) for q in partial for top in tops)

        def drop_under(lst, top, key=lambda x: x):
            lst[:] = [x for x in lst if not (key(x) == top or key(x).startswith(top + "/"))]

        # --- folders renamed/moved on the PC -> do the same in iCloud ---
        gone = [p for p in to_cloud if base.get(p) and base[p].is_dir]
        new = [p for p in mkdir_remote if p not in remote]
        if gone and new:
            sig_old = {g: _signature({p[len(g) + 1:]: base[p].lsize for p in under(base, g) if not base[p].is_dir})
                       for g in gone}
            sig_new = {n: _signature({p[len(n) + 1:]: local[p].size for p in under(local, n) if not local[p].is_dir})
                       for n in new}
            for g, n in _pairs(sig_old, sig_new):
                if touches_partial(g, n):
                    self._deferred |= {g, n}  # decided once iCloud lists it completely
                    continue
                if any(n.startswith(m + "/") for m in new if m != n):
                    continue
                if not self._same_content_moved(base, g, n, remote):
                    continue  # look-alike, or not provably the same files: synced as add + delete
                moved = self._remote_move(remote, g, n, res)
                if moved == "partial":  # finished next pass; nothing else under these two now
                    drop_under(to_cloud, g)
                    drop_under(mkdir_remote, n)
                    drop_under(uploads, n)
                    drop_under(downloads, g, key=lambda r: r.path)
                    continue
                if not moved:
                    continue
                with self.state.transaction():  # rows and the move's record together
                    self._rebase(base, g, n, keep_etags=False)
                    self._journal_put(self._moved_ident, None)
                drop_under(to_cloud, g)
                drop_under(mkdir_remote, n)
                drop_under(uploads, n)
                drop_under(downloads, g, key=lambda r: r.path)
                res.followup = True

        # --- folders renamed/moved in iCloud -> do the same on the PC ---
        gone = [p for p in to_pc if base.get(p) and base[p].is_dir]
        new = [r.path for r in mkdir_local]
        if gone and new:
            sig_old = {g: _signature({p[len(g) + 1:]: base[p].size for p in under(base, g) if not base[p].is_dir})
                       for g in gone}
            sig_new = {n: _signature({p[len(n) + 1:]: remote[p].size for p in under(remote, n) if not remote[p].is_dir})
                       for n in new}
            for g, n in _pairs(sig_old, sig_new):
                if touches_partial(g, n):
                    self._deferred |= {g, n}
                    continue
                if not self._same_item(g, n, remote) or not self._pc_move(g, n, res, remote):
                    continue  # not provably the same folder (e.g. deleted and a look-alike created)
                with self.state.transaction():  # rows and the move's record together
                    self._rebase(base, g, n, keep_etags=True, remote=remote)
                    self._journal_put(self._pc_key, None)
                drop_under(to_pc, g)
                drop_under(mkdir_local, n, key=lambda r: r.path)
                drop_under(downloads, n, key=lambda r: r.path)

        # --- single files renamed/moved on the PC ---
        gone = [p for p in to_cloud if base.get(p) and not base[p].is_dir and p in remote
                and base[p].etag is not None and remote[p].etag == base[p].etag]
        new = [p for p in uploads if p not in base and p not in remote and p in local]
        if gone and new:
            key_old = {g: (base[g].lsize, round(base[g].lmtime or 0, 2)) for g in gone}
            key_new = {n: (local[n].size, round(local[n].mtime, 2)) for n in new}
            for g, n in _pairs(key_old, key_new):
                if not self._same_content_moved(base, g, n):
                    continue  # same size and time, but not provably the same file: add + delete
                moved = self._remote_move(remote, g, n, res)
                if moved == "partial":  # finished next pass; nothing else for these two now
                    to_cloud.remove(g)
                    uploads.remove(n)
                    continue
                if not moved:
                    continue
                lo = local[n]
                with self.state.transaction():  # rows and the move's record together
                    self.state.drop(g)
                    self.state.put(Base(n, False, base[g].etag, base[g].size, lo.mtime, lo.size, False,
                                        base[g].hash))
                    self._journal_put(self._moved_ident, None)
                to_cloud.remove(g)
                uploads.remove(n)

        # --- single files renamed/moved in iCloud ---
        gone = [p for p in to_pc if base.get(p) and not base[p].is_dir and p in local
                and (local[p].size, round(local[p].mtime, 2)) == (base[p].lsize, round(base[p].lmtime or 0, 2))]
        new = [r for r in downloads if r.path not in base and r.path not in local]
        if gone and new:
            key_old = {g: (base[g].size, round(base[g].lmtime or 0)) for g in gone}
            key_new = {r.path: (r.size, round(r.mtime or 0)) for r in new}
            for g, n in _pairs(key_old, key_new):
                if not self._same_item(g, n, remote) or not self._pc_move(g, n, res, remote):
                    continue
                r = remote[n]
                b = base[g]
                with self.state.transaction():  # rows and the move's record together
                    self.state.drop(g)
                    # everything stays as synced (a rename keeps size and time): an edit saved right
                    # after the rename is uploaded, and iCloud's new tag is taken once its content is seen
                    self.state.put(Base(n, False, b.etag, b.size, b.lmtime, b.lsize, False, b.hash))
                    self._journal_put(self._pc_key, None)
                to_pc.remove(g)
                downloads[:] = [d for d in downloads if d.path != n]

    def _same_content_moved(self, base: dict[str, Base], old: str, new: str,
                            remote: dict[str, DriveItem] | None = None) -> bool:
        """Is what is now at `new` on this PC provably what was synced at `old`? Every file's content
        must match its stored fingerprint; a missing fingerprint or an unreadable file proves nothing."""
        b = base.get(old)
        if b is None:
            return False
        if remote is not None and b.is_dir:
            # iCloud's folder must hold exactly what was synced (same items, same versions): anything
            # added or changed there since never travels with a move it was not part of
            below = {q for q in remote if q.startswith(old + "/")}
            if below != {q for q in base if q.startswith(old + "/")}:
                return False
            for q in below:
                r, bq = remote[q], base[q]
                if r.is_dir != bq.is_dir or (not r.is_dir and (not r.etag or r.etag != bq.etag)):
                    return False
        pairs = [(old, new)] if not b.is_dir else \
            [(p, new + p[len(old):]) for p, q in base.items() if p.startswith(old + "/") and not q.is_dir]
        if not pairs:
            return False
        for p, q in pairs:
            self._check_cancel()  # (a large folder takes a while to read)
            fp = base[p].hash
            if not fp:
                return False
            try:
                if file_hash(self._abs(q)) != fp:
                    return False
            except OSError:
                return False
        return True

    def _same_item(self, old: str, new: str, remote: dict[str, DriveItem]) -> bool:
        """iCloud keeps an item's id when it is renamed or moved; a deleted and re-created look-alike
        gets a new one."""
        before, now = self._prev.get(old), remote.get(new)
        return bool(before and now and before.docwsid and before.docwsid == now.docwsid)

    def _remote_move(self, remote: dict[str, DriveItem], old: str, new: str, res: DriveResult) -> bool | str:
        """Rename/move in iCloud. True: done. False: iCloud refused before anything changed (sync as
        add + delete). "partial": iCloud changed part of it, or the answer is unknown; the database
        follows what is known and the next pass finishes it (never a second copy)."""
        item = remote.get(old)
        parent = remote.get(_parent(new))
        if item is None or parent is None or not parent.is_dir:
            return False  # the new place doesn't exist in iCloud yet: sync as add + delete
        old_name, new_name = PurePosixPath(old).name, PurePosixPath(new).name
        steps: list[str] = []
        if _parent(old) != _parent(new) and old_name != new_name:
            via_target = f"{_parent(new)}/{old_name}" if _parent(new) else old_name
            via_source = f"{_parent(old)}/{new_name}" if _parent(old) else new_name
            if via_target in remote or via_target.lower() in {p.lower() for p in remote}:
                if via_source in remote or via_source.lower() in {p.lower() for p in remote}:
                    return False  # both ways pass through a taken name: sync as add + delete
                steps = ["rename", "move"]  # the target folder has the old name: rename where it is first
            else:
                steps = ["move", "rename"]
        elif _parent(old) != _parent(new):
            steps = ["move"]
        elif old_name != new_name:
            steps = ["rename"]
        if not steps:
            return False
        # Written down before iCloud is touched: after a crash or an unknown answer the next full
        # pass finds the item by its id and the database follows where it really is.
        ident = item.docwsid or item.drivewsid
        rows = self.state.all()
        entry = {"old": old, "new": new, "at": old,
                 "rows": [[p[len(old):], b.is_dir, b.etag, b.size, b.lmtime, b.lsize, b.hash]
                          for p, b in rows.items() if p == old or p.startswith(old + "/")]}
        with self.state.transaction():
            self._journal_put(ident, entry)
        here = old
        for n, step in enumerate(steps):
            if self._stopped():  # quitting/paused between the steps: nothing more is changed in iCloud
                if n == 0:
                    with self.state.transaction():
                        self._journal_put(ident, None)  # nothing was changed
                else:  # half done: stays written down with where it is now, finished next time
                    self._move_rows(old, here, None, ident, {**entry, "at": here})
                    self._forget_cached(old)
                raise Cancelled()
            try:
                if step == "move":
                    got = self.remote.move(item, parent)
                    here = f"{parent.path}/{PurePosixPath(here).name}" if parent.path else PurePosixPath(here).name
                else:
                    got = self.remote.rename(item, new_name)
                    here = f"{_parent(here)}/{new_name}" if _parent(here) else new_name
            except Exception as exc:  # noqa: BLE001
                unknown = isinstance(exc, (AuthRequired, RemoteUnavailable)) or is_transient(exc)
                log.warning("%s in iCloud failed (%s -> %s): %s", "Move" if step == "move" else "Rename",
                            here, new, brief(exc))
                if n == 0 and not unknown:
                    with self.state.transaction():
                        self._journal_put(ident, None)  # refused: nothing changed in iCloud
                    return False
                # Stays written down with where the database has it now (moved together, in one
                # transaction): the next full pass finds the item by its id and settles it.
                self._move_rows(old, here, None, ident, {**entry, "at": here})
                if n > 0:
                    self._forget_cached(old)
                    res.followup = True
                if isinstance(exc, (AuthRequired, RemoteUnavailable)):
                    raise
                return "partial"
            item = DriveItem(here, item.drivewsid, item.docwsid, item.zone, got or item.etag,
                             item.is_dir, item.size, item.mtime)
        self._moved_ident = ident  # the caller lets the rows follow and drops the record, together
        self._forget_cached(old)
        res.moved += 1
        res.followup = True  # the next pass learns the new iCloud versions
        self.cb.activity("upload", t("Drive: renamed/moved in iCloud: {old} → {new}", old=old, new=new),
                         str(self._abs(new)))
        return True

    def _journal(self) -> dict[str, dict]:
        raw = self.state.get_meta(PENDING_MOVE)
        if not raw:
            return {}
        try:
            j = json.loads(raw)
        except ValueError:
            return {}
        if isinstance(j, dict) and "id" in j and "old" in j:  # written by an earlier 1.0.6 build
            j = {j["id"]: j}
        return {k: v for k, v in j.items() if isinstance(v, dict) and "old" in v and "new" in v} \
            if isinstance(j, dict) else {}

    def _journal_put(self, ident: str, entry: dict | None) -> None:
        """Call inside a transaction."""
        j = self._journal()
        if entry is None:
            j.pop(ident, None)
        else:
            j[ident] = entry
        self.state.set_meta(PENDING_MOVE, json.dumps(j) if j else None)

    def _move_rows(self, src: str, dst: str, _unused: object, ident: str, entry: dict | None,
                   snapshot: dict | None = None) -> None:
        """Let the database rows of `src` (and below) follow the item to `dst`, and update its
        journal entry, in one transaction. Rows that are missing are rebuilt from the snapshot taken
        when the move started. Every row keeps the version and fingerprint it had: the database says
        what this PC has; what iCloud has now is found out by looking at its content."""
        with self.state.transaction():
            rows = self.state.all()
            mine = {p: r for p, r in rows.items() if p == src or p.startswith(src + "/")}
            if not mine and snapshot:
                mine = {}
                for row in snapshot.get("rows") or []:
                    rel, d, e, sz, lm, ls = row[:6]
                    mine[src + rel] = Base(src + rel, bool(d), e, sz, lm, ls, False, row[6] if len(row) > 6 else None)
            for p in mine:
                self.state.drop_one(p)
            for p, r in mine.items():
                q = dst + p[len(src):]
                if r.is_dir:
                    self.state.put(Base(q, True, None, None, None, None, False))
                else:
                    self.state.put(Base(q, False, r.etag, r.size, r.lmtime, r.lsize, False, r.hash))
            self._journal_put(ident, entry)

    def _settle_pending_moves(self, remote: dict[str, DriveItem], partly: set[str], res: DriveResult) -> None:
        """Renames/moves in iCloud that may have been cut short (crash, unknown answer, refused
        halfway): find each item by its id, let the database follow where it really is, and finish
        the move when the PC copy is still the one that was moved. Nothing is decided about an item
        while iCloud lists any of the folders involved only partly."""
        for ident, e in list(self._journal().items()):
            old, new = e["old"], e["new"]
            if e.get("kind") == "pc":  # renamed on this PC to follow iCloud: did the rename happen?
                now_old, now_new = self._exact_id(old), self._exact_id(new)
                fid = e.get("fid")
                if not isinstance(fid, list) or not fid[-1]:
                    fid = None  # recorded by an earlier build, or no file ids on this file system
                if now_old is False or now_new is False:  # couldn't look: kept (held below), shown
                    res.errors += 1
                    self.cb.activity("error", t("Drive: can't read {name} on this PC right now; it is left "
                                                "alone and checked again next time", name=new))
                    continue
                if fid is None and now_old is not None and now_new is not None:
                    # no file id and something under both names: which one is the moved file can't be
                    # shown. Kept written down: both names are left alone until one of them is gone.
                    log.info("Drive: an interrupted rename on this PC can't be told apart (%s, %s); held", old, new)
                    continue
                if now_old is not None and (now_old == fid if fid else now_new is None):
                    with self.state.transaction():  # the rename never happened
                        self._journal_put(ident, None)
                    continue
                if now_new is None and now_old is not None:
                    # something else under the old name and nothing under the new one: can't show the
                    # rename happened -- treated as not done (no deletion is decided from it)
                    with self.state.transaction():
                        self._journal_put(ident, None)
                    continue
                if fid and now_new is not None and now_new != fid and now_old != fid:
                    # Another file (by its id) is at the new name: an editor's save over the renamed file,
                    # or an unrelated one. Either way it is never taken for the synced version: the
                    # database follows iCloud's item, and the PC file there is then compared as new
                    # (identical: kept as it is; different: both kept, nothing replaced in iCloud).
                    log.info("Drive: an interrupted rename on this PC found another file at %s; both kept", new)
                    self._move_rows(old, new, None, ident, None)
                    self.state.drop_one(new)
                    continue
                # Otherwise the rename happened. The database follows iCloud's item to the new name
                # and the usual rules take it from there: deleted there since -> a deletion on this PC
                # (to iCloud, with the usual approval); saved over since (an editor's save gives a new
                # file id) -> an edit, uploaded; something new under the old name -> a new file.
                log.info("Drive: an interrupted rename on this PC is picked up")
                self._move_rows(old, new, None, ident, None)
                continue
            at = e.get("at") or old
            if "rows" not in e:  # written by an earlier build: the database still has the rows
                known = self.state.all()
                e = {**e, "rows": [[p[len(at):], b.is_dir, b.etag, b.size, b.lmtime, b.lsize, b.hash]
                                   for p, b in known.items() if p == at or p.startswith(at + "/")]}
            hit = next((r for r in remote.values() if ident and ident in (r.docwsid, r.drivewsid)), None)
            touched = {old, at, new} | ({hit.path} if hit else set())
            if partly and (hit is None or any(_overlaps(q, t) for q in partly for t in touched)):
                # decided once iCloud lists it completely: nothing is done around it meanwhile
                self.remote.incomplete |= touched | ({_parent(t) for t in touched} if hit is None else set())
                continue
            if hit is None:  # gone from iCloud: the PC copy (under its new name) follows that deletion
                if not known_missing(self._abs(new)):
                    try:
                        os.lstat(self._abs(new))
                    except OSError:
                        continue  # can't tell where the PC copy is: decided next time
                dst = at if known_missing(self._abs(new)) else new
                self._move_rows(at, dst, None, ident, None, snapshot=e)
                continue
            log.info("Drive: an interrupted rename/move in iCloud is picked up where iCloud has it")
            finish = hit.path != new and self._still_moved_copy(new, at, e)
            self._move_rows(at, hit.path, None, ident, {**e, "at": hit.path} if finish else None, snapshot=e)
            if not finish:
                continue
            moved = self._remote_move(remote, hit.path, new, res)  # keeps its own journal entry
            if moved is False:  # refused, or the target folder is gone: synced as add + delete instead
                with self.state.transaction():
                    self._journal_put(ident, None)
                continue
            if moved is True:
                for p in [p for p in list(remote) if p == hit.path or p.startswith(hit.path + "/")]:
                    r = remote.pop(p)
                    q = new + p[len(hit.path):]
                    remote[q] = self.cache[q] = DriveItem(q, r.drivewsid, r.docwsid, r.zone, r.etag,
                                                          r.is_dir, r.size, r.mtime)
                self._move_rows(hit.path, new, None, ident, None)
        # Whatever is still written down now (an unknown answer again, a refusal halfway) is kept
        # out of this pass entirely: nothing is uploaded, downloaded or deleted around it.
        for e in self._journal().values():
            held = {e["old"], e.get("at") or e["old"], e["new"]}
            self.remote.incomplete |= held

    def _still_moved_copy(self, new: str, at: str, e: dict) -> bool:
        """Is the PC copy at `new` still the one whose move was started (not edited or replaced)?"""
        p = self._abs(new)
        top = next((r for r in e.get("rows") or [] if r[0] == ""), None)
        try:
            if top is None or bool(top[1]) != p.is_dir() or _is_link(p):
                return False
            if top[1]:
                return True
            st = p.stat()
            return st.st_size == top[5] and round(st.st_mtime, 2) == round(top[4] or 0, 2)
        except OSError:
            return False

    def _forget_cached(self, path: str) -> None:
        prefix = path + "/"
        for p in [p for p in self.cache if p == path or p.startswith(prefix)]:
            del self.cache[p]

    def _pc_move(self, old: str, new: str, res: DriveResult, remote: dict[str, DriveItem]) -> bool:
        """Rename on this PC what iCloud renamed, written down first: after a crash right after the
        rename the next full pass lets the database follow (never "new on the PC" = upload again)."""
        r = remote[new]
        self._pc_key = "pc:" + (r.docwsid or r.drivewsid or new)
        try:
            st = os.lstat(self._abs(old))  # file id (with its volume): stays the same through a rename
            fid = [st.st_dev, st.st_ino] if st.st_ino else None  # 0: this file system has none
        except OSError:
            fid = None
        with self.state.transaction():
            self._journal_put(self._pc_key, {"kind": "pc", "old": old, "new": new, "fid": fid})
        if self._local_move(old, new, res):
            return True
        with self.state.transaction():
            self._journal_put(self._pc_key, None)
        return False

    def _exact_id(self, rel: str) -> list | None | bool:
        """[volume, file id] of what is under exactly this name (Windows names ignore letter case);
        None: nothing there; False: couldn't be read (unknown)."""
        p = self._abs(rel)
        try:
            if p.name not in os.listdir(p.parent):
                return None
            st = os.lstat(p)
            return [st.st_dev, st.st_ino]
        except (FileNotFoundError, NotADirectoryError):
            return None  # (a parent that is an ordinary file now: nothing can be below it)
        except OSError:
            return False

    def _local_move(self, old: str, new: str, res: DriveResult) -> bool:
        src, dst = self._abs(old), self._abs(new)
        try:
            if old.lower() == new.lower() and src.exists():
                pass  # only the letter case changes: Windows sees one name, rename in place
            elif dst.exists():
                made = self.state.all().get(new)
                if made is not None and made.is_dir and dst.is_dir() and not _is_link(dst) and not any(dst.iterdir()):
                    dst.rmdir()  # an empty folder Skyfold made for the new name: replace it
                else:
                    return False
            dst.parent.mkdir(parents=True, exist_ok=True)
            self._touch_own(src)
            self._touch_own(dst)
            carried = self._records_below(old)
            moved_keys = {key: f"{key.split(':', 1)[0]}:{new}{key.split(':', 1)[1][len(old):]}" for key in carried}
            before = {nk: self.state.get_meta(nk) for nk in moved_keys.values()}  # what was there: put back on failure
            with self.state.transaction():
                for key, nk in moved_keys.items():  # written down at the new place too before the move:
                    self.state.set_meta(nk, carried[key])  # whichever name the file has after a crash, it is known
            try:
                os.rename(src, dst)  # refuses an existing name (os.replace would overwrite a file saved just now)
            except BaseException:
                with self.state.transaction():
                    for nk, val in before.items():
                        self.state.set_meta(nk, val)
                raise
            with self.state.transaction():
                for key, nk in moved_keys.items():
                    if key != nk:  # (a letter-case-only move: the same record)
                        self.state.set_meta(key, None)
        except FileExistsError:
            log.info("Rename on this PC skipped (%s -> %s): the new name was taken meanwhile", old, new)
            return False
        except OSError as exc:
            log.warning("Rename on this PC failed (%s -> %s): %s", old, new, exc)
            return False
        res.moved += 1
        self.cb.activity("download", t("Drive: renamed/moved on this PC: {old} → {new}", old=old, new=new), str(dst))
        return True

    def _records_below(self, folder: str) -> dict[str, str]:
        """Skyfold's private files (replaced versions, temp and set-aside files) written down inside
        this folder, matched the way Windows matches names (letter case doesn't count)."""
        low = folder.lower() + "/"
        out: dict[str, str] = {}
        for kind in ("prev:", "aside:", "tmp:"):
            for key, val in self.state.meta_items(kind).items():
                if key[len(kind):].lower().startswith(low):
                    out[key] = val
        return out

    def _rebase(self, base: dict[str, Base], old: str, new: str, keep_etags: bool,
                remote: dict[str, DriveItem] | None = None) -> None:
        """Move the remembered state of a folder and everything in it to its new path."""
        for p in [p for p in base if p == old or p.startswith(old + "/")]:
            b = base[p]
            q = new + p[len(old):]
            etag = remote[q].etag if keep_etags and remote and q in remote else None
            self.state.drop(p)
            if b.is_dir:
                self.state.put(Base(q, True, etag, None, None, None, False))
            else:
                if b.etag is not None:
                    etag = b.etag  # compared next pass: unchanged content is recognised, changes come down
                self.state.put(Base(q, False, etag, b.size, b.lmtime, b.lsize, False, b.hash))

    def approve(self, paths: list[str], side: str | None = None, gen: int | None = None) -> list[str]:
        """The user's OK for these paths, given for one side ("cloud": delete in iCloud, "pc": on this PC)
        of the question that was shown (gen: that side's version). Paths not in that question, or an answer to
        an older version of it, don't count."""
        if gen is not None and gen != (self.side_gen[side] if side in self.side_gen else self.question_gen):
            return []
        asked = self._asked_cloud if side == "cloud" else self._asked_pc if side == "pc" else \
            set(self._asked_cloud) | set(self._asked_pc)
        ok = [p for p in paths if p in asked]
        self.approved.update(ok)
        if side:
            self._approved_side.update({p: side for p in ok})
        return ok

    def _only_waiting(self, to_cloud: list[str], to_pc: list[str]) -> bool:
        """Are all these deletions part of the question already asked, on their side, and not OK'd?"""
        def waiting(p: str, asked: dict) -> bool:
            q = p
            while True:
                if q in asked:
                    return q not in self.approved
                if "/" not in q:
                    return False
                q = q.rsplit("/", 1)[0]

        return (all(waiting(p, self._asked_cloud) for p in to_cloud)
                and all(waiting(p, self._asked_pc) for p in to_pc))

    def _could_be_moved(self, to_cloud: list[str], to_pc: list[str], uploads: list[str],
                        downloads: list, local: dict, base: dict[str, Base]) -> bool:
        """Could something new in this pass be one of the waiting items, moved or renamed? (Same size,
        or a whole folder is waiting.) Then a full read decides: it recognises a move, which a quick
        one can't - otherwise the item would end up twice in iCloud."""
        known = self.state.all() if (to_cloud or to_pc) else {}

        def sizes(paths: list[str], attr: str) -> tuple[set, bool]:
            out, folder = set(), False
            for p in paths:
                b = base.get(p) or known.get(p)
                if b is None or b.is_dir:
                    folder = True
                    continue
                out.add(getattr(b, attr))
            return out, folder

        if to_cloud:  # gone from this PC: is it back under another name here?
            held, folder = sizes(to_cloud, "lsize")
            new_here = [p for p in uploads if p not in base and p not in known and p in local]
            if new_here and (folder or any(local[p].size in held for p in new_here)):
                return True
        if to_pc:  # gone from iCloud: is it there under another name?
            held, folder = sizes(to_pc, "size")
            new_there = [d for d in downloads if d.path not in base and d.path not in known]
            if new_there and (folder or any(d.size in held for d in new_there)):
                return True
        return False

    def _pc_shape(self, p: str) -> dict | None:
        """Which items are at p and below on this PC, by Windows' own file identity (a copy put back
        at the same path is another file): what a deletion there would remove. None when it can't be
        read now."""
        top = self._abs(p)
        out: dict[str, tuple] = {}

        def ident(rel: str, st: os.stat_result) -> None:
            kind = "dir" if stat.S_ISDIR(st.st_mode) else "file"
            if not st.st_ino:  # the file system can't tell which file this is: nothing to compare
                raise OSError("no file identity")
            out[rel] = (kind, st.st_dev, st.st_ino)

        try:
            ident(p, os.lstat(top))
        except FileNotFoundError:
            return {p: ("absent",)}
        except OSError:
            return None
        def fail(exc: OSError) -> None:
            raise exc  # a folder inside that can't be read: the shape can't be told

        if out[p][0] == "dir":
            try:
                for here, dirs, files in os.walk(top, onerror=fail):
                    rel = p + "/" + Path(here).relative_to(top).as_posix() if Path(here) != top else p
                    for name in [*dirs, *files]:
                        ident(f"{rel}/{name}", os.lstat(os.path.join(here, name)))
            except OSError:
                return None
        return out

    def _cloud_shape(self, p: str, d: dict[str, DriveItem], known: dict[str, Base],
                     verify: bool = False, ordered: list[str] | None = None) -> dict | None:
        """Which items (and versions) are at p and below in iCloud: what a deletion there would remove.
        None when that can't be told for some item (no identity; no version tag and the content can't
        be shown to be what was synced). verify: untagged files are downloaded and compared."""
        item = d.get(p)
        if item is None:
            return {p: ("absent",)}
        if item.is_dir and ordered is not None:  # (sorted paths: only this folder's part is looked at)
            import bisect

            below = ordered[bisect.bisect_left(ordered, p + "/"):bisect.bisect_left(ordered, p + "0")]
        else:
            below = [q for q in d if q.startswith(p + "/")] if item.is_dir else []
        keys = [p] + below
        out: dict[str, tuple] = {}
        for q in keys:
            r = d[q]
            ident = r.docwsid or r.drivewsid
            if not ident:
                return None
            if r.is_dir:
                out[q] = ("dir", ident)
            elif r.etag:
                out[q] = ("file", ident, r.etag)
            else:
                b = known.get(q)
                if b is None or not b.hash or (verify and self._cloud_hash(r) != b.hash):
                    return None
                out[q] = ("file", ident, "content", b.hash)
        return out

    def _needs_answer(self, paths: list[str], side: str, over: bool, remote: dict[str, DriveItem],
                      known: dict[str, Base]) -> bool:
        """Must these deletions wait for the user? Yes when there are more than the limit, or when any
        of them was asked about before (also if fewer are left now), until that very question - same
        side, same items in iCloud - was answered with OK."""
        asked = self._asked_cloud if side == "cloud" else self._asked_pc

        def asked_as(p: str) -> str | None:
            """The path itself, or the folder above it, as the question had it (a folder asked about
            covers what is inside, also when a later pass lists its files one by one)."""
            q = p
            while True:
                if q in asked:
                    return q
                if "/" not in q:
                    return None
                q = q.rsplit("/", 1)[0]

        keys = {p: asked_as(p) for p in paths}
        # A folder whose files were asked about one by one is part of that question too (they are now
        # listed as the folder): asked again as it is now, never deleted unanswered.
        regrouped = [p for p in paths if keys[p] is None and any(q.startswith(p + "/") for q in asked)]
        if regrouped:
            return True
        if not over and not any(keys.values()):
            return False
        for p in paths:
            k = keys[p]
            if k is None or k not in self.approved or self._approved_side.get(k, side) != side:
                return True
            if side == "cloud":
                then = self._asked_cloud[k]
                if then is None:
                    return True
                if k != p:
                    then = {q: v for q, v in then.items() if q == p or q.startswith(p + "/")} or None
                if then is None or self._cloud_shape(p, remote, known, verify=True) != then:
                    return True  # another item (or version) is there now than the one you OK'd
            else:
                then = self._asked_pc[k]
                if then is not None and k != p:
                    then = {q: v for q, v in then.items() if q == p or q.startswith(p + "/")} or None
                if then is None or self._pc_shape(p) != then:
                    return True  # another file is there now (e.g. a copy put back): not the one you OK'd
        return False

    def apply_approved_deletions(self) -> None:
        """The user OK'd the waiting deletions while this pass is still busy: carry them out now."""
        late = self._late
        if late is None:
            return
        remote, base, res = late
        waiting = set(res.pending_to_cloud) | set(res.pending_to_pc)
        if not waiting:
            self._late = None
            return
        if not waiting <= self.approved:
            return
        to_cloud, to_pc = res.pending_to_cloud, res.pending_to_pc
        res.pending_to_cloud, res.pending_to_pc = [], []
        self._late = None
        changed = [p for p in to_cloud if not self._unchanged_in_icloud(p, remote)]
        if changed:  # something was added or edited there since the question: ask again next time
            for p in changed:
                self.approved.discard(p)
                self.cb.activity("info", t("Not deleted in iCloud, it changed there since you confirmed: {name}",
                                           name=p))
            to_cloud = [p for p in to_cloud if p not in changed]
        self._deletions(to_cloud, to_pc, remote, base, res)

    def _unchanged_in_icloud(self, p: str, remote: dict[str, DriveItem]) -> bool:
        """Before an approved deletion runs later in the pass: is iCloud's copy still exactly what the
        user was asked about? Something added or edited there since never goes to Recently Deleted unasked."""
        item = remote.get(p)
        if item is None:
            return True  # nothing there to delete
        top = item if item.is_dir else remote.get(_parent(p))
        if top is None:
            return False
        before = set(getattr(self.remote, "incomplete", set()) or ())
        now: dict[str, DriveItem] = {}
        try:
            if item.is_dir:  # the folder itself, as its parent lists it now (the same folder, not a new one)
                parent = remote.get(_parent(p))
                if parent is None:
                    return False
                there = self.remote.walk(folders=[parent]).get(p)
                if there is None or not there.is_dir:
                    return False
                now[p] = there
            level = [top]
            for _ in range(64):
                if not level:
                    break
                asked = {f.path for f in level}
                fresh = {q: r for q, r in self.remote.walk(folders=level).items() if q not in asked}
                now.update(fresh)
                level = [r for r in fresh.values() if r.is_dir] if item.is_dir else []
            else:
                return False
        except (AuthRequired, RemoteUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001
            log.info("Drive: couldn't re-check %s before deleting it: %s", p, exc)
            return False
        if set(getattr(self.remote, "incomplete", set()) or ()) - before:
            return False

        def shape(d: dict[str, DriveItem]) -> dict[str, tuple] | None:
            """Every item with what it is, which item it is and which version - or None when that
            can't be told for some item (no version tag, no identity): then nothing is proven."""
            keys = [p] + ([q for q in d if q.startswith(p + "/")] if item.is_dir else [])
            out: dict[str, tuple] = {}
            for q in keys:
                r = d.get(q)
                if r is None:
                    out[q] = ("absent",)
                    continue
                ident = r.docwsid or r.drivewsid
                if not ident:
                    return None
                if r.is_dir:
                    out[q] = ("dir", ident)
                elif r.etag:
                    out[q] = ("file", ident, r.etag)
                else:  # no version tag: only the content can tell, against what was synced
                    b = known.get(q)
                    if b is None or not b.hash or (d is now and self._cloud_hash(r) != b.hash):
                        return None
                    out[q] = ("file", ident, "content", b.hash)
            return out

        known = self.state.all()

        before_shape, now_shape = shape(remote), shape(now)
        return before_shape is not None and now_shape is not None and before_shape == now_shape

    def discard_pending(self, paths: list[str]) -> None:
        """The user said no: these are no longer waiting in the pass that is running."""
        if self._late is not None:
            res = self._late[2]
            drop = set(paths)
            res.pending_to_cloud = [p for p in res.pending_to_cloud if p not in drop]
            res.pending_to_pc = [p for p in res.pending_to_pc if p not in drop]

    def _deletions(self, to_cloud: list[str], to_pc: list[str], remote: dict[str, DriveItem],
                   base: dict[str, Base], res: DriveResult) -> None:
        threshold = max(1, self.delete_threshold)

        # a quick pass only holds a slice: a deleted folder's files are elsewhere
        everything = self.state.all() if (to_cloud or to_pc) else {}

        def tops_of(p: str, tops: set[str]) -> list[str]:
            """The affected entries this path is (or lies inside)."""
            found = []
            while True:
                if p in tops:
                    found.append(p)
                if "/" not in p:
                    return found
                p = p.rsplit("/", 1)[0]

        def count(paths: list[str], _tree: dict) -> int:
            # each file once, with its affected ancestors: one walk, never file x entry pairs
            tops = set(paths)
            files, covered = 0, set()
            for p, b in everything.items():
                if not b.is_dir:
                    hit = tops_of(p, tops)
                    if hit:
                        files += 1
                        covered.update(hit)
            n = files + len(tops - covered)  # (an empty folder, or an entry with no file row, counts once)
            total = sum(1 for b in everything.values() if not b.is_dir)
            if n >= 5 and n > total // 2:
                return threshold + 1  # more than half of everything (5 items or more): always ask
            return n

        if to_cloud:
            if not self.delete_pc_to_cloud:
                for p in to_cloud:  # keep in iCloud: download it again
                    self.state.drop(p)
            elif self._needs_answer(to_cloud, "cloud", count(to_cloud, base) > threshold, remote, everything):
                res.pending_to_cloud = to_cloud
            else:
                for p in to_cloud:
                    self._check_cancel()
                    if p in remote and base.get(p) is not None and remote[p].is_dir != base[p].is_dir:
                        continue  # iCloud has something else there now: not what was deleted on this PC
                    if not known_missing(self._abs(p)):
                        try:
                            os.lstat(self._abs(p))
                        except OSError as exc:  # can't be told now: nothing decided, tried again
                            log.info("Drive: %s couldn't be checked before deleting it in iCloud: %s", p, brief(exc))
                            continue
                        self.state.drop(p)  # back on this PC since the question: it goes up again
                        continue
                    try:
                        self.remote.trash(remote[p])
                    except (AuthRequired, RemoteUnavailable):
                        raise
                    except Exception as exc:  # noqa: BLE001
                        res.errors += 1
                        self.cb.activity("error", t("Couldn't delete in iCloud ({name}): {error}", name=p, error=brief(exc)))
                        continue
                    self.state.drop(p)
                    self._forget_cached(p)
                    res.deleted_cloud += 1
                    self.cb.activity("cloud", t("Drive: deleted in iCloud: {name}", name=p))
        if to_pc:
            if not self.delete_cloud_to_pc:
                for p in to_pc:  # keep on PC: it will be uploaded again
                    self.state.drop(p)
            elif self._needs_answer(to_pc, "pc", count(to_pc, base) > threshold, remote, everything):
                res.pending_to_pc = to_pc
            else:
                queue = list(to_pc)
                while queue:
                    p = queue.pop(0)
                    self._check_cancel()
                    target = self._abs(p)
                    b = base.get(p)
                    if not known_missing(target):
                        try:
                            os.lstat(target)
                        except OSError as exc:  # can't be looked at now: nothing decided, asked again
                            log.info("Drive: %s couldn't be checked before deleting it: %s", p, brief(exc))
                            continue
                    if b is not None and b.is_dir and target.is_dir():
                        # Time has passed since this was decided (other requests, the user's answer):
                        # look into the folder again now. Files added or changed meanwhile stay;
                        # only what is still the synced content goes to the Recycle Bin.
                        still = self._keep_unsynced_local([p], base)
                        if still != [p]:
                            queue = still + queue
                            continue
                    if b is not None and ((not b.is_dir and target.is_dir()) or (b.is_dir and target.is_file())):
                        self.state.drop(p)  # file and folder swapped since the question: not what was approved
                        continue
                    if b is not None and not b.is_dir and target.is_file():
                        # still the synced content? (size, time and - when known - the fingerprint; time
                        # has passed since it was decided, and since the user was asked)
                        try:
                            same = self._pc_has_synced_content(b, target, strict=True)
                        except OSError as exc:  # can't be read now: nothing decided, tried again
                            log.info("Drive: %s couldn't be checked before deleting it: %s", p, brief(exc))
                            continue
                        if not same:
                            self.state.drop(p)  # edited on this PC meanwhile: keep it (it goes up)
                            continue
                    try:
                        if not known_missing(target):
                            self._trash_local(target)
                    except OSError as exc:
                        res.errors += 1
                        self.cb.activity("error", t("Couldn't delete {name}: {error}", name=p, error=brief(exc)))
                        continue
                    self.state.drop(p)
                    res.deleted_local += 1
                    self.cb.activity("trash", t("Drive: deleted on this PC (Recycle Bin): {name}", name=p))
        if to_cloud or to_pc:  # an OK is used up by what was done; one left alone (couldn't be read) keeps it
            rows = self.state.all()
            done = {p for p in [*to_cloud, *to_pc] if p not in rows}
            self.approved -= done
            for p in done:
                self._approved_side.pop(p, None)

    def _downloads(self, items: list[DriveItem], base: dict[str, Base], res: DriveResult) -> None:
        items = [i for i in items if i.path not in self.mac_packages]
        if not items:
            return
        total_bytes = sum(i.size for i in items)
        # Needed: what the downloads add to the disk (a file that replaces one, or only gets compared
        # with one, adds little or nothing) plus room for the temporary files of one batch. A new
        # file's temporary copy becomes the file itself, so it is counted once.
        growth = 0
        beside: list[int] = []  # files that exist already: their new copy is made next to them
        for i in items:
            try:
                have = self._abs(i.path).stat().st_size
            except OSError:
                have = None
            growth += max(0, (i.size or 0) - (have or 0))
            if have is not None:
                beside.append(i.size or 0)
        staging = sum(sorted(beside, reverse=True)[:WORKERS])
        check_free_space(self.root, growth + staging)
        total = total_bytes or len(items)
        done = 0
        done_lock = threading.Lock()
        replaced: list[tuple[Path, str]] = []  # previous versions, to the Recycle Bin from this thread

        def recycle_replaced() -> None:
            with done_lock:
                old, replaced[:] = list(replaced), []
            for p, rel in old:
                self._recycle_previous(p, rel)

        abort = threading.Event()  # one download failed for good: the others stop (not a user stop)
        space_lock = threading.Lock()
        in_flight = [0]  # bytes of downloads started and not finished: their space is spoken for
        kept_both: list[str] = []  # the PC had another version: both kept

        def on_bytes(k: int) -> None:
            nonlocal done
            THROTTLE.take(k)
            with done_lock:
                done += k
            if self._stopped() or abort.is_set():
                raise Cancelled()

        def fetch(item: DriveItem) -> tuple[Path, int, float, str, bool]:
            """Runs on a worker thread: download to a temporary file and put it in place. Returns the
            size and time of the version put there (an edit made right after stays an edit), its
            fingerprint, and whether the PC file was replaced (False: it already had this content)."""
            if self._stopped() or abort.is_set():
                raise Cancelled()
            target = self._abs(item.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._check_inside(target.parent)
            fd, tmp = tempfile.mkstemp(prefix=TMP_PREFIX, dir=target.parent)
            os.close(fd)
            tmp_path = Path(tmp)
            self._own(tmp_path, "tmp")
            try:
                self.remote.download(item, tmp_path, on_bytes)
                if item.size >= 0 and tmp_path.stat().st_size != item.size:
                    with open(tmp_path, "rb") as fh:
                        zipped = fh.read(4) == b"PK"
                    if zipped and not item.path.lower().endswith(".zip"):
                        raise MacPackage(item.path)  # iCloud zipped a Mac package (e.g. .xcodeproj) for us
                    raise SyncError(t("file size doesn't match"))
                digest = file_hash(tmp_path)
                b = base.get(item.path)
                if target.is_file() and not _is_link(target):
                    # Already this very content on the PC (only iCloud's version tag was new, or it is
                    # our own upload/download): leave the file alone, just remember the version.
                    st0 = target.stat()
                    same = st0.st_size == tmp_path.stat().st_size and file_hash(target) == digest
                    st1 = target.stat()
                    if same and _snapshot(st0) == _snapshot(st1):  # the very file that was read, untouched
                        return target, st0.st_size, st0.st_mtime, digest, False
                    if b is not None and b.hash and digest == b.hash:
                        # iCloud holds exactly the synced content (only its tag is new). What is on this
                        # PC now is this PC's own change: it stays and goes up (time -1 = "changed here").
                        return target, b.lsize, -1.0, digest, False
                if item.mtime:
                    os.utime(tmp_path, (item.mtime, item.mtime))  # kept by the rename below
                self._touch_own(target)
                b = base.get(item.path)
                aside = None
                if target.exists() and target.is_file():
                    aside = target.with_name(_aside_name(target))
                    self._own(aside, "aside")
                    os.rename(target, aside)  # moved, never overwritten (whatever is saved there right now)
                put = tmp_path.stat()  # the rename keeps size and time
                try:
                    try:
                        os.rename(tmp_path, target)  # refuses if a file was saved there meanwhile
                    except FileExistsError:
                        raise SyncError(t("{name} changed while it was downloading; tried again next time",
                                          name=item.path)) from None
                except BaseException:
                    if aside is not None:
                        self._release_aside(aside, item.path)  # back in place, or kept as a conflict copy
                    raise
                if aside is not None:
                    if self._pc_has_synced_content(b, aside):
                        # The version both sides had (by its fingerprint when one is known, else by
                        # size and time): it goes to the Recycle Bin, never deleted for good.
                        with done_lock:
                            replaced.append((aside, item.path))
                    else:  # another version on this PC (changed, or never synced): kept under a conflict name
                        self._release_aside(aside, item.path, restore=False)
                        with done_lock:
                            kept_both.append(item.path)
                hide_like_mac(target)
                return target, put.st_size, put.st_mtime, digest, True
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                if known_missing(tmp_path):
                    self._disown(tmp_path, "tmp")  # otherwise kept on record: removed next time

        def fetch_reserved(item: DriveItem) -> tuple[Path, int, float, str, bool]:
            """fetch(), with this file's space set aside first. Each download is checked against the
            free space left after all downloads still running (a replaced version stays on the disk,
            in the Recycle Bin, so the free space is read afresh every time)."""
            need = max(0, item.size or 0)
            with space_lock:
                check_free_space(self.root, in_flight[0] + need)
                in_flight[0] += need
            try:
                return fetch(item)
            finally:
                with space_lock:
                    in_flight[0] -= need

        # Several files at once: most Drive files are small, so waiting on Apple is the slow part.
        # Only the worker threads talk to the network; the database and messages stay on this thread.
        from .ui_text import Rate

        rate = Rate()
        finished = 0
        todo = iter(items)
        running: dict = {}
        pool = ThreadPoolExecutor(WORKERS)
        try:
            while True:
                while len(running) < WORKERS and not self._stopped() and not abort.is_set():
                    item = next(todo, None)
                    if item is None:
                        break
                    running[pool.submit(fetch_reserved, item)] = item
                if not running:
                    break
                ready, _ = wait(running, timeout=0.3, return_when=FIRST_COMPLETED)
                fatal: BaseException | None = None
                for fut in ready:
                    item = running.pop(fut)
                    finished += 1
                    try:
                        target, lsize, lmtime, digest, replaced_file = fut.result()
                    except MacPackage:
                        self.mac_packages.add(item.path)
                        self.cb.activity("info", t("Mac package left in iCloud (it only works on a Mac): {name}",
                                                   name=item.path))
                        continue
                    except (Cancelled, AuthRequired, RemoteUnavailable) as exc:
                        fatal = fatal or exc  # finish recording the others in this batch first
                        continue
                    except Exception as exc:  # noqa: BLE001
                        res.errors += 1
                        self.cb.activity("error", t("Download failed ({name}): {error}", name=item.path, error=brief(exc)))
                        continue
                    self.state.put(Base(item.path, False, item.etag, item.size, lmtime, lsize, False, digest))
                    if not replaced_file:
                        continue  # compared only: the PC already had this content
                    res.downloaded += 1
                    res.last_down = str(target) if not res.downloaded - 1 else str(self.root)
                    res.down_files.append(item.path)
                recycle_replaced()
                with done_lock:
                    both, kept_both[:] = list(kept_both), []
                for name in both:
                    res.conflicts += 1
                    self.cb.activity("info", t("Changed on both sides, kept both: {name}", name=name))
                if fatal is not None:
                    raise fatal
                self.apply_approved_deletions()
                with done_lock:
                    so_far = done
                rate.add(so_far, finished)
                self.cb.progress(so_far, total, transfer_text(t("Drive: "), finished, len(items), so_far,
                                                              total_bytes, rate))
                self._check_cancel()
        except BaseException:
            abort.set()  # make downloads still running stop at their next chunk
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            recycle_replaced()

    def _release_aside(self, aside: Path, rel: str, restore: bool = True) -> None:
        """A PC file moved aside while a download was put in place: back to its own name when that is
        free, otherwise next to it as a conflict copy. Never deleted."""
        for dest in ([rel] if restore else []) + [None]:
            try:
                full = self._abs(dest if dest is not None else self._conflict_name(rel))
                self._touch_own(full)
                os.rename(aside, full)  # refuses an existing name
                self._disown(aside, "aside")
                return
            except OSError:
                continue
        log.warning("Drive: the PC version of %s is kept as %s; shown again on the next full sync", rel, aside.name)

    def _recycle_previous(self, aside: Path, rel: str) -> None:
        """The version replaced by iCloud's newer one goes to the Recycle Bin, under a readable name."""
        p = PurePosixPath(rel)
        named = self._abs(str(p.with_name(f"{p.stem} ({t('previous version')}){p.suffix}")))
        original = aside
        fid = self._file_id(aside)
        promoted = False  # whether this call wrote the "prev:" record of the readable name
        if fid and known_missing(named) and not self.state.get_meta(f"prev:{self._rel(named)}"):
            try:
                self._touch_own(named)
                self._own(named, "prev", fid)  # written down first: after a crash it is still known as ours
                promoted = True
                os.rename(aside, named)
                self._disown(original, "aside")
                aside = named
            except OSError:
                there = None if known_missing(named) else self._file_id(named)
                if known_missing(named) or (there is not None and there != fid):
                    self._disown(named, "prev")  # the rename didn't happen: nothing of ours there
                    promoted = False
                # (can't tell: the record stays, the next full scan decides)
        try:
            self._trash_local(aside)
            self._disown(original, "aside")
            if promoted and aside == named:  # (after an unclear rename the next full scan decides)
                self._disown(named, "prev")
        except Exception as exc:  # noqa: BLE001 - kept; shown as a conflict copy on the next full sync
            log.warning("Drive: couldn't move the previous version of %s to the Recycle Bin: %s", rel, exc)
            if aside == named:
                self._release_aside(aside, rel, restore=False)
                if known_missing(named):
                    self._disown(named, "prev")  # now an ordinary, visible copy

    def _recover_aside(self, p: Path) -> None:
        """Left behind by an interrupted download (crash, power cut): shown again as a conflict copy."""
        name = _aside_original(p.name)
        try:
            rel = (p.parent / name).relative_to(self.root).as_posix()
        except ValueError:
            return
        log.info("Drive: recovering %s left by an interrupted download", rel)
        b = self.state.all().get(rel)
        try:
            present = not known_missing(self._abs(rel))
            if present:
                os.lstat(self._abs(rel))
            if b is not None and present and self._pc_has_synced_content(b, p, strict=True):
                self._recycle_previous(p, rel)  # just the version both sides had: to the Recycle Bin
                return
        except OSError as exc:  # can't be looked at now: stays as it is, tried again next time
            log.info("Drive: %s couldn't be checked now: %s", rel, brief(exc))
            return
        self._release_aside(p, rel, restore=False)

    def _uploads(self, paths: list[str], remote: dict[str, DriveItem], base: dict[str, Base],
                 local: dict[str, Local], res: DriveResult) -> None:
        """New files go up several at a time (each upload is one connection, and Apple limits each
        connection); a file replacing one in iCloud goes alone, after them, because its old
        version is moved to Recently Deleted first. Only this thread writes the database."""
        if not paths:
            return
        from .engine import _upload_reader
        from .ui_text import Rate

        fresh: list[tuple[str, DriveItem]] = []
        replacing: list[tuple[str, DriveItem, DriveItem]] = []
        for path in paths:
            parent_path = str(PurePosixPath(path).parent) if "/" in path else ""
            parent = remote.get(parent_path)
            if parent is None or not parent.is_dir:
                res.followup = True  # parent folder is created this round; the file follows next run
                continue
            existing = remote.get(path)
            if existing is None:
                fresh.append((path, parent))
            else:
                replacing.append((path, parent, existing))
        paths = [p for p, _ in fresh] + [p for p, _, _ in replacing]  # (the ones that go now)
        if not paths:
            return

        rate = Rate()
        total = sum(local[p].size for p in paths if p in local) or 0
        lock = threading.Lock()
        sent = [0]  # bytes sent in this batch (all uploads together)
        finished = [0]  # files done (sent or failed)
        shown = [0.0]
        abort = threading.Event()  # one upload stopped the batch: the others stop at their next piece

        def show(force: bool = False) -> None:  # (this thread only)
            clock = time.monotonic()
            if not force and clock - shown[0] < 0.5:
                return
            shown[0] = clock
            with lock:
                now_bytes, done_files = sent[0], finished[0]
            rate.add(now_bytes, done_files)
            from .ui_text import count_text

            # several go at once: how many are done, not "which one" (e.g. "done 0 of 3" while 3 go up)
            self.cb.progress(now_bytes, total or 1, transfer_text(
                "", done_files, len(paths), now_bytes, total, rate,
                first=t("Drive: uploading, {i} of {n} files done", i=count_text(done_files), n=count_text(len(paths)))))

        def counter(here: bool) -> Callable[[int], None]:
            def on_bytes(k: int) -> None:  # (from an upload thread, or this one for a replacement)
                if self._stopped() or abort.is_set():
                    raise Cancelled()  # Stop works in the middle of a big upload too
                with lock:
                    sent[0] += k
                if here:
                    show()
            return on_bytes

        def send(path: str, parent: DriveItem, here: bool, handle=None) -> tuple:
            if getattr(self.remote, "streams_progress", False):
                extra = {"handle": handle} if handle is not None and getattr(self.remote, "takes_handle", False) else {}
                got = self.remote.upload(parent, self._abs(path), on_bytes=counter(here), **extra)
            else:
                got = self.remote.upload(parent, self._abs(path))
            if isinstance(got, tuple) and len(got) in (2, 3):
                return got  # exactly what was sent (and its version in iCloud, when told)
            return _file_digest(self._abs(path))

        def recorded(path: str, lo: Local, got: tuple) -> None:
            size, digest = got[0], got[1]
            etag = got[2] if len(got) > 2 else None
            # What it must contain. The version iCloud named in its confirmation is kept aside: when a
            # listing shows that very version the file counts as synced without a download; until a
            # listing shows it at all, the usual wait for our own upload applies (never "deleted").
            self.state.put(Base(path, False, None, size, lo.mtime, lo.size, False, digest))
            self.state.set_meta(RECEIPT + path, etag or None)
            res.uploaded += 1
            res.last_up = res.last_up or str(self._abs(path))
            res.up_files.append(path)

        def failed(path: str, exc: Exception) -> None:
            res.errors += 1
            self.cb.activity("error", t("Upload failed ({name}): {error}", name=path, error=brief(exc)))

        # new files: a few at a time
        self._check_cancel()
        show(force=True)
        if fresh:
            def send_new(path: str, parent: DriveItem) -> tuple:
                if self._stopped() or abort.is_set():
                    raise Cancelled()
                self._check_inside(self._abs(path))
                with open(self._abs(path), "rb") as fh:  # locked or unreadable: nothing is sent
                    fh.read(1)
                return send(path, parent, False)

            pool = ThreadPoolExecutor(UPLOAD_WORKERS)
            futures = {pool.submit(send_new, path, parent): path for path, parent in fresh}
            taken: set = set()

            def take(f) -> None:
                """A finished upload's outcome, on this thread. A network failure stops the batch."""
                taken.add(f)
                path = futures[f]
                with lock:
                    finished[0] += 1
                try:
                    got = f.result()
                except (AuthRequired, RemoteUnavailable, Cancelled):
                    abort.set()
                    raise
                except Exception as exc:  # noqa: BLE001
                    if is_transient(exc):
                        abort.set()  # the connection is gone: the others stop, replacements wait
                        raise
                    failed(path, exc)
                else:
                    recorded(path, local[path], got)

            try:
                waiting = set(futures)
                while waiting:
                    done, waiting = wait(waiting, timeout=0.5, return_when=FIRST_COMPLETED)
                    for f in done:
                        take(f)
                    show()
                    if self._stopped():
                        abort.set()
                        raise Cancelled()
            except BaseException:
                abort.set()  # the uploads still running stop at their next piece, unregistered
                pool.shutdown(wait=True, cancel_futures=True)
                # ones iCloud confirmed meanwhile are written down all the same
                for f in futures:
                    if f not in taken and f.done() and not f.cancelled() and f.exception() is None:
                        try:
                            recorded(futures[f], local[futures[f]], f.result())
                        except Exception:  # noqa: BLE001
                            pass
                raise
            finally:
                pool.shutdown(wait=True, cancel_futures=True)

        # replacements: one at a time
        for path, parent, existing in replacing:
            self._check_cancel()
            show(force=True)
            lo = local[path]
            try:
                self._check_inside(self._abs(path))
                # Locked against writers before anything happens in iCloud, and read through once (in
                # pieces) from that same handle: a file another program is still writing, or one that
                # can't be read, never costs the iCloud version. The upload streams from it too.
                with _upload_reader(self._abs(path)) as fh:
                    while fh.read(1 << 20):
                        self._check_cancel()
                    fh.seek(0)
                    self._check_cancel()
                    # Replacing a file: move the old iCloud version to Recently Deleted first. Mark the row
                    # "pending" so a failed upload is retried, never mistaken for a deletion. The tag of
                    # the version replaced is kept: another version found there later is someone else's
                    # save (or this upload, when its reply got lost: compared then), never trashed.
                    self.state.put(Base(path, False, existing.etag, existing.size, lo.mtime, lo.size, True))
                    self.remote.trash(existing)
                    self._check_cancel()  # (the row stays "pending": the upload is done next time)
                    st = os.fstat(fh.fileno())  # (an edit saved while it waited is what was sent)
                    recorded(path, Local(False, st.st_size, st.st_mtime), send(path, parent, True, handle=fh))
            except (AuthRequired, RemoteUnavailable, Cancelled):
                raise
            except Exception as exc:  # noqa: BLE001
                if is_transient(exc):
                    raise
                failed(path, exc)
            finally:
                with lock:
                    finished[0] += 1
        show(force=True)

def _file_digest(p: Path) -> tuple[int, str]:
    h, n = hashlib.sha256(), 0
    with open(p, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
            n += len(chunk)
    return n, h.hexdigest()


_BAD_CHARS = set('<>:"|?*\\') | {chr(c) for c in range(32)}
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_PATH = 259


def windows_name_problem(path: str, root: Path) -> str | None:
    """Why Windows can't store this iCloud path, or None when it can."""
    for part in path.split("/"):
        if any(c in _BAD_CHARS for c in part):
            return "its name has a character Windows doesn't allow"
        if part.endswith((" ", ".")):
            return "its name ends with a space or dot"
        if part.split(".")[0].upper() in _RESERVED:
            return "its name is reserved by Windows"
    if len(str(root / Path(path))) > MAX_PATH:
        return "its path is too long for Windows"
    return None


_HIDDEN = 0x2


def hide_like_mac(p: Path, attrs: int | None = None) -> None:
    """Names starting with "." are hidden on a Mac (e.g. Shortcuts' .WorkflowHiddenFile).
    Windows doesn't do that by name, so give them the Hidden attribute: same look on both.
    Only an attribute: the file's content and time stay as they are."""
    if os.name != "nt" or not p.name.startswith("."):
        return
    try:
        if attrs is None:
            attrs = os.stat(p).st_file_attributes
        if not attrs & _HIDDEN:
            import ctypes

            ctypes.windll.kernel32.SetFileAttributesW(str(p), attrs | _HIDDEN)
    except (OSError, AttributeError):
        pass


def _is_link(p: Path) -> bool:
    try:
        return p.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(p))
    except OSError:
        return True


def _ignored_path(path: str) -> bool:
    return any(ignored(part) for part in path.split("/"))


def in_scope(path: str, is_dir: bool, folders: list[str] | None) -> bool:
    """Is this Drive path synced? None = everything. Otherwise: the chosen folders with all they
    contain, the folders on the way to them (but not their other contents) and files in the root."""
    if folders is None or path == "":
        return True
    if "/" not in path and not is_dir:
        return True
    for f in folders:
        if path == f or path.startswith(f + "/") or (is_dir and f.startswith(path + "/")):
            return True
    return False


def folder_sizes(cache_file: Path) -> dict[str, tuple[int, int]]:
    """(files, bytes) per folder from the last full Drive read, for the folder chooser."""
    try:
        items = json.loads(cache_file.read_text(encoding="utf-8")).get("items", [])
    except (OSError, ValueError, AttributeError):
        return {}
    out: dict[str, list[int]] = {}
    for it in items:
        if it.get("is_dir"):
            continue
        parts = str(it.get("path", "")).split("/")[:-1]
        for i in range(1, len(parts) + 1):
            acc = out.setdefault("/".join(parts[:i]), [0, 0])
            acc[0] += 1
            acc[1] += int(it.get("size") or 0)
    return {k: (v[0], v[1]) for k, v in out.items()}


def unsynced_local_dirs(root: Path, old: list[str] | None, new: list[str] | None) -> list[str]:
    """Top-most folders on the PC that were synced with the `old` choice but aren't with `new`."""
    out: list[str] = []

    def visit(rel: str) -> None:
        d = root / rel if rel else root
        try:
            entries = sorted(e.name for e in os.scandir(d) if e.is_dir() and not ignored(e.name))
        except OSError:
            return
        for name in entries:
            path = f"{rel}/{name}" if rel else name
            was, now = in_scope(path, True, old), in_scope(path, True, new)
            if was and not now:
                out.append(path)
            elif now and new is not None and not any(path == f or path.startswith(f + "/") for f in new):
                visit(path)  # only on the way to a chosen folder: look one level deeper
    visit("")
    return out


def _signature(files: dict[str, int | None]) -> tuple | None:
    """What a folder holds (relative paths and sizes); None for an empty folder (never matched)."""
    if any(v is None for v in files.values()):
        return None  # a size isn't known: never matched
    return tuple(sorted((k.casefold(), v) for k, v in files.items())) or None


def _pairs(old: dict, new: dict) -> list[tuple[str, str]]:
    """One-to-one matches by key; keys shared by several entries on either side are skipped."""
    from collections import Counter

    c_old = Counter(v for v in old.values() if v is not None)
    c_new = Counter(v for v in new.values() if v is not None)
    by_key = {v: k for k, v in new.items() if v is not None and c_new[v] == 1}
    return [(k, by_key[v]) for k, v in old.items() if v is not None and c_old[v] == 1 and v in by_key]


def _aside_name(target: Path) -> str:
    """`.skyfold-tmp-old-<8 hex>-<name>`, the name shortened when the full path would get too long."""
    import uuid

    tag = f"{ASIDE_PREFIX}{uuid.uuid4().hex[:8]}-"
    name = target.name
    room = min(255, 259 - len(str(target.parent)) - 1) - len(tag)
    if len(name) > room:
        stem, suffix = os.path.splitext(name)
        name = stem[: max(1, room - len(suffix))] + suffix
    return tag + name


def _aside_original(name: str) -> str:
    """The file name an aside name was made from (both the current and the 1.0.6-candidate format)."""
    import re

    rest = name[len(ASIDE_PREFIX):]
    m = re.match(r"(?:\d+-\d+-|[0-9a-f]{8}-)(.+)", rest)
    return m.group(1) if m else (rest or "file")


def _snapshot(st: os.stat_result) -> tuple:
    """Enough of a file's state to tell that it is the same, untouched file: size, time and identity."""
    return st.st_size, st.st_mtime_ns, st.st_ino, st.st_dev


def file_hash(p: Path) -> str:
    """SHA-256 of a file's content."""
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tag_only(b: Base | None, r: DriveItem | None) -> bool:
    """A HINT only, used by no decision: iCloud's version tag differs while size and modified time are
    those of the synced version. That often means "merely renamed/moved/opened", but it proves nothing
    about the content (a copy can keep size and time), so the engine compares content instead
    (_is_synced_content, and the comparison in _downloads)."""
    return bool(b is not None and r is not None and not b.is_dir and not r.is_dir
                and b.etag is not None and r.etag != b.etag
                and r.size == b.size and r.mtime is not None and b.lmtime is not None
                and abs(r.mtime - b.lmtime) < 1.0)


def _overlaps(a: str, b: str) -> bool:
    """Is one path the other or inside it? "" (the Drive itself) contains every path."""
    return a == b or a == "" or b == "" or a.startswith(b + "/") or b.startswith(a + "/")


def _parent(path: str) -> str:
    return str(PurePosixPath(path).parent) if "/" in path else ""


def _collapse(paths: list[str]) -> list[str]:
    """Keep only top-most entries: deleting 'A' already covers 'A/b.txt'."""
    kept: set[str] = set()
    out: list[str] = []
    for p in sorted(paths, key=lambda x: x.count("/")):  # parents before their contents
        q, covered = p, False
        while "/" in q:
            q = q.rsplit("/", 1)[0]
            if q in kept:
                covered = True
                break
        if not covered:
            kept.add(p)
            out.append(p)
    return sorted(out)


ProgressFn = Callable[[int], None]
