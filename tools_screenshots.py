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
for f in ("SegUIVar.ttf", "segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "seguisym.ttf", "seguiemj.ttf"):
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
# The offscreen "screen" is small (800x600): windows are shown at the size they take on a normal
# screen, where Settings opens without scrolling.
dialogs._fit_screen = lambda w, h: (w, h)
OUT.mkdir(parents=True, exist_ok=True)
NOW = datetime.now(timezone.utc)
GB = 1000 ** 3
GIB = 1024 ** 3  # iCloud storage is counted in these (a "200 GB" plan is 200 GiB)
HOME = r"C:\Users\you"
EMAIL = "someone@example.com"

STATS = {"photos": 1284, "videos": 36, "bytes": int(9.6 * GB), "last_sync": NOW.isoformat(),
         "drive_enabled": True, "drive_files": 4210, "drive_bytes": 31 * GB, "drive_last_sync": NOW.isoformat(),
         "notes_enabled": True, "notes_count": 86, "notes_last_sync": NOW.isoformat(),
         "storage": {"used": 74 * GIB, "total": 200 * GIB,
                     "parts": [("Photos", 38 * GIB, "photos"), ("Docs", 29 * GIB, "docs"), ("Backups", 7 * GIB, "backup")]}}


def settings() -> Settings:
    return Settings(apple_id=EMAIL, photos_dir=HOME + r"\Pictures\iCloud Photos", photos_dir_confirmed=True,
                    folder_structure="year_month", drive_enabled=True, drive_dir=HOME + r"\iCloud Drive",
                    drive_folders=["Documents", "Projects", "Scans"], notes_enabled=True,
                    notes_dir=HOME + r"\Documents\Apple Notes", start_with_windows=True,
                    bookmarks_enabled=True, bookmarks_dir=HOME + r"\Documents")


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


def notes_window(mode: str, name: str) -> None:
    """The Notes window over a small made-up library (written to a temporary folder)."""
    import tempfile
    import time

    from skyfold.notes_engine import NotesState, polish_note_html
    from skyfold.ui.notes_window import NotesWindow

    c = use(mode)
    tmp = Path(tempfile.mkdtemp(prefix="skyfold-shot-"))
    root = tmp / "Notes"
    garden = """<div class="note-content"><h1>Garden plan</h1>
<p>Spring jobs for the back garden, before the end of April.</p><h2>To do</h2>
<ul><li><input type="checkbox" disabled checked> Prune the apple tree</li>
<li><input type="checkbox" disabled checked> Buy tomato seedlings</li>
<li><input type="checkbox" disabled> Fix the fence by the shed</li><li><input type="checkbox" disabled> Plant beans</li></ul>
<h2>Budget</h2><table><tr><th>Item</th><th>Price</th></tr><tr><td>Seedlings</td><td>12 €</td></tr>
<tr><td>Fence boards</td><td>48 €</td></tr><tr><td>Compost</td><td>9 €</td></tr></table>
<p><span style="background-color:var(--hl3-bg)">Ask the neighbour about the ladder.</span></p>
<h2>Notes</h2><ol><li>Water in the evening, not at noon.</li>
<li>Beans go next to the fence, tomatoes by the wall.</li></ol>
<p><b>Next weekend:</b> plant the beans if it stays dry.</p></div>"""
    st = NotesState(tmp / "notes.db")
    ago = lambda d, h=0: (NOW - timedelta(days=d, hours=h)).strftime("%Y-%m-%dT%H:%M:%S")  # noqa: E731
    rows = [("g", "Home/Garden plan.html", "Garden plan", "Home", ago(0, 2), False, garden),
            ("r", "Home/Recipes/Pancakes.html", "Pancakes", "Home/Recipes", ago(1), False,
             "Flour, two eggs, milk and a pinch of salt."),
            ("s", "Home/Shopping.html", "Shopping list", "Home", ago(2), False, "Milk, bread, coffee, apples."),
            ("m", "Work/Meeting notes.html", "Meeting notes", "Work", ago(3), False,
             "Monday: plan the spring catalogue and the new price list."),
            ("i", "Work/Ideas/Website.html", "Ideas for the website", "Work/Ideas", ago(6), False,
             "A gallery page and a short video."),
            ("t", "Travel/Packing list.html", "Packing list", "Travel", ago(9), False,
             "Passport, charger, sunglasses, a good book."),
            ("b", None, "Bank details", "Notes", ago(12), True, ""),
            ("q", "Notes/Quotes.html", "Quotes", "Notes", ago(20), False, "Small steps every day.")]
    for nid, rel, title, folder, when, locked, text in rows:
        st.put(nid, rel, title, folder, when, locked)
        if rel:
            f = root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            body = text if text.startswith("<") else f'<div class="note-content"><h1>{title}</h1><p>{text}</p></div>'
            up = "../" * rel.count("/")
            f.write_text(polish_note_html(body.replace("../_assets", up + "_assets"), up + "Notes.html",
                                          "All notes", nid), encoding="utf-8")
    w = NotesWindow(c, lambda: (st, root))
    w.resize(1180, 720)
    w.show()
    end = time.time() + 5
    while w.indexing and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    for it in w._folder_items():
        if it.data(0, Qt.UserRole) == "Home":
            w.folders.setCurrentItem(it)
    w.notes.setCurrentRow(0)
    w.split.setSizes([210, 280, 690])
    for _ in range(10):
        app.processEvents()
    w.current_key = None
    w._show_current()
    for _ in range(10):
        app.processEvents()
    save(w, name)
    w.clear()


flyout("light")
flyout("dark")
notes_window("dark", "notes-window.png")
first_run()
settings_page(0, "settings-general.png")
settings_page(2, "settings-drive.png")
settings_page(4, "settings-bookmarks.png")
drive_folders()
delete_confirm()
find_my()
