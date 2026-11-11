"""Background worker: owns the sync engines, timers and folder watchers; lives in its own QThread.

Two services run in the same cycle: Photos (cheap change feed, checked often) and iCloud Drive
(full tree walk, which Apple serves slowly, so it is checked less often and right away when
something changes in the Drive folder on this PC).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot

from . import auth
from .config import STATE_DB, Settings
from .drive_engine import MARKER as DRIVE_MARKER
from .drive_engine import TMP_PREFIX as DRIVE_TMP
from .drive_engine import DriveState, DriveSyncer, ignored
from .drive_remote import ICloudDrive
from .notes_engine import ICloudNotes, NotesState, NotesSyncer
from . import extras
from .engine import MARKER, TMP_PREFIX, Callbacks, Cancelled, Pending, SyncError, Syncer, now_iso
from .i18n import t
from .safelog import brief, describe
from .remote import AuthRequired, ICloudRemote, RemoteUnavailable
from .state import State

log = logging.getLogger(__name__)

DRIVE_FULL_INTERVAL = 60 * 60  # seconds between full Drive reads (a large Drive takes ~10 min to read)


class _Bridge(Callbacks):
    def __init__(self, worker: "SyncWorker", service: str = "app") -> None:
        self.w = worker
        self.service = service
        self._last_progress = 0.0
        self._last_status = 0.0

    def status(self, text: str) -> None:
        now = time.monotonic()
        if now - self._last_status >= 0.25:  # don't flood the UI thread
            self._last_status = now
            self.w._set_state("syncing", text, self.service)

    def progress(self, done: int, total: int, text: str) -> None:
        now = time.monotonic()
        # download progress arrives per 1 MB chunk; ~10 updates/s are plenty for a progress bar
        if now - self._last_progress >= 0.1 or done >= total:
            self._last_progress = now
            self.w.progress.emit(done, total, text)

    def activity(self, kind: str, text: str, target: str = "") -> None:
        self.w.log(kind, text, target)


class _Watcher:
    """Watches one folder tree with watchdog and calls `hit(path, kind)` from watchdog's thread."""

    def __init__(self, root: str, hit: Callable[[str, str], None]) -> None:
        self.root = root
        self.observer: Any = None
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):  # noqa: N802
                if event.event_type in ("deleted", "moved", "created", "modified"):
                    hit(event.src_path, event.event_type)
                    if event.event_type == "moved" and getattr(event, "dest_path", None):
                        hit(event.dest_path, "created")

        self.observer = Observer()
        self.observer.schedule(Handler(), root, recursive=True)
        self.observer.daemon = True
        self.observer.start()

    def alive(self) -> bool:
        return self.observer is not None and self.observer.is_alive()

    def stop(self) -> None:
        if self.observer is not None:
            self.observer.stop()
            self.observer = None


class SyncWorker(QObject):
    stateChanged = Signal(str, str)  # state, text  (idle|syncing|paused|error|auth|offline|setup)
    progress = Signal(object, object, str)  # done, total (may exceed 2^31), text
    activity = Signal(str, str, str, str)  # iso time, kind, text, file/folder it is about ("" = none)
    statsChanged = Signal(dict)
    pendingChanged = Signal(object)
    notify = Signal(str, str)
    accountChanged = Signal(str)
    _localChange = Signal()
    _driveChange = Signal()
    _drivePendingSeen = Signal(object, object)  # to_cloud, to_pc; raised from the Drive thread
    _authLost = Signal(object)  # the api that failed; raised from the Drive thread too

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings.copy()
        from .engine import THROTTLE

        THROTTLE.rate = max(0, int(self.settings.speed_limit_mb or 0)) * 1_000_000
        self.state: State | None = None
        self.drive_state: DriveState | None = None
        self.api: Any = None
        self.syncer: Syncer | None = None
        self.drive: DriveSyncer | None = None
        self.notes: NotesSyncer | None = None
        self.notes_state: NotesState | None = None
        self.busy = False
        self._again: tuple[bool, bool] | None = None  # (full photos check, force drive)
        self._watchers: dict[str, _Watcher] = {}
        self._pending: Pending | None = None
        self._drive_pending: Pending | None = None
        self._drive_last = 0.0
        self._storage: dict | None = None
        self._storage_at = 0.0
        self._drive_changes: set[str] = set()  # PC paths changed since the last quick Drive pass
        self._drive_retry: set[str] = set()  # paths of a failed quick pass
        self._drive_lock = threading.Lock()
        self._last_state = ("idle", "")  # Photos / app-wide state (sign-in, setup, pause…)
        self._drive_ui = ("idle", "")  # state of the background Drive pass
        self._state_lock = threading.Lock()
        self._drive_thread: threading.Thread | None = None
        self._drive_want_full = False
        self._closing = False  # set first thing on Quit: nothing is started after that
        self._pause_now = False  # set by the UI thread the moment Pause is pressed
        self._last_silent_refresh = -1e9
        self._silent_refreshes: list[float] = []

    # lifecycle --------------------------------------------------------------
    @Slot()
    def start(self) -> None:
        if self._closing_now():
            return
        self.state = State(STATE_DB)
        self.drive_state = DriveState(STATE_DB)
        self.notes_state = NotesState(STATE_DB)
        st = self.state.stats()
        log.info("Sync database %s: %d photos, %d videos tracked; %d notes; %d Drive items",
                 STATE_DB, st["photos"], st["videos"], self.notes_state.count(),
                 self.drive_state.stats()["files"])
        self.poll = QTimer(self)
        self.poll.timeout.connect(lambda: self.sync_now(False))
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(4000)
        self.debounce.timeout.connect(lambda: self.sync_now(False))
        self._localChange.connect(self.debounce.start)
        self.drive_debounce = QTimer(self)
        self.drive_debounce.setSingleShot(True)
        self.drive_debounce.setInterval(5000)
        self.drive_debounce.timeout.connect(self._drive_changed)
        self._driveChange.connect(self.drive_debounce.start)
        self._authLost.connect(self._on_auth_lost, Qt.QueuedConnection)  # after the current cycle
        self._drivePendingSeen.connect(self._on_drive_pending, Qt.QueuedConnection)
        if self._closing_now():
            return  # Quit came while the database was being opened
        self._apply_timers()
        self._ensure_watchers()
        self._emit_stats()
        for ts, kind, text, target in reversed(self.state.recent_activity(40)):
            self.activity.emit(ts, kind, text, target)
        self.restore_session()

    @Slot()
    def shutdown(self) -> None:
        self._closing = True  # nothing is started any more
        self.cancel()
        self._stop_watchers()

    def _closing_now(self) -> bool:
        return bool(getattr(self, "_closing", False))

    def set_paused(self, paused: bool) -> None:
        """Thread-safe, called by the UI the moment Pause/Resume is pressed: the worker may be busy
        and only hears about the changed setting later."""
        self._pause_now = bool(paused)

    def _paused(self) -> bool:
        return bool(self.settings.paused or getattr(self, "_pause_now", False))

    def _halt(self) -> bool:
        """Between the services of a cycle: has Quit or Pause been asked for meanwhile?"""
        if self._closing_now():
            return True
        if self._paused():
            self._set_state("paused", t("Syncing is paused."))
            return True
        return False

    def _stop_requested(self, service: str) -> bool:
        """Was the pass of this service asked to stop (quit, pause)? Each pass clears its flag when it starts."""
        s = {t("iCloud Drive"): self.drive, t("Notes"): self.notes}.get(service, self.syncer)
        return self._closing_now() or (s is not None and s.cancel_flag.is_set())

    def cancel(self) -> None:
        """Thread-safe: may be called directly from the UI thread."""
        if not self._paused():  # (paused: Resume is what starts work again)
            self._drive_stopped = True  # queued Drive work waits for the next request to sync
        for s in (self.syncer, self.drive, self.notes):
            if s:
                if self._closing_now() and hasattr(s, "closing"):
                    s.closing.set()  # quitting: a pass that is just starting can't clear this one
                s.cancel_flag.set()

    def _set_state(self, state: str, text: str, service: str = "app") -> None:
        """Photos/app and Drive run side by side; the panel shows the one that matters most."""
        with self._state_lock:
            if service == "drive":
                self._drive_ui = (state, text)
            else:
                self._last_state = (state, text)
            app, drive = self._last_state, self._drive_ui
            if app[0] in ("auth", "setup", "paused", "offline"):
                shown = app
            elif app[0] == "syncing":
                shown = app
            elif drive[0] == "syncing":
                shown = drive
            elif app[0] == "error":
                shown = app
            elif drive[0] in ("error", "offline"):
                shown = drive
            else:
                shown = app
        self.stateChanged.emit(*shown)

    def log(self, kind: str, text: str, target: str = "") -> None:
        ts = now_iso()
        if self.state:
            self.state.log_activity(ts, kind, text, target)
        self.activity.emit(ts, kind, text, target)
        log.info("[%s] %s", kind, text)

    def _emit_stats(self) -> None:
        if not self.state:
            return
        stats: dict[str, Any] = dict(self.state.stats())
        stats["last_sync"] = self.state.get("last_sync")
        stats["drive_enabled"] = self.settings.drive_enabled
        if self.drive_state is not None:
            d = self.drive_state.stats()
            stats["drive_files"], stats["drive_bytes"] = d["files"], d["bytes"]
        stats["drive_last_sync"] = self.state.get("drive_last_sync")
        stats["notes_enabled"] = self.settings.notes_enabled
        stats["storage"] = self._storage
        if self.notes_state is not None:
            stats["notes_count"] = self.notes_state.count()
            stats["notes_last_sync"] = self.notes_state.get("last_sync")
        self.statsChanged.emit(stats)

    # session ----------------------------------------------------------------
    @Slot()
    def restore_session(self) -> None:
        if self._closing_now():
            return
        if not self.settings.photos_dir_confirmed:
            self._set_state("setup", t("Choose where your photos should be kept before the first sync."))
            return
        if not self.settings.apple_id:
            self._set_state("auth", t("Sign in with your Apple ID to start syncing."))
            return
        self._set_state("syncing", t("Connecting to iCloud…"))
        try:
            api = auth.restore(self.settings.apple_id)
        except auth.Offline:
            # keep the session; the poll timer retries via sync_now → restore_session
            self._set_state("offline", t("Can't reach iCloud. Will retry shortly."))
            return
        if api is None:
            if auth.SIGNED_OUT.exists():  # signed out on purpose: nothing has expired
                self._set_state("auth", t("You are signed out. Sign in to continue."))
                return
            self._set_state("auth", t("Your session has expired. Please sign in again."))
            self.notify.emit(t("Sign-in required"), t("Your iCloud session has expired. Click to sign in again."))
            return
        self.set_api(api)

    @staticmethod
    def _serialize_requests(api: Any) -> None:
        """Photos and Drive run in parallel threads but share one Apple session. pyicloud saves
        the session token to disk after every request, so requests go one at a time (each
        request is short; downloads stream outside the lock)."""
        session = getattr(api, "session", None)
        if session is None or getattr(session, "_skyfold_locked", False):
            return
        lock = threading.RLock()
        original = session.request

        def request(method, url, *args, **kwargs):
            with lock:
                return original(method, url, *args, **kwargs)

        session.request = request
        session._skyfold_locked = True

    @Slot(object)
    def set_api(self, api: Any) -> None:
        self._serialize_requests(api)
        auth.make_current(api)
        self._check_account()
        self.api = api
        for old in (self.drive, self.notes):  # they hold the previous session
            if old is not None:
                old.cancel_flag.set()
        self.drive = None
        self.notes = None
        self._set_state("idle", "")
        self.syncer = Syncer(self.state, ICloudRemote(api), self.settings, _Bridge(self))
        self._make_drive()
        self._make_notes()
        self.accountChanged.emit(self.settings.apple_id)
        if self.settings.paused:
            self._set_state("paused", t("Syncing is paused."))
        else:
            self.sync_now(False)

    def _check_account(self) -> None:
        """The sync databases belong to one Apple ID. If they were written for another one (e.g. the
        settings were lost and someone else signed in), Photos and Notes start afresh: nothing of the
        other account is ever taken for a deletion."""
        import hashlib

        if not self.settings.apple_id or self.state is None:
            return
        fp = hashlib.sha256(self.settings.apple_id.strip().lower().encode()).hexdigest()[:16]
        known = self.state.get("account")
        if known and known != fp:
            log.warning("The sync database belongs to another Apple ID; Photos and Notes start afresh")
            self.state.reset()
            self._pending = None
            self.pendingChanged.emit(self._drive_pending)
            if self.notes_state is not None:
                self.notes_state.reset()
        self.state.set("account", fp)

    def _make_drive(self) -> None:
        s = self.settings
        if not (s.drive_enabled and self.api is not None and self.drive_state is not None):
            if self.drive is not None:
                self.drive.cancel_flag.set()
            self.drive = None
            self._set_state("idle", "", "drive")
            return
        if self.drive is None or self.drive.root != Path(s.drive_dir):
            if self.drive is not None:
                self.drive.cancel_flag.set()
            self.drive = DriveSyncer(self.drive_state, ICloudDrive(self.api), Path(s.drive_dir),
                                     s.delete_threshold, _Bridge(self, "drive"),
                                     cache_file=STATE_DB.parent / "drive_cache.json")
            self.drive.on_pending = self._drivePendingSeen.emit
            import hashlib

            self.drive.account = hashlib.sha256(s.apple_id.strip().lower().encode()).hexdigest()[:16] \
                if s.apple_id else ""
            self._drive_last = 0.0
        self.drive.delete_threshold = s.delete_threshold
        self.drive.folders = list(s.drive_folders) if s.drive_folders is not None else None
        self.drive.delete_pc_to_cloud = s.drive_delete_pc_to_cloud
        self.drive.delete_cloud_to_pc = s.drive_delete_cloud_to_pc

    def _make_notes(self) -> None:
        s = self.settings
        if not (s.notes_enabled and self.api is not None and self.notes_state is not None):
            self.notes = None
            return
        last = self.notes_state.get("root")
        if last is None and self.notes is not None:
            last = str(self.notes.root)
        if last is None and self.notes_state.count():
            last = "?"  # exported before this was remembered: can't tell, assume the same folder
        if last not in (None, "?") and Path(last) != Path(s.notes_dir):
            self.notes_state.reset()  # another notes folder (even one set while Notes was off): export all there
        self.notes_state.set("root", str(s.notes_dir))
        if self.notes is None or self.notes.root != Path(s.notes_dir):
            self.notes = NotesSyncer(self.notes_state, ICloudNotes(self.api), Path(s.notes_dir), _Bridge(self))

    @Slot()
    def sign_out(self) -> None:
        self.cancel()
        self.api = None
        self.syncer = None
        self.drive = None
        self.notes = None
        # (the session files were removed by the window side at once; doing it here, later, could
        # remove a sign-in made meanwhile)
        self._set_state("auth", t("You are signed out. Sign in to continue."))

    # settings ---------------------------------------------------------------
    @Slot(object, bool, bool)
    def apply_settings(self, settings: Settings, reset_state: bool, reset_drive: bool = False) -> None:
        old_folders = self.settings.drive_folders
        albums_changed = settings.albums_as_folders != self.settings.albums_as_folders
        new_account = bool(self.settings.apple_id) and settings.apple_id.lower() != self.settings.apple_id.lower()
        self.settings.__dict__.update(settings.copy().__dict__)
        if new_account:
            reset_state = reset_drive = True
            # the Drive and Notes folders hold the other account's files: never sync them into this one
            self.settings.drive_enabled = False
            self.settings.notes_enabled = False
            if self.notes_state:
                self.notes_state.reset()
        from .engine import THROTTLE

        THROTTLE.rate = max(0, int(self.settings.speed_limit_mb or 0)) * 1_000_000
        if settings.drive_folders != old_folders and self.drive is not None and self.drive_running():
            self.drive.cancel_flag.set()  # the running pass still uses the old choice
        if settings.drive_folders != old_folders and self.drive_state and not reset_drive:
            # Folders no longer synced are forgotten (never deleted anywhere): if one is chosen again,
            # its files are matched up afresh instead of being taken for deletions.
            from .drive_engine import in_scope

            self.drive_state.drop_many([path for path, row in self.drive_state.all().items()
                                        if not in_scope(path, row.is_dir, settings.drive_folders)])
            self._drive_last = 0.0  # read Drive again with the new choice
        if albums_changed and self.state and not reset_state:
            self.state.set("last_full_scan", None)  # next check reads the library and the albums
            self.state.set("albums_refreshed", None)
        if reset_state and self.state:
            self.state.reset()
            self._pending = None
            self.pendingChanged.emit(self._drive_pending)
        if reset_drive and self.drive_state:
            self.drive_state.reset()
            try:
                (STATE_DB.parent / "drive_cache.json").unlink(missing_ok=True)
            except OSError as exc:
                log.warning("Couldn't remove the Drive cache: %s", exc)
            self.state.set("drive_last_sync", None)
            if self.drive is not None:
                self.drive.cache = {}
            self._drive_pending = None
            self.pendingChanged.emit(self._pending)
        self._make_drive()
        self._make_notes()
        self._ensure_watchers()
        self._apply_timers()
        self._emit_stats()
        if self.settings.paused:
            self.cancel()
            self._set_state("paused", t("Syncing is paused."))
        elif self._last_state[0] == "paused" or (self.drive and not self._drive_last):
            self.sync_now(False)

    def _apply_timers(self) -> None:
        self.poll.stop()
        if not self.settings.paused:
            self.poll.start(max(1, int(self.settings.poll_minutes)) * 60_000)

    # watchers ---------------------------------------------------------------
    def _photos_hit(self, path: str, kind: str) -> None:
        if kind not in ("deleted", "moved"):
            return
        name = Path(path).name
        if name.startswith(TMP_PREFIX) or name == MARKER:
            return
        if self.syncer and self.syncer.is_own_path(path):
            return
        if self.settings.instant_local_deletes and not self.settings.paused:
            self._localChange.emit()

    def _drive_hit(self, path: str, kind: str) -> None:
        name = Path(path).name
        if name.startswith(DRIVE_TMP) or name == DRIVE_MARKER or ignored(name):
            return
        if self.drive and self.drive.is_own_path(path):
            return
        if not self.settings.paused:
            try:
                rel = Path(path).relative_to(self.settings.drive_dir).as_posix()
            except ValueError:
                return
            with self._drive_lock:
                self._drive_changes.add(rel)
            self._driveChange.emit()

    def _ensure_watchers(self) -> None:
        """(Re)start folder watchers once the folders exist (the first sync creates them; a
        drive can also disappear and come back), so this is checked again after every sync."""
        if self._closing_now():
            return
        wanted = {"photos": (self.settings.photos_dir, self._photos_hit)}
        if self.settings.drive_enabled:
            wanted["drive"] = (self.settings.drive_dir, self._drive_hit)
        for key in list(self._watchers):
            w = self._watchers[key]
            if key not in wanted or w.root != wanted[key][0] or not w.alive():
                w.stop()
                del self._watchers[key]
        for key, (root, hit) in wanted.items():
            if key in self._watchers or not Path(root).exists():
                continue
            try:
                self._watchers[key] = _Watcher(root, hit)
            except (OSError, ImportError) as exc:
                log.warning("Watcher for %s failed: %s", root, exc)

    def _stop_watchers(self) -> None:
        for w in self._watchers.values():
            w.stop()
        self._watchers.clear()

    # sync -------------------------------------------------------------------
    @Slot(bool)
    def sync_now(self, manual: bool = True) -> None:
        """Quick sync: Photos reads only what changed; Drive runs if it's due (or on manual sync)."""
        self._request(full=False, manual=manual, force_drive=manual)

    @Slot()
    def full_check(self) -> None:
        """Read the whole Photos library and compare everything (the daily safety net, on demand)."""
        self._request(full=True, manual=True, force_drive=True)

    def _drive_changed(self) -> None:
        """Debounced reaction to changes in the Drive folder: a quick pass over those folders only."""
        if self.drive is None or self.settings.paused:
            return
        self._kick_drive(full=False)

    # Drive runs in its own thread so a long read (a big Drive takes ~10 min) never holds up Photos.
    def _kick_drive(self, full: bool) -> None:
        with self._drive_lock:
            if getattr(self, "_closing", False) or self._paused():
                return  # quitting or paused: nothing new starts (Resume asks again)
            self._drive_stopped = False  # a new request to sync
            if full:
                self._drive_want_full = True
            if self._drive_thread is not None and self._drive_thread.is_alive():
                return  # the running pass picks the request up when it finishes
            self._drive_thread = threading.Thread(target=self._drive_loop, name="SkyfoldDrive", daemon=True)
            self._drive_thread.start()

    def drive_running(self) -> bool:
        t_ = self._drive_thread
        return t_ is not None and t_.is_alive()

    def _drive_loop(self) -> None:
        while True:
            with self._drive_lock:
                if self.drive is None:
                    break
                if self._paused() or getattr(self, "_closing", False) \
                        or getattr(self, "_drive_stopped", False):
                    break  # paused, stopped or quitting meanwhile: the waiting changes stay queued
                if self._drive_want_full:
                    self._drive_want_full, changed = False, None
                elif self._drive_changes:
                    changed, self._drive_changes = self._drive_changes | self._drive_retry, set()
                    self._drive_retry = set()
                else:
                    break
            problems: list[str] = []
            ok = self._run_drive(problems, changed)
            self._emit_stats()
            if not ok:
                if problems:
                    self._set_state("error", problems[0], "drive")
                break
            self._set_state("error" if problems else "idle", problems[0] if problems else "", "drive")

    SILENT_REFRESH_COOLDOWN = 10 * 60  # seconds; never loop on a session that really expired

    @Slot()
    @Slot(object)
    def _on_auth_lost(self, failed_api: Any = None) -> None:
        if self._closing_now():
            return
        """Apple rejected a request. Often that's a short-lived web token that just needs renewing
        (HTTP 421/450), not an expired sign-in: renew it quietly from the saved session first and
        only ask the user to sign in when that fails."""
        if failed_api is None or failed_api is not self.api:
            return  # about a session that was already replaced or signed out (e.g. Photos and Drive failed together)
        for s in (self.syncer, self.drive, self.notes):
            if s:
                s.cancel_flag.set()
        self.api = None
        self.syncer = None
        self.drive = None
        self.notes = None
        self._set_state("idle", "", "drive")
        now = time.monotonic()
        recent = [x for x in self._silent_refreshes if now - x < self.SILENT_REFRESH_COOLDOWN]
        if len(recent) < 3 and self.settings.apple_id:
            self._silent_refreshes = recent + [now]
            self._last_silent_refresh = now
            try:
                api = auth.restore(self.settings.apple_id)
            except auth.Offline:
                self._set_state("offline", t("Can't reach iCloud. Will retry shortly."))
                return  # the next poll goes through restore_session again
            if api is not None:
                log.info("iCloud session renewed silently")
                self.set_api(api)
                return
        self._set_state("auth", t("Your session has expired. Please sign in again."))
        self.notify.emit(t("Sign-in required"), t("Your iCloud session has expired. Click to sign in again."))

    def _request(self, full: bool, manual: bool, force_drive: bool = False) -> None:
        if self._closing_now():
            return
        if not self.settings.photos_dir_confirmed:
            self._set_state("setup", t("Choose where your photos should be kept before the first sync."))
            return
        if self.settings.paused and not manual:
            return
        if self.syncer is None:
            if self.api is None and self.settings.apple_id and self._last_state[0] not in ("auth",):
                self.restore_session()
            return
        if self.busy:
            prev = self._again or (False, False)
            self._again = (prev[0] or full, prev[1] or force_drive)
            return
        self.busy = True
        try:
            self._cycle(full, force_drive)
            while self._again is not None:
                (again_full, again_drive), self._again = self._again, None
                self._cycle(again_full, again_drive)
        finally:
            self.busy = False

    def _cycle(self, full: bool, force_drive: bool) -> None:
        problems: list[str] = []
        if self._closing_now():
            return
        if not self._run_photos(full, problems):
            return  # session/auth state already shown
        if self._halt():
            return
        self._refresh_storage(force=full)
        if self._halt():
            return  # quitting or paused: no further service starts (a new pass would clear the stop)
        if self.notes is not None and not self._run_notes(full, problems):
            return
        if self._halt():
            return
        drive_due = self.drive is not None and (
            # 0 = never read yet (monotonic() counts from boot, so it may still be < the interval)
            force_drive or not self._drive_last or time.monotonic() - self._drive_last >= DRIVE_FULL_INTERVAL)
        if drive_due:
            self._kick_drive(full=True)  # in the background; Photos carries on
        self._ensure_watchers()
        self._emit_stats()
        if problems:
            self._set_state("error", problems[0])
        elif self.settings.paused:
            self._set_state("paused", t("Syncing is paused."))
        else:
            self._set_state("idle", t("Everything is in sync."))

    def _handle(self, exc: Exception, problems: list[str], service: str, svc: str = "app",
                api: Any = None) -> bool:
        """Shared error handling. Returns False if the whole cycle must stop. `api` is the session the
        failing pass used (a pass may still finish after a new sign-in replaced it)."""
        failed_api = api if api is not None else self.api
        if not isinstance(exc, Cancelled) and self._stop_requested(service):
            # Quitting or pausing cut the pass short (e.g. Python refuses new work while shutting
            # down): that is a stop, not a sync error to show in the panel.
            log.info("%s stopped while quitting/pausing (%s)", service, type(exc).__name__)
            exc = Cancelled()
        if isinstance(exc, Cancelled):
            self._set_state("idle", t("Sync stopped."), svc)
            return False
        if isinstance(exc, AuthRequired):
            cause = exc.__cause__
            # Only the kinds of error: Apple's texts can carry cookie values (the log is shared in bug reports).
            log.warning("Sign-in lost during %s (%s, cause %s)", service, type(exc).__name__,
                        type(cause).__name__ if cause else "-")
        if isinstance(exc, AuthRequired) and svc == "drive":
            self._authLost.emit(failed_api)  # session objects are owned by the worker thread
            return False
        if isinstance(exc, AuthRequired):
            # Defer: we may be inside a sync cycle; renewing replaces the session objects.
            self._authLost.emit(failed_api)
            return False
        text = brief(exc)  # Skyfold's own messages as they are; Apple's answers only by kind and code
        if isinstance(exc, (RemoteUnavailable, SyncError)):
            problems.append(f"{service}: {text}")
            self.log("error", f"{service}: {text}")
            return True
        log.error("Sync failed (%s): %s", service, describe(exc, frames=8))
        from pyicloud.exceptions import PyiCloudConnectionException

        raw = str(exc)
        if isinstance(exc, PyiCloudConnectionException) or "ConnectionError" in type(exc).__name__ \
                or "Timeout" in type(exc).__name__ or "Connection" in raw or "timed out" in raw:
            self._set_state("offline", t("Can't reach iCloud. Will retry shortly."), svc)
            return False
        problems.append(t("Error: {error}", error=f"{service}: {text[:200]}"))
        self.log("error", t("Sync error: {error}", error=f"{service}: {text[:200]}"))
        return True

    def _run_photos(self, full: bool, problems: list[str]) -> bool:
        assert self.syncer is not None
        self._set_state("syncing", t("Checking for changes…"))
        first = self.state is not None and not self.state.get("last_sync")
        try:
            res = self.syncer.run(force_full=full)
        except Exception as exc:  # noqa: BLE001
            self._emit_stats()
            return self._handle(exc, problems, t("Photos"))
        if res.pending != self._pending:
            self._pending = res.pending
            if res.pending:
                res.pending.service = "photos"
                n = len(res.pending.to_cloud) + len(res.pending.to_pc)
                self.notify.emit(t("Confirmation needed"), t("{n} items are waiting for your OK to be deleted.", n=n))
            self.pendingChanged.emit(self._pending or self._drive_pending)
        if self.settings.notifications:
            parts = []
            if res.downloaded:
                parts.append(t("Downloaded: {n}", n=res.downloaded))
            if res.deleted_local:
                parts.append(t("Deleted on PC: {n}", n=res.deleted_local))
            if res.deleted_cloud:
                parts.append(t("Deleted in iCloud: {n}", n=res.deleted_cloud))
            if parts:
                self.notify.emit(t("Photos synced"), ", ".join(parts))
        if first and res.downloaded and not res.errors:
            self.notify.emit(t("Your photos are on this PC"),
                             t("The first download is done: {n} files. From now on only changes are synced.",
                               n=res.downloaded))
        if res.errors:
            problems.append(t("Finished with {n} errors. Will retry.", n=res.errors))
        return True

    def _refresh_storage(self, force: bool = False) -> None:
        """iCloud storage use for the panel; one small request, at most every 30 minutes."""
        if self.api is None or (not force and time.monotonic() - self._storage_at < 30 * 60 and self._storage):
            return
        try:
            self._storage = extras.storage(self.api)
            self._storage_at = time.monotonic()
        except Exception as exc:  # noqa: BLE001 - purely informational
            log.info("Storage info unavailable: %s", describe(exc))

    def _run_notes(self, full: bool, problems: list[str]) -> bool:
        assert self.notes is not None
        try:
            self.notes.run(full=full)
        except Exception as exc:  # noqa: BLE001
            return self._handle(exc, problems, t("Notes"))
        return True

    def _run_drive(self, problems: list[str], changed: set[str] | None = None) -> bool:
        assert self.drive is not None and self.state is not None
        first = not self.state.get("drive_last_sync")
        used_api = getattr(getattr(self.drive, "remote", None), "_api", None)  # the session this pass runs with
        covered: set[str] = set()
        if changed is None:  # a full pass covers what is waiting now; changes made while it runs still count
            with self._drive_lock:
                covered, self._drive_changes = self._drive_changes, set()
        try:
            res = self.drive.run(changed=changed)
        except Exception as exc:  # noqa: BLE001
            if changed or covered:
                with self._drive_lock:
                    self._drive_retry |= (changed or set()) | covered  # tried again with the next change or full read
            self._handle(exc, problems, t("iCloud Drive"), "drive", used_api)
            return False  # never straight into another attempt
        if res.full:
            self._drive_last = time.monotonic()
            with self._drive_lock:
                self._drive_retry.clear()  # (changes saved during the pass stay queued for a quick one)
        if res.followup and changed:  # a folder was just created: send its contents next
            with self._drive_lock:
                self._drive_changes |= changed
            self._driveChange.emit()  # timers live on the worker thread
        self.state.set("drive_last_sync", now_iso())
        if res.full or res.pending_to_cloud or res.pending_to_pc:  # a quick pass sees only a few folders
            self._drivePendingSeen.emit(list(res.pending_to_cloud), list(res.pending_to_pc))
        if self.settings.notifications:
            parts = []
            if res.downloaded:
                parts.append(t("Downloaded: {n}", n=res.downloaded))
            if res.uploaded:
                parts.append(t("Uploaded: {n}", n=res.uploaded))
            if res.deleted_local:
                parts.append(t("Deleted on PC: {n}", n=res.deleted_local))
            if res.deleted_cloud:
                parts.append(t("Deleted in iCloud: {n}", n=res.deleted_cloud))
            if parts:
                self.notify.emit(t("iCloud Drive synced"), ", ".join(parts))
        if first and res.full:
            self.notify.emit(t("iCloud Drive is ready"),
                             t("The first sync is done: {n} files downloaded. From now on only changes are synced.",
                               n=res.downloaded))
        if res.errors:
            problems.append(t("iCloud Drive: finished with {n} errors. Will retry.", n=res.errors))
        return True

    @Slot(object, object)
    def _on_drive_pending(self, to_cloud: list[str], to_pc: list[str]) -> None:
        pend = None
        if to_cloud or to_pc:
            pend = Pending(to_cloud=list(to_cloud), to_pc=list(to_pc), names={p: p for p in [*to_cloud, *to_pc]})
            pend.service = "drive"
            if self.drive_state is not None:  # a folder counts with all the files inside it
                rows = self.drive_state.all()

                def files(tops: list[str]) -> int:
                    top = set(tops)

                    def under(p: str) -> bool:
                        while True:
                            if p in top:
                                return True
                            if "/" not in p:
                                return False
                            p = p.rsplit("/", 1)[0]

                    return sum(1 for p, b in rows.items() if not b.is_dir and under(p)) or len(tops)

                pend.files_to_pc, pend.files_to_cloud = files(pend.to_pc), files(pend.to_cloud)
        if pend != self._drive_pending:
            self._drive_pending = pend
            if pend:
                n = len(pend.to_cloud) + len(pend.to_pc)
                self.notify.emit(t("Confirmation needed"), t("{n} items are waiting for your OK to be deleted.", n=n))
            self.pendingChanged.emit(self._pending or self._drive_pending)

    @Slot(str, list)
    def resolve_pending(self, decision: str, ids: list) -> None:
        p = self._pending
        if p is not None and set(ids) <= set(p.to_cloud) | set(p.to_pc) and self.syncer:
            if decision == "approve":
                self.syncer.approve(ids)
            else:
                if set(ids) & set(p.to_cloud):
                    self.syncer.reject_to_cloud([i for i in ids if i in p.to_cloud])
                if set(ids) & set(p.to_pc):
                    self.syncer.reject_to_pc([i for i in ids if i in p.to_pc])
                self.syncer.held -= set(ids)
            self._pending = None
            self.pendingChanged.emit(self._drive_pending)
            self.full_check()  # restored items didn't change in iCloud, so only a full read finds them
            return
        d = self._drive_pending
        if d is not None and self.drive and self.drive_state:
            if decision == "approve":
                self.drive.approved.update(ids)
            else:
                # Forget the base entries: the surviving copy is then copied back to the other side.
                self.drive.discard_pending(ids)
                for path in ids:
                    self.drive_state.drop(path)
            self._drive_pending = None
            self.pendingChanged.emit(self._pending)
            self._kick_drive(full=True)

    @Slot(list)
    def trash_drive_paths(self, paths: list) -> None:
        """Move PC copies of Drive folders the user stopped syncing to the Recycle Bin."""
        if not self.drive:
            return
        for rel in paths:
            target = self.drive.root / rel
            try:
                if target.exists():
                    self.drive._trash_local(target)
                    self.log("trash", t("Drive: moved to the Recycle Bin (no longer synced): {name}", name=rel))
            except OSError as exc:
                self.log("error", t("Couldn't delete {name}: {error}", name=rel, error=brief(exc)))

    @Slot()
    def clear_activity(self) -> None:
        if self.state:
            self.state.clear_activity()

    def restore_excluded(self) -> None:
        if self.state:
            self.state.clear_exclusions()
            self.log("info", t("Photos you deleted only on this PC will be downloaded again."))
        self.full_check()


def fmt_time(ts: str | None) -> str:
    if not ts:
        return t("never")
    try:
        dt = datetime.fromisoformat(ts).astimezone()
    except ValueError:
        return ts
    delta = datetime.now().astimezone() - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return t("just now")
    if secs < 3600:
        return t("{n} min ago", n=secs // 60)
    from PySide6.QtCore import QDateTime, QLocale, QTime

    loc = QLocale.system()  # dates and times as set in Windows' region settings
    if dt.date() == datetime.now().date():
        return t("today at {time}", time=loc.toString(QTime(dt.hour, dt.minute), QLocale.ShortFormat))
    qdt = QDateTime.fromSecsSinceEpoch(int(dt.timestamp()))
    return loc.toString(qdt, QLocale.ShortFormat)
