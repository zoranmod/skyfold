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

from skyfold.ui import backdrop  # noqa: E402

# Windows 11's see-through look: every window is drawn see-through (as on a real screen with
# transparency effects on) and laid over a painted, blurred wallpaper of our own, with the title bar
# and shadow Windows would draw. SKYFOLD_SHOT_GLASS=0 gives the plain, solid windows.
GLASS = os.environ.get("SKYFOLD_SHOT_GLASS", "1") == "1"
if GLASS:
    backdrop.glass_supported = lambda: True
    backdrop.apply_backdrop = lambda *a, **k: True
    backdrop.blur_behind = lambda *a, **k: True

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


def _wallpaper(w: int, h: int, light: bool):
    """An original painted landscape (sky, a sun, hills, a lake), blurred as Windows' acrylic blurs
    what lies behind a window."""
    from PySide6.QtCore import QPointF, Qt as _Qt
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPolygonF

    img = QImage(w, h, QImage.Format_ARGB32)
    p = QPainter(img)
    g = QLinearGradient(0, 0, 0, h)
    for at, col in ((0, "#9bbfc0" if light else "#5f8682"), (0.5, "#ffe2ad" if light else "#d9b27f"),
                    (0.52, "#91b3a9" if light else "#4f726c"), (1, "#587a78" if light else "#22464b")):
        g.setColorAt(at, QColor(col))
    p.fillRect(img.rect(), g)
    p.setPen(_Qt.NoPen)
    p.setBrush(QColor("#f8efd7"))
    p.drawEllipse(QPointF(w * 0.22, h * 0.2), h * 0.07, h * 0.07)
    p.setBrush(QColor("#416b6b"))
    p.drawPolygon(QPolygonF([QPointF(0, h * 0.52), QPointF(w * 0.18, h * 0.32), QPointF(w * 0.4, h * 0.47),
                             QPointF(w * 0.63, h * 0.27), QPointF(w * 0.86, h * 0.45), QPointF(w, h * 0.36),
                             QPointF(w, h * 0.53), QPointF(0, h * 0.53)]))
    p.setBrush(QColor("#22464b"))
    for i in range(40):
        x = i * w / 40
        p.drawPolygon(QPolygonF([QPointF(x, h * 0.6), QPointF(x + w / 80, h * 0.5 - (i % 5) * h * 0.012),
                                 QPointF(x + w / 40, h * 0.6)]))
    p.end()
    return _blur(img, 18)


def _blur(img, factor: int):
    from PySide6.QtCore import Qt as _Qt

    small = img.scaled(max(1, img.width() // factor), max(1, img.height() // factor), _Qt.IgnoreAspectRatio,
                       _Qt.SmoothTransformation)
    return small.scaled(img.width(), img.height(), _Qt.IgnoreAspectRatio, _Qt.SmoothTransformation)


def _on_glass(shot, title: str | None, light: bool, oled: bool = False, veil=None):
    """The window as Windows 11 shows it: on acrylic over the wallpaper, rounded, with a shadow and
    (for a framed window) its title bar."""
    from PySide6.QtCore import QRectF, Qt as _Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath

    m, bar = 44, (32 if title else 0)
    w, h = shot.width(), shot.height() + bar
    out = QImage(w + 2 * m, h + 2 * m, QImage.Format_ARGB32)
    wall = _wallpaper(out.width(), out.height(), light)
    sharp = wall.copy()
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.drawImage(0, 0, _blur(sharp, 2))  # the desktop around the window, barely soft
    # the shadow Windows draws around a window
    shade = QImage(out.size(), QImage.Format_ARGB32)
    shade.fill(0)
    sp = QPainter(shade)
    sp.setRenderHint(QPainter.Antialiasing)
    sp.setPen(_Qt.NoPen)
    sp.setBrush(QColor(0, 0, 0, 120))
    sp.drawRoundedRect(QRectF(m, m + 8, w, h), 10, 10)
    sp.end()
    p.drawImage(0, 0, _blur(shade, 14))
    frame = QPainterPath()
    frame.addRoundedRect(QRectF(m, m, w, h), 8, 8)
    p.setClipPath(frame)
    p.drawImage(0, 0, wall)  # acrylic: the blurred desktop ...
    tint = QColor(0, 0, 0, 120) if oled else QColor(32, 32, 32, 90) if not light else QColor(243, 243, 243, 110)
    p.fillRect(QRectF(m, m, w, h), tint)  # ... with Windows' own tint
    if title:
        p.fillRect(QRectF(m, m, w, bar), backdrop.rgba(backdrop.glass_colours(
            theme.palette("oled" if oled else "light" if light else "dark"), not light)["tint"]))
        from skyfold.ui.icons import app_icon

        app_icon().paint(p, int(m + 12), int(m + 8), 16, 16)
        f = QFont("Segoe UI Variable Text")
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor("#1a1c21") if light else QColor(243, 245, 248, 220))
        p.drawText(QRectF(m + 38, m, w - 200, bar), _Qt.AlignVCenter | _Qt.AlignLeft, title)
        for i, ch in enumerate(("—", "☐", "✕")):
            p.drawText(QRectF(m + w - 46 * (3 - i), m, 46, bar), _Qt.AlignCenter, ch)
    if veil is not None:  # (a frameless window gets its tint from Windows' blur, not from its own paint)
        p.fillRect(QRectF(m, m, w, h), veil)
    p.drawImage(m, m + bar, shot)
    p.setClipping(False)
    p.setPen(QColor(0, 0, 0, 40) if light else QColor(255, 255, 255, 30))
    p.setBrush(_Qt.NoBrush)
    p.drawRoundedRect(QRectF(m + 0.5, m + 0.5, w - 1, h - 1), 8, 8)
    p.end()
    return out


def save(widget, name: str) -> None:
    widget.show()
    for _ in range(3):
        app.processEvents()
    focus = app.focusWidget()
    if focus is not None:  # (no keyboard focus ring in a picture)
        focus.clearFocus()
        app.processEvents()
    if GLASS and getattr(widget, "glass", False):
        from PySide6.QtGui import QImage

        scale = float(os.environ.get("SKYFOLD_SHOT_SCALE", "1"))
        shot = QImage(int(widget.width() * scale), int(widget.height() * scale), QImage.Format_ARGB32)
        shot.setDevicePixelRatio(scale)
        shot.fill(0)
        widget.render(shot)
        shot.setDevicePixelRatio(1)
        c = getattr(widget, "c", None) or {}
        from PySide6.QtGui import QColor

        light = QColor(c.get("bg", "#000000")).lightness() > 128
        oled = c.get("bg") == "#000000"
        framed = not bool(widget.windowFlags() & Qt.FramelessWindowHint)
        title = widget.windowTitle() if framed else None
        veil = None if framed else backdrop.rgba(backdrop.glass_colours(c, not light)["tint"])
        _on_glass(shot, title, light, oled, veil).save(str(OUT / name))
        print(name, widget.width(), widget.height(), "(on glass)")
        widget.hide()
        widget.deleteLater()
        app.processEvents()
        return
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
    d.resize(980, 700)  # (the design's window size; a long page scrolls, as on a real screen)
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
    import shutil
    import tempfile
    import time

    from PySide6.QtGui import QColor, QImage

    root = Path(tempfile.mkdtemp(prefix="skyfold-shot-"))
    art = sorted(Path(os.environ.get("SKYFOLD_SHOT_ART", str(root / "none"))).glob("IMG_*.png"))
    for i, n in enumerate(names):
        (root / n).parent.mkdir(parents=True, exist_ok=True)
        if art:
            shutil.copy(art[(i * 5) % len(art)], root / n)
        else:
            img = QImage(160, 160, QImage.Format_RGB32)
            img.fill(QColor.fromHsv((i * 41) % 360, 90, 200))
            img.save(str(root / n), "PNG")
    d = dialogs.PendingDialog(pending, c, root=root, limit=5)
    d.show()
    end = time.time() + 2
    while time.time() < end:  # (the small pictures arrive from a background thread)
        app.processEvents()
        time.sleep(0.02)
    save(d, "delete-confirm.png")


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


def photos_window(mode: str, name: str) -> None:
    """The Photos window over made-up paintings (SKYFOLD_SHOT_ART: a folder of IMG_*.png), or soft
    generated colour fields when there are none."""
    import shutil
    import tempfile
    import time

    from PySide6.QtGui import QColor, QImage

    from skyfold.state import FileRow, State
    from skyfold.ui.photos_window import PhotosWindow

    c = use(mode)
    tmp = Path(tempfile.mkdtemp(prefix="skyfold-shot-"))
    root = tmp / "Photos"
    art = Path(os.environ.get("SKYFOLD_SHOT_ART", str(tmp / "none")))
    pics = sorted(art.glob("IMG_*.png"))[:48]
    st = State(tmp / "photos.db")
    t0 = datetime(2026, 11, 28, 12, tzinfo=timezone.utc)
    for i in range(48):
        when = t0 - timedelta(days=i)
        rel = f"{when:%Y/%m}/IMG_{2001 + i}.png"
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        if pics:
            shutil.copy(pics[i % len(pics)], root / rel)
        else:
            img = QImage(400, 300, QImage.Format_RGB32)
            img.fill(QColor.fromHsv((i * 37) % 360, 90, 200))
            img.save(str(root / rel))
        st.add_file(FileRow(f"A{i}", "original", rel, 1000, False, when.isoformat()), when.isoformat())
    w = PhotosWindow(c, lambda: (st, root), lambda: SimpleNamespace(delete_pc_to_cloud=True))
    w.resize(1180, 720)
    w.show()
    end = time.time() + 6
    while time.time() < end and len(getattr(w.model, "items", [])) < 48:
        app.processEvents()
        time.sleep(0.02)
    end = time.time() + 2
    while time.time() < end:  # the thumbnails arrive from a background thread
        app.processEvents()
        time.sleep(0.02)
    save(w, name)


def update_window() -> None:
    from skyfold.updater import Release

    c = use("dark")
    notes = "\n".join([
        "## Polished, steadier, safer", "",
        "- The tray panel keeps one size; Stop is a clear red button.",
        "- Notifications in Windows 11's own layout.",
        "- Zoom you can see: − 100% + in Notes and Photos.", "",
        "## Smaller things", "",
        "- Signing in looks like the first-run steps.",
        "- Counts read naturally ("1 photo")."])
    rel = Release("1.2.0", notes, "https://github.com/zoranmod/skyfold/releases/tag/v1.2.0", "", "")
    save(dialogs.UpdateDialog(rel, True, c), "update.png")


flyout("light")
flyout("dark")
photos_window("dark", "photos-window.png")
notes_window("dark", "notes-window.png")
first_run()
settings_page(0, "settings-general.png")
settings_page(2, "settings-drive.png")
settings_page(4, "settings-bookmarks.png")
drive_folders()
delete_confirm()
find_my()
update_window()
