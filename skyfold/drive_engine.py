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
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from .drive_remote import WORKERS, DriveItem, DriveRemote, MacPackage
from .engine import Callbacks, Cancelled, SyncError, check_free_space, inside_root, now_iso, transfer_text, THROTTLE
from .i18n import t
from .remote import AuthRequired, RemoteUnavailable

log = logging.getLogger(__name__)

MARKER = ".skyfold-drive"
TMP_PREFIX = ".skyfold-tmp-"
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
    pending INTEGER NOT NULL DEFAULT 0  -- 1: PC version must be uploaded (iCloud copy was trashed)
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

    def all(self) -> dict[str, Base]:
        with self._lock:
            rows = self._db.execute("SELECT path, is_dir, etag, size, lmtime, lsize, pending FROM drive_files")
            return {r[0]: Base(r[0], bool(r[1]), r[2], r[3], r[4], r[5], bool(r[6])) for r in rows}

    def put(self, b: Base) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO drive_files VALUES(?,?,?,?,?,?,?)",
                             (b.path, int(b.is_dir), b.etag, b.size, b.lmtime, b.lsize, int(b.pending)))

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
        with self._lock:
            self._db.execute("DELETE FROM drive_files")


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
        self.cancel_flag = threading.Event()
        self.unsupported: set[str] = set()
        self.folders: list[str] | None = None  # chosen Drive folders; None = all of iCloud Drive
        # Called (from the Drive thread) as soon as a big deletion needs the user's OK, so the
        # question appears right away instead of after a long download.
        self.on_pending: Callable[[list[str], list[str]], None] | None = None
        self._late: tuple[dict, dict, DriveResult] | None = None
        self.mac_packages: set[str] = set()  # left in iCloud: Macs show these folders as one file
        self.approved: set[str] = set()
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
        except (OSError, ValueError, KeyError, TypeError):
            self.cache = {}

    def _save_cache(self) -> None:
        if not self.cache_file:
            return
        try:
            tmp = self.cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"root": str(self.root),
                                       "items": [asdict(i) for i in self.cache.values()]}), encoding="utf-8")
            os.replace(tmp, self.cache_file)
        except OSError as exc:
            log.warning("Could not save Drive cache: %s", exc)

    # helpers ------------------------------------------------------------------------
    def _abs(self, rel: str) -> Path:
        return self.root / Path(rel)

    def _touch_own(self, p: Path) -> None:
        self.own_paths[str(p).lower()] = time.monotonic()

    def is_own_path(self, p: str) -> bool:
        ts = self.own_paths.get(str(Path(p)).lower())
        return ts is not None and time.monotonic() - ts < 10

    def _check_cancel(self) -> None:
        if self.cancel_flag.is_set():
            raise Cancelled()

    def _check_root(self, has_base: bool) -> None:
        if not self.root.exists():
            if has_base:
                raise SyncError(t("Drive folder is not available: {path}", path=self.root))
            self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / MARKER
        account_line = f"account: {self.account}" if self.account else ""
        if not marker.exists():
            if has_base:
                raise SyncError(t(
                    "The Drive folder doesn't look like before (its sync marker is missing). "
                    "Syncing has stopped so nothing gets deleted by mistake."))
            marker.write_text(t("This folder is synced by Skyfold. Don't delete this file.\n")
                              + (account_line + "\n" if account_line else ""), encoding="utf-8")
            if os.name == "nt":
                import ctypes

                ctypes.windll.kernel32.SetFileAttributesW(str(marker), 0x2)
            return
        if not account_line:
            return
        try:
            text = marker.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise SyncError(t("Couldn't read the Drive folder's sync marker: {error}", error=exc)) from exc
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
        if not marker.exists():
            return
        try:
            text = marker.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise SyncError(t("Couldn't read the Drive folder's sync marker: {error}", error=exc)) from exc
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
                out[rel] = Local(True, 0, 0.0)
                if d.startswith("."):
                    hide_like_mac(Path(dirpath) / d)
            for f in files:
                if f.startswith(ASIDE_PREFIX):
                    self._recover_aside(Path(dirpath) / f)
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
        from send2trash import send2trash

        self._check_inside(p.parent)
        self._touch_own(p)
        send2trash(str(p))

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
                path = str(PurePosixPath(path).parent) if "/" in path else ""
            folders[path] = self.cache[path]
        return list(folders.values())

    def scan_dirs(self, folders: list[str]) -> dict[str, Local]:
        """Direct contents of some folders (quick sync), same shape as scan_local()."""
        out: dict[str, Local] = {}
        self.unreadable = set()
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

    def run(self, changed: set[str] | None = None) -> DriveResult:
        """Full sync, or a quick one limited to the folders around `changed` local paths."""
        self.cancel_flag.clear()
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
        self._prev = dict(self.cache)  # iCloud as of the last read: identities for rename detection
        if hasattr(self.remote, "incomplete"):
            self.remote.incomplete = set()
        res = DriveResult()
        base = self.state.all()  # (after the folder check above)
        everything_known = dict(base)  # before a quick pass narrows `base` to a few folders
        self._check_root(bool(base))

        scope = self._scope_folders(changed) if changed else None
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
        remote = {p: r for p, r in remote.items() if not _ignored_path(p)}
        base = {p: b for p, b in base.items() if not _ignored_path(p)}
        if self.folders is not None:  # only the chosen folders: everything else is left alone
            remote = {p: r for p, r in remote.items() if in_scope(p, r.is_dir, self.folders)}
            local = {p: lo for p, lo in local.items() if in_scope(p, lo.is_dir, self.folders)}
            for p in [p for p, b in base.items() if not in_scope(p, b.is_dir, self.folders)]:
                self.state.drop(p)  # e.g. written by a pass that ran while the choice changed
                del base[p]
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
                if f in base and f in remote and f not in local and not self._abs(f).exists():
                    log.info("Drive: chosen folder %s is gone on this PC; downloading it again", f)
                    self.cb.activity("info", t("Folder {name} is missing on this PC, it is downloaded again "
                                               "(it isn't deleted in iCloud).", name=f))
                    for p in [p for p in base if p == f or p.startswith(f + "/")]:
                        self.state.drop(p)
                        del base[p]
        if scope is not None:
            paths = [p for p in paths if _parent(p) in dirs]  # only the re-read folders' contents
        for path in paths:
            r, lo, b = remote.get(path), local.get(path), base.get(path)
            if partial and (listed_partly(path) or path in partial):
                continue  # iCloud didn't list this folder completely: nothing is done in it this pass
            if r and not lo and not b and self._skip_unsupported(path):
                continue
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
                    else:
                        mkdir_local.append(r)
                elif lo and not r:
                    if b and b.etag is not None and listed_partly(path):
                        pass  # iCloud's answer for its folder was incomplete: decide next time
                    elif b and b.etag is not None:
                        to_pc.append(path)  # folder deleted in iCloud
                    elif not b:
                        mkdir_remote.append(path)
                    # else: we created it last pass and iCloud hasn't listed it yet: wait
                elif b:
                    self.state.drop(path)
                continue

            settled = lo is not None and not (0 <= now - lo.mtime < SETTLE_SECONDS)
            if r and lo:
                same = r.size == lo.size and r.mtime is not None and abs(r.mtime - lo.mtime) < 1.0
                if b is None:
                    if same:  # same file on both sides (e.g. first sync): adopt
                        self.state.put(Base(path, False, r.etag, r.size, lo.mtime, lo.size, False))
                    else:
                        self._conflict(path, r, res, downloads)
                    continue
                if b.pending:  # a replacement upload that didn't finish: the PC version still has to go up
                    if settled:
                        uploads.append(path)
                    continue
                if b.etag is None:  # we uploaded or renamed it: learn its iCloud version, if it is ours
                    if r.size == b.size:
                        self.state.put(Base(path, False, r.etag, r.size, b.lmtime, b.lsize, False))
                        remote_changed = False
                    else:
                        remote_changed = True  # edited in iCloud meanwhile
                else:
                    remote_changed = r.etag != b.etag
                local_changed = (lo.size, round(lo.mtime, 2)) != (b.lsize, round(b.lmtime or 0, 2))
                if remote_changed and not local_changed and b.etag is not None and r.size == b.size                         and r.mtime is not None and b.lmtime is not None and abs(r.mtime - b.lmtime) < 1.0:
                    # only iCloud's version tag moved (opened on a phone, folder renamed): content as before
                    self.state.put(Base(path, False, r.etag, r.size, b.lmtime, b.lsize, False))
                    continue
                if remote_changed and not local_changed and same and r.mtime is not None and r.size == b.size:
                    # Only iCloud's version tag changed (e.g. the file was opened on the iPhone, which
                    # records "last opened"): same size and same modified time = same content.
                    self.state.put(Base(path, False, r.etag, r.size, b.lmtime, b.lsize, False))
                    continue
                if remote_changed and local_changed and r.size == lo.size and r.mtime is not None \
                        and abs(r.mtime - lo.mtime) < 0.01:  # we gave it exactly iCloud's time: our own download
                    # already the iCloud version (e.g. downloaded just before a crash): remember it
                    self.state.put(Base(path, False, r.etag, r.size, lo.mtime, lo.size, False))
                elif remote_changed and local_changed:
                    self._conflict(path, r, res, downloads)
                elif remote_changed:
                    downloads.append(r)
                elif local_changed and settled:
                    uploads.append(path)
            elif r and not lo:
                if b and not b.pending and b.etag is not None and r.etag != b.etag:
                    self.state.drop(path)
                    downloads.append(r)  # deleted on PC, but edited in iCloud since: keep the edit
                elif b and not b.pending:
                    to_cloud.append(path)  # deleted on PC
                else:
                    downloads.append(r)  # new in iCloud
            elif lo and not r and b and listed_partly(path):
                pass  # iCloud's answer for its folder was incomplete: never read that as "deleted"
            elif lo and not r:
                if b and b.pending:
                    if settled:
                        uploads.append(path)  # replacement upload that didn't finish: retry
                elif b and b.etag is not None and (lo.size, round(lo.mtime, 2)) != (b.lsize, round(b.lmtime or 0, 2)):
                    self.state.drop(path)  # deleted in iCloud, but edited on this PC since: keep the edit
                    if settled:
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

        if scope is not None and any(base.get(p) is not None and base[p].is_dir for p in to_cloud + to_pc):
            # A folder went away on one side. A quick pass doesn't see what is inside it (or where it
            # went, if it was renamed): only a full read can decide safely.
            log.info("Drive: a folder was removed or renamed; doing a full read instead of a quick one")
            return self.run()
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
                self.cb.activity("error", t("Couldn't create folder ({name}): {error}", name=r.path, error=exc))
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
        if scope is None:  # approvals only count for deletions that are still waiting
            self.approved &= set(res.pending_to_cloud) | set(res.pending_to_pc)
        if res.pending_to_cloud or res.pending_to_pc:
            self._late = (remote, base, res)
            if self.on_pending:
                self.on_pending(list(res.pending_to_cloud), list(res.pending_to_pc))

        # Folders first, then files.
        for path in mkdir_remote:
            parent = remote.get(str(PurePosixPath(path).parent) if "/" in path else "")
            if parent is None:
                continue  # parent is being created this round; next run creates this one
            try:
                self.remote.mkdir(parent, PurePosixPath(path).name)
                self.state.put(Base(path, True, None, None, None, None, False))
                res.followup = True
            except (AuthRequired, RemoteUnavailable):
                raise
            except Exception as exc:  # noqa: BLE001
                res.errors += 1
                self.cb.activity("error", t("Couldn't create folder in iCloud ({name}): {error}", name=path, error=exc))

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
        for p in to_pc:
            b = base.get(p)
            if b is None or not b.is_dir:
                out.append(p)
                continue
            dirty, clean = False, []
            unreadable: list[str] = []
            for dirpath, _dirs, files in os.walk(self._abs(p), onerror=lambda e: unreadable.append(str(e))):
                for d in _dirs:
                    if _is_link(Path(dirpath) / d):
                        unreadable.append(f"link: {Path(dirpath) / d}")
                rel_dir = Path(dirpath).relative_to(self.root).as_posix()
                for f in files:
                    if ignored(f):
                        continue
                    rel = f"{rel_dir}/{f}"
                    fb = base.get(rel) or known.get(rel)
                    try:
                        st = os.stat(Path(dirpath) / f)
                    except OSError:
                        dirty = True
                        continue
                    if fb is None or (st.st_size, round(st.st_mtime, 2)) != (fb.lsize, round(fb.lmtime or 0, 2)):
                        dirty = True
                        if fb is not None:
                            self.state.drop(rel)
                    else:
                        clean.append(rel)
            if unreadable:
                log.warning("Drive: %s couldn't be read completely; not deleted (%s)", p, unreadable[:2])
                continue  # can't check what's inside: decide another time
            if not dirty:
                out.append(p)
                continue
            log.info("Drive: %s was deleted in iCloud but has changes on this PC; keeping them", p)
            self.cb.activity("info", t("Deleted in iCloud, but changed on this PC: kept {name}", name=p))
            for q in [q for q in list(base) if q == p or q.startswith(p + "/")]:
                if base[q].is_dir:
                    self.state.drop(q)  # recreated in iCloud on the next pass
            out.extend(clean)
        return out

    def _keep_unsynced_remote(self, to_cloud: list[str], remote: dict[str, DriveItem],
                              base: dict[str, Base]) -> list[str]:
        """A folder deleted on this PC that holds new or edited files in iCloud: those come back
        down; only what is unchanged since the last sync goes to Recently Deleted."""
        import bisect

        out: list[str] = []
        known = self.state.all() if to_cloud else {}
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
            if not all_below:
                out.append(p)  # nothing inside listed (e.g. a quick pass): judged by its files' rows
                continue
            changed = [q for q in inside if known.get(q) is None
                       or known[q].etag not in (None, remote[q].etag)]
            if not changed:
                out.append(p)
                continue
            log.info("Drive: %s was deleted on this PC but has changes in iCloud; keeping them", p)
            self.cb.activity("info", t("Deleted on this PC, but changed in iCloud: kept {name}", name=p))
            for q in [q for q in list(base) if q == p or q.startswith(p + "/")]:
                self.state.drop(q)  # comes back down on the next pass
            out.extend(q for q in inside if q not in changed)
        return out

    def state_set_last_sync(self) -> None:
        self.last_sync = now_iso()

    # steps ---------------------------------------------------------------------------
    def _conflict(self, path: str, r: DriveItem, res: DriveResult, downloads: list[DriveItem]) -> None:
        """Both sides changed: iCloud's copy keeps the name; the PC copy is renamed and uploaded later."""
        src = self._abs(path)
        dst_rel = self._conflict_name(path)
        try:
            os.replace(src, self._abs(dst_rel))
        except OSError as exc:
            res.errors += 1
            self.cb.activity("error", t("Couldn't resolve conflict ({name}): {error}", name=path, error=exc))
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
            return any(q == top or q.startswith(top + "/") or top.startswith(q + "/") for q in partial for top in tops)

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
                if any(n.startswith(m + "/") for m in new if m != n) or not self._remote_move(remote, g, n, res):
                    continue
                self._rebase(base, g, n, keep_etags=False)
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
                if not self._same_item(g, n, remote) or not self._local_move(g, n, res):
                    continue  # not provably the same folder (e.g. deleted and a look-alike created)
                self._rebase(base, g, n, keep_etags=True, remote=remote)
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
                if not self._remote_move(remote, g, n, res):
                    continue
                lo = local[n]
                self.state.drop(g)
                self.state.put(Base(n, False, base[g].etag, base[g].size, lo.mtime, lo.size, False))
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
                if not self._same_item(g, n, remote) or not self._local_move(g, n, res):
                    continue
                r = remote[n]
                st = self._abs(n).stat()
                self.state.drop(g)
                self.state.put(Base(n, False, r.etag, r.size, st.st_mtime, st.st_size, False))
                to_pc.remove(g)
                downloads[:] = [d for d in downloads if d.path != n]

    def _same_item(self, old: str, new: str, remote: dict[str, DriveItem]) -> bool:
        """iCloud keeps an item's id when it is renamed or moved; a deleted and re-created look-alike
        gets a new one."""
        before, now = self._prev.get(old), remote.get(new)
        return bool(before and now and before.docwsid and before.docwsid == now.docwsid)

    def _remote_move(self, remote: dict[str, DriveItem], old: str, new: str, res: DriveResult) -> bool:
        item = remote.get(old)
        parent = remote.get(_parent(new))
        if item is None or parent is None or not parent.is_dir:
            return False  # the new place doesn't exist in iCloud yet: sync as add + delete
        try:
            if _parent(old) != _parent(new):
                etag = self.remote.move(item, parent)
                item = DriveItem(item.path, item.drivewsid, item.docwsid, item.zone, etag or item.etag,
                                 item.is_dir, item.size, item.mtime)
            if PurePosixPath(old).name != PurePosixPath(new).name:
                self.remote.rename(item, PurePosixPath(new).name)
        except (AuthRequired, RemoteUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Rename in iCloud failed (%s -> %s): %s", old, new, exc)
            return False
        self._forget_cached(old)
        res.moved += 1
        res.followup = True  # the next pass learns the new iCloud versions
        self.cb.activity("upload", t("Drive: renamed/moved in iCloud: {old} → {new}", old=old, new=new),
                         str(self._abs(new)))
        return True

    def _forget_cached(self, path: str) -> None:
        prefix = path + "/"
        for p in [p for p in self.cache if p == path or p.startswith(prefix)]:
            del self.cache[p]

    def _local_move(self, old: str, new: str, res: DriveResult) -> bool:
        src, dst = self._abs(old), self._abs(new)
        try:
            if old.lower() == new.lower() and src.exists():
                pass  # only the letter case changes: Windows sees one name, rename in place
            elif dst.exists():
                if dst.is_dir() and not any(dst.iterdir()):
                    dst.rmdir()  # an empty folder made for the new name: replace it
                else:
                    return False
            dst.parent.mkdir(parents=True, exist_ok=True)
            self._touch_own(src)
            self._touch_own(dst)
            os.replace(src, dst)
        except OSError as exc:
            log.warning("Rename on this PC failed (%s -> %s): %s", old, new, exc)
            return False
        res.moved += 1
        self.cb.activity("download", t("Drive: renamed/moved on this PC: {old} → {new}", old=old, new=new), str(dst))
        return True

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
                self.state.put(Base(q, False, etag, b.size, b.lmtime, b.lsize, False))

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

        def shape(d: dict[str, DriveItem]) -> dict[str, object]:
            if not item.is_dir:
                r = d.get(p)
                return {p: None if r is None else r.etag}
            return {q: ("dir" if r.is_dir else r.etag) for q, r in d.items()
                    if q.startswith(p + "/") and not _ignored_path(q)}

        return shape(now) == shape(remote)

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

        def under_any(p: str, tops: set[str]) -> bool:
            while True:
                if p in tops:
                    return True
                if "/" not in p:
                    return False
                p = p.rsplit("/", 1)[0]

        def count(paths: list[str], _tree: dict) -> int:
            tops = set(paths)
            n = sum(1 for p, b in everything.items() if not b.is_dir and under_any(p, tops)) or len(paths)
            files = sum(1 for b in everything.values() if not b.is_dir)
            if n >= 5 and n > files // 2:
                return threshold + 1  # more than half of everything: always ask
            return n

        if to_cloud:
            if not self.delete_pc_to_cloud:
                for p in to_cloud:  # keep in iCloud: download it again
                    self.state.drop(p)
            elif count(to_cloud, base) > threshold and not set(to_cloud) <= self.approved:
                res.pending_to_cloud = to_cloud
            else:
                for p in to_cloud:
                    self._check_cancel()
                    if self._abs(p).exists():  # back on this PC since the question: not deleted any more
                        self.state.drop(p)
                        continue
                    try:
                        self.remote.trash(remote[p])
                    except (AuthRequired, RemoteUnavailable):
                        raise
                    except Exception as exc:  # noqa: BLE001
                        res.errors += 1
                        self.cb.activity("error", t("Couldn't delete in iCloud ({name}): {error}", name=p, error=exc))
                        continue
                    self.state.drop(p)
                    self._forget_cached(p)
                    res.deleted_cloud += 1
                    self.cb.activity("cloud", t("Drive: deleted in iCloud: {name}", name=p))
        if to_pc:
            if not self.delete_cloud_to_pc:
                for p in to_pc:  # keep on PC: it will be uploaded again
                    self.state.drop(p)
            elif count(to_pc, base) > threshold and not set(to_pc) <= self.approved:
                res.pending_to_pc = to_pc
            else:
                approved_now = set(to_pc) & self.approved
                if approved_now:  # time has passed since the question: changed files stay
                    to_pc = self._keep_unsynced_local(to_pc, base)
                for p in to_pc:
                    self._check_cancel()
                    target = self._abs(p)
                    b = base.get(p)
                    if b is not None and ((not b.is_dir and target.is_dir()) or (b.is_dir and target.is_file())):
                        self.state.drop(p)  # file and folder swapped since the question: not what was approved
                        continue
                    if b is not None and not b.is_dir and target.is_file():
                        try:
                            st = target.stat()
                            if (st.st_size, round(st.st_mtime, 2)) != (b.lsize, round(b.lmtime or 0, 2)):
                                self.state.drop(p)  # edited on this PC meanwhile: keep it (it goes up)
                                continue
                        except OSError:
                            continue
                    try:
                        if target.exists():
                            self._trash_local(target)
                    except OSError as exc:
                        res.errors += 1
                        self.cb.activity("error", t("Couldn't delete {name}: {error}", name=p, error=exc))
                        continue
                    self.state.drop(p)
                    res.deleted_local += 1
                    self.cb.activity("trash", t("Drive: deleted on this PC (Recycle Bin): {name}", name=p))
        self.approved -= set(to_cloud) | set(to_pc)

    def _downloads(self, items: list[DriveItem], base: dict[str, Base], res: DriveResult) -> None:
        items = [i for i in items if i.path not in self.mac_packages]
        if not items:
            return
        total_bytes = sum(i.size for i in items)
        check_free_space(self.root, total_bytes)
        total = total_bytes or len(items)
        done = 0
        done_lock = threading.Lock()
        replaced: list[tuple[Path, str]] = []  # previous versions, to the Recycle Bin from this thread

        def recycle_replaced() -> None:
            with done_lock:
                old, replaced[:] = list(replaced), []
            for p, rel in old:
                self._recycle_previous(p, rel)

        def on_bytes(k: int) -> None:
            nonlocal done
            THROTTLE.take(k)
            with done_lock:
                done += k
            if self.cancel_flag.is_set():
                raise Cancelled()

        def fetch(item: DriveItem) -> Path:
            """Runs on a worker thread: download to a temporary file and put it in place."""
            if self.cancel_flag.is_set():
                raise Cancelled()
            target = self._abs(item.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._check_inside(target.parent)
            fd, tmp = tempfile.mkstemp(prefix=TMP_PREFIX, dir=target.parent)
            os.close(fd)
            tmp_path = Path(tmp)
            try:
                self.remote.download(item, tmp_path, on_bytes)
                if item.size >= 0 and tmp_path.stat().st_size != item.size:
                    with open(tmp_path, "rb") as fh:
                        zipped = fh.read(4) == b"PK"
                    if zipped and not item.path.lower().endswith(".zip"):
                        raise MacPackage(item.path)  # iCloud zipped a Mac package (e.g. .xcodeproj) for us
                    raise SyncError(t("file size doesn't match"))
                self._touch_own(target)
                b = base.get(item.path)
                aside = None
                if target.exists() and target.is_file():
                    aside = target.with_name(f"{ASIDE_PREFIX}{os.getpid()}-{threading.get_ident()}-{target.name}")
                    os.rename(target, aside)  # moved, never overwritten (whatever is saved there right now)
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
                    st = aside.stat()
                    if b is not None and (st.st_size, round(st.st_mtime, 2)) == (b.lsize, round(b.lmtime or 0, 2)):
                        # Most likely the version both sides had, but only its size and time say so:
                        # it goes to the Recycle Bin, never deleted for good.
                        with done_lock:
                            replaced.append((aside, item.path))
                    else:  # changed on this PC since the scan: keep that version under a conflict name
                        self._release_aside(aside, item.path, restore=False)
                if item.mtime:
                    os.utime(target, (item.mtime, item.mtime))
                hide_like_mac(target)
                return target
            finally:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)

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
                while len(running) < WORKERS and not self.cancel_flag.is_set():
                    item = next(todo, None)
                    if item is None:
                        break
                    running[pool.submit(fetch, item)] = item
                if not running:
                    break
                ready, _ = wait(running, timeout=0.3, return_when=FIRST_COMPLETED)
                fatal: BaseException | None = None
                for fut in ready:
                    item = running.pop(fut)
                    finished += 1
                    try:
                        target = fut.result()
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
                        self.cb.activity("error", t("Download failed ({name}): {error}", name=item.path, error=exc))
                        continue
                    st = target.stat()
                    self.state.put(Base(item.path, False, item.etag, item.size, st.st_mtime, st.st_size, False))
                    res.downloaded += 1
                    res.last_down = str(target) if not res.downloaded - 1 else str(self.root)
                    res.down_files.append(item.path)
                recycle_replaced()
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
            self.cancel_flag.set()  # make downloads still running stop at their next chunk
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
                return
            except OSError:
                continue
        log.warning("Drive: the PC version of %s is kept as %s; shown again on the next full sync", rel, aside.name)

    def _recycle_previous(self, aside: Path, rel: str) -> None:
        """The version replaced by iCloud's newer one goes to the Recycle Bin, under a readable name."""
        p = PurePosixPath(rel)
        named = self._abs(str(p.with_name(f"{p.stem} ({t('previous version')}){p.suffix}")))
        try:
            if not named.exists():
                self._touch_own(named)
                os.rename(aside, named)
                aside = named
        except OSError:
            pass
        try:
            self._trash_local(aside)
        except Exception as exc:  # noqa: BLE001 - kept; shown as a conflict copy on the next full sync
            log.warning("Drive: couldn't move the previous version of %s to the Recycle Bin: %s", rel, exc)
            if aside == named:
                self._release_aside(aside, rel, restore=False)

    def _recover_aside(self, p: Path) -> None:
        """Left behind by an interrupted download (crash, power cut): shown again as a conflict copy."""
        parts = p.name[len(ASIDE_PREFIX):].split("-", 2)
        name = parts[2] if len(parts) == 3 and parts[2] else "file"
        try:
            rel = (p.parent / name).relative_to(self.root).as_posix()
        except ValueError:
            return
        log.info("Drive: recovering %s left by an interrupted download", rel)
        self._release_aside(p, rel, restore=False)

    def _uploads(self, paths: list[str], remote: dict[str, DriveItem], base: dict[str, Base],
                 local: dict[str, Local], res: DriveResult) -> None:
        for n, path in enumerate(paths, 1):
            self._check_cancel()
            self.cb.progress(n - 1, len(paths), t("Drive: uploading {i} of {n}", i=n, n=len(paths)))
            parent_path = str(PurePosixPath(path).parent) if "/" in path else ""
            parent = remote.get(parent_path)
            if parent is None or not parent.is_dir:
                res.followup = True  # parent folder is created this round; the file follows next run
                continue
            existing = remote.get(path)
            lo = local[path]
            try:
                self._check_inside(self._abs(path))
                data = self._abs(path).read_bytes()  # locked or unreadable: fail before touching iCloud
                if existing is not None:
                    # Replacing a file: move the old iCloud version to Recently Deleted first.
                    # Mark the row "pending" so a failed upload is retried, never mistaken for a deletion.
                    self.state.put(Base(path, False, None, existing.size, lo.mtime, lo.size, True))
                    self.remote.trash(existing)
                self.remote.upload(parent, self._abs(path), data)
                self.state.put(Base(path, False, None, len(data), lo.mtime, lo.size, False))
                res.uploaded += 1
                res.last_up = res.last_up or str(self._abs(path))
                res.up_files.append(path)
            except (AuthRequired, RemoteUnavailable):
                raise
            except Exception as exc:  # noqa: BLE001
                res.errors += 1
                self.cb.activity("error", t("Upload failed ({name}): {error}", name=path, error=exc))


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
    return tuple(sorted((k.casefold(), v or 0) for k, v in files.items())) or None


def _pairs(old: dict, new: dict) -> list[tuple[str, str]]:
    """One-to-one matches by key; keys shared by several entries on either side are skipped."""
    from collections import Counter

    c_old = Counter(v for v in old.values() if v is not None)
    c_new = Counter(v for v in new.values() if v is not None)
    by_key = {v: k for k, v in new.items() if v is not None and c_new[v] == 1}
    return [(k, by_key[v]) for k, v in old.items() if v is not None and c_old[v] == 1 and v in by_key]


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
