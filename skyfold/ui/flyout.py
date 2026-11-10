"""The panel that pops up above the tray icon, Windows 11 flyout style."""

from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import APP_NAME
from ..i18n import t
from ..worker import fmt_time
from .icons import STATE_COLORS, draw_logo, glyph

STATE_TITLES = {
    "idle": t("Everything is in sync"),
    "syncing": t("Syncing…"),
    "paused": t("Paused"),
    "offline": t("Offline"),
    "error": t("Sync problem"),
    "auth": t("Sign-in required"),
    "setup": t("Choose a folder for your photos"),
}

ACTIVITY_GLYPH = {
    "download": "download",
    "trash": "trash",
    "cloud": "cloud",
    "local": "info",
    "info": "info",
    "error": "error",
    "upload": "upload",
}


_IMAGE = {".heic", ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp", ".bmp", ".dng", ".raw"}
_VIDEO = {".mov", ".mp4", ".m4v", ".avi", ".mkv"}


def _open_url(url: str) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl(url))


def split_activity(text: str, kind: str = "") -> tuple[str, str]:
    """"Deleted in iCloud: 2026/11/IMG_1.HEIC" -> ("IMG_1.HEIC", "Deleted in iCloud · 2026/11").
    "Drive: uploaded: Work/Budget.xlsx" -> ("Budget.xlsx", "Uploaded · iCloud Drive/Work").
    Errors and messages that aren't about one file stay whole (title only)."""
    if kind in ("error", "info"):
        return text, ""
    head, sep, tail = text.rpartition(": ")
    tail = tail.strip()
    if not sep or not head or chr(10) in tail or len(tail) > 260 or " → " in tail:
        return text, ""
    name = tail.rsplit("/", 1)[-1]
    if "." not in name and "/" not in tail:
        return text, ""
    folder = tail.rsplit("/", 1)[0] if "/" in tail else ""
    drive = head.startswith("Drive: ")
    if drive:
        head = head[len("Drive: "):]
        head = head[:1].upper() + head[1:]
        folder = t("iCloud Drive") + (f"/{folder}" if folder else "")
    return name, head + (f" · {folder}" if folder else "")


def file_glyph(name: str, kind: str) -> str:
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext in _IMAGE or ext in _VIDEO:
        return "photo"
    if ext == ".html":
        return "note"
    if ext:
        return "file"
    return ACTIVITY_GLYPH.get(kind, "info")


def short_text(text: str, limit: int = 140) -> str:
    """At most about three lines in the panel; the rest is in the details/tooltip."""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def break_at_dots(text: str) -> str:
    """Let a long status line wrap only between its " · " parts, never inside "2,1 MB/s"."""
    nbsp = chr(0xA0)
    return " · ".join(part.replace(" ", nbsp).replace("/", "/\u2060") for part in text.split(" · "))


def fmt_bytes(n: int, base: int = 1000) -> str:
    """Human size. base=1000 matches how Apple counts iCloud storage (a "200 GB" plan)."""
    from ..ui_text import size_text

    return size_text(n, base)


def round_corners(widget: QWidget) -> None:
    """Ask Windows 11 DWM for native rounded corners and shadow on a frameless window."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(widget.winId())
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:  # noqa: BLE001 - purely cosmetic
        pass


BADGE = 32  # header badges (status, iCloud storage): small round buttons beside the gear


class StatusDot(QWidget):
    """Filled circle in the state's colour with a white glyph: the whole sync status at a glance.
    Hover for the full text."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(BADGE, BADGE)
        self.state = "idle"

    def set_state(self, state: str) -> None:
        self.state = state
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(STATE_COLORS.get(self.state, "#22c55e")))
        p.drawEllipse(1, 1, BADGE - 2, BADGE - 2)
        name = {"idle": "check", "syncing": "sync", "paused": "pause", "error": "error",
                "auth": "error", "offline": "cloud", "setup": "folder", "attention": "error"}.get(self.state, "check")
        g = 16
        glyph(name, "#ffffff", g).paint(p, QRect((BADGE - g) // 2, (BADGE - g) // 2, g, g))


class StorageRing(QWidget):
    """iCloud storage as a ring that fills up, with the percentage inside. Hover for details."""

    def __init__(self, palette: dict[str, str]) -> None:
        super().__init__()
        self.setFixedSize(BADGE, BADGE)
        self.c = palette
        self.fraction = 0.0

    def set_fraction(self, f: float) -> None:
        self.fraction = max(0.0, min(1.0, f))
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = 3.2
        r = QRectF(w / 2 + 1, w / 2 + 1, BADGE - w - 2, BADGE - w - 2)
        p.setPen(QPen(QColor(self.c["track"]), w))
        p.drawEllipse(r)
        full = self.fraction >= 0.9
        p.setPen(QPen(QColor("#ef4444" if full else self.c["accent"]), w, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, 90 * 16, -int(360 * 16 * self.fraction))
        p.setPen(QColor(self.c["text"]))
        f = p.font()
        f.setPixelSize(9)
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, f"{round(self.fraction * 100)}%")


class ServiceRow(QFrame):
    """One service in the services card: glyph, name, one-line status, coloured dot."""

    clicked = Signal()

    def __init__(self, glyph_name: str, name: str, palette: dict[str, str]) -> None:
        super().__init__()
        self.setObjectName("Row")
        self.setCursor(Qt.PointingHandCursor)
        self.glyph_name = glyph_name
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 10, 6)
        lay.setSpacing(10)
        self.icon = QLabel()
        self.icon.setFixedSize(20, 20)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.name = QLabel(name)
        self.name.setStyleSheet("font-weight: 600;")
        self.detail = QLabel("")
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        col.addWidget(self.name)
        col.addWidget(self.detail)
        self.dot = QLabel()
        self.dot.setFixedSize(10, 10)
        lay.addWidget(self.icon, 0, Qt.AlignVCenter)
        lay.addLayout(col, 1)
        lay.addWidget(self.dot, 0, Qt.AlignVCenter)
        self.apply_palette(palette)

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        self.icon.setPixmap(glyph(self.glyph_name, palette["accent_fg"], 20).pixmap(QSize(20, 20)))

    def set(self, detail: str, state: str | None) -> None:
        self.detail.setText(break_at_dots(detail))
        self.setAccessibleName(f"{self.name.text()}: {detail}")
        if state is None:
            self.dot.setStyleSheet("background: transparent;")
        else:
            self.dot.setStyleSheet(f"background: {STATE_COLORS.get(state, '#22c55e')}; border-radius: 5px;")

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(e)


def reveal(target: str) -> None:
    """Show what an activity entry is about: a note page opens in the browser, a file is selected
    in Explorer, a folder opens. If the file is gone, its folder opens instead."""
    import subprocess
    from pathlib import Path

    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    p = Path(target)
    if p.is_file() and p.suffix.lower() == ".html":
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
    elif p.is_file():
        subprocess.Popen([os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe"), "/select,", str(p)])
    else:
        while not p.exists() and p.parent != p:
            p = p.parent
        if p.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))


class ActivityRow(QFrame):
    """Activity entry: glyph, the event text, and the time as a second muted line. Entries about a
    file or folder open it when clicked; errors open their details. The same event repeated right
    after itself is counted on one row ("×3") instead of filling the list."""

    errorClicked = Signal(object)

    def __init__(self, ts: str, kind: str, text: str, palette: dict[str, str], target: str = "") -> None:
        super().__init__()
        self.setObjectName("ActRow")
        self.ts, self.kind, self.target, self.message, self.count = ts, kind, target, text, 1
        if target or kind == "error":
            self.setProperty("link", True)
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(target or t("Click for details"))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 8)
        lay.setSpacing(12)
        self.tile = QFrame()
        self.tile.setObjectName("FileTile")
        self.tile.setFixedSize(36, 36)
        tl = QVBoxLayout(self.tile)
        tl.setContentsMargins(0, 0, 0, 0)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignCenter)
        tl.addWidget(self.icon)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title_text, self.detail_text = split_activity(text, kind)
        self.text = QLabel()  # the file name (or the whole message when it isn't about one file)
        self.text.setObjectName("RowTitle")
        self.text.setMinimumWidth(0)
        self.text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.text.setWordWrap(not self.detail_text)
        from PySide6.QtGui import QFontMetrics

        self.detail = QLabel()
        self.detail.setObjectName("Muted")
        self.detail.setText(QFontMetrics(self.detail.font()).elidedText(self.detail_text, Qt.ElideMiddle, 290))
        self.detail.setToolTip(self.detail_text)
        self.detail.setMinimumWidth(0)
        self.detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.detail.setVisible(bool(self.detail_text))
        self.when = QLabel(fmt_time(ts))
        self.when.setObjectName("Hint")
        col.addWidget(self.text)
        col.addWidget(self.detail)
        col.addWidget(self.when)
        lay.addWidget(self.tile, 0, Qt.AlignTop)
        lay.addLayout(col, 1)
        self._set_title()
        self.apply_palette(palette)

    def _set_title(self) -> None:
        from PySide6.QtGui import QFontMetrics

        title = self.title_text + (f"  (×{self.count})" if self.count > 1 else "")
        if self.detail_text:  # one line: a file name, shortened in the middle like Explorer does
            self.text.ensurePolished()
            self.text.setText(QFontMetrics(self.text.font()).elidedText(title, Qt.ElideMiddle, 270))
            self.text.setToolTip(title)
        else:
            self.text.setText(short_text(title, 110))

    def apply_palette(self, palette: dict[str, str]) -> None:
        if self.kind == "error":
            name, color = "error", palette["err_text"]
        else:
            name, color = file_glyph(self.title_text if self.detail_text else "", self.kind), palette["accent_fg"]
        self.icon.setPixmap(glyph(name, color, 20).pixmap(QSize(20, 20)))

    def refresh(self) -> None:
        self.when.setText(fmt_time(self.ts))

    def same_as(self, kind: str, text: str, target: str = "") -> bool:
        return self.kind == kind and self.message == text and (target or "") == (self.target or "")

    def bump(self, ts: str, target: str = "") -> None:
        self.count += 1
        self.ts = ts
        self.target = target or self.target
        self._set_title()
        self.setToolTip(self.target or (t("Click for details") if self.kind == "error" else ""))
        self.refresh()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802 - Qt naming
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            if self.target:
                reveal(self.target)
            elif self.kind == "error":
                self.errorClicked.emit(self)
        super().mouseReleaseEvent(e)


class Banner(QFrame):
    clicked = Signal()

    def __init__(self, kind: str, button: str) -> None:
        super().__init__()
        self.setObjectName("Warn" if kind == "warn" else "Err")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 10, 10)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.btn = QPushButton(button)
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self.clicked)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.btn)


class Flyout(QWidget):
    syncNow = Signal()
    clearActivity = Signal()
    cancelSync = Signal()
    openFolder = Signal()
    togglePause = Signal()
    openSettings = Signal()
    login = Signal()
    reviewPending = Signal()
    setup = Signal()
    findMy = Signal()
    serviceClicked = Signal(str)  # "photos" | "drive" | "notes"
    quitApp = Signal()

    WIDTH = 400

    def __init__(self, palette: dict[str, str]) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.c = palette
        self.setObjectName("Root")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(self.WIDTH)
        self.setFocusPolicy(Qt.StrongFocus)
        self.state = "idle"
        self.paused = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QWidget()
        body.setObjectName("Root")
        outer.addWidget(body, 1)
        root = QVBoxLayout(body)
        root.setContentsMargins(16, 16, 16, 8)
        root.setSpacing(10)

        # header
        head = QHBoxLayout()
        logo = QLabel()
        pm = QPixmap(56, 56)
        pm.fill(Qt.transparent)
        pp = QPainter(pm)
        draw_logo(pp, 56)
        pp.end()
        pm.setDevicePixelRatio(2)
        logo.setPixmap(pm)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        app_title = QLabel(APP_NAME)
        app_title.setObjectName("Title")
        self.account = QLabel(t("Not signed in"))  # shown as the title's tooltip and in Settings
        self.account.setObjectName("Muted")
        self.account.hide()
        self._app_title = app_title
        status = QHBoxLayout()
        status.setSpacing(5)
        self.status_icon = QLabel()
        self.status_icon.setFixedSize(14, 14)
        self.status_line = QLabel("")
        self.status_line.setObjectName("StatusLine")
        status.addWidget(self.status_icon, 0, Qt.AlignVCenter)
        status.addWidget(self.status_line, 1)
        titles.addWidget(app_title)
        titles.addLayout(status)
        self.gear = QToolButton()
        self.gear.setIcon(glyph("gear", palette["muted"]))
        self.gear.setIconSize(QSize(18, 18))
        self.gear.setToolTip(t("Settings"))
        self.gear.setCursor(Qt.PointingHandCursor)
        self.gear.clicked.connect(self.openSettings)
        self.gear.setAccessibleName(t("Settings"))
        self.dot = StatusDot()
        self.storage_ring = StorageRing(palette)
        self.storage_ring.hide()
        head.addWidget(logo)
        head.addSpacing(4)
        head.addLayout(titles, 1)
        self.dot.hide()  # the status line under the title says it now (kept for its state logic)
        head.addWidget(self.storage_ring, 0, Qt.AlignVCenter)
        head.addWidget(self.gear, 0, Qt.AlignVCenter)
        root.addLayout(head)

        # status card
        card = QFrame()
        card.setObjectName("Card")
        self.status_card = card
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(12)
        txt = QVBoxLayout()
        txt.setSpacing(2)
        self.status_title = QLabel(STATE_TITLES["idle"])
        self.status_title.setObjectName("H2")
        self.status_text = QLabel("")
        self.status_text.setObjectName("Muted")
        self.status_text.setWordWrap(True)
        txt.addWidget(self.status_title)
        txt.addWidget(self.status_text)
        row.addLayout(txt, 1)
        cl.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        self.bar_label = QLabel()
        self.bar_label.setObjectName("Hint")
        cl.addWidget(self.bar)
        cl.addWidget(self.bar_label)
        self.bar.hide()
        self.bar_label.hide()
        root.addWidget(card)

        self.pending_banner = Banner("warn", t("Review"))
        self.pending_banner.clicked.connect(self.reviewPending)
        self.pending_banner.hide()
        root.addWidget(self.pending_banner)
        self.auth_banner = Banner("err", t("Sign in"))
        self.auth_banner.clicked.connect(self.login)
        self.auth_banner.hide()
        root.addWidget(self.auth_banner)

        # services
        services = QFrame()
        services.setObjectName("Card")
        self.services_card = services
        sl = QVBoxLayout(services)
        sl.setContentsMargins(4, 4, 4, 4)
        sl.setSpacing(0)
        self.row_photos = ServiceRow("photo", t("Photos"), palette)
        self.row_drive = ServiceRow("cloud", t("iCloud Drive"), palette)
        self.row_photos.clicked.connect(lambda: self.serviceClicked.emit("photos"))
        self.row_drive.clicked.connect(lambda: self.serviceClicked.emit("drive"))
        self.row_notes = ServiceRow("note", t("Notes"), palette)
        self.row_notes.clicked.connect(lambda: self.serviceClicked.emit("notes"))
        for i, row in enumerate((self.row_photos, self.row_drive, self.row_notes)):
            if i:
                sep = QFrame()
                sep.setObjectName("Sep")
                sl.addWidget(sep)
            sl.addWidget(row)
        root.addWidget(services)

        # activity
        sec = QHBoxLayout()
        lbl = QLabel(t("RECENT ACTIVITY"))
        lbl.setObjectName("Section")
        self.last_sync = QLabel("")
        self.last_sync.setObjectName("Hint")
        self.clear_btn = QPushButton(t("Clear"))
        self.clear_btn.setObjectName("Link")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.setToolTip(t("Clear the activity list. Your files are not touched."))
        self.clear_btn.clicked.connect(self._clear_activity)
        self.clear_btn.hide()
        sec.addWidget(lbl)
        sec.addSpacing(6)
        sec.addWidget(self.clear_btn)
        sec.addStretch(1)
        self.last_sync.hide()  # each service row now shows its own "checked …" time
        root.addLayout(sec)
        self.list = QScrollArea()
        self.list.setWidgetResizable(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setFrameShape(QFrame.NoFrame)
        self._rows_box = QWidget()
        self._rows_box.setObjectName("Root")
        self._rows = QVBoxLayout(self._rows_box)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(0)
        self._rows.addStretch(1)
        self.list.setWidget(self._rows_box)
        self.empty = QLabel(t("No activity yet."))
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignCenter)
        root.addWidget(self.list)
        root.addWidget(self.empty)
        self.list.hide()

        # bottom bar: icon + label buttons, like the OneDrive panel
        bar = QFrame()
        bar.setObjectName("BottomBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 6, 8, 8)
        bl.setSpacing(2)

        def bar_button(text: str, slot) -> QToolButton:
            b = QToolButton()
            b.setObjectName("Bar")
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(20, 20))
            b.setText(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(slot)
            bl.addWidget(b)
            return b

        self.sync_btn = bar_button(t("Sync"), self._on_sync_clicked)
        self.sync_btn.setToolTip(t("Sync now"))
        self.sync_btn.setProperty("primary", True)
        self.open_btn = bar_button(t("Open folder"), self._open_folder_menu)
        self.web_btn = bar_button(t("iCloud.com"), lambda: _open_url("https://www.icloud.com"))
        self.folder_btn = bar_button(t("Find My"), self.findMy)  # (name kept: other code refers to it)
        self.folder_btn.setToolTip(t("Find My devices…"))
        self.folder_btn.setAccessibleName(t("Find My devices…"))
        self.pause_btn = bar_button(t("Pause"), self.togglePause)
        outer.addWidget(bar)
        self._bar_icons()
        self.set_paused(False)

    def _bar_icons(self) -> None:
        self.open_btn.setIcon(glyph("folder", self.c["text"]))
        self.web_btn.setIcon(glyph("globe", self.c["text"]))
        self.folder_btn.setIcon(glyph("device", self.c["text"]))

    def _open_folder_menu(self) -> None:
        """One folder per synced service: open it straight away, or choose when there are several."""
        from PySide6.QtWidgets import QMenu

        s = getattr(self, "_stats", {}) or {}
        choices = [("photos", t("Photos"))]
        if s.get("drive_enabled"):
            choices.append(("drive", t("iCloud Drive")))
        if s.get("notes_enabled"):
            choices.append(("notes", t("Notes")))
        if len(choices) == 1:
            self.serviceClicked.emit("photos")
            return
        menu = QMenu(self)
        for key, label in choices:
            menu.addAction(label, lambda k=key: self.serviceClicked.emit(k))
        menu.exec(self.open_btn.mapToGlobal(self.open_btn.rect().topLeft()) - QPoint(0, menu.sizeHint().height()))

    def apply_palette(self, palette: dict[str, str]) -> None:
        """Called when Windows switches light/dark: re-tint the hand-drawn icons."""
        self.c = palette
        self.gear.setIcon(glyph("gear", palette["muted"]))
        self.storage_ring.c = palette
        self.storage_ring.update()
        self._bar_icons()
        self.set_paused(self.paused)
        self.set_state(self.state, self.status_text.text())
        for w in self._activity_rows():
            w.apply_palette(palette)
        self.row_photos.apply_palette(palette)
        self.row_drive.apply_palette(palette)
        self.row_notes.apply_palette(palette)

    # state updates -------------------------------------------------------------
    def _on_sync_clicked(self) -> None:
        if self.state == "syncing":
            self.cancelSync.emit()
        elif self.state == "auth":
            self.login.emit()
        elif self.state == "setup":
            self.setup.emit()
        elif self.state == "paused":
            self.togglePause.emit()
        else:
            self.syncNow.emit()

    def set_account(self, apple_id: str) -> None:
        from PySide6.QtGui import QFontMetrics

        text = apple_id or t("Not signed in")
        self.account.setText(QFontMetrics(self.account.font()).elidedText(text, Qt.ElideMiddle, 170))
        self.account.setToolTip(apple_id)
        self._app_title.setToolTip(apple_id or text)

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.pause_btn.setIcon(glyph("play" if paused else "pause", self.c["text"]))
        self.pause_btn.setText(t("Resume") if paused else t("Pause"))
        self.pause_btn.setToolTip(t("Resume syncing") if paused else t("Pause syncing"))
        self.pause_btn.setAccessibleName(self.pause_btn.toolTip())

    def _dot_state(self) -> str:
        return "attention" if self.state == "idle" and getattr(self, "_pending_text", "") else self.state

    def set_state(self, state: str, text: str) -> None:
        self.state = state
        self.dot.set_state(self._dot_state())
        self.status_title.setText(STATE_TITLES.get(state, state))
        self.status_text.setText("" if text.rstrip(".…") == STATE_TITLES.get(state, "").rstrip(".…")
                                 else short_text(text, 260 if state in ("error", "auth") else 110))
        self.status_text.setVisible(bool(self.status_text.text()))
        self._dot_tip()
        self.status_card.setVisible(state != "idle")  # all in sync: the green badge says it
        syncing = state == "syncing"
        if not syncing:
            self.bar.hide()
            self.bar_label.hide()
        self._render_services()
        self.auth_banner.hide()  # the status card and the main button already say "Sign in"
        if state == "auth":
            self.auth_banner.text.setText(t("Signing in to iCloud is required to sync."))
        name, label = {"syncing": ("close", t("Stop")), "setup": ("folder", t("Set up")),
                       "auth": ("cloud", t("Sign in"))}.get(
            state, ("sync", t("Sync")))
        self.sync_btn.setText(label)
        self.sync_btn.setIcon(glyph(name, self.c["accent_fg"] if state != "paused" else self.c["disabled"]))
        self.sync_btn.setEnabled(state != "paused")  # one way to continue: Resume
        self._status_line()
        self._size_list()

    def _status_line(self) -> None:
        state = self._dot_state()
        title = STATE_TITLES.get(self.state, self.state)
        if state == "attention":
            title = t("Waiting for your OK")
        icon = {"idle": "check", "syncing": "sync", "paused": "pause", "error": "error", "auth": "error",
                "offline": "cloud", "setup": "folder", "attention": "error"}.get(state, "check")
        color = STATE_COLORS.get(state, "#16a34a")
        self.status_icon.setPixmap(glyph(icon, color, 14).pixmap(QSize(14, 14)))
        self.status_line.setText(title)

    def set_progress(self, done: int, total: int, text: str) -> None:
        if self.state != "syncing" or total <= 0:
            return
        self.bar.setValue(int(1000 * min(done, total) / total))
        text = break_at_dots(text) if text else text
        if text and text != self.status_text.text():
            self.status_text.setText(text)  # one line of text: the status line itself
            self.status_text.show()
            self.dot.setToolTip(STATE_TITLES.get(self.state, self.state) + "\n" + text.replace(chr(0xA0), " "))
        if self.bar.isHidden():
            self.bar.show()
            self._fit()

    def set_stats(self, stats: dict) -> None:
        self._stats = stats
        st = stats.get("storage")
        if st and st.get("total"):
            used, total = st["used"], st["total"]
            self.storage_ring.set_fraction(used / total)
            lines = [t("iCloud storage"),
                     t("{used} of {total}", used=fmt_bytes(used, 1000), total=fmt_bytes(total, 1000).replace(",0 ", " ").replace(".0 ", " ")),
                     ""]
            lines += [f"{p[0]}: {fmt_bytes(p[1], 1000)}" for p in st.get("parts", [])]
            self.storage_ring.setToolTip("\n".join(lines).strip())
            self.storage_ring.setAccessibleName(t("iCloud storage") + f" {round(100 * used / total)}%")
            self.storage_ring.show()
        else:
            self.storage_ring.hide()
        self._last_sync_ts = stats.get("last_sync")
        self.refresh_times()

    def _service_state(self) -> str | None:
        return None if self.state == "idle" else self.state

    def _render_services(self) -> None:
        s = getattr(self, "_stats", {}) or {}
        from ..ui_text import count_text as n
        detail = t("{photos} photos · {videos} videos · {size}", photos=n(s.get("photos", 0)),
                   videos=n(s.get("videos", 0)), size=fmt_bytes(s.get("bytes", 0)))
        when = s.get("last_sync")
        detail += " · " + (t("checked {when}", when=fmt_time(when)) if when else t("first sync in progress"))
        waiting = {"auth": t("Waiting for sign-in"), "setup": t("Not set up yet")}.get(self.state)
        if waiting and not when:
            detail = waiting
        self.row_photos.set(detail, "idle" if self.state == "idle" else self.state)
        self.folder_btn.setEnabled(self.state not in ("auth", "setup"))
        self.pause_btn.setEnabled(self.state not in ("auth", "setup"))
        if s.get("drive_enabled"):
            when = s.get("drive_last_sync")
            detail = t("{files} files · {size}", files=n(s.get("drive_files", 0)), size=fmt_bytes(s.get("drive_bytes", 0)))
            detail += " · " + (t("checked {when}", when=fmt_time(when)) if when else t("first sync in progress"))
            if waiting and not when:
                detail = waiting
            self.row_drive.set(detail, "idle" if self.state == "idle" else self.state)
        else:
            self.row_drive.set(t("Off · click to turn on"), None)
        if s.get("notes_enabled"):
            when = s.get("notes_last_sync")
            detail = t("{n} notes", n=n(s.get("notes_count", 0)))
            detail += " · " + (t("checked {when}", when=fmt_time(when)) if when else t("first read pending"))
            if waiting and not when:
                detail = waiting
            self.row_notes.set(detail, "idle" if self.state == "idle" else self.state)
        else:
            self.row_notes.set(t("Off · click to turn on"), None)

    def refresh_times(self) -> None:
        ts = getattr(self, "_last_sync_ts", None)
        self.last_sync.setText(t("Last check: {when}", when=fmt_time(ts)) if ts else "")
        self._render_services()

    def _dot_tip(self) -> None:
        if self._dot_state() == "attention":
            tip = self._pending_text
        else:
            tip = STATE_TITLES.get(self.state, self.state)
            if self.status_text.text():
                tip += "\n" + self.status_text.text().replace(chr(0xA0), " ")
        self.dot.setToolTip(tip)
        self.dot.setAccessibleName(tip)

    def set_pending(self, pending) -> None:
        self._pending_text = ""
        if not pending:
            self.pending_banner.hide()
        else:
            parts = []
            if pending.to_pc:
                parts.append(t("{n} deleted in iCloud", n=getattr(pending, "files_to_pc", 0) or len(pending.to_pc)))
            if pending.to_cloud:
                parts.append(t("{n} deleted on this PC",
                               n=getattr(pending, "files_to_cloud", 0) or len(pending.to_cloud)))
            self._pending_text = t("A large deletion is waiting for your OK: {what}.", what=t(" and ").join(parts))
            self.pending_banner.text.setText(self._pending_text)
            self.pending_banner.show()
        self.dot.set_state(self._dot_state())
        self._dot_tip()
        self._status_line()
        self._fit()

    MAX_ROWS_VISIBLE = 6
    MIN_LIST_HEIGHT = 300  # the panel keeps a calm, steady height instead of jumping with each entry

    def _activity_rows(self) -> list["ActivityRow"]:
        return [self._rows.itemAt(i).widget() for i in range(self._rows.count() - 1)]

    def add_activity(self, ts: str, kind: str, text: str, target: str = "") -> None:
        rows = self._activity_rows()
        if rows and rows[0].same_as(kind, text, target):
            rows[0].bump(ts, target)
            QTimer.singleShot(0, self._size_list)
            return
        row = ActivityRow(ts, kind, text, self.c, target)
        row.errorClicked.connect(self._error_details)
        self._rows.insertWidget(0, row)
        while self._rows.count() - 1 > 60:  # keep the newest 60 (the last item is the stretch)
            old = self._rows.takeAt(self._rows.count() - 2).widget()
            old.deleteLater()
        self.list.show()
        self.empty.hide()
        self.clear_btn.show()
        QTimer.singleShot(0, self._size_list)  # after styles are applied, so heights are exact

    def _error_details(self, row: "ActivityRow") -> None:
        from PySide6.QtWidgets import QApplication, QMessageBox

        box = QMessageBox(self)
        box.setWindowTitle(t("Sync problem"))
        box.setIcon(QMessageBox.Warning)
        box.setText(row.message)
        when = fmt_time(row.ts) + (f"  (×{row.count})" if row.count > 1 else "")
        network = any(w in row.message.lower() for w in ("connection", "timed out", "reach", "network",
                                                            "veza", "dohvatiti"))
        box.setInformativeText(t("Last seen: {when}", when=when) + "\n\n" + (t(
            "Skyfold tries again by itself. Most problems like this are a short break in the connection to "
            "Apple and go away on the next sync.") if network else t("Skyfold tries again by itself.")))
        again = box.addButton(t("Try again now"), QMessageBox.AcceptRole)
        again.setObjectName("Primary")
        again.style().unpolish(again)
        again.style().polish(again)
        copy = box.addButton(t("Copy text"), QMessageBox.ActionRole)
        box.addButton(t("Close"), QMessageBox.RejectRole)
        box.setDefaultButton(again)
        self.hide()  # the panel closes on focus loss anyway; the dialog stands on its own
        box.exec()
        if box.clickedButton() is again:
            self.syncNow.emit()
        elif box.clickedButton() is copy:
            QApplication.clipboard().setText(f"{row.message}\n{row.ts}")

    def _clear_activity(self) -> None:
        """Empty the list (and its saved history); nothing else changes."""
        for row in self._activity_rows():
            self._rows.removeWidget(row)
            row.deleteLater()
        self.list.hide()
        self.empty.show()
        self.clear_btn.hide()
        self.clearActivity.emit()
        self._fit()

    def _size_list(self) -> None:
        """Show up to four entries without empty space; scroll for older ones."""
        width = self.WIDTH - 2 * 16 - 12  # panel margins and room for the scroll bar
        rows = self._activity_rows()[: self.MAX_ROWS_VISIBLE]
        height = 0
        for row in rows:
            row.ensurePolished()
            row.text.ensurePolished()
            lay = row.layout()
            lay.activate()
            height += (lay.totalHeightForWidth(width) if lay.hasHeightForWidth() else row.sizeHint().height()) + 1
        self.services_card.show()
        self.list.setFixedHeight(max(height, self.MIN_LIST_HEIGHT if rows else 1) + 2)
        room = self._limit() - (self._needed_height() - self.list.height())
        if self.list.height() > room:
            if room < 60:
                self.list.setFixedHeight(0)  # no room at all: the buttons matter more than the list
                if self._needed_height() > self._limit():
                    self.services_card.hide()
            else:
                self.list.setFixedHeight(room)
        self._fit()

    def refresh_activity_times(self) -> None:
        for w in self._activity_rows():
            w.refresh()

    # placement -----------------------------------------------------------------
    def _limit(self) -> int:
        screen = self.screen() or QGuiApplication.primaryScreen()
        return screen.availableGeometry().height() - 24

    def _needed_height(self) -> int:
        lay = self.layout()
        lay.activate()
        return lay.totalHeightForWidth(self.WIDTH) if lay.hasHeightForWidth() else lay.totalSizeHint().height()

    def _fit(self) -> None:
        limit = self._limit()
        self.setMinimumHeight(0)
        self.setMaximumHeight(limit)
        self.resize(self.WIDTH, min(self._needed_height(), limit))
        if self.isVisible():
            self._place()
            QTimer.singleShot(0, self._place)  # again once Qt has applied the new size

    def _place(self, anchor: QRect | None = None) -> None:
        if anchor is not None:
            self._anchor = anchor
        anchor = getattr(self, "_anchor", None)
        screen = QGuiApplication.screenAt(anchor.center()) if anchor else None
        screen = screen or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        full = screen.geometry()
        if self.height() > avail.height() - 24:  # (adjustSize() would undo the exact size _fit() set)
            self.resize(self.width(), avail.height() - 24)
        w, h = self.width(), self.height()
        margin = 12
        # Taskbar position decides which corner we pop out of.
        if avail.bottom() < full.bottom():  # taskbar at bottom
            x = (anchor.center().x() - w // 2) if anchor else avail.right() - w - margin
            y = avail.bottom() - h - margin
        elif avail.top() > full.top():  # top
            x = (anchor.center().x() - w // 2) if anchor else avail.right() - w - margin
            y = avail.top() + margin
        elif avail.left() > full.left():  # left
            x = avail.left() + margin
            y = avail.bottom() - h - margin
        else:
            x = avail.right() - w - margin
            y = avail.bottom() - h - margin
        x = max(avail.left() + margin, min(x, avail.right() - w - margin))
        y = max(avail.top() + margin, min(y, avail.bottom() - h - margin))
        self.move(QPoint(x, y))

    def toggle(self, anchor: QRect | None) -> None:
        """Tray click: open, or close if open. Clicking the tray icon first takes focus away from
        the panel (which hides it), so a click right after that means "close", not "open again"."""
        if self.isVisible():
            self.hide()
        elif time.monotonic() - getattr(self, "_hidden_at", 0.0) > 0.4:
            self.show_at(anchor)

    def show_at(self, anchor: QRect | None) -> None:
        self.refresh_times()
        self.refresh_activity_times()
        self._size_list()
        self._place(anchor if anchor and anchor.isValid() and not anchor.isEmpty() else None)
        self.show()
        round_corners(self)
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.OtherFocusReason)  # the panel itself, not its first button
        QTimer.singleShot(0, lambda: (self._fit(), self._place()))  # final size known: keep it on screen

    def event(self, e) -> bool:  # noqa: N802
        if e.type() == QEvent.WindowDeactivate:
            self._hidden_at = time.monotonic()
            self.hide()
        return super().event(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)
