"""Portable paths and settings.

Settings live in a `data` folder next to the app (or in %LOCALAPPDATA%\\Skyfold\\data when the app
folder can't be written, e.g. under Program Files). The Apple session, sync database and log always
live in %LOCALAPPDATA%\\Skyfold, so they never leave this PC.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .i18n import set_language, t

APP_NAME = "Skyfold"
APP_ID = "Skyfold"
VERSION = "1.0.6"


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
    try:
        here.mkdir(parents=True, exist_ok=True)
        probe = here / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
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


PRIVATE_FILES = ("session", "state.sqlite3", "state.sqlite3-wal", "state.sqlite3-shm")


def _move_private(src_dir: Path) -> None:
    if src_dir == PRIVATE_DIR or not src_dir.exists():
        return
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    for name in PRIVATE_FILES:
        src, dst = src_dir / name, PRIVATE_DIR / name
        if src.exists() and not dst.exists():
            shutil.move(str(src), str(dst))
        # If both exist, the local copy wins and the other one is simply left alone: never
        # delete a session or database automatically.


def migrate_private_files() -> None:
    """Keep the Apple session and sync database where they belong.

    - app moved into a synced folder: pull them out of data/ to this PC only;
    - upgrade from the app's earlier name (iSync): take over its session and database,
      so the user doesn't have to sign in or download everything again.
    """
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    if not (PRIVATE_DIR / "state.sqlite3").exists():
        _move_private(local / "iSync")
    _move_private(DATA_DIR)
    legacy_settings = APP_DIR.parent / "iSync" / "data" / "settings.json"
    if not SETTINGS_FILE.exists() and legacy_settings.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_settings, SETTINGS_FILE)


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
    full_check_hours: int = 24  # safety-net full read; everyday syncs only read changes
    folder_structure: str = "flat"
    language: str = "en"
    theme: str = "system"  # system | light | dark
    drive_enabled: bool = False
    drive_dir: str = field(default_factory=lambda: str(Path.home() / "iCloud Drive"))
    drive_delete_cloud_to_pc: bool = True
    drive_delete_pc_to_cloud: bool = True
    drive_folders: list[str] | None = None  # chosen top folders to sync; None = all of iCloud Drive
    notes_enabled: bool = False
    notes_dir: str = field(default_factory=lambda: str(Path.home() / "Documents" / "Apple Notes"))
    include_videos: bool = True
    include_live_video: bool = True
    albums_as_folders: bool = True
    delete_pc_to_cloud: bool = True
    delete_cloud_to_pc: bool = True
    delete_threshold: int = 25
    instant_local_deletes: bool = True
    notifications: bool = True
    start_with_windows: bool = False
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
        if "poll_minutes" in clean:  # hand-edited values: keep timers in a sane range
            clean["poll_minutes"] = min(max(1, clean["poll_minutes"]), 24 * 60)
        if "delete_threshold" in clean:
            clean["delete_threshold"] = max(1, clean["delete_threshold"])
        return cls(**clean)  # anything of the wrong type falls back to its default

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)

    def copy(self) -> "Settings":
        return Settings(**asdict(self))


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_start_with_windows(enabled: bool) -> None:
    """Toggle the per-user autostart entry (HKCU Run). Only on Windows, only when frozen."""
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
