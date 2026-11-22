"""Two-way sync logic: new photos come down, deletions travel in both directions.

Nothing here deletes permanently: local files go to the Recycle Bin and iCloud deletions
land in "Recently Deleted" for 30 days.
"""

from __future__ import annotations

import logging
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from .config import Settings
from .i18n import t
from .safelog import brief
from .remote import AuthRequired, Remote, RemoteAsset, RemoteResource, RemoteUnavailable, LookupIncomplete, UploadRejected
from .state import FileRow, State

log = logging.getLogger(__name__)

MARKER = ".skyfold"
OWNER_FILE = ".skyfold-account"  # which Apple ID the photos folder was set up for (a short fingerprint)
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


def known_missing(p: Path | str) -> bool:
    """True only when p is known not to exist. A path that couldn't be looked at (access denied, a disk
    hiccup) is not gone: a record about it is kept and the file is left alone until it can be looked at."""
    try:
        os.lstat(p)
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return False
    return False


def marker_present(marker: Path) -> bool:
    """Is the folder's sync marker there? A marker that can't be looked at (access denied, a disk that
    isn't ready) stops the sync: it is never taken for a missing one and written over."""
    try:
        st = os.lstat(marker)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as exc:
        raise SyncError(t("Couldn't check the sync marker in {path}: {error}. Syncing has stopped so nothing "
                          "gets changed by mistake; it is tried again next time.",
                          path=marker.parent, error=brief(exc))) from exc
    if not stat.S_ISREG(st.st_mode) or _is_link(marker):  # a folder, link or shortcut there: not a marker
        raise SyncError(t("Couldn't check the sync marker in {path}: {error}. Syncing has stopped so nothing "
                          "gets changed by mistake; it is tried again next time.",
                          path=marker.parent, error=t("it isn't an ordinary file")))
    return True


def _regular_file(p: Path) -> bool:
    """An ordinary file (not a link) at p? Can't be looked at: no."""
    try:
        return stat.S_ISREG(os.lstat(p).st_mode) and not _is_link(p)
    except OSError:
        return False


def create_marker(marker: Path, text: str) -> None:
    """Write a new sync marker. Only a new file is ever created: if something appeared at that name
    meanwhile, it is left exactly as it is and this pass stops."""
    try:
        with open(marker, "x", encoding="utf-8") as fh:
            fh.write(text)
    except FileExistsError:
        raise SyncError(t("Couldn't check the sync marker in {path}: {error}. Syncing has stopped so nothing "
                          "gets changed by mistake; it is tried again next time.",
                          path=marker.parent, error=t("it appeared while it was being checked"))) from None
    if os.name == "nt":
        import ctypes

        ctypes.windll.kernel32.SetFileAttributesW(str(marker), 0x2)  # hidden


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


def _file_hash(p: Path) -> str:
    """SHA-256 of a file's content."""
    import hashlib

    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Cancelled(Exception):
    pass


def _upload_member(path: Path, directory: bool = False) -> bool:
    """Inspect metadata without opening a cloud placeholder or following a link."""
    try:
        st = path.lstat()
        # Hidden, system, reparse point, offline, recall-on-open, recall-on-data-access.
        if getattr(st, "st_file_attributes", 0) & (0x2 | 0x4 | 0x400 | 0x1000 | 0x40000 | 0x400000):
            return False
        return stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)
    except OSError:
        return False


@contextmanager
def _upload_reader(path: Path):
    """On Windows deny writers and renames until pyicloud finishes reopening and streaming the path."""
    if os.name == "nt":
        import _winapi
        import msvcrt

        handle = _winapi.CreateFile(str(path), 0x80000000, 1, 0, 3, 0x00200000, 0)
        stream = os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), "rb")
    else:
        stream = path.open("rb")
    with stream:
        yield stream


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


def transfer_text(prefix: str, i: int, n: int, done: int, total: int, rate, first: str | None = None) -> str:
    """'Drive: 3.413 of 9.935 files · 30,5 of 59,0 GB · 2,1 MB/s · about 1 h 20 min left'.
    first: another opening part (uploads say how many files are done, as several go at once)."""
    from .ui_text import count_text, size_text

    parts = [first or t("{prefix}{i} of {n} files", prefix=prefix, i=count_text(i), n=count_text(n))]
    if total > 0:
        parts.append(t("{done} of {total}", done=size_text(done), total=size_text(total)))
    bps, _fps = rate.speed()
    if bps > 0:
        parts.append(t("{speed}/s", speed=size_text(int(bps))))
    left = rate.seconds_left(total - done, n - i)
    if left is not None:
        parts.append(t("{time} left", time=duration_text(left)))
    return " · ".join(parts)


def upload_text(name: str, done: int, total: int, rate) -> str:
    """'Uploading IMG_0412.MOV · 120.0 MB of 600.0 MB · 2.1 MB/s · about 4 min left'."""
    from .ui_text import size_text

    parts = [t("Uploading {name}", name=name)]
    if total > 0:
        parts.append(t("{done} of {total}", done=size_text(done), total=size_text(total)))
    bps, _fps = rate.speed()
    if bps > 0:
        parts.append(t("{speed}/s", speed=size_text(int(bps))))
    left = rate.seconds_left(total - done, 0)
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
        self.approved: set[str] = set()  # pending deletions the user confirmed (an answer without a side)
        self.approved_side: dict[str, set[str]] = {"cloud": set(), "pc": set()}  # confirmed for that side
        self.held_side: dict[str, set[str]] = {"cloud": set(), "pc": set()}  # asked about, per side
        self.question_gen = 0  # goes up whenever the question changes: an answer names the one it saw
        self.side_gen = {"cloud": 0, "pc": 0}  # the same, per side of the question
        self.held: set[str] = set()  # deletions currently waiting for the user's decision
        self._upload_saw_missing = False
        self._upload_idle = {}
        self._new_marker = False
        self._held_set: set | None = None
        self._id_index: dict | None = None
        self._displaced: set = set()
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

    def _upload_progress(self, path: Path, total: int) -> Callable[[int], None]:
        """Shows how much of a photo or video has gone, the speed and the time left (like
        downloads); Stop works in the middle of a big video too."""
        from .ui_text import Rate

        rate, sent, shown = Rate(), [0], [0.0]

        def on_bytes(k: int) -> None:
            self._check_cancel()
            sent[0] += k
            now = time.monotonic()
            if now - shown[0] < 0.5 and sent[0] < total:
                return
            shown[0] = now
            rate.add(sent[0], 0)
            self.cb.progress(sent[0], max(total, 1), upload_text(path.name, sent[0], total, rate))
        return on_bytes

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
        self._new_marker = False
        root = self.root
        has_files = bool(self.state.asset_ids())
        if not root.exists():
            if has_files:
                raise SyncError(t("Photos folder is not available: {path}", path=root))
            root.mkdir(parents=True, exist_ok=True)
        self._refuse_foreign_root()  # (before anything is written there)
        marker = root / MARKER
        # A folder set up by an earlier version of the app has one of the old marker names. Nothing
        # proves who wrote such a file, so it is never renamed or changed: when this database tracks
        # files here, its presence only lets Skyfold add its own new marker next to it.
        if not marker_present(marker):
            # (the old names are only looked at when the current marker is missing)
            legacy = has_files and any(_regular_file(root / old) for old in LEGACY_MARKERS)
            if has_files and not legacy:
                raise SyncError(t(
                    "The photos folder doesn't look like before (its sync marker is missing). "
                    "Syncing has stopped so nothing gets deleted by mistake."
                ))
            create_marker(marker, t("This folder is synced by Skyfold. Don't delete this file.\n"))
            # (a folder of an older version with one of its marker names isn't a new one)
            self._new_marker = not any(_regular_file(root / old) for old in LEGACY_MARKERS)

    # local changes --------------------------------------------------------
    def scan_local(self) -> tuple[list[str], int]:
        """Return (asset ids whose main file was deleted on the PC, number of files relocated)."""
        followed = self._reconcile_ids()
        rows = self.state.files()
        # Only files that are really missing count; one that can't be looked at now decides nothing.
        missing = []
        self._upload_saw_missing = False
        fids = {k.removeprefix("fid:"): v for k, v in self.state.items("fid:")}
        for row in rows:
            if f"{row.asset_id}#{row.resource_key}" in self._displaced:
                missing.append(row)  # (another photo's file was moved over it)
                self._upload_saw_missing = True
                continue
            try:
                st = os.lstat(self._abs(row.rel_path))
            except (FileNotFoundError, NotADirectoryError):
                missing.append(row)
                self._upload_saw_missing = True
                continue
            except OSError:
                self._upload_saw_missing = True  # a new-looking file might be this one
                continue
            if st.st_ino and f"{row.asset_id}#{row.resource_key}" not in fids:
                # (photos of earlier versions: their file id is noted the first time they are seen)
                self.state.set(f"fid:{row.asset_id}#{row.resource_key}", f"{st.st_dev}:{st.st_ino}")
        if not missing:
            self._told_unsure = None  # nothing held: a later hold is told again
            return [], followed

        # The user may have moved files around inside the folder: follow them by name + size.
        tracked = {r.rel_path.lower() for r in rows}
        # Files the upload dealt with (sent, a copy iCloud had, one kept here, a Live Photo's video) and
        # another Apple ID's files: never taken for a moved photo, or deleting that photo in iCloud later
        # would recycle them.
        album_entries = {rel.lower() for rel in self.state.album_links()}  # same name and size as the originals
        by_id: dict[str, str] = {}  # untracked files by Windows file id: a moved or renamed photo keeps it
        index: dict[tuple[str, int], list[str]] = defaultdict(list)  # every look-alike, not just one
        by_size: dict[int, list[str]] = defaultdict(list)  # untracked files by size: renamed photos
        unsure = []  # a folder that couldn't be read: any missing photo may have moved there
        unsure_names: set[str] = set()  # files that couldn't be looked at: a photo of that name may be one

        for dirpath, dirs, files in os.walk(self.root, onerror=unsure.append):
            dirs[:] = [d for d in dirs if not _is_link(Path(dirpath) / d)  # a junction may lead anywhere
                       and not self._nested_library(Path(dirpath) / d)]
            for fn in files:
                if fn.startswith(TMP_PREFIX) or fn == MARKER:
                    continue
                full = Path(dirpath) / fn
                rel = full.relative_to(self.root).as_posix()
                if rel.lower() in tracked or rel.lower() in album_entries:
                    continue
                try:
                    st = full.stat()
                    size = st.st_size
                except OSError:
                    unsure_names.add(fn.lower())
                    continue
                if fn == OWNER_FILE:
                    continue
                if st.st_ino:
                    by_id[f"{st.st_dev}:{st.st_ino}"] = rel
                if self._marked(st, rel):
                    continue  # sent, kept or a copy iCloud had: someone's own file, never a moved photo
                index[(fn.lower(), size)].append(rel)
                by_size[size].append(rel)  # (a photo renamed in Explorer keeps its size, not its name)
        if unsure or unsure_names:
            log.info("Photos: %d folders and %d files couldn't be read; missing photos that may be there "
                     "are looked at again next time", len(unsure), len(unsure_names))
        held = 0

        moved = 0
        by_asset: dict[str, list[FileRow]] = defaultdict(list)
        for r in rows:
            by_asset[r.asset_id].append(r)
        deleted_assets: list[str] = []
        lone_videos: list[FileRow] = []
        # A file with the same name and size elsewhere in the folder may be this photo, moved - or
        # someone's own file. Only the content tells: iCloud's copy is downloaded and compared.
        maybe: dict[tuple[str, str], list[str]] = {}
        by_content: dict[tuple[str, str], str] = {}  # renamed (and maybe moved): proven by its content
        taken: set[str] = set()
        by_ident: dict[tuple[str, str], str] = {}
        for r in missing:  # the same file (by id) somewhere else: moved or renamed, edited or not
            rel = by_id.get(fids.get(f"{r.asset_id}#{r.resource_key}", ""))
            if rel and rel not in taken:
                by_ident[(r.asset_id, r.resource_key)] = rel
                taken.add(rel)
        for files in list(index.values()) + list(by_size.values()):
            files[:] = [x for x in files if x not in taken]
        for r in missing:
            if (r.asset_id, r.resource_key) in by_ident:
                continue
            key = (PurePosixPath(r.rel_path).name.lower(), r.size or -1)
            if index.get(key):
                maybe[(r.asset_id, r.resource_key)] = index[key]  # shared: each file is claimed once
                continue
            others = by_size.get(r.size) if r.size else None
            if not others:
                continue
            if r.hash:  # the fingerprint of what was downloaded: compared here, nothing downloaded
                for rel in others:
                    if rel in taken:
                        continue
                    try:
                        same = _file_hash(self._abs(rel)) == r.hash
                    except OSError:
                        same = False
                    if same:
                        by_content[(r.asset_id, r.resource_key)] = rel
                        taken.add(rel)
                        break
            else:  # (from before 1.0.6) iCloud's copy is downloaded and compared, as for look-alikes
                maybe[(r.asset_id, r.resource_key)] = others
        for where in by_content:  # a file proven to be one photo is never another one's look-alike
            for files in maybe.values():
                for rel in [x for x in files if x in taken]:
                    files.remove(rel)
        confirmed, unknown = self._confirm_moves({k: v for k, v in maybe.items() if v}) if maybe else ({}, set())
        confirmed.update(by_content)
        confirmed.update(by_ident)
        # Photos sharing the same look-alikes are decided together: while one of them couldn't be
        # compared, none of the others may claim a file (it might be the unknown one's) or count as deleted.
        shared = {id(maybe[w]) for w in unknown}
        unknown |= {w for w, files in maybe.items() if id(files) in shared}
        untracked: list[FileRow] = []
        for r in missing:
            where = (r.asset_id, r.resource_key)
            if where in unknown:
                continue  # couldn't be compared now: nothing is decided about it this pass
            new_rel = confirmed.get(where)
            if new_rel:
                self.state.move_file(r.asset_id, r.resource_key, new_rel)
                self._note_file_id(r.asset_id, r.resource_key, new_rel)  # (moved by copying: its new file)
                if where not in by_ident:  # followed by contents only: may be someone's copy, never recycled
                    self.state.set(f"fidsoft:{r.asset_id}#{r.resource_key}", "1")
                moved += 1
                continue
            if unsure or PurePosixPath(r.rel_path).name.lower() in unsure_names:
                held += 1
                continue  # it may be in a place that couldn't be read: nothing is decided this pass
            if r.resource_key == "original" and where in maybe:
                # Look-alikes exist but none is this photo. Whether it was deleted or changed and
                # moved can't be told: nothing is deleted in iCloud and no file is touched; Skyfold
                # only stops tracking the photo on this PC.
                untracked.append(r)
            elif r.resource_key == "original":
                deleted_assets.append(r.asset_id)
            else:
                lone_videos.append(r)
        told = (held, tuple(sorted(str(getattr(e, "filename", "") or "") for e in unsure)), tuple(sorted(unsure_names)))
        if held and told != getattr(self, "_told_unsure", None):  # once, not on every pass
            self._told_unsure = told
            where = next((str(e.filename) for e in unsure if getattr(e, "filename", None)), None)
            if where:
                self.cb.activity("info", t("Photos: {n} missing files are kept until {folder} can be read. Fix its "
                                           "access rights, or move that folder out of the photos folder.",
                                           n=held, folder=where), where)
            else:
                self.cb.activity("info", t("Photos: {n} missing files are kept for now, part of the photos folder "
                                           "couldn't be read", n=held))
        elif not held:
            self._told_unsure = None
        for r in untracked:
            self._keep_on_pc(r.asset_id, by_asset[r.asset_id])
            self.state.exclude(r.asset_id, "deleted on PC")
            self.cb.activity("local", t("Deleted on this PC only: {name}", name=PurePosixPath(r.rel_path).name))
        lone_videos = [r for r in lone_videos if not any(x.asset_id == r.asset_id for x in untracked)]
        for r in lone_videos:
            if r.asset_id in deleted_assets:
                continue  # the photo is gone too: decided together (and brought back together if asked)
            # Only the Live Photo video was removed: forget it and don't bring it back.
            self.state.remove_file(r.asset_id, r.resource_key)
            self.state.exclude(f"{r.asset_id}#{r.resource_key}", "video deleted on PC")
        return sorted(set(deleted_assets)), moved + followed

    UPLOAD_EXT = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp", ".dng",
                  ".mov", ".mp4", ".m4v"}
    UPLOAD_MAX = 100  # per pass; the rest follows next time
    PHOTO_UPLOAD_WORKERS = 2  # photos and videos sent at the same time (Apple limits each connection)
    UPLOAD_SETTLE = 120  # seconds a file must stay unchanged first (still being copied otherwise)

    # The upload is one-way: a file sent from this PC stays an ordinary file here (deleting or renaming
    # it on either side changes nothing on the other side), and the photo it became in iCloud is never
    # downloaded back. Files the upload dealt with are remembered by Windows file id (the same after a
    # rename) together with their contents (an id alone can be reused, or change on some drives):
    # "sent", "copy" (iCloud already had it), "kept" (kept on this PC after iCloud lost it), "live"
    # (the video of a Live Photo). A wrong guess here can only skip or repeat an upload.
    def _mark_key(self, st: os.stat_result, rel: str) -> str:
        return f"upload_file:{st.st_dev}:{st.st_ino}" if st.st_ino else "upload_file:p:" + rel.lower()

    def _mark(self, st: os.stat_result, rel: str, digest: str | None, kind: str) -> None:
        """digest None: the file couldn't be read; its size and time stand in until it can be."""
        self.state.set(self._mark_key(st, rel), json.dumps([digest or f"?{st.st_size}:{st.st_mtime_ns}", kind]))

    def _marked(self, st: os.stat_result, rel: str) -> list | None:
        value = self.state.get(self._mark_key(st, rel))
        return json.loads(value) if value else None

    @staticmethod
    def _same_marked(mark: list, st: os.stat_result, digest: str) -> bool:
        if mark[0] == "kept":
            return True  # (dc4ca0d: kept but unreadable then; bound to its contents now)
        if mark[0].startswith("?"):
            return mark[0] == f"?{st.st_size}:{st.st_mtime_ns}"  # unchanged since it couldn't be read
        return mark[0] == digest

    def _is_marked_file(self, full: Path, rel: str, st: os.stat_result) -> bool:
        """Is this exactly a file the upload dealt with (same id or path AND same contents)?"""
        mark = self._marked(st, rel)
        if not mark:
            return False
        if mark[0] == "kept" or mark[0].startswith("?"):
            return self._same_marked(mark, st, "")
        try:
            return _file_hash(full) == mark[0]
        except OSError:
            return False

    def _owner_fp(self) -> str:
        return hashlib.sha256((self.settings.apple_id or "").strip().lower().encode()).hexdigest()[:16]

    def _note_file_id(self, asset_id: str, key: str, rel: str) -> None:
        """The Windows file id of a photo Skyfold wrote: a rename or move keeps it, so the photo is
        followed by it (also after it was edited). A stale upload mark on a reused id is dropped."""
        try:
            st = os.lstat(self._abs(rel))
        except OSError:
            return
        if st.st_ino:
            self.state.set(f"fid:{asset_id}#{key}", f"{st.st_dev}:{st.st_ino}")
            self.state.set(f"upload_file:{st.st_dev}:{st.st_ino}", None)

    def _owner_of(self, folder: Path):
        """(fingerprint, library id) from folder/.skyfold-account; ("?", "") for a photos folder of a
        version before 1.0.16 (its marker only); None when folder isn't a photos folder."""
        try:
            parts = (folder / OWNER_FILE).read_text(encoding="utf-8").split()
            return (parts[0] if parts else "?", parts[1] if len(parts) > 1 else "")
        except FileNotFoundError:
            pass
        except OSError:
            return ("?", "")  # can't be read: not taken for this user's
        if any((folder / m).exists() for m in (MARKER, *LEGACY_MARKERS)):
            return ("?", "")
        return None

    def _refuse_foreign_root(self) -> None:
        """Another Apple ID's photos folder, or a folder inside one: stop before anything is written."""
        if not self.settings.apple_id:
            return
        me = self._owner_fp()
        for folder in self.root.parents:
            owner = self._owner_of(folder)
            if owner is not None and owner[0] != me:
                raise SyncError(t("The photos folder is inside another Skyfold photos folder ({path}). Choose "
                                  "a folder outside it in Settings.", path=folder))
        try:
            parts = (self.root / OWNER_FILE).read_text(encoding="utf-8").split()
        except OSError:
            return
        if not parts or parts[0] != me:
            raise SyncError(t("This photos folder belongs to another Apple ID. Choose another folder in Settings."))

    def _reconcile_ids(self) -> int:
        """A tracked name that now holds another file than the one Skyfold wrote there (by file id):
        if that photo's file is elsewhere in the folder, the photo moved there (names swapped, or a new
        file took the old name); if it is nowhere, the file was replaced in place (an editor's save) and
        the new one is that photo. Returns how many photos were followed."""
        rows = self.state.files()
        by_key = {f"{r.asset_id}#{r.resource_key}": r for r in rows}
        fids = {k.removeprefix("fid:"): v for k, v in self.state.items("fid:")}
        owners = {v: k for k, v in fids.items() if k in by_key}
        moves, replaced = [], []
        self._displaced = set()

        def id_at(rel):
            try:
                st = os.lstat(self._abs(rel))
            except OSError:
                return None
            return f"{st.st_dev}:{st.st_ino}" if st.st_ino else None

        for key, r in by_key.items():
            fid = fids.get(key)
            if not fid:
                continue
            here = id_at(r.rel_path)
            if here is None or here == fid:
                continue
            dev, _, ino = fid.partition(":")
            where = self._find_by_id([int(dev), int(ino)]) if ino.isdigit() and dev.lstrip("-").isdigit() else None
            if where and where.lower() != r.rel_path.lower():
                moves.append((r, where))
            elif here in owners and owners[here] != key:
                # another tracked photo's file is here now (moved over this one, e.g. Explorer's Replace):
                # that photo moved here, and this one's own file is gone
                self._displaced.add(key)
                other = by_key[owners[here]]
                if id_at(other.rel_path) != here and other.rel_path.lower() != r.rel_path.lower():
                    moves.append((other, r.rel_path))
            elif where is None:
                replaced.append((r, here))
        for r, where in moves:
            self.state.move_file(r.asset_id, r.resource_key, where)
        for r, here in replaced:
            self.state.set(f"fid:{r.asset_id}#{r.resource_key}", here)
            self.state.set(f"fidsoft:{r.asset_id}#{r.resource_key}", "1")  # (replaced: kept, never recycled)
        return len(moves)

    def _check_owner(self) -> None:
        """A photos folder belongs to one Apple ID (recorded in .skyfold-account with an id of this
        library). Another Apple ID's folder, a folder inside another photos folder, or another library
        that appeared at this path (e.g. a different disk) stops the sync: nothing is downloaded, sent,
        taken over or deleted there."""
        if not self.settings.apple_id:
            return
        me = self._owner_fp()
        for folder in self.root.parents:
            owner = self._owner_of(folder)
            if owner is not None and owner[0] != me:
                raise SyncError(t("The photos folder is inside another Skyfold photos folder ({path}). Choose "
                                  "a folder outside it in Settings.", path=folder))
        owner_file = self.root / OWNER_FILE
        try:
            parts = owner_file.read_text(encoding="utf-8").split()
        except FileNotFoundError:
            parts = None
        except OSError as exc:
            raise SyncError(t("Couldn't check the sync marker in {path}: {error}. Syncing has stopped so nothing "
                              "gets changed by mistake; it is tried again next time.",
                              path=self.root, error=brief(exc))) from exc
        if parts is not None:
            if not parts or parts[0] != me:
                raise SyncError(t("This photos folder belongs to another Apple ID. Choose another folder in "
                                  "Settings."))
            library = parts[1] if len(parts) > 1 else ""
        else:
            library = uuid.uuid4().hex[:16]
            others = [n for n in os.listdir(self.root) if n not in (MARKER, OWNER_FILE)]
            if others and not self._new_marker:
                # A photos folder of a version before 1.0.16 (also an upgraded one: in 1.0.15 another
                # Apple ID could have used it). Files in it that this database doesn't track can't be told
                # apart: they are never sent - by their file id and by their name.
                tracked = self.state.paths()
                held = []
                for dirname, dirs, files in os.walk(self.root):
                    dirs[:] = [d for d in dirs if not _is_link(Path(dirname) / d)]
                    for name in files:
                        p = Path(dirname) / name
                        rel = p.relative_to(self.root).as_posix().lower()
                        if rel in tracked:
                            continue
                        try:
                            st = p.lstat()
                        except OSError:
                            continue
                        held.append([st.st_dev, st.st_ino, rel])
                old = json.loads(self.state.get("upload_legacy:held") or "[]")
                self.state.set("upload_legacy:held", json.dumps(old + held))
            with open(owner_file, "x", encoding="utf-8") as fh:
                fh.write(f"{me} {library}\n")
            if os.name == "nt":
                import ctypes

                ctypes.windll.kernel32.SetFileAttributesW(str(owner_file), 0x2)  # hidden
        known = self.state.get("library")
        if known is not None and known != library:
            raise SyncError(t("The photos folder now holds another Skyfold library (e.g. another disk with the "
                              "same drive letter, or an older copy). Syncing has stopped so nothing gets deleted "
                              "by mistake; choose the folder again in Settings."))
        self.state.set("library", library)

    def _held_ids(self) -> set:
        """(dev, ino) and ("path", rel) of files that were in an older photos folder when it was taken over."""
        if getattr(self, "_held_set", None) is None:
            self._held_set = set()
            for x in json.loads(self.state.get("upload_legacy:held") or "[]"):
                if x[1]:
                    self._held_set.add((x[0], x[1]))
                if len(x) > 2:
                    self._held_set.add(("path", x[2]))
        return self._held_set

    def _protected(self, p: Path, rel: str, key: str | None = None) -> bool:
        """Right before a file is recycled for a photo deleted in iCloud: is it still the file Skyfold
        downloaded (same file id), and not one the upload dealt with? A photo followed only by its
        contents (moved by copying, or replaced in place) may be someone's own copy: never recycled."""
        try:
            st = os.lstat(p)
        except OSError:
            return False
        if key and self.state.get("fidsoft:" + key):
            return True
        fid = self.state.get("fid:" + key) if key else None
        if fid and st.st_ino and fid != f"{st.st_dev}:{st.st_ino}":
            return True  # another file was put at this name: not Skyfold's to recycle
        return bool(self._marked(st, rel))

    def _nested_library(self, folder: Path) -> bool:
        """A folder inside this one that is another Apple ID's (or an unknown) photos folder. A folder of
        this Apple ID chosen before (e.g. a month folder) is just part of this one."""
        if folder == self.root:
            return False
        owner = self._owner_of(folder)
        return owner is not None and owner[0] != self._owner_fp()

    def _find_by_id(self, ident) -> str | None:
        if not ident or len(ident) != 2 or not ident[1]:
            return None
        if getattr(self, "_id_index", None) is None:
            self._id_index = {}
            albums = {rel.lower() for rel in self.state.album_links()}
            for dirname, dirs, files in os.walk(self.root):
                parent = Path(dirname)
                dirs[:] = [d for d in dirs if not _is_link(parent / d) and not self._nested_library(parent / d)
                           and not (parent == self.root and d.lower() == ALBUMS_DIR.lower())]
                for name in files:
                    p = parent / name
                    rel = p.relative_to(self.root).as_posix()
                    if rel.lower() in albums:
                        continue  # (an album entry is a hard link: same id as its photo)
                    try:
                        st = p.lstat()
                    except OSError:
                        continue
                    if st.st_ino:
                        self._id_index[(st.st_dev, st.st_ino)] = rel
        return self._id_index.get(tuple(ident))

    def _migrate_upload_records(self) -> None:
        """Records of earlier versions (two-way uploads, marks by name or by contents) become the
        one-way ones. A record whose file can't be read now stays for the next pass."""
        tracked_ids = self.state.asset_ids()
        tracked = self.state.paths()

        def mark_at(rel: str, check, kind: str):
            """Mark the file at rel when check(st, path) says it is that file: "marked", "other" (not
            there, or another file), or None (can't be read now: tried again next pass)."""
            p = self._abs(rel)
            try:
                st = os.lstat(p)
            except (FileNotFoundError, NotADirectoryError):
                return "other"
            except OSError:
                return None
            if rel.lower() in tracked:
                return "tracked"  # (a matched 1.0.15 upload: an ordinary synced photo, nothing to remember)
            try:
                digest = check(st, p)
            except OSError:
                return None
            if digest:
                self._mark(st, rel, digest, kind)
                return "marked"
            return "other"

        marked_digests = set()

        def by_digest(d):
            return lambda st, p: d if _file_hash(p) == d else None

        for key, value in self.state.items("upload_done:"):
            rel = key.removeprefix("upload_done:")
            if value and len(value) == 64:
                done = mark_at(rel, by_digest(value), "kept")
                if done == "marked":
                    marked_digests.add(value)
                elif done == "other":  # (renamed while Skyfold was closed: a file with these bytes)
                    count = int(self.state.get("upload_legacy:" + value) or 0)
                    self.state.set("upload_legacy:" + value, str(count + 1))
            elif value and ":" in value:  # oldest format: size and whole seconds
                size, _, secs = value.partition(":")
                done = mark_at(rel, lambda st, p: _file_hash(p) if f"{st.st_size}:{int(st.st_mtime)}" == value
                               else None, "kept")
            else:  # "kept" without contents
                done = mark_at(rel, lambda st, p: f"?{st.st_size}:{st.st_mtime_ns}", "kept")
            if done:
                self.state.set(key, None)
        for key, _ in self.state.items("upload_known:"):
            digest = key.removeprefix("upload_known:")
            if digest not in marked_digests and not self.state.get("upload_legacy:" + digest):
                self.state.set("upload_legacy:" + digest, "1")
            self.state.set(key, None)
        for key, value in self.state.items("upload_pending:"):
            record = json.loads(value)
            ident = record.get("identity") or []
            try:
                here = os.lstat(self._abs(record["rel"]))
                moved = len(ident) == 2 and bool(ident[1]) and [here.st_dev, here.st_ino] != ident
            except OSError:
                moved = True
            if moved:  # renamed meanwhile (its old name may hold another file by now)
                record["rel"] = self._find_by_id(ident) or record["rel"]
            if record.get("asset"):
                if record["asset"] not in tracked_ids:
                    self.state.set("upload_sent:" + record["asset"], "1")
            else:
                self.state.set("upload_lost:" + record["digest"], "1")
            done = mark_at(record["rel"], by_digest(record["digest"]), "copy" if record.get("duplicate") else "sent")
            if done == "other":  # not in the folder now (moved away): these bytes are never sent again
                count = int(self.state.get("upload_legacy:" + record["digest"]) or 0)
                self.state.set("upload_legacy:" + record["digest"], str(count + 1))
            if done:
                self.state.set(key, None)
        for key, value in self.state.items("upload_seen:"):
            aid = key.removeprefix("upload_seen:")
            rel, digest = json.loads(value)[:2]
            if aid not in tracked_ids:
                self.state.set("upload_sent:" + aid, "1")
            if mark_at(rel, by_digest(digest), "copy"):
                self.state.set(key, None)
        for key, _ in self.state.items("upload_unlisted:"):
            # sent and matched, not listed yet: it becomes one-way like every upload now
            # 1.0.15 matched this upload before a full listing showed it: it stays a synced photo, but
            # if a listing misses it, its PC file is kept rather than recycled
            aid = key.removeprefix("upload_unlisted:")
            for r in self.state.files():
                if r.asset_id == aid:
                    self.state.set(f"fidsoft:{r.asset_id}#{r.resource_key}", "1")
            self.state.set(key, None)

    def _upload_settled(self, rel: str, st: os.stat_result) -> bool:
        """Unchanged for UPLOAD_SETTLE seconds. A copy or an editor can keep the camera's old modified
        time, so a file seen changing waits that long from when the change was seen - looked at by its
        name (a replacement) and by its file id (a rename), and saved across a restart."""
        ok = time.time() - st.st_mtime >= self.UPLOAD_SETTLE
        keys = ["upload_sig:" + rel.lower()] + ([f"upload_sig:#{st.st_dev}:{st.st_ino}"] if st.st_ino else [])
        sig = [st.st_size, st.st_mtime_ns, st.st_ino]
        for key in keys:
            raw = self.state.get(key)
            seen = json.loads(raw) if raw else None
            if seen is None or seen[0] != sig:
                seen = [sig, time.time(), seen is not None]  # seen before with other contents: changed now
                self.state.set(key, json.dumps(seen))
            if seen[2] and time.time() - seen[1] < self.UPLOAD_SETTLE:
                ok = False
        return ok

    def _upload_candidates(self):
        if not _upload_member(self.root, directory=True):
            return
        tracked = self.state.paths() | {rel.lower() for rel in self.state.album_links()}
        present: set[str] = set()
        for dirname, dirs, files in os.walk(self.root):
            parent = Path(dirname)
            dirs[:] = sorted(d for d in dirs if not d.startswith(".")
                             and not (parent == self.root and d.lower() == ALBUMS_DIR.lower())
                             and _upload_member(parent / d, directory=True)
                             and not self._nested_library(parent / d))
            stills = {Path(f).stem.casefold() for f in files
                      if Path(f).suffix.lower() in {".heic", ".heif", ".jpg", ".jpeg"}}
            for name in sorted(files):
                p = parent / name
                ext = p.suffix.lower()
                rel = p.relative_to(self.root).as_posix()
                if name.startswith(".") or ext not in self.UPLOAD_EXT or rel.lower() in tracked:
                    continue
                pair = ext == ".mov" and p.stem.casefold() in stills  # the video part of a Live Photo
                if not _upload_member(p):
                    continue
                try:
                    st = p.lstat()
                except OSError:
                    continue
                present.add(rel.lower())
                if st.st_ino:
                    present.add(f"#{st.st_dev}:{st.st_ino}")
                if st.st_size and self._upload_settled(rel, st):
                    yield rel, p, pair
        # a file that was gone in between is new when it is back (e.g. restored), not "changed"
        for key, _ in self.state.items("upload_sig:"):
            if key.removeprefix("upload_sig:") not in present:
                self.state.set(key, None)

    def upload_new(self) -> int:
        """Send new photos and videos in the folder to iCloud Photos (one-way, see above).

        Up to PHOTO_UPLOAD_WORKERS go at a time, each on its own connection. Everything that decides
        what a file is (marks, retries, receipts) happens on this thread; the upload threads only
        send. Each file stays locked against writers from the check until its upload is over."""
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
        from contextlib import ExitStack

        from .ui_text import Rate

        attempts = 0
        sent = [0]
        now = time.monotonic()
        self._upload_idle = {k: v for k, v in self._upload_idle.items() if v[1] > now}
        tracked_ids = self.state.asset_ids()
        streaming = getattr(self.remote, "streams_progress", False)
        workers = self.PHOTO_UPLOAD_WORKERS if streaming else 1
        lock = threading.Lock()
        abort = threading.Event()  # one upload stopped the batch: the others stop at their next piece
        flight: dict = {}  # future -> job
        moving: dict[int, list] = {}  # job id -> [bytes sent, size, name]
        rate, shown, sent_bytes = Rate(), [0.0], [0]
        sending: set[tuple[int, int]] = set()  # file identities of the uploads under way

        def show(force: bool = False) -> None:  # (this thread only)
            clock = time.monotonic()
            if not force and clock - shown[0] < 0.5:
                return
            shown[0] = clock
            with lock:
                parts = list(moving.values())
                total_sent = sent_bytes[0]
            if not parts:
                return
            done, size = sum(p[0] for p in parts), sum(p[1] for p in parts)
            rate.add(total_sent, 0)
            name = parts[0][2] if len(parts) == 1 else t("{n} files", n=len(parts))
            self.cb.progress(done, max(size, 1), upload_text(name, done, size, rate))

        def counter(jid: int) -> Callable[[int], None]:
            def on_bytes(k: int) -> None:  # (from an upload thread)
                if self.cancel_flag.is_set() or self.closing.is_set() or abort.is_set():
                    raise Cancelled()  # Stop works in the middle of a big video too
                with lock:
                    moving[jid][0] += k
                    sent_bytes[0] += k
            return on_bytes

        def send(job: dict):
            if streaming:
                return self.remote.upload(job["path"], on_bytes=counter(job["id"]))
            return self.remote.upload(job["path"])

        def finish(job: dict, f) -> None:
            """What came of one upload, on this thread."""
            path, rel, key, st, digest = job["path"], job["rel"], job["key"], job["st"], job["digest"]
            try:
                try:
                    receipt = f.result() if f is not None else job["result"]()
                except (AuthRequired, RemoteUnavailable, UploadRejected) as exc:
                    if not isinstance(exc, UploadRejected):
                        abort.set()
                        raise
                    retry = job["retry"]
                    self.uploads_waiting = True
                    self.state.set("upload_retry:" + key, json.dumps([digest, time.time() + 3600]))
                    if st.st_ino:
                        self.state.set(f"upload_retry:#{st.st_dev}:{st.st_ino}", json.dumps([digest, time.time() + 3600]))
                    if not retry or json.loads(retry)[0] != digest:
                        self.cb.activity("error", t("Upload failed ({name}): {error}", name=path.name, error=brief(exc)))
                    return
                except Cancelled:
                    abort.set()
                    raise
                except Exception:  # noqa: BLE001 - the photo may have arrived before the reply was lost
                    receipt = None
                show(force=True)
                self.state.set("upload_retry:" + key, None)
                if st.st_ino:
                    self.state.set(f"upload_retry:#{st.st_dev}:{st.st_ino}", None)
                self._mark(st, rel, digest, "copy" if receipt and receipt.is_duplicate else "sent")
                if receipt and receipt.asset_id:
                    if receipt.asset_id not in tracked_ids:
                        self.state.set("upload_sent:" + receipt.asset_id, "1")  # never downloaded back
                else:
                    # any item in iCloud with these bytes is this file's (or the same picture): one-way
                    self.state.set("upload_lost:" + digest, "1")
                if receipt is None or (not receipt.asset_id and not receipt.is_duplicate):
                    self.cb.activity("error", t("Upload unconfirmed: {name}. Check iCloud Photos and add it there manually if missing.", name=path.name))
                elif receipt.is_duplicate:
                    self.cb.activity("info", t("Already in iCloud Photos: {name}", name=path.name))
                else:
                    sent[0] += 1
                    self.cb.activity("upload", t("Uploaded to iCloud Photos: {name}", name=rel), str(path))
            finally:
                with lock:
                    moving.pop(job["id"], None)
                sending.discard((st.st_dev, st.st_ino))
                job["hold"].close()  # the file can be changed again

        def take(done) -> None:
            for f in done:
                finish(flight.pop(f), f)

        pool = ThreadPoolExecutor(workers) if workers > 1 else None
        try:
            for rel, path, live_video in self._upload_candidates():
                self._check_cancel()
                while pool is not None and len(flight) >= workers:  # room for one more
                    done, _ = wait(list(flight), timeout=0.5, return_when=FIRST_COMPLETED)
                    take(done)
                    show()
                    self._check_cancel()
                if attempts >= self.UPLOAD_MAX:
                    break
                key = rel.lower()
                hold = ExitStack()
                try:
                    stream = hold.enter_context(_upload_reader(path))
                    if not _upload_member(path) or not inside_root(self.root, path):
                        hold.close()
                        continue
                    st = os.fstat(stream.fileno())
                    if not self._upload_settled(rel, st):
                        hold.close()
                        continue
                    if st.st_ino and (st.st_dev, st.st_ino) in sending:
                        hold.close()
                        continue  # the same file under another name is being sent: decided next pass
                    signature = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
                    idle = self._upload_idle.get(key)
                    if idle and idle[0] == signature:
                        hold.close()
                        continue  # bounded delay only; the bytes are read again before anything is sent
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    self._check_cancel()  # (reading a big video takes a while: Stop meanwhile counts)
                    show()
                    mark = self._marked(st, rel)
                    if mark and self._same_marked(mark, st, digest):
                        if mark[0] != digest:
                            self._mark(st, rel, digest, mark[1])  # readable now: bound to its contents
                        self._upload_idle[key] = (signature, now + 600)
                        hold.close()
                        continue  # (renamed or moved: the same file; edited: new contents, sent)
                    left = self.state.get("upload_legacy:" + digest)
                    if left:
                        self._mark(st, rel, digest, "kept")  # dealt with by an earlier version: this file
                        try:
                            left = int(left) - 1
                        except ValueError:
                            left = 0
                        self.state.set("upload_legacy:" + digest, str(left) if left > 0 else None)
                        self._upload_idle[key] = (signature, now + 600)
                        hold.close()
                        continue
                    if (st.st_ino and (st.st_dev, st.st_ino) in self._held_ids()) or ("path", key) in self._held_ids():
                        self._upload_idle[key] = (signature, now + 600)
                        hold.close()
                        continue  # in an older photos folder when it was taken over: whose isn't known
                    if live_video:
                        # The single-file upload can't make a Live Photo pair; on its own it would be a
                        # separate video. It stays on this PC, also after its photo is gone.
                        self._mark(st, rel, digest, "live")
                        self._upload_idle[key] = (signature, now + 600)
                        hold.close()
                        continue
                    ident_key = f"#{st.st_dev}:{st.st_ino}" if st.st_ino else None
                    retry = self.state.get("upload_retry:" + key)
                    if ident_key and not retry:
                        retry = self.state.get("upload_retry:" + ident_key)  # (another name, same file)
                    if retry:
                        previous, after = json.loads(retry)
                        if previous == digest and time.time() < after:
                            self.uploads_waiting = True  # (the panel says so, not "Everything is in sync")
                            hold.close()
                            continue
                except OSError:
                    hold.close()
                    continue  # inaccessible or open for writing; no bytes sent
                except BaseException:
                    hold.close()  # (Stop while it was read: unlocked now, before waiting for the others)
                    raise
                attempts += 1
                self.cb.status(t("Uploading {name}…", name=path.name))
                job = {"id": attempts, "rel": rel, "path": path, "key": key, "st": st, "digest": digest,
                       "retry": retry, "hold": hold}
                with lock:
                    moving[job["id"]] = [0, st.st_size, path.name]
                if st.st_ino:
                    sending.add((st.st_dev, st.st_ino))
                if pool is None:
                    def run(job=job):
                        return send(job)
                    job["result"] = run
                    finish(job, None)
                else:
                    flight[pool.submit(send, job)] = job
            while flight:
                done, _ = wait(list(flight), timeout=0.5, return_when=FIRST_COMPLETED)
                take(done)
                show()
                if self.cancel_flag.is_set() or self.closing.is_set():
                    abort.set()
                    raise Cancelled()
        except BaseException:
            abort.set()  # uploads still running stop at their next piece, unregistered
            if pool is not None:
                pool.shutdown(wait=True, cancel_futures=True)
            for f, job in list(flight.items()):  # (iCloud may have confirmed some meanwhile)
                flight.pop(f)
                if f.done() and not f.cancelled() and not isinstance(f.exception(), Cancelled):
                    try:
                        finish(job, f)
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    job["hold"].close()
            raise
        finally:
            if pool is not None:
                pool.shutdown(wait=True, cancel_futures=True)
        return sent[0]

    def _confirm_moves(self, maybe: dict[tuple[str, str], list[str]]) -> tuple[dict[tuple[str, str], str], set]:
        """Which of the look-alike files really are the missing photos? iCloud's copy of each missing
        photo is downloaded once and compared with every look-alike not claimed yet. Returns (confirmed
        moves, the ones that couldn't be checked). A look-alike that matches nothing is someone's own."""
        import filecmp

        confirmed: dict[tuple[str, str], str] = {}
        unknown: set[tuple[str, str]] = set()
        claimed: set[str] = set()
        assets = self.remote.lookup(sorted({a for a, _ in maybe}))
        for (aid, key), candidates in maybe.items():
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
                if not res.size or tmp_path.stat().st_size != res.size:
                    unknown.add((aid, key))  # not the whole photo (or its size isn't known): proves nothing
                    continue
                for rel in candidates:
                    if rel not in claimed and filecmp.cmp(tmp_path, self._abs(rel), shallow=False):
                        confirmed[(aid, key)] = rel
                        claimed.add(rel)
                        break
            except (AuthRequired, RemoteUnavailable, Cancelled):
                raise
            except Exception as exc:  # noqa: BLE001
                log.info("Photos: couldn't compare a moved photo with iCloud's copy: %s", brief(exc))
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
        self._held_set = None
        self._id_index = None
        self._check_owner()
        self._migrate_upload_records()  # (only does something for records of an earlier version)
        local_deleted, moved = self.scan_local()
        if moved:
            self.cb.activity("info", t("Followed {n} files moved inside the folder", n=moved))
        if getattr(self.settings, "photos_upload", False) and not self._upload_saw_missing and not self.held:
            # (only when nothing is missing: a new-looking file might be a photo moved or renamed)
            self.uploads_waiting = False
            try:
                self.upload_new()
            except RemoteUnavailable as exc:
                self.uploads_waiting = True  # (the panel says so instead of "Everything is in sync")
                # Uploads wait for the next pass; what's new in iCloud still comes down now (a real
                # outage stops the reading below on its own).
                told = str(exc)
                if told != getattr(self, "_upload_wait_told", None):
                    self._upload_wait_told = told
                    self.cb.activity("info", t("Uploads to iCloud Photos wait: {error}", error=brief(exc)))
            else:
                self._upload_wait_told = None

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
                    try:
                        found = self.remote.lookup(need)
                    except LookupIncomplete:
                        found = {}
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
                self._keep_on_pc(a, rows_by_asset[a])
        if to_cloud:
            if not self.settings.delete_pc_to_cloud:
                for a in to_cloud:
                    if self._original_is_back(rows_by_asset[a]):
                        continue
                    self._forget_local_only(a, rows_by_asset[a])
                    self.cb.activity("local", t("Deleted on this PC only: {name}", name=remote_assets[a].filename))
            elif self._needs_answer(to_cloud, "cloud", len(to_cloud) > threshold):
                pending.to_cloud = to_cloud
            else:
                for i, a in enumerate(to_cloud, 1):
                    self._check_cancel()
                    self.cb.progress(i, len(to_cloud), t("Deleting in iCloud…"))
                    if self._original_is_back(rows_by_asset[a]):
                        continue  # restored on this PC since it was found missing: not deleted any more
                    try:
                        self.remote.delete(a)
                    except (AuthRequired, RemoteUnavailable):
                        raise
                    except Exception as exc:  # noqa: BLE001
                        result.errors += 1
                        self.cb.activity("error", t("Couldn't delete in iCloud ({name}): {error}", name=remote_assets[a].filename, error=brief(exc)))
                        continue
                    # remove the leftover Live Photo video, if any (never a photo put back meanwhile)
                    for r in rows_by_asset[a]:
                        p = self._abs(r.rel_path)
                        if (r.resource_key != "original" and not known_missing(p) and not self._changed_on_pc(r)
                                and not self._protected(p, r.rel_path, f"{r.asset_id}#{r.resource_key}")):
                            try:
                                self._trash_inside(p)
                            except OSError:
                                pass
                    self._keep_on_pc(a, rows_by_asset[a])
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
                    self._keep_on_pc(a, rows_by_asset[a])
                    self.cb.activity("local", t("Deleted in iCloud, kept on this PC: {name}", name=rows_by_asset[a][0].rel_path))
            elif self._needs_answer(to_pc, "pc", len(to_pc) > threshold):
                pending.to_pc = to_pc
            else:
                for i, a in enumerate(to_pc, 1):
                    self._check_cancel()
                    self.cb.progress(i, len(to_pc), t("Moving deleted photos to the Recycle Bin…"))
                    ok = True
                    rows_now = rows_by_asset[a]
                    if any(self._changed_on_pc(r) or self._protected(self._abs(r.rel_path), r.rel_path, f"{r.asset_id}#{r.resource_key}")
                           for r in rows_now):
                        # edited on this PC (another size than the photo Skyfold downloaded): kept
                        self._keep_on_pc(a, rows_now)
                        self.cb.activity("local", t("Deleted in iCloud, kept on this PC: {name}",
                                                    name=rows_now[0].rel_path))
                        continue
                    for r in rows_now:
                        p = self._abs(r.rel_path)
                        if not known_missing(p):  # also one that can't be looked at: that attempt fails, kept
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
        new_side = {"cloud": set(pending.to_cloud), "pc": set(pending.to_pc)}
        if not full:  # a quick look sees only part: what was asked stays asked
            new_side = {k: v | self.held_side[k] for k, v in new_side.items()}
            held |= self.held
        else:
            self.approved &= held  # approvals are single-use
            self.approved_side = {k: v & new_side[k] for k, v in self.approved_side.items()}
        if new_side != self.held_side:
            self.question_gen += 1  # another question: answers to the earlier one don't count
        for k in ("cloud", "pc"):  # each side has its own version: answering one leaves the other valid
            if new_side[k] != self.held_side[k]:
                self.side_gen[k] += 1
        self.held_side = new_side
        self.held = held
        if pending:
            pending.gen = self.question_gen
            pending.gen_side = {"to_cloud": self.side_gen["cloud"], "to_pc": self.side_gen["pc"]}

        # 3) new in iCloud ---------------------------------------------------
        excluded = self.state.excluded()
        tracked_keys = {(r.asset_id, r.resource_key) for rows in rows_by_asset.values() for r in rows}
        retry_failed = force_full or stale  # parked failures are tried again on a scheduled or asked-for full read
        parked = 0
        todo: list[tuple[RemoteAsset, RemoteResource]] = []
        for a in remote_assets.values():
            if a.id in excluded or a.id in held or a.id in local_deleted_set:
                continue
            if self.state.get("upload_sent:" + a.id):
                continue  # sent from this PC: the file is here already (one-way)
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
        todo.sort(key=lambda t: t[1].key != "original")  # a photo's original before its other parts
        todo.sort(key=lambda t: t[0].date, reverse=True)  # newest first (stable: originals stay first)

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
                if self.state.get("upload_sent:" + a.id):
                    continue  # recognised as sent from this PC just now (its still came first)
                try:
                    rel = self._place(a, res, taken)
                    if rel is None:  # an identical file already existed and was adopted
                        continue
                    target = self._abs(rel)
                    for folder in [target.parent, *target.parent.parents]:
                        if folder == self.root or not inside_root(self.root, folder):
                            break
                        if self._nested_library(folder):
                            raise SyncError(t("{path} is another photos folder inside this one; move it out of "
                                              "the photos folder", path=folder))
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
                        digest = _file_hash(tmp_path)  # what Skyfold downloaded: compared before it is ever recycled
                        if res.key == "original" and self.state.get("upload_lost:" + digest):
                            # the same bytes as a file sent from here whose reply got lost: never managed here
                            self.state.set("upload_sent:" + a.id, "1")
                            continue
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
                                                        a.date.isoformat(), digest), now_iso())
                            self._note_file_id(a.id, res.key, rel)
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
                        FileRow(a.id, res.key, rel, res.size, res.is_video, a.date.isoformat(), digest), now_iso()
                    )
                    self._note_file_id(a.id, res.key, rel)
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
        self._recover_album_stage()
        links = self.state.album_links()
        if not self.settings.albums_as_folders:
            sources = defaultdict(list)
            for r in self.state.files():
                sources[r.asset_id].append(self._abs(r.rel_path))
            for rel, aid in links.items():
                self._unlink_album(rel, sources.get(aid, []))
            self._prune_empty()
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
            self._prune_empty()
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
                    if not self.state.get("albumid:" + rel):  # a link made by an earlier version
                        if self._same_file(target, self._abs(source[rel])):
                            self._note_album_file(rel, target)
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
            try:
                self._make_album_dir(target.parent)
            except OSError as exc:
                log.warning("Album folder %s: %s", rel, exc)
                continue
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
                self._note_album_file(rel, target)
            except OSError as exc:
                log.warning("Album link %s: %s", rel, exc)
        self.state.set("albums_refreshed", now_iso())
        self._prune_empty()

    def _unlink_album(self, rel: str, sources: list[Path] | tuple = ()) -> None:
        """Remove an album entry - only when it is provably a second name of the photo itself. Anything
        else at that path (e.g. a picture someone saved there instead) is theirs: it stays where it is
        and Skyfold just stops counting it as its link."""
        p = self._abs(rel)
        if not inside_root(self.root, p.parent):
            self.state.drop_album_link(rel)  # leads elsewhere now: forget it, touch nothing there
            return
        try:
            if not known_missing(p):  # also when it can't be looked at: the move below fails and all is kept
                self._touch_own(p)
                # Moved to a private name first: the file checked is then the file removed, even if
                # something is saved at the album path in between.
                import uuid

                staged = p.with_name(f"{TMP_PREFIX}album-{uuid.uuid4().hex}{p.suffix}")
                stage_key = "albumstage:" + staged.relative_to(self.root).as_posix()
                self.state.set(stage_key, rel)  # written down first: put back after a crash
                os.rename(p, staged)
                ours = False
                for src in sources:
                    if self._same_file(staged, src):
                        ours = True  # provably a second name of the photo (both identities known)
                        break
                recorded, found = self.state.get("albumid:" + rel), self._file_id(staged)
                if ours:
                    staged.unlink()  # just a second name of the photo: nothing is lost
                elif recorded and found and recorded == found:
                    # the very file Skyfold put here, but its photo is gone: recoverable, as before
                    self._trash(self._put_back(staged, p))
                else:
                    self._put_back(staged, p)  # can't prove it's ours: it stays, untouched
                if known_missing(staged):
                    self.state.set(stage_key, None)
        except OSError:
            return
        self.state.drop_album_link(rel)
        self.state.set("albumid:" + rel, None)

    @staticmethod
    def _file_id(p: Path) -> str | None:
        try:
            st = os.stat(p)
        except OSError:
            return None
        return f"{st.st_dev}:{st.st_ino}" if st.st_ino else None

    @classmethod
    def _same_file(cls, a: Path, b: Path) -> bool:
        """Provably one file under two names: both identities known, and equal. Where the file system
        can't tell (no file IDs, an error), the answer is no - and nothing is removed because of it."""
        ia, ib = cls._file_id(a), cls._file_id(b)
        return bool(ia and ib and ia == ib)

    def _recover_album_stage(self) -> None:
        """An album entry that was under its private name when Skyfold stopped gets its name back."""
        for key, rel in self.state.items("albumstage:"):
            staged = self._abs(key[len("albumstage:"):])
            try:
                if staged.is_file() and inside_root(self.root, staged.parent):
                    self._put_back(staged, self._abs(rel))
            except OSError:
                continue
            if known_missing(staged):
                self.state.set(key, None)

    def _note_album_file(self, rel: str, p: Path) -> None:
        """Remember which file Skyfold put at an album path: a different file found there later
        (someone saved their own picture under that name) is never removed."""
        self.state.set("albumid:" + rel, self._file_id(p))

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

    def _make_album_dir(self, folder: Path) -> None:
        """Create an album folder, writing down every level Skyfold itself had to make: only those
        are ever removed again when they end up empty."""
        for level in [folder, *folder.parents]:
            if level == self.root or not inside_root(self.root, level):
                break
            if level.exists() and self._nested_library(level):
                raise SyncError(t("{path} is another photos folder inside this one; move it out of "
                                  "the photos folder", path=level))
        missing = []
        p = folder
        while p != self.root and not p.exists():
            missing.append(p)
            p = p.parent
        for d in reversed(missing):
            try:
                d.mkdir()
            except FileExistsError:
                continue  # someone made it meanwhile: theirs
            self.state.set("albumdir:" + d.relative_to(self.root).as_posix(), self._file_id(d))

    def _prune_empty(self) -> None:
        """Remove empty album folders - only the ones Skyfold made (written down when it made them).
        An empty folder someone created under Albums is theirs and stays."""
        for key, made in sorted(self.state.items("albumdir:"), key=lambda kv: len(kv[0]), reverse=True):
            p = self._abs(key[len("albumdir:"):])
            try:
                if not inside_root(self.root, p.parent) or p.is_symlink() or                         (hasattr(os.path, "isjunction") and os.path.isjunction(p)):
                    self.state.set(key, None)  # no longer the folder Skyfold made
                    continue
                if known_missing(p):
                    self.state.set(key, None)
                    continue
                found = self._file_id(p)
                if found is None:
                    continue  # can't tell which folder this is now: kept, looked at again next time
                if found != made:
                    self.state.set(key, None)  # another folder has that name now: someone's
                    continue
                p.rmdir()  # only succeeds when empty
                self.state.set(key, None)
            except OSError:
                pass  # still in use: kept, and looked at again next time

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
                if same and (self._marked(st, cand) or (st.st_ino and (st.st_dev, st.st_ino) in self._held_ids())):
                    same = False  # one kept here or sent from here, or of an older folder: not this photo
                if same:  # looks like this very photo (e.g. the database was rebuilt): compare after download
                    self._verify.add(cand)
                    return cand
            n += 1

    def _changed_on_pc(self, r: FileRow) -> bool:
        """Right before a photo file goes to the Recycle Bin: is it still what Skyfold downloaded?
        Another size, or (when the fingerprint is known) another content, means it was edited on this
        PC and stays. A file that can't be read is left alone too."""
        p = self._abs(r.rel_path)
        try:
            if r.size and os.stat(p).st_size != r.size:
                return True
        except OSError:
            return not known_missing(p)  # not there: nothing to keep; can't be looked at: kept
        if not r.hash:
            return False  # downloaded before fingerprints existed: only the size can be compared
        try:
            return _file_hash(p) != r.hash
        except OSError:
            return True

    def _original_is_back(self, rows: list[FileRow]) -> bool:
        rows = [r for r in rows if f"{r.asset_id}#{r.resource_key}" not in getattr(self, "_displaced", ())]
        """Asked right before a "deleted on this PC" decision is carried out: the photo was missing
        when the folder was scanned - is it there again (Undo in Explorer, an editor's save)?"""
        # "can't be looked at" counts as back: nothing is deleted on an answer that isn't sure
        return any(r.resource_key == "original" and not known_missing(self._abs(r.rel_path)) for r in rows)

    def _forget_local_only(self, asset_id: str, rows: list[FileRow]) -> None:
        for r in rows:
            p = self._abs(r.rel_path)
            if not known_missing(p) and not self._changed_on_pc(r) and not self._protected(p, r.rel_path, f"{r.asset_id}#{r.resource_key}"):
                try:
                    self._trash_inside(p)
                except OSError:
                    pass
        self._keep_on_pc(asset_id, rows)
        self.state.exclude(asset_id, "deleted on PC")

    # decisions on held deletions -------------------------------------------
    def approve(self, ids: list[str], side: str | None = None) -> None:
        """The user's OK; with a side ("cloud"/"pc") it only counts for deletions on that side."""
        if side in self.approved_side:
            self.approved_side[side].update(ids)
        else:
            self.approved.update(ids)

    def _needs_answer(self, ids: list[str], side: str, over: bool) -> bool:
        """Must these deletions wait for the user? When there are more than the limit, or when any of
        them was asked about before (also if fewer are left now), until they were OK'd for that side."""
        if not over and not any(a in self.held_side[side] for a in ids):
            return False
        ok = self.approved | self.approved_side[side]
        return not set(ids) <= ok

    def reject_to_cloud(self, ids: list[str]) -> None:
        """User wants files deleted on the PC back: forget them so they download again."""
        rows = self.state.files()
        for a in ids:
            self._keep_on_pc(a, [r for r in rows if r.asset_id == a])
            self.state.unexclude_parts(a)  # its Live Photo video comes back too

    def reject_to_pc(self, ids: list[str]) -> None:
        """User wants to keep local copies of photos deleted in iCloud: stop tracking them."""
        rows = self.state.files()
        for a in ids:
            self._keep_on_pc(a, [r for r in rows if r.asset_id == a])

    def _keep_on_pc(self, asset_id, rows):
        rows = [r for r in rows if f"{r.asset_id}#{r.resource_key}" not in getattr(self, "_displaced", ())]
        for row in rows:
            p = self._abs(row.rel_path)
            if known_missing(p):
                continue
            digest = None  # (can't be read now: its size and time stand in)
            if _upload_member(p) and inside_root(self.root, p):
                try:
                    digest = _file_hash(p)
                except OSError:
                    pass
            try:
                self._mark(os.lstat(p), row.rel_path, digest, "kept")
            except OSError:
                pass
        self.state.remove_asset(asset_id)
