"""Render the README screenshots from the real UI, offscreen, with made-up demo data only.

    python tools_screenshots.py [out_dir]     (default: docs/screenshots)

No sign-in, no network, nothing of the person running it: every name, path and number is invented.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication, QTreeWidgetItem  # noqa: E402

app = QApplication([])
for f in ("SegUIVar.ttf", "segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
    p = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / f
    if p.exists():
        QFontDatabase.addApplicationFont(str(p))

from skyfold import i18n  # noqa: E402
from skyfold.config import Settings  # noqa: E402
from skyfold.drive_remote import DriveItem  # noqa: E402
from skyfold.extras import Device  # noqa: E402
from skyfold.ui import dialogs, theme  # noqa: E402
from skyfold.ui.flyout import Flyout  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
NOW = datetime.now(timezone.utc)
GB = 1000 ** 3
HOME = r"C:\Users\you"
EMAIL = "someone@example.com"

STATS = {"photos": 1284, "videos": 36, "bytes": int(9.6 * GB), "last_sync": NOW.isoformat(),
         "drive_enabled": True, "drive_files": 4210, "drive_bytes": 31 * GB, "drive_last_sync": NOW.isoformat(),
         "notes_enabled": True, "notes_count": 86, "notes_last_sync": NOW.isoformat(),
         "storage": {"used": 74 * GB, "total": 200 * GB,
                     "parts": [("Photos", 38 * GB, "photos"), ("Docs", 29 * GB, "docs"), ("Backups", 7 * GB, "backup")]}}


def settings() -> Settings:
    return Settings(apple_id=EMAIL, photos_dir=HOME + r"\Pictures\iCloud Photos", photos_dir_confirmed=True,
                    folder_structure="year_month", drive_enabled=True, drive_dir=HOME + r"\iCloud Drive",
                    drive_folders=["Documents", "Projects", "Scans"], notes_enabled=True,
                    notes_dir=HOME + r"\Documents\Apple Notes", start_with_windows=True)


def use(mode: str) -> dict:
    i18n.set_language("en")
    c = theme.palette(mode)
    app.setPalette(theme.qpalette(c))
    app.setStyleSheet(theme.stylesheet(c))
    dialogs.refresh_palette(c) if hasattr(dialogs, "refresh_palette") else None
    return c


def save(widget, name: str) -> None:
    widget.show()
    for _ in range(3):
        app.processEvents()
    scale = float(os.environ.get("SKYFOLD_SHOT_SCALE", "1"))  # e.g. 2: sharp images for posts
    if scale == 1:
        widget.grab().save(str(OUT / name))
    else:
        from PySide6.QtGui import QImage

        img = QImage(int(widget.width() * scale), int(widget.height() * scale), QImage.Format_ARGB32)
        img.setDevicePixelRatio(scale)
        img.fill(0)
        widget.render(img)
        img.save(str(OUT / name))
    print(name, widget.width(), widget.height())
    widget.hide()
    widget.deleteLater()
    app.processEvents()


def flyout(mode: str) -> None:
    c = use(mode)
    f = Flyout(c)
    f.set_stats(STATS)
    f.set_account(EMAIL)
    for kind, msg, mins in reversed([("download", "Drive: downloaded: Projects/Budget 2026.xlsx", 0),
                                     ("upload", "Drive: uploaded: Documents/Letter.docx", 4),
                                     ("trash", "Deleted on this PC (Recycle Bin): 2026/11/IMG_2048.HEIC", 52)]):
        f.add_activity((NOW - timedelta(minutes=mins)).isoformat(), kind, msg, "C:/x")
    f.set_state("idle", "")
    f._anchor = None
    f.show()
    app.processEvents()
    f._size_list()
    save(f, f"flyout-{mode}.png")


def settings_page(row: int, name: str) -> None:
    c = use("dark")
    d = dialogs.SettingsDialog(settings(), c, storage=STATS["storage"])
    d.nav.setCurrentRow(row)
    save(d, name)


def first_run() -> None:
    c = use("dark")
    save(dialogs.FolderSetupDialog(HOME + r"\Pictures\iCloud Photos", c), "first-run.png")


def drive_folders() -> None:
    c = use("dark")
    sizes = {"Documents": (812, int(2.4 * GB)), "Projects": (1460, int(8.1 * GB)), "Scans": (233, int(0.9 * GB)),
             "Archive": (1705, int(19.6 * GB)), "Shared": (64, int(0.2 * GB))}
    d = dialogs.DriveFoldersDialog(["Documents", "Projects", "Scans"], c, lambda: None, sizes)
    d.tree.clear()
    d.add_children(None, [DriveItem(p, "d" + p, "d" + p, "z", "e", True) for p in sorted(sizes)])
    d.info.setText(i18n.t("3 of 5 folders chosen"))
    d.ok.setEnabled(True)
    save(d, "drive-folders.png")


def delete_confirm() -> None:
    c = use("dark")
    names = ["2026/11/IMG_2041.HEIC", "2026/11/IMG_2042.HEIC", "2026/11/IMG_2043.MOV", "2026/11/IMG_2044.HEIC",
             "2026/11/IMG_2045.HEIC", "2026/11/IMG_2046.HEIC", "2026/11/IMG_2047.HEIC"]
    pending = SimpleNamespace(to_pc=[f"A{i}" for i in range(len(names))], to_cloud=[],
                              names={f"A{i}": n for i, n in enumerate(names)}, service="photos", gen=1)
    save(dialogs.PendingDialog(pending, c), "delete-confirm.png")


def find_my() -> None:
    c = use("dark")
    d = dialogs.FindMyDialog(c, lambda: None)
    ago = lambda m: (NOW - timedelta(minutes=m)).isoformat()  # noqa: E731
    devs = [Device("1", "iPhone", "iPhone 16", 0.82, False, True, 48.858, 2.294, ago(3), True),
            Device("2", "iPad", "iPad Air", 0.46, True, True, 48.858, 2.294, ago(18), True),
            Device("3", "MacBook Air", "MacBook Air", None, False, False, None, None, None, False)]
    d.signin_btn.hide()
    for i, dev in enumerate(devs):
        if i:
            sep = dialogs.QFrame()
            sep.setObjectName("Sep")
            d.rows.addWidget(sep)
        d.rows.addWidget(dialogs.DeviceRow(dev, c, lambda: None))
    d.card.show()
    d._devices_scroll.show()
    d.info.hide()
    save(d, "find-my.png")


flyout("light")
flyout("dark")
first_run()
settings_page(0, "settings-general.png")
settings_page(2, "settings-drive.png")
settings_page(3, "settings-notes.png")
drive_folders()
delete_confirm()
find_my()
