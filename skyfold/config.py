"""Portable paths and settings.

Settings live in a `data` folder next to the app (or in %LOCALAPPDATA%\\Skyfold\\data when the app
folder can't be written, e.g. under Program Files). The Apple session, sync database and log always
live in %LOCALAPPDATA%\\Skyfold, so they never leave this PC.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .i18n import set_language, t

APP_NAME = "Skyfold"
APP_ID = "Skyfold"
VERSION = "1.1.2"


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def cloud_service(p: Path) -> str | None:
    """Name of the cloud service that syncs path p, or None for a local folder."""
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(var)
        if root and _within(p, Path(root)):
            return "OneDrive"
    s = str(p).lower()
    for marker, name in (("\\iclouddrive\\", "iCloud Drive"), ("\\icloud drive\\", "iCloud Drive"),
                         ("\\onedrive", "OneDrive"), ("\\dropbox\\", "Dropbox"), ("\\google drive\\", "Google Drive")):
        if marker in s + "\\":
            return name
    return None


APP_DIR = app_dir()
APP_IN_CLOUD = cloud_service(APP_DIR)
# The Apple session, sync database and log ALWAYS live in this user's local app-data folder.
# (Choosing the place from what the program's path looks like broke after a reboot: when Skyfold
# started before iCloud Drive, its path didn't look like a cloud folder, so it looked for its
# session somewhere else and appeared signed out with an empty database.)
PRIVATE_DIR = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / APP_ID
SESSION_DIR = PRIVATE_DIR / "session"
STATE_DB = PRIVATE_DIR / "state.sqlite3"
LOG_FILE = PRIVATE_DIR / "skyfold.log"


def _data_dir() -> Path:
    """Settings next to the app (portable), unless that folder can't be written (Program Files…)."""
    here = APP_DIR / "data"
    # pytest and its child interpreters must not probe or read a developer's portable data.
    test_data = os.environ.get("_SKYFOLD_TEST_DATA_DIR")
    if test_data and not getattr(sys, "frozen", False):
        here = Path(test_data)
    try:
        here.mkdir(parents=True, exist_ok=True)
        import tempfile

        fd, probe = tempfile.mkstemp(prefix=".write-test-", dir=here)  # a new file of its own, never an existing one
        os.close(fd)
        os.unlink(probe)
        return here
    except OSError:
        return PRIVATE_DIR / "data"


DATA_DIR = _data_dir()
SETTINGS_FILE = DATA_DIR / "settings.json"


def _load_language() -> None:
    """Pick the UI language before any module builds its labels (default: English)."""
    try:
        set_language(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get("language", "en"))
    except (OSError, ValueError, AttributeError, TypeError):
        set_language("en")


_load_language()


DATABASE = "state.sqlite3"
DATABASE_FILES = (DATABASE, DATABASE + "-wal", DATABASE + "-shm")  # one unit: never mixed between copies


def save_text(path: Path, text: str) -> None:
    """Write path through a private temp file next to it, then put it in place. Only path itself is
    replaced: no fixed scratch name that could already belong to someone else."""
    import tempfile

    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".new", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _missing(p: Path) -> bool:
    """Known not to exist. A file that couldn't be looked at is not missing."""
    try:
        os.lstat(p)
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return False
    return False


def _copy_new(src: Path, dst: Path) -> bool:
    """Copy src to dst only as a new file: an existing dst is never replaced (False then)."""
    import tempfile

    fd, tmp = tempfile.mkstemp(prefix=dst.name + ".", suffix=".new", dir=dst.parent)
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            shutil.copyfileobj(inp, out)
        shutil.copystat(src, tmp)
        if os.name == "nt":
            os.rename(tmp, dst)  # refuses an existing name
        else:
            os.link(tmp, dst)  # refuses an existing name
        return True
    except FileExistsError:
        return False
    finally:
        try:
            os.unlink(tmp)  # the private copy (already gone when it was renamed into place)
        except FileNotFoundError:
            pass


def _move_new(src: Path, dst: Path) -> None:
    """Move src to dst only as a new file; the source is removed only once the copy is in place."""
    if os.name == "nt":
        try:
            os.rename(src, dst)  # same drive: refuses an existing name
            return
        except FileExistsError:
            return
        except OSError:
            pass  # another drive: copied below
    if _copy_new(src, dst):
        src.unlink()


def place_new(tmp: str | Path, dst: Path) -> bool:
    """Put a private temp file or folder in place under dst only as a new name (False if taken)."""
    try:
        if os.name == "nt" or Path(tmp).is_dir():
            os.rename(tmp, dst)  # Windows refuses an existing name
        else:
            os.link(tmp, dst)  # refuses an existing name
            os.unlink(tmp)
        return True
    except FileExistsError:
        return False


def _move_dir_new(src: Path, dst: Path) -> None:
    """Move a folder to dst only as a new name; the source goes only once the copy is in place."""
    if os.name == "nt":
        try:
            os.rename(src, dst)  # same drive: refuses an existing name
            return
        except FileExistsError:
            return
        except OSError:
            pass  # another drive: copied below
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix=dst.name + ".", suffix=".new", dir=dst.parent))
    try:
        shutil.copytree(src, tmp, dirs_exist_ok=True)
        if not place_new(tmp, dst):
            return
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # only the private copy (gone when it was put in place)
    shutil.rmtree(src)


MIGRATION_PROBLEMS: list[str] = []  # shown to the user once the app has started
JOURNAL_PROBLEMS: list[str] = []  # a journal that couldn't be kept safe: nothing may open the database
BACKUP_SECONDS = 30.0  # an earlier database kept busy by another program longer than this: not taken over


def _copy_database(src_dir: Path) -> None:
    """Take over a database together with its journal, as one consistent snapshot. Only when this PC
    has no database at all (none of its files): a journal is never put next to another database.
    The earlier copy is left exactly as it is: nothing proves no other program wrote to it after the
    snapshot, so it is never deleted."""
    import sqlite3
    import tempfile
    import time
    from contextlib import closing

    deadline = time.monotonic() + BACKUP_SECONDS

    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError("the earlier database is kept busy by another program")

    # The earlier files are only ever read: copied as they are into a private folder, and the copy is
    # opened there (SQLite applies its journal - a WAL, or a crashed writer's rollback journal - to
    # the copy, never to the original). Nothing is created, changed or removed next to the original.
    snap = Path(tempfile.mkdtemp(prefix=DATABASE + ".", suffix=".snap", dir=PRIVATE_DIR))
    fd, tmp = tempfile.mkstemp(prefix=DATABASE + ".", suffix=".new", dir=PRIVATE_DIR)
    os.close(fd)
    try:
        def copy_files() -> None:
            for suffix in ("", "-wal", "-journal"):  # (a -shm is only an index: rebuilt from the WAL)
                src = src_dir / (DATABASE + suffix)
                if not _missing(src):
                    shutil.copyfile(src, snap / (DATABASE + suffix))

        with open(src_dir / DATABASE, "rb") as fh:
            header = fh.read(100)
        wal_mode = len(header) > 19 and header[18] == 2  # (the file format says WAL or rollback journal)
        has = {s: not _missing(src_dir / (DATABASE + s)) for s in ("-wal", "-shm", "-journal")}
        # A read lock keeps any writer from changing what is copied: in rollback mode it blocks writing;
        # in WAL mode it holds the WAL in place (a checkpoint can't move past it). Opened read-only and
        # only when every file it needs is already there, so it creates nothing next to the original.
        # Without such files no program has the database open: a plain copy is consistent.
        locked = not has["-journal"] and ((wal_mode and has["-wal"] and has["-shm"]) or not wal_mode)
        if locked:
            uri = (src_dir / DATABASE).resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=max(0.1, min(5.0, BACKUP_SECONDS)),
                                         isolation_level=None)) as lock:
                lock.execute("BEGIN")
                lock.execute("SELECT count(*) FROM sqlite_master").fetchone()
                copy_files()
                lock.execute("COMMIT")
        else:
            copy_files()  # (a WAL is checked by its own checksums; the copy is checked below)
        with closing(sqlite3.connect(snap / DATABASE, timeout=5)) as old, closing(sqlite3.connect(tmp)) as new:
            check = old.execute("PRAGMA quick_check").fetchone()
            if check != ("ok",):
                raise sqlite3.DatabaseError(f"the copy isn't consistent ({check[0] if check else '?'})")
            old.backup(new, progress=progress)  # the database as it is with its journal applied
        place_new(tmp, PRIVATE_DIR / DATABASE)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        shutil.rmtree(snap, ignore_errors=True)  # only the private copy


def _set_aside_orphan_journal() -> None:
    """A journal (-wal/-shm, or a rollback -journal) here without its database belongs to no database
    any more (left by a crash): it is renamed, never applied to a database made or taken over later,
    and never deleted. When the database itself can't be looked at while a journal is there, nothing
    is decided: Skyfold doesn't start (opening the database could overwrite the journal)."""
    import uuid

    names = (DATABASE + "-wal", DATABASE + "-shm", DATABASE + "-journal")
    try:
        os.lstat(PRIVATE_DIR / DATABASE)
        return  # the database is there: its journal belongs to it
    except (FileNotFoundError, NotADirectoryError):
        pass
    except OSError as exc:
        if any(not _missing(PRIVATE_DIR / n) for n in names):
            JOURNAL_PROBLEMS.append(f"{PRIVATE_DIR / DATABASE}: {exc}")
        return
    tag = uuid.uuid4().hex[:8]  # the same for all: they stay recognisable as one set
    for name in names:
        p = PRIVATE_DIR / name
        if _missing(p):
            continue
        aside = PRIVATE_DIR / f"{name}.orphan-{tag}"
        try:
            os.rename(p, aside)  # a new name: refuses to replace
            logging.getLogger("skyfold").warning("A database journal without its database was set aside: %s", name)
        except OSError as exc:
            try:  # can't be renamed now: at least its bytes are kept before a new database is made
                if not _copy_new(p, aside):
                    raise FileExistsError(aside)
                logging.getLogger("skyfold").warning("A database journal without its database was copied aside: %s", name)
                # ...but it is still here under its own name. A rollback journal would be replayed into
                # a database made or taken over now (a WAL from another database is ignored by SQLite):
                # Skyfold doesn't start until it can be set aside (tried again next start).
                if name.endswith("-journal"):
                    JOURNAL_PROBLEMS.append(f"{p}: {exc}")
            except OSError:
                # Neither: opening the database would overwrite it. Skyfold doesn't start (see app.main).
                JOURNAL_PROBLEMS.append(f"{p}: {exc}")


def _move_private(src_dir: Path) -> None:
    if src_dir == PRIVATE_DIR or _missing(src_dir):
        return
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("skyfold")
    src = src_dir / "session"
    if src.is_dir() and not src.is_symlink() and _missing(PRIVATE_DIR / "session"):
        try:
            _move_dir_new(src, PRIVATE_DIR / "session")
        except OSError as exc:  # left where it is: tried again next start
            log.info("Couldn't move the session: %s", exc)
    import stat as _stat

    try:
        has_db = _stat.S_ISREG(os.stat(src_dir / DATABASE).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        has_db = False
    except OSError as exc:  # can't be looked at (any reason): not taken over, and the user is told
        log.warning("Couldn't look at the database in %s: %s", src_dir, exc)
        MIGRATION_PROBLEMS.append(f"{src_dir / DATABASE}: {exc}")
        has_db = False
    if has_db and all(_missing(PRIVATE_DIR / n) for n in (*DATABASE_FILES, DATABASE + "-journal")):
        try:
            _copy_database(src_dir)
        except Exception as exc:  # noqa: BLE001 - left where it is, unchanged; the user is told
            log.warning("Couldn't take over the database from %s: %s", src_dir, exc)
            MIGRATION_PROBLEMS.append(f"{src_dir / DATABASE}: {exc}")
    # If both exist, the local copy wins and the other one is simply left alone: never
    # delete a session or database automatically.


def migrate_private_files() -> None:
    """Keep the Apple session and sync database where they belong.

    - app moved into a synced folder: pull them out of data/ to this PC only;
    - upgrade from the app's earlier name (iSync): take over its session and database,
      so the user doesn't have to sign in or download everything again.
    """
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    MIGRATION_PROBLEMS.clear()
    JOURNAL_PROBLEMS.clear()
    # Before anything can open (and so create) the database: a journal left here without it is set
    # aside, whether or not there is an earlier database to take over.
    _set_aside_orphan_journal()
    if JOURNAL_PROBLEMS:
        return  # nothing is taken over next to a journal that is still in place (Skyfold doesn't start)
    if _missing(PRIVATE_DIR / DATABASE):
        _move_private(local / "iSync")
    _move_private(DATA_DIR)
    legacy_settings = APP_DIR.parent / "iSync" / "data" / "settings.json"
    if _missing(SETTINGS_FILE) and legacy_settings.is_file():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            _copy_new(legacy_settings, SETTINGS_FILE)  # never over settings that exist
        except OSError as exc:
            logging.getLogger("skyfold").info("Couldn't take over the earlier settings: %s", exc)


def check_photos_dir(path: str) -> tuple[str | None, str | None]:
    """Return (error, warning) for a proposed gallery folder."""
    if not path.strip():
        return t("Choose a folder."), None
    p = Path(path).expanduser()
    if not p.is_absolute():
        return t("Enter the full folder path."), None
    if _within(p, APP_DIR) or _within(APP_DIR, p):
        return t("This folder can't contain Skyfold itself ({app}). Choose another folder, or move Skyfold "
                 "somewhere else first.", app=str(APP_DIR)), None
    if _within(p, PRIVATE_DIR):
        return t("Skyfold uses this folder internally. Choose another folder."), None
    drive = Path(p.anchor)
    if not drive.exists():
        return t("Drive {drive} is not available.", drive=p.anchor), None
    service = cloud_service(p)
    if service:
        return None, t("This folder is in {service}: photos would also be uploaded to that cloud, using space and "
                       "bandwidth. A normal folder such as Pictures is recommended.", service=service)
    return None, None

FOLDER_STRUCTURES = {
    "flat": "All in one folder",  # English keys; translated with t() where shown
    "year": "Year  (2025)",
    "year_month": "Year / Month  (2025/11)",
}


def default_photos_dir() -> str:
    return str(Path.home() / "Pictures" / "iCloud Photos")


@dataclass
class Settings:
    apple_id: str = ""
    photos_dir: str = field(default_factory=default_photos_dir)
    photos_dir_confirmed: bool = False  # set once the user picked the gallery folder
    poll_minutes: int = 2
    speed_limit_mb: int = 0  # download limit in MB/s; 0 = no limit
    upload_buffer_mb: int = 0  # send buffer per upload connection in MB; 0 = Windows decides (automatic)
    full_check_hours: int = 24  # safety-net full read; everyday syncs only read changes
    folder_structure: str = "flat"
    language: str = "en"
    theme: str = "system"  # system | light | dark | oled
    drive_enabled: bool = False
    drive_dir: str = field(default_factory=lambda: str(Path.home() / "iCloud Drive"))
    drive_delete_cloud_to_pc: bool = True
    drive_delete_pc_to_cloud: bool = True
    drive_folders: list[str] | None = None  # chosen top folders to sync; None = all of iCloud Drive
    notes_enabled: bool = False
    notes_dir: str = field(default_factory=lambda: str(Path.home() / "Documents" / "Apple Notes"))
    bookmarks_enabled: bool = False  # Safari bookmarks as a file Firefox/Chrome can import (read-only)
    bookmarks_dir: str = field(default_factory=lambda: str(Path.home() / "Documents"))
    include_videos: bool = True
    include_live_video: bool = True
    albums_as_folders: bool = True
    photos_upload: bool = False  # new photos/videos put in the photos folder go up to iCloud Photos
    photos_window: bool = True  # a click on Photos opens the Photos window; off: the photos folder in Explorer
    delete_pc_to_cloud: bool = True
    delete_cloud_to_pc: bool = True
    delete_threshold: int = 5  # more deletions than this at once are asked about first
    instant_local_deletes: bool = True
    notifications: bool = True
    notification_style: str = "skyfold"  # skyfold (its own cards) | windows; notifications=False: off
    start_with_windows: bool = False
    check_updates: bool = True  # ask GitHub once a day whether a newer Skyfold is out
    update_told: str = ""  # the newest version already announced (told once per version)
    paused: bool = False

    @classmethod
    def load(cls) -> "Settings":
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not SETTINGS_FILE.exists():
            return cls()
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        default = cls()
        clean: dict = {}
        for f in fields(cls):
            if f.name not in raw:
                continue
            v, d = raw[f.name], getattr(default, f.name)
            if f.name == "drive_folders":  # None (= everything) or a list of folder paths
                if v is None or (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                    clean[f.name] = v
            elif isinstance(d, bool):
                if isinstance(v, bool):
                    clean[f.name] = v
            elif isinstance(d, int):
                if isinstance(v, int) and not isinstance(v, bool):
                    clean[f.name] = v
            elif isinstance(d, str):
                if isinstance(v, str):
                    clean[f.name] = v
        if clean.get("folder_structure") not in FOLDER_STRUCTURES:
            clean.pop("folder_structure", None)
        if clean.get("notification_style") not in ("skyfold", "windows"):
            clean.pop("notification_style", None)
        if "poll_minutes" in clean:  # hand-edited values: keep timers in a sane range
            clean["poll_minutes"] = min(max(1, clean["poll_minutes"]), 24 * 60)
        if "delete_threshold" in clean:
            clean["delete_threshold"] = max(1, clean["delete_threshold"])
        if "upload_buffer_mb" in clean:
            clean["upload_buffer_mb"] = min(max(0, clean["upload_buffer_mb"]), 64)
        return cls(**clean)  # anything of the wrong type falls back to its default

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        save_text(SETTINGS_FILE, json.dumps(asdict(self), indent=2, ensure_ascii=False))

    def copy(self) -> "Settings":
        return Settings(**asdict(self))


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _start_entry() -> tuple[str | None, str | None]:
    """(what Windows' autostart entry says now, what it would say for this exe)."""
    import winreg

    exe = sys.executable if getattr(sys, "frozen", False) else None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            current = winreg.QueryValueEx(key, APP_ID)[0]
    except OSError:
        current = None
    return current, (f'"{exe}" --tray' if exe else None)


def starts_with_windows(setting: bool) -> bool:
    """Whether Windows really starts this copy of Skyfold. Skyfold never rewrites the entry by itself
    (a program that keeps re-adding itself to Run looks like malware to antivirus programs), so an
    entry that points at an older copy or was removed shows as off, and switching it on in Settings
    writes it again. Outside the packaged app only the setting counts."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return setting
    current, wanted = _start_entry()
    return setting and current == wanted


def set_start_with_windows(enabled: bool) -> None:
    """Toggle the per-user autostart entry (HKCU Run). Only on Windows, only when frozen, and only
    when the user changes it in Settings: never on its own at start."""
    if sys.platform != "win32":
        return
    import winreg

    exe = sys.executable if getattr(sys, "frozen", False) else None
    wanted = f'"{exe}" --tray' if enabled and exe else None
    try:  # written only when it changes (e.g. the app was moved), not on every start
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            current = winreg.QueryValueEx(key, APP_ID)[0]
    except OSError:
        current = None
    if current == wanted:
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if wanted:
            winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, wanted)
        else:
            try:
                winreg.DeleteValue(key, APP_ID)
            except FileNotFoundError:
                pass
