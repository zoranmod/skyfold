"""Two-way sync logic: new photos come down, deletions travel in both directions.

Nothing here deletes permanently: local files go to the Recycle Bin and iCloud deletions
land in "Recently Deleted" for 30 days.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from .config import Settings
from .i18n import t
from .safelog import brief
from .remote import AuthRequired, Remote, RemoteAsset, RemoteResource, RemoteUnavailable
from .state import FileRow, State

log = logging.getLogger(__name__)

MARKER = ".skyfold"
ALBUMS_DIR = "Albums"  # album mirror inside the photos folder (hard links)
ALBUM_REFRESH_SECONDS = 30 * 60
LEGACY_MARKERS = (".isync", ".galerija-sync")
TMP_PREFIX = ".skyfold-tmp-"
UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')


class SyncError(Exception):
    pass


DISK_RESERVE = 2 * 1024**3  # always leave this much free on the disk


def recycle(path: Path) -> None:
    """Move to the Windows Recycle Bin, and never anything less. Windows itself deletes for good
    whatever it can't recycle (network shares, subst drives, a disabled or full Recycle Bin), so
    every item is checked as Windows is about to delete it and the whole operation is stopped if
    it wouldn't go to the Recycle Bin. Raises OSError then; the file stays where it is."""
    if os.name != "nt":
        from send2trash import send2trash

        send2trash(str(path))
        return
    import ctypes

    p = os.path.abspath(str(path))
    if p.startswith("\\\\"):
        raise OSError(t("{name} is on a network drive without a Recycle Bin; not deleted", name=p))
    drive_type = ctypes.windll.kernel32.GetDriveTypeW(os.path.splitdrive(p)[0] + "\\")
    if drive_type in (2, 4, 5):  # removable, network, CD: no Recycle Bin
        raise OSError(t("{name} is on a drive without a Recycle Bin; not deleted", name=p))
    _recycle_windows(p)


def _recycle_windows(p: str) -> None:
    import pythoncom
    import pywintypes
    from win32com.server.exception import COMException
    from win32com.server.policy import DesignatedWrapPolicy
    from win32com.shell import shell, shellcon

    class Sink(DesignatedWrapPolicy):
        _com_interfaces_ = [shell.IID_IFileOperationProgressSink]
        _public_methods_ = ["StartOperations", "FinishOperations", "PreRenameItem", "PostRenameItem",
                            "PreMoveItem", "PostMoveItem", "PreCopyItem", "PostCopyItem", "PreDeleteItem",
                            "PostDeleteItem", "PreNewItem", "PostNewItem", "UpdateProgress", "ResetTimer",
                            "PauseTimer", "ResumeTimer"]

        def __init__(self) -> None:
            self._wrap_(self)
            self.refused = False

        def PreDeleteItem(self, flags, item):  # noqa: N802 - COM naming
            if not flags & shellcon.TSF_DELETE_RECYCLE_IF_POSSIBLE:
                self.refused = True  # it would be deleted for good: stop (only an exception stops it)
                raise COMException(hresult=-2147467260)  # E_ABORT
            return 0

        def __getattr__(self, name):  # every other callback: "carry on"
            if name in Sink._public_methods_:
                return lambda *a: 0
            raise AttributeError(name)

    pythoncom.CoInitialize()
    try:
        sink_obj = Sink()
        sink = pythoncom.WrapObject(sink_obj, shell.IID_IFileOperationProgressSink)
        op = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_ALL,
                                        shell.IID_IFileOperation)
        op.SetOperationFlags(shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_NOERRORUI
                             | shellcon.FOF_SILENT | shellcon.FOFX_EARLYFAILURE
                             | 0x20000000 | 0x00080000)  # FOFX_ADDUNDORECORD | FOFX_RECYCLEONDELETE
        try:
            op.DeleteItem(shell.SHCreateItemFromParsingName(p, None, shell.IID_IShellItem), sink)
            result = op.PerformOperations()
            aborted = op.GetAnyOperationsAborted()
        except pywintypes.com_error as exc:
            if sink_obj.refused:
                raise OSError(t("{name} can't go to the Recycle Bin; not deleted", name=p)) from exc
            raise OSError(None, exc.strerror, p) from exc
        if sink_obj.refused:
            raise OSError(t("{name} can't go to the Recycle Bin; not deleted", name=p))
        if result or aborted:
            raise OSError(t("{name} couldn't be moved to the Recycle Bin", name=p))
    finally:
        pythoncom.CoUninitialize()


def _is_link(p: Path) -> bool:
    try:
        return p.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(p))
    except OSError:
        return True


def inside_root(root: Path, p: Path) -> bool:
    """Is p really inside root once links/junctions are followed? (p may not exist yet: its nearest
    existing parent is checked.)"""
    q = Path(p)
    while not q.exists() and q.parent != q:
        q = q.parent
    real_root = Path(os.path.realpath(root))
    real = Path(os.path.realpath(q))
    return real == real_root or real_root in real.parents


def check_free_space(folder: Path, needed: int) -> None:
    """Refuse to start downloads that would fill the disk (keeps a 2 GB reserve)."""
    import shutil

    probe = folder
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if needed > free - DISK_RESERVE:
        from .ui_text import size_text

        raise SyncError(t("Not enough disk space: {need} to download, {free} free on {drive}. "
                          "Free up space or choose a folder on another disk in Settings.",
                          need=size_text(needed), free=size_text(free), drive=probe.anchor or str(probe)))


class Cancelled(Exception):
    pass


@dataclass
class Pending:
    """Deletions held back because there were more than the safety threshold."""

    to_cloud: list[str] = field(default_factory=list)  # deleted on PC → delete in iCloud?
    to_pc: list[str] = field(default_factory=list)  # deleted in iCloud → delete on PC?
    names: dict[str, str] = field(default_factory=dict)
    service: str = "photos"  # "photos" or "drive"

    def __bool__(self) -> bool:
        return bool(self.to_cloud or self.to_pc)


@dataclass
class Result:
    full_scan: bool = False
    downloaded: int = 0
    deleted_cloud: int = 0
    deleted_local: int = 0
    errors: int = 0
    pending: Pending | None = None


class Throttle:
    """Keeps downloads (Photos and Drive together) under a set speed, so syncing doesn't take the
    whole connection. Download threads call take() with each chunk and sleep when ahead."""

    def __init__(self) -> None:
        self._rate = 0.0  # bytes per second; 0 = no limit
        self._lock = threading.Lock()
        self._next = 0.0  # when the line is free again (monotonic seconds)

    @property
    def rate(self) -> float:
        return self._rate

    @rate.setter
    def rate(self, value: float) -> None:
        with self._lock:
            if value != self._rate:
                self._rate = value
                self._next = 0.0  # a new limit starts fresh

    def take(self, n: int) -> None:
        if self._rate <= 0 or n <= 0:
            return
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + n / self._rate
            wait = start - now
        while wait > 0:  # the whole wait, in short steps
            step = min(wait, 1.0)
            time.sleep(step)
            wait -= step


THROTTLE = Throttle()


def duration_text(seconds: float) -> str:
    if seconds < 60:
        return t("less than a minute")
    minutes = round(seconds / 60)
    if minutes < 60:
        return t("about {m} min", m=minutes)
    h, m = divmod(minutes, 60)
    return t("about {h} h {m} min", h=h, m=m) if m and h < 10 else t("about {h} h", h=h)


def transfer_text(prefix: str, i: int, n: int, done: int, total: int, rate) -> str:
    """'Drive: 3.413 of 9.935 files · 30,5 of 59,0 GB · 2,1 MB/s · about 1 h 20 min left'."""
    from .ui_text import count_text, size_text

    parts = [t("{prefix}{i} of {n} files", prefix=prefix, i=count_text(i), n=count_text(n))]
    if total > 0:
        parts.append(t("{done} of {total}", done=size_text(done), total=size_text(total)))
    bps, _fps = rate.speed()
    if bps > 0:
        parts.append(t("{speed}/s", speed=size_text(int(bps))))
    left = rate.seconds_left(total - done, n - i)
    if left is not None:
        parts.append(t("{time} left", time=duration_text(left)))
    return " · ".join(parts)


class Callbacks:
    """Overridden by the Qt worker; defaults are no-ops so the engine runs headless in tests."""

    def status(self, text: str) -> None: ...
    def progress(self, done: int, total: int, text: str) -> None: ...
    def activity(self, kind: str, text: str, target: str = "") -> None: ...


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_name(name: str) -> str:
    name = UNSAFE.sub("_", PurePosixPath(name.replace("\\", "/")).name).strip(" .")
    return name or "foto"


class Syncer:
    def __init__(self, state: State, remote: Remote, settings: Settings, cb: Callbacks | None = None) -> None:
        self.state = state
        self.remote = remote
        self.settings = settings
        self.cb = cb or Callbacks()
        self.cancel_flag = threading.Event()  # this pass (cleared when the next one starts)
        self.closing = threading.Event()  # the app is quitting: never cleared
        self.approved: set[str] = set()  # pending deletions the user confirmed
        self.held: set[str] = set()  # deletions currently waiting for the user's decision
        # Paths the engine itself touched recently, so the folder watcher can ignore them.
        self.own_paths: dict[str, float] = {}

    # helpers --------------------------------------------------------------
    @property
    def root(self) -> Path:
        return Path(self.settings.photos_dir)

    def _abs(self, rel: str) -> Path:
        return self.root / Path(rel)

    OWN_WINDOW = 10  # seconds a self-made deletion is ignored by the folder watcher

    def _touch_own(self, p: Path) -> None:
        """Remember a file *this app* is about to remove, so the watcher doesn't react to it.
        Only for our own deletions; never for downloads, or the user's deletes would be missed."""
        self.own_paths[str(p).lower()] = time.monotonic()
        cutoff = time.monotonic() - self.OWN_WINDOW
        for k in [k for k, t in self.own_paths.items() if t < cutoff]:
            self.own_paths.pop(k, None)

    def is_own_path(self, p: str) -> bool:
        t = self.own_paths.get(str(Path(p)).lower())
        return t is not None and time.monotonic() - t < self.OWN_WINDOW

    def _check_cancel(self) -> None:
        if self.cancel_flag.is_set() or self.closing.is_set():
            raise Cancelled()

    def _rel_for(self, asset: RemoteAsset, res: RemoteResource) -> str:
        local = asset.date.astimezone()
        fs = self.settings.folder_structure
        name = _safe_name(res.filename)
        if fs == "year_month":
            return f"{local:%Y}/{local:%m}/{name}"
        if fs == "year":
            return f"{local:%Y}/{name}"
        return name

    def _check_root(self) -> None:
        root = self.root
        has_files = bool(self.state.asset_ids())
        if not root.exists():
            if has_files:
                raise SyncError(t("Photos folder is not available: {path}", path=root))
            root.mkdir(parents=True, exist_ok=True)
        marker = root / MARKER
        for old in LEGACY_MARKERS:  # folder set up by an earlier version of the app
            if not marker.exists() and (root / old).exists():
                os.replace(root / old, marker)
        if not marker.exists():
            if has_files:
                raise SyncError(t(
                    "The photos folder doesn't look like before (its sync marker is missing). "
                    "Syncing has stopped so nothing gets deleted by mistake."
                ))
            marker.write_text(t("This folder is synced by Skyfold. Don't delete this file.\n"), encoding="utf-8")
            if os.name == "nt":
                import ctypes

                ctypes.windll.kernel32.SetFileAttributesW(str(marker), 0x2)  # hidden

    # local changes --------------------------------------------------------
    def scan_local(self) -> tuple[list[str], int]:
        """Return (asset ids whose main file was deleted on the PC, number of files relocated)."""
        rows = self.state.files()
        missing = [r for r in rows if not self._abs(r.rel_path).exists()]
        if not missing:
            return [], 0

        # The user may have moved files around inside the folder: follow them by name + size.
        tracked = {r.rel_path.lower() for r in rows}
        album_entries = {rel.lower() for rel in self.state.album_links()}  # same name and size as the originals
        index: dict[tuple[str, int], str] = {}
        for dirpath, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if not _is_link(Path(dirpath) / d)]  # a junction may lead anywhere
            for fn in files:
                if fn.startswith(TMP_PREFIX) or fn == MARKER:
                    continue
                full = Path(dirpath) / fn
                rel = full.relative_to(self.root).as_posix()
                if rel.lower() in tracked or rel.lower() in album_entries:
                    continue
                try:
                    index.setdefault((fn.lower(), full.stat().st_size), rel)
                except OSError:
                    pass

        moved = 0
        by_asset: dict[str, list[FileRow]] = defaultdict(list)
        for r in rows:
            by_asset[r.asset_id].append(r)
        deleted_assets: list[str] = []
        lone_videos: list[FileRow] = []
        # A file with the same name and size elsewhere in the folder may be this photo, moved - or
        # someone's own file. Only the content tells: iCloud's copy is downloaded and compared.
        maybe = {}
        for r in missing:
            key = (PurePosixPath(r.rel_path).name.lower(), r.size or -1)
            if key in index:
                maybe[(r.asset_id, r.resource_key)] = index.pop(key)
        confirmed, unknown = self._confirm_moves(maybe) if maybe else ({}, set())
        local_only: list[FileRow] = []
        for r in missing:
            where = (r.asset_id, r.resource_key)
            if where in unknown:
                continue  # couldn't be compared now: nothing is decided about it this pass
            new_rel = confirmed.get(where)
            if new_rel:
                self.state.move_file(r.asset_id, r.resource_key, new_rel)
                moved += 1
                continue
            if r.resource_key == "original" and where in maybe:
                # A look-alike exists but isn't this photo. Whether the photo was deleted or changed and
                # moved can't be told, so nothing is deleted in iCloud: it is only forgotten on this PC.
                local_only.append(r)
            elif r.resource_key == "original":
                deleted_assets.append(r.asset_id)
            else:
                lone_videos.append(r)
        for r in local_only:
            self._forget_local_only(r.asset_id, by_asset[r.asset_id])
            self.cb.activity("local", t("Deleted on this PC only: {name}", name=PurePosixPath(r.rel_path).name))
        for r in lone_videos:
            if r.asset_id in deleted_assets or any(x.asset_id == r.asset_id for x in local_only):
                continue  # the photo is gone too: decided together (and brought back together if asked)
            # Only the Live Photo video was removed: forget it and don't bring it back.
            self.state.remove_file(r.asset_id, r.resource_key)
            self.state.exclude(f"{r.asset_id}#{r.resource_key}", "video deleted on PC")
        return sorted(set(deleted_assets)), moved

    def _confirm_moves(self, maybe: dict[tuple[str, str], str]) -> tuple[dict[tuple[str, str], str], set]:
        """Which of the look-alike files really are the missing photos? Returns (confirmed moves,
        the ones that couldn't be checked). A file that differs is left alone as someone's own."""
        import filecmp

        confirmed: dict[tuple[str, str], str] = {}
        unknown: set[tuple[str, str]] = set()
        assets = self.remote.lookup(sorted({a for a, _ in maybe}))
        for (aid, key), rel in maybe.items():
            self._check_cancel()
            asset = assets.get(aid)
            res = next((x for x in asset.resources if x.key == key), None) if asset else None
            if res is None:
                unknown.add((aid, key))  # no answer from iCloud is not "it isn't this photo"
                continue
            fd, tmp = tempfile.mkstemp(prefix=TMP_PREFIX, dir=self.root)
            os.close(fd)
            tmp_path = Path(tmp)
            try:
                self.remote.download(res, tmp_path, lambda n: self._check_cancel())
                if (not res.size or tmp_path.stat().st_size == res.size) \
                        and filecmp.cmp(tmp_path, self._abs(rel), shallow=False):
                    confirmed[(aid, key)] = rel
            except (AuthRequired, RemoteUnavailable, Cancelled):
                raise
            except Exception as exc:  # noqa: BLE001
                log.info("Photos: couldn't compare %s with iCloud's copy: %s", rel, brief(exc))
                unknown.add((aid, key))
            finally:
                tmp_path.unlink(missing_ok=True)
        return confirmed, unknown

    def _trash(self, path: Path) -> None:
        self._touch_own(path)
        recycle(path)

    def _trash_inside(self, path: Path) -> None:
        """To the Recycle Bin, but only a file really inside the photos folder: a path that now runs
        through a link or junction may lead to someone else's file."""
        if not inside_root(self.root, path):
            raise OSError(t("{name} leads outside the photos folder; left alone", name=path))
        self._trash(path)

    # main entry -----------------------------------------------------------
    def run(self, force_full: bool = False) -> Result:
        self.cancel_flag.clear()
        self._check_cancel()  # quitting: this pass never starts
        self._verify: set[str] = set()  # paths whose existing file is compared with the download first
        result = Result()
        self._check_root()

        self.cb.status(t("Checking for changes…"))
        local_deleted, moved = self.scan_local()
        if moved:
            self.cb.activity("info", t("Followed {n} files moved inside the folder", n=moved))

        cursor = self.remote.cursor()
        stored = self.state.get("cursor")
        last_full = self.state.get("last_full_scan")
        stale = True
        if last_full:
            stale = datetime.now(timezone.utc) - datetime.fromisoformat(last_full) > timedelta(
                hours=max(1, self.settings.full_check_hours)
            )
        # A full scan reads the whole library; it is only needed the first time, as a periodic
        # safety net, on request, or when CloudKit can't give us a change feed.
        full = force_full or stale or not stored or bool(self.approved)
        local_new = set(local_deleted) - self.held
        if not full and cursor == stored and not local_new and not self.held:
            self.state.set("last_sync", now_iso())  # checked, nothing changed: still a check
            return result

        remote_assets: dict[str, RemoteAsset] = {}
        gone: set[str] = set()
        if not full:
            self.cb.status(t("Checking iCloud for changes…"))
            ch = self.remote.changes(stored) if cursor != stored else None
            if cursor != stored and ch is None:
                full = True  # change feed unusable (e.g. expired token)
            else:
                if ch is not None:
                    remote_assets = {a.id: a for a in ch.alive}
                    gone = set(ch.gone)
                    cursor = ch.cursor or cursor
                # Photos deleted on this PC: fetch their current iCloud record so they can be deleted there.
                need = [a for a in local_deleted if a not in remote_assets and a not in gone]
                if need:
                    found = self.remote.lookup(need)
                    remote_assets.update(found)
                    if any(a not in found for a in need):
                        full = True  # no answer isn't "deleted in iCloud": the whole library decides

        if full:
            result.full_scan = True
            self.cb.status(t("Reading your iCloud library…"))
            remote_assets = {}
            for a in self.remote.list_assets(lambda n: self.cb.status(t("Reading your iCloud library… {n}", n=n))):
                self._check_cancel()
                remote_assets[a.id] = a

        known_ids = self.state.asset_ids()
        rows_by_asset: dict[str, list[FileRow]] = defaultdict(list)
        for r in self.state.files():
            rows_by_asset[r.asset_id].append(r)

        pending = Pending()
        threshold = max(1, self.settings.delete_threshold)

        # 1) deleted on the PC ------------------------------------------------
        to_cloud = [a for a in local_deleted if a in remote_assets]
        for a in local_deleted:
            if a not in remote_assets:  # gone on both sides already
                self.state.remove_asset(a)
        if to_cloud:
            if not self.settings.delete_pc_to_cloud:
                for a in to_cloud:
                    self._forget_local_only(a, rows_by_asset[a])
                    self.cb.activity("local", t("Deleted on this PC only: {name}", name=remote_assets[a].filename))
            elif len(to_cloud) > threshold and not set(to_cloud) <= self.approved:
                pending.to_cloud = to_cloud
            else:
                for i, a in enumerate(to_cloud, 1):
                    self._check_cancel()
                    self.cb.progress(i, len(to_cloud), t("Deleting in iCloud…"))
                    try:
                        self.remote.delete(a)
                    except (AuthRequired, RemoteUnavailable):
                        raise
                    except Exception as exc:  # noqa: BLE001
                        result.errors += 1
                        self.cb.activity("error", t("Couldn't delete in iCloud ({name}): {error}", name=remote_assets[a].filename, error=brief(exc)))
                        continue
                    # remove the leftover Live Photo video, if any
                    for r in rows_by_asset[a]:
                        p = self._abs(r.rel_path)
                        if p.exists():
                            try:
                                self._trash_inside(p)
                            except OSError:
                                pass
                    self.state.remove_asset(a)
                    remote_assets.pop(a, None)
                    result.deleted_cloud += 1
                    self.cb.activity("cloud", t("Deleted in iCloud: {name}", name=rows_by_asset[a][0].rel_path))

        # 2) deleted in iCloud -----------------------------------------------
        local_deleted_set = set(local_deleted)
        if full:
            to_pc = [a for a in known_ids if a not in remote_assets and a not in local_deleted_set]
        else:
            to_pc = [a for a in known_ids if a in gone and a not in local_deleted_set]
        if to_pc:
            if not self.settings.delete_cloud_to_pc:
                for a in to_pc:
                    self.state.remove_asset(a)
                    self.cb.activity("local", t("Deleted in iCloud, kept on this PC: {name}", name=rows_by_asset[a][0].rel_path))
            elif len(to_pc) > threshold and not set(to_pc) <= self.approved:
                pending.to_pc = to_pc
            else:
                for i, a in enumerate(to_pc, 1):
                    self._check_cancel()
                    self.cb.progress(i, len(to_pc), t("Moving deleted photos to the Recycle Bin…"))
                    ok = True
                    for r in rows_by_asset[a]:
                        p = self._abs(r.rel_path)
                        if p.exists():
                            try:
                                self._trash_inside(p)
                            except OSError as exc:
                                ok = False
                                result.errors += 1
                                self.cb.activity("error", t("Couldn't delete {name}: {error}", name=r.rel_path, error=brief(exc)))
                    if ok:
                        self.state.remove_asset(a)
                        result.deleted_local += 1
                        self.cb.activity("trash", t("Deleted on this PC (Recycle Bin): {name}", name=rows_by_asset[a][0].rel_path))

        if pending:
            for a in pending.to_cloud:
                pending.names[a] = remote_assets[a].filename
            for a in pending.to_pc:
                pending.names[a] = rows_by_asset[a][0].rel_path
            result.pending = pending
        held = set(pending.to_cloud) | set(pending.to_pc)
        self.approved &= held  # approvals are single-use
        self.held = held

        # 3) new in iCloud ---------------------------------------------------
        excluded = self.state.excluded()
        tracked_keys = {(r.asset_id, r.resource_key) for rows in rows_by_asset.values() for r in rows}
        retry_failed = force_full or stale  # parked failures are tried again on a scheduled or asked-for full read
        parked = 0
        todo: list[tuple[RemoteAsset, RemoteResource]] = []
        for a in remote_assets.values():
            if a.id in excluded or a.id in held or a.id in local_deleted_set:
                continue
            if a.is_video and not self.settings.include_videos:
                continue
            for res in a.resources:
                if (a.id, res.key) in tracked_keys or f"{a.id}#{res.key}" in excluded:
                    continue
                if res.key == "original_video" and not (
                    self.settings.include_live_video and self.settings.include_videos
                ):
                    continue
                if not retry_failed and self.state.get(f"failed:{a.id}#{res.key}"):
                    continue  # failed before: waits for the next full read instead of every poll
                todo.append((a, res))
        todo.sort(key=lambda t: t[0].date, reverse=True)  # newest first

        if todo:
            total_bytes = sum(r.size or 0 for _, r in todo)
            check_free_space(self.root, total_bytes)
            done_bytes = 0
            from .ui_text import Rate

            rate = Rate()
            taken = self.state.paths()
            last_file = None  # the newest photo downloaded (todo is newest first)
            for i, (a, res) in enumerate(todo, 1):
                self._check_cancel()
                rate.add(done_bytes, i - 1)
                label = transfer_text(t("Photos: "), i - 1, len(todo), done_bytes, total_bytes, rate)
                self.cb.progress(done_bytes, total_bytes or len(todo), label)
                try:
                    rel = self._place(a, res, taken)
                    if rel is None:  # an identical file already existed and was adopted
                        continue
                    target = self._abs(rel)
                    if not inside_root(self.root, target.parent):
                        raise SyncError(t("{name} leads outside the photos folder; left alone", name=target.parent))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd, tmp = tempfile.mkstemp(prefix=TMP_PREFIX, dir=target.parent)
                    os.close(fd)
                    tmp_path = Path(tmp)
                    try:
                        def on_bytes(n: int) -> None:
                            nonlocal done_bytes
                            THROTTLE.take(n)
                            done_bytes += n
                            self.cb.progress(done_bytes, total_bytes or len(todo), label)
                            self._check_cancel()

                        self.remote.download(res, tmp_path, on_bytes)
                        if res.size and tmp_path.stat().st_size != res.size:
                            raise SyncError(t("file size doesn't match"))
                        adopted = False
                        while rel in self._verify:  # a file is already there: adopt it only if identical
                            self._verify.discard(rel)
                            import filecmp

                            if filecmp.cmp(tmp_path, target, shallow=False):
                                adopted = True
                                break
                            taken.add(rel.lower())  # someone else's file: try the next name (it may be taken too)
                            rel = self._place(a, res, taken)
                            target = self._abs(rel)
                        if adopted:
                            self.state.add_file(FileRow(a.id, res.key, rel, res.size, res.is_video,
                                                        a.date.isoformat()), now_iso())
                            taken.add(rel.lower())
                            continue
                        for _attempt in range(20):
                            try:
                                os.rename(tmp_path, target)  # fails if a file appeared there: never overwrites
                                break
                            except FileExistsError:
                                taken.add(rel.lower())  # someone just saved a file there: take the next name
                                rel = self._place(a, res, taken)
                                self._verify.discard(rel)
                                target = self._abs(rel)
                        else:
                            raise SyncError(t("{name} already exists; not replaced", name=rel))
                        ts = a.date.timestamp()
                        os.utime(target, (ts, ts))
                    finally:
                        if tmp_path.exists():
                            tmp_path.unlink(missing_ok=True)
                    self.state.add_file(
                        FileRow(a.id, res.key, rel, res.size, res.is_video, a.date.isoformat()), now_iso()
                    )
                    self.state.set(f"failed:{a.id}#{res.key}", None)
                    taken.add(rel.lower())
                    result.downloaded += 1
                    last_file = last_file or target
                except (Cancelled, AuthRequired, RemoteUnavailable):
                    raise
                except Exception as exc:  # noqa: BLE001
                    result.errors += 1
                    parked += 1
                    self.state.set(f"failed:{a.id}#{res.key}", now_iso())  # retried on the next full read
                    self.cb.activity("error", t("Download failed ({name}): {error}", name=res.filename, error=brief(exc)))
            if result.downloaded:
                text = (t("Downloaded 1 new file") if result.downloaded == 1
                        else t("Downloaded {n} new files", n=result.downloaded))
                self.cb.activity("download", text, str(last_file))  # click: show it in Explorer

        if result.errors == parked and not pending:  # failed downloads are parked, not a reason to read again
            self.state.set("cursor", cursor)
        if full:  # failed items are retried by the next daily full read, not by every poll
            self.state.set("last_full_scan", now_iso())
        self.state.set("last_sync", now_iso())
        try:
            self.sync_albums(refresh=full or cursor != stored)
        except (AuthRequired, RemoteUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001 - albums are a convenience; never fail the sync
            log.warning("Album folders: %s", exc)
            self.cb.activity("error", t("Album folders couldn't be updated: {error}", error=brief(exc)))
        return result

    # album folders ------------------------------------------------------------------------
    def sync_albums(self, refresh: bool) -> None:
        """Mirror iCloud albums as folders of hard links (no extra disk space).

        One-way: the album list comes from iCloud. Links of photos that are gone are removed
        on every run; the album list itself is re-read when the library changed (at most
        every 30 minutes) or on a full check.
        """
        links = self.state.album_links()
        if not self.settings.albums_as_folders:
            sources = defaultdict(list)
            for r in self.state.files():
                sources[r.asset_id].append(self._abs(r.rel_path))
            for rel, aid in links.items():
                self._unlink_album(rel, sources.get(aid, []))
            self._prune_empty(self.root / ALBUMS_DIR)
            return
        rows = {(r.asset_id, r.resource_key): r for r in self.state.files()}
        by_asset: dict[str, list[FileRow]] = defaultdict(list)
        for r in rows.values():
            by_asset[r.asset_id].append(r)
        last = self.state.get("albums_refreshed")
        due = refresh and (not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
                           >= ALBUM_REFRESH_SECONDS)
        if not due and last:
            for rel, aid in links.items():  # cheap pass: drop links of photos that no longer exist
                if aid not in by_asset:
                    self._unlink_album(rel, [])
            self._prune_empty(self.root / ALBUMS_DIR)
            return
        self.cb.status(t("Updating album folders…"))
        wanted: dict[str, str] = {}
        source: dict[str, str] = {}
        for album, ids in self.remote.albums():
            folder = "/".join(_safe_name(part) for part in album.split("/") if part.strip()) or "Album"
            for aid in ids:
                for r in by_asset.get(aid, []):
                    name = PurePosixPath(r.rel_path)
                    rel = f"{ALBUMS_DIR}/{folder}/{name.name}"
                    if rel in wanted and source[rel] != r.rel_path:  # e.g. IMG_0001 from two months
                        rel = f"{ALBUMS_DIR}/{folder}/{name.stem}_{aid[:6]}{name.suffix}"
                    wanted[rel] = aid
                    source[rel] = r.rel_path
        for rel, aid in links.items():
            if rel not in wanted:
                self._unlink_album(rel, [self._abs(r.rel_path) for r in by_asset.get(aid, [])])
        for rel, aid in wanted.items():
            target = self._abs(rel)
            if rel in links and target.exists():
                if links[rel] == aid:
                    continue
                # the name now belongs to another photo (e.g. two IMG_0001 and the album changed)
                self._unlink_album(rel, [self._abs(r.rel_path) for r in by_asset.get(links[rel], [])])
                if target.exists():
                    continue
            src = self._abs(source[rel])
            if not src.exists():
                continue
            if not inside_root(self.root, target.parent):
                log.warning("Album folder %s leads outside the photos folder; skipped", rel)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if target.exists():
                    if rel not in links:  # not one of our links: someone's own file, leave it alone
                        log.info("Album folder: %s exists and isn't Skyfold's; left as it is", rel)
                        continue
                    self._unlink_album(rel, [src])
                    if target.exists():
                        continue
                try:
                    os.link(src, target)  # same file, no extra space; refuses an existing name
                except FileExistsError:
                    log.info("Album folder: %s appeared meanwhile; left as it is", rel)
                    continue
                except OSError:
                    import shutil

                    fd, tmp = tempfile.mkstemp(prefix=TMP_PREFIX, dir=target.parent)  # e.g. no hard links here
                    os.close(fd)
                    try:
                        shutil.copy2(src, tmp)
                        os.rename(tmp, target)  # refuses an existing name: nothing is overwritten
                    except FileExistsError:
                        log.info("Album folder: %s appeared meanwhile; left as it is", rel)
                        continue
                    finally:
                        Path(tmp).unlink(missing_ok=True)
                self.state.add_album_link(rel, aid)
            except OSError as exc:
                log.warning("Album link %s: %s", rel, exc)
        self.state.set("albums_refreshed", now_iso())
        self._prune_empty(self.root / ALBUMS_DIR)

    def _unlink_album(self, rel: str, sources: list[Path] | tuple = ()) -> None:
        """Remove an album entry. Only a provable link to the photo itself is removed directly; anything
        else at that path (e.g. a file someone put there instead) goes to the Recycle Bin."""
        p = self._abs(rel)
        if not inside_root(self.root, p.parent):
            self.state.drop_album_link(rel)  # leads elsewhere now: forget it, touch nothing there
            return
        try:
            if p.exists():
                self._touch_own(p)
                # Moved to a private name first: the file checked is then the file removed, even if
                # something is saved at the album path in between.
                import uuid

                staged = p.with_name(f"{TMP_PREFIX}album-{uuid.uuid4().hex}{p.suffix}")
                os.rename(p, staged)
                ours = False
                for src in sources:
                    try:
                        if src.exists() and os.path.samefile(staged, src):
                            ours = True
                            break
                    except OSError:
                        pass
                if ours:
                    staged.unlink()  # just a second name of the photo: nothing is lost
                else:
                    self._trash(self._put_back(staged, p))  # can't prove it's ours: recoverable
        except OSError:
            return
        self.state.drop_album_link(rel)

    def _put_back(self, staged: Path, p: Path) -> Path:
        """A file moved to a private name goes back under its own name, or a free one next to it."""
        for n in range(1, 50):
            dest = p if n == 1 else p.with_name(f"{p.stem} ({n}){p.suffix}")
            try:
                self._touch_own(dest)
                os.rename(staged, dest)
                return dest
            except FileExistsError:
                continue
        return staged

    @staticmethod
    def _prune_empty(top: Path) -> None:
        if not top.exists():
            return
        def is_link(p: Path) -> bool:
            try:
                return p.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(p))
            except OSError:
                return True

        found = []
        for dirpath, dirs, _files in os.walk(top):
            dirs[:] = [d for d in dirs if not is_link(Path(dirpath) / d)]  # someone's link: leave it alone
            found.append(dirpath)
        for dirpath in sorted(found, key=len, reverse=True):
            if is_link(Path(dirpath)):
                continue
            try:
                Path(dirpath).rmdir()  # only succeeds when empty
            except OSError:
                pass

    def _place(self, a: RemoteAsset, res: RemoteResource, taken: set[str]) -> str | None:
        """Pick a free relative path; adopt an identical untracked file if one is already there.

        Candidates are tried in the same order as when the photo was first downloaded (plain name,
        then name_<id>, name_<id>_2, …), so after the sync database is rebuilt every file on disk is
        recognised again - including two iCloud photos that share a file name.
        """
        p = PurePosixPath(self._rel_for(a, res))
        n = 0
        while True:
            if n == 0:
                cand = p.as_posix()
            else:
                suffix = f"_{a.id[:6]}" if n == 1 else f"_{a.id[:6]}_{n}"
                name = f"{p.stem}{suffix}{p.suffix}"
                cand = name if p.parent == PurePosixPath(".") else (p.parent / name).as_posix()
            if cand.lower() not in taken:
                target = self._abs(cand)
                if not target.exists():
                    return cand
                try:
                    st = target.stat()
                    # Skyfold gives each photo the photo's own date: same size + same date = same photo
                    same = res.size is not None and st.st_size == res.size                         and abs(st.st_mtime - a.date.timestamp()) < 2.5
                except OSError:
                    same = False
                if same:  # looks like this very photo (e.g. the database was rebuilt): compare after download
                    self._verify.add(cand)
                    return cand
            n += 1

    def _forget_local_only(self, asset_id: str, rows: list[FileRow]) -> None:
        for r in rows:
            p = self._abs(r.rel_path)
            if p.exists():
                try:
                    self._trash_inside(p)
                except OSError:
                    pass
        self.state.remove_asset(asset_id)
        self.state.exclude(asset_id, "deleted on PC")

    # decisions on held deletions -------------------------------------------
    def approve(self, ids: list[str]) -> None:
        self.approved.update(ids)

    def reject_to_cloud(self, ids: list[str]) -> None:
        """User wants files deleted on the PC back: forget them so they download again."""
        for a in ids:
            self.state.remove_asset(a)
            self.state.unexclude_parts(a)  # its Live Photo video comes back too

    def reject_to_pc(self, ids: list[str]) -> None:
        """User wants to keep local copies of photos deleted in iCloud: stop tracking them."""
        for a in ids:
            self.state.remove_asset(a)
