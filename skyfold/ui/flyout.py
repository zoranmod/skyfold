"""The panel that pops up above the tray icon, Windows 11 flyout style."""

from __future__ import annotations

import os
import sys
import time

import logging

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap
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
from .icons import STATE_COLORS, glyph
from .panel_parts import (CenteredButton, LogoTile, RowScroll, SoftCard, StateBadge, Thumbs, TintTile, TwoLineLabel,
                          rounded, tint)
from .glow import GlowTile, animations_on
from .theme import OLED, service_color, state_color

log = logging.getLogger(__name__)

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
    "move": "folder",  # a note moved to another folder
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
    if sep and head and " → " in tail and chr(10) not in tail:  # a rename or move: the new name, and from where
        old, _, new = tail.partition(" → ")
        if head.startswith("Drive: "):
            head = head[len("Drive: "):]
        head = head[:1].upper() + head[1:]
        before = old if old.rsplit("/", 1)[0] != new.rsplit("/", 1)[0] else old.rsplit("/", 1)[-1]
        return new.rsplit("/", 1)[-1], head + " · " + t("was {old}", old=before)
    if not sep or not head or chr(10) in tail or len(tail) > 260 or " → " in tail:
        return text, ""
    name = tail.rsplit("/", 1)[-1]
    if "." not in name and "/" not in tail and not head.startswith("Drive: "):
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
    """Human size of files on this PC (1 GB = 1000^3 bytes)."""
    from ..ui_text import size_text

    return size_text(n, base)


def storage_text(n: int) -> str:
    """iCloud storage the way Apple shows it: a "200 GB" plan is 200 x 1024^3 bytes, so Apple's
    GB here is 1024^3 (with 1000^3 the plan would read 214.7 GB). "88 GB", not "88.0 GB"."""
    return fmt_bytes(n, 1024).replace(",0 ", " ").replace(".0 ", " ")


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


def _short_time(ts: str) -> str:
    """When an activity happened, short: now, 09:36 (today), Sat (this week), 18 Nov (earlier)."""
    from datetime import datetime

    from PySide6.QtCore import QDate, QLocale, QTime

    from ..i18n import language

    try:
        dt = datetime.fromisoformat(ts).astimezone()
    except (TypeError, ValueError):
        return fmt_time(ts)
    now = datetime.now().astimezone()
    secs = (now - dt).total_seconds()
    if secs < 60:
        return t("now")
    loc = QLocale(QLocale.Bosnian, QLocale.BosniaAndHerzegowina) if language() == "bs" else QLocale(
        QLocale.English, QLocale.UnitedKingdom)
    if dt.date() == now.date():
        return loc.toString(QTime(dt.hour, dt.minute), "HH:mm")
    day = QDate(dt.year, dt.month, dt.day)
    if (now.date() - dt.date()).days < 7:
        return loc.toString(day, "ddd")
    return loc.toString(day, "d MMM" if dt.year == now.year else "d MMM yyyy")


def _status_dot(color: str, dpr: float) -> QPixmap:
    """A 7 px dot in the state's colour inside a soft halo of the same colour (13 px in all)."""
    pm = QPixmap(round(13 * dpr), round(13 * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    halo = QColor(color)
    halo.setAlphaF(0.22)
    p.setBrush(halo)
    p.drawEllipse(QRectF(1.5, 1.5, 10, 10))
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(3, 3, 7, 7))
    p.end()
    return pm


class StatusDot(QWidget):
    """Filled circle in the state's colour with a white glyph: the whole sync status at a glance.
    Hover for the full text."""

    def __init__(self, palette: dict[str, str]) -> None:
        super().__init__()
        self.c = palette
        self.setFixedSize(BADGE, BADGE)
        self.state = "idle"

    def set_state(self, state: str) -> None:
        self.state = state
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        fill = state_color(self.c, self.state) if self.c is OLED else STATE_COLORS.get(self.state, "#22c55e")
        p.setBrush(QColor(fill))
        p.drawEllipse(1, 1, BADGE - 2, BADGE - 2)
        name = {"idle": "check", "syncing": "sync", "paused": "pause", "error": "error",
                "auth": "error", "offline": "cloud", "setup": "folder", "attention": "error"}.get(self.state, "check")
        g = 16
        glyph(name, self.c["accent_text"] if self.c is OLED else "#ffffff", g).paint(p, QRect((BADGE - g) // 2, (BADGE - g) // 2, g, g))


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
        warning = self.c["err"] if self.c is OLED else "#ef4444"
        p.setPen(QPen(QColor(warning if full else self.c["accent"]), w, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, 90 * 16, -int(360 * 16 * self.fraction))
        p.setPen(QColor(self.c["text"]))
        f = p.font()
        f.setPixelSize(9)
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, f"{round(self.fraction * 100)}%")


class StorageBar(QFrame):
    """iCloud storage: "88 of 200 GB", one thin bar split into Photos / Drive / Other, and a legend.
    Click (or Enter) for the details line. The only place in the panel that uses category colours."""

    def __init__(self, palette: dict[str, str], large: bool = False) -> None:
        super().__init__()
        self.c = palette
        self.large = large  # (Settings) "74 GB of 200 GB used" as a heading, the free space on the right
        self.parts: list[tuple[str, int, str]] = []  # (label, bytes, colour key)
        self.used = self.total = 0
        self.setObjectName("Storage")  # its focus frame is in the style sheet (room for it kept)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 3, 4, 3)
        lay.setSpacing(6)
        head = QHBoxLayout()
        self.title = QLabel(t("iCloud storage"))
        self.title.setObjectName("Hint")
        self.amount = QLabel("")
        self.amount.setObjectName("Amount")
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.amount)
        lay.addLayout(head)
        self.bar = QWidget()
        self.bar.setFixedHeight(6)
        self.bar.paintEvent = self._paint_bar  # type: ignore[method-assign]
        lay.addWidget(self.bar)
        self.legend = QLabel("")
        self.legend.setObjectName("Legend")
        self.legend.setTextFormat(Qt.RichText)
        lay.addWidget(self.legend)
        self.details = QLabel("")
        self.details.setObjectName("Legend")
        self.details.setWordWrap(True)
        lay.addWidget(self.details)
        self.details.hide()
        if large:
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(10)
            self.title.setObjectName("StorageHead")
            self.title.setTextFormat(Qt.RichText)
            self.amount.setObjectName("SetSub")
            self.bar.setFixedHeight(8)
            self.setFocusPolicy(Qt.NoFocus)  # (nothing to open: everything is shown)
            self.setCursor(Qt.ArrowCursor)

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        self._render()

    def set(self, used: int, total: int, parts: list) -> None:
        """parts: (label, bytes, key) as iCloud reports them; grouped here into Photos / Drive / Other."""
        self.used, self.total = used, total
        photos = sum(p[1] for p in parts if len(p) > 2 and str(p[2]).lower() == "photos")
        drive = sum(p[1] for p in parts if len(p) > 2 and str(p[2]).lower() == "docs")
        other = max(0, used - photos - drive)
        self.parts = [(t("Photos"), photos, "c_photos"), (t("Drive"), drive, "c_drive"), (t("Other"), other, "c_other")]
        self._render()

    def _render(self) -> None:
        size = storage_text

        heard = t("{used} of {total}", used=size(self.used), total=size(self.total))
        used, total = size(self.used), size(self.total)
        if used.rpartition(" ")[2] == total.rpartition(" ")[2]:  # "74 / 200 GB": the unit once
            used = used.rpartition(" ")[0]
        self.amount.setText(f"{used} / {total}")
        if self.large:
            self.title.setText(f'<span style="font-size:15pt;font-weight:600">{size(self.used)}</span>&nbsp; '
                               + t("of {total} used", total=size(self.total)))
            self.amount.setText(t("{free} free", free=size(max(0, self.total - self.used))))
        self.legend.setText("&nbsp;&nbsp;&nbsp;&nbsp;".join(
            f'<span style="color:{self.c[key]};font-size:10pt">&#9679;</span>&nbsp;{label}&nbsp;<b>{size(n)}</b>'
            for label, n, key in self.parts if n))
        pct = round(100 * self.used / self.total) if self.total else 0
        self.details.setText(t("{pct}% used · {free} available", pct=pct, free=size(max(0, self.total - self.used))))
        text = t("iCloud storage") + ": " + heard + ". " + ", ".join(
            f"{label} {size(n)}" for label, n, _ in self.parts if n)
        self.setAccessibleName(text)
        self.setToolTip(self.details.text())
        self.bar.update()

    def _paint_bar(self, _e) -> None:
        p = QPainter(self.bar)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.bar.width(), self.bar.height()
        from PySide6.QtGui import QPainterPath

        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        p.setClipPath(clip)
        p.fillRect(QRectF(0, 0, w, h), QColor(self.c["track"]))
        x = 0.0
        p.setClipping(False)
        for _label, n, key in self.parts:
            if not n or not self.total:
                continue
            seg = max(h, w * n / self.total)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self.c[key]))
            p.drawRoundedRect(QRectF(x, 0, max(h, seg - 2), h), h / 2, h / 2)  # each part rounded, 2 px apart
            x += seg
        p.end()

    def _toggle(self) -> None:
        if self.large:
            return
        self.details.setVisible(not self.details.isVisible())
        top = self.window()
        if hasattr(top, "_size_list"):
            QTimer.singleShot(0, top._size_list)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton:
            self._toggle()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self._toggle()
            return
        super().keyPressEvent(e)


class ServiceRow(QFrame):
    """One service in the services group: icon tile, name, one line of detail, a chevron. A coloured
    dot appears only when the service needs attention."""

    clicked = Signal()

    def __init__(self, glyph_name: str, name: str, palette: dict[str, str]) -> None:
        super().__init__()
        self.setObjectName("Row")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)  # Tab reaches it; Enter or Space opens it, like a button
        self.glyph_name = glyph_name
        self.setMinimumHeight(58)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(12)
        self.icon = GlowTile(glyph_name, palette, service_color(palette, glyph_name) or palette["muted"],
                             size=32, icon=17, radius=9)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.name = QLabel(name)
        self.name.setObjectName("RowTitle")
        self.detail = QLabel("")
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        col.addWidget(self.name)
        col.addWidget(self.detail)
        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.chevron = QLabel()
        self.chevron.setFixedSize(14, 14)
        lay.addWidget(self.icon, 0, Qt.AlignVCenter)
        lay.addLayout(col, 1)
        lay.addWidget(self.dot, 0, Qt.AlignVCenter)
        lay.addWidget(self.chevron, 0, Qt.AlignVCenter)
        self.apply_palette(palette)

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        self.icon.set_palette(palette, service_color(palette, self.glyph_name) or palette["muted"])
        self.chevron.setPixmap(glyph("chevron", palette.get("muted2", palette["muted"]), 14).pixmap(QSize(14, 14)))

    def set(self, detail: str, state: str | None, checked: str = "") -> None:
        """detail: the one quiet line; checked: when it was last checked (tooltip and screen readers)."""
        self.detail.setText(break_at_dots(detail))
        self.setToolTip(checked)
        self.setAccessibleName(f"{self.name.text()}: {detail}" + (f", {checked}" if checked else ""))
        if state in (None, "idle", "syncing"):  # nothing to flag: the row stays quiet
            self.dot.setStyleSheet("background: transparent;")
        else:
            self.dot.setStyleSheet(f"background: {state_color(self.c, state)}; border-radius: 4px;")

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(e)

    def enterEvent(self, e) -> None:  # noqa: N802 - the tile glows while the row is under the pointer
        self.icon.set_hot(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.icon.set_hot(self.hasFocus())
        super().leaveEvent(e)

    def focusInEvent(self, e) -> None:  # noqa: N802 - and while it has keyboard focus
        self.icon.set_hot(True)
        super().focusInEvent(e)

    def focusOutEvent(self, e) -> None:  # noqa: N802
        self.icon.set_hot(self.underMouse())
        super().focusOutEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(e)


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
    """Activity entry: glyph, the file name with what happened to it (and where) on a second muted line,
    and the time. Entries about a
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
        lay.setContentsMargins(4, 5, 4, 5)
        lay.setSpacing(12)
        self.tile = TintTile(32, 9)  # drawn smoothly (a style sheet's rounded corners are jagged)
        self.tile.setObjectName("FileTile")
        self.thumbed = False
        tl = QVBoxLayout(self.tile)
        tl.setContentsMargins(0, 0, 0, 0)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignCenter)
        tl.addWidget(self.icon)
        self.title_text, self.detail_text = split_activity(text, kind)
        self.text = QLabel()  # the file name (or the message), on one line; the rest is in the tooltip
        self.text.setObjectName("ActTitle")
        self.text.setMinimumWidth(0)
        self.text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.detail = QLabel(self)  # what happened, and where: "Deleted in iCloud · iCloud Drive/Downloads"
        # (a child from the start: shown without a parent it would flash up as a window of its own)
        self.detail.setObjectName("Hint")
        self.detail.setMinimumWidth(0)
        self.detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.detail.setVisible(bool(self.detail_text))
        self.when = QLabel(fmt_time(ts))
        self.when.setObjectName("ActWhen")
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self.text)
        col.addWidget(self.detail)
        lay.addWidget(self.tile, 0, Qt.AlignVCenter)
        lay.addLayout(col, 1)
        lay.addWidget(self.when, 0, Qt.AlignVCenter)
        if target or kind == "error":
            self.setFocusPolicy(Qt.StrongFocus)  # Tab reaches it; Enter or Space opens it
        self._set_title()
        self.apply_palette(palette)
        self.refresh()
        # every row the same height, with or without its second line: the list is a whole number
        # of rows tall and the wheel moves one row at a time
        self.text.ensurePolished()
        self.detail.ensurePolished()
        self.setFixedHeight(max(42, self.text.fontMetrics().height() + self.detail.fontMetrics().height() + 10))

    def _set_title(self) -> None:
        from PySide6.QtGui import QFontMetrics

        title = self.title_text + (f"  (×{self.count})" if self.count > 1 else "")
        self.text.ensurePolished()
        self.text.setText(QFontMetrics(self.text.font()).elidedText(title.replace(chr(10), " "), Qt.ElideMiddle, 236))
        if self.detail_text:
            self.detail.ensurePolished()
            self.detail.setText(QFontMetrics(self.detail.font()).elidedText(self.detail_text, Qt.ElideMiddle, 236))
        tip = [title] + ([self.detail_text] if self.detail_text else []) + ([self.target] if self.target else [])
        if self.kind == "error" and not self.target:
            tip.append(t("Click for details"))
        self.setToolTip(chr(10).join(tip))

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        if self.thumbed:
            return  # the photo itself is shown
        if self.kind == "error":
            name, color = "error", palette["err_text"]
        else:
            name = ACTIVITY_GLYPH.get(self.kind) or file_glyph(self.title_text if self.detail_text else "", self.kind)
            color = self._service_colour(palette)
            if color == service_color(palette, "note") and self.kind not in ("trash", "error"):
                name = "note"  # a note changed: the note glyph, not an arrow
        alpha = 34 if QColor(palette["bg"]).lightness() > 128 else 44
        self.tile.set_fill(tint(color, alpha))
        self.icon.setPixmap(glyph(name, color, 17).pixmap(QSize(17, 17)))

    def _service_colour(self, palette: dict[str, str]) -> str:
        """The colour of the service this entry is about (Photos pink, Drive blue, Notes amber...)."""
        where = f"{self.detail_text} {self.target}".lower()
        if "icloud drive" in where or self.message.lower().startswith("drive:"):
            key = "folder"
        elif "note" in where or "bilje" in where:
            key = "note"
        elif "bookmark" in where or "oznak" in where:
            key = "bookmark"
        elif Thumbs.wanted(self.target) or "photo" in where or "fotograf" in where:
            key = "photo"
        else:
            return palette["muted"]
        return service_color(palette, key) or palette["muted"]

    def set_thumb(self, img: QImage) -> None:
        """The photo this entry is about, in place of the icon."""
        if img.isNull():
            return
        self.thumbed = True
        self.tile.set_fill(None)
        self.icon.setPixmap(rounded(img, 32, 8, max(1.0, self.devicePixelRatioF())))

    def refresh(self) -> None:
        self.when.setText(_short_time(self.ts))
        self.when.setToolTip(fmt_time(self.ts))
        self.setAccessibleName(", ".join(x for x in (self.title_text, self.detail_text, self.when.text()) if x))

    def _activate(self) -> None:
        if self.target:
            reveal(self.target)
        elif self.kind == "error":
            self.errorClicked.emit(self)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and (self.target or self.kind == "error"):
            self._activate()
            return
        super().keyPressEvent(e)

    def same_as(self, kind: str, text: str, target: str = "") -> bool:
        return self.kind == kind and self.message == text and (target or "") == (self.target or "")

    def bump(self, ts: str, target: str = "") -> None:
        self.count += 1
        self.ts = ts
        self.target = target or self.target
        self._set_title()
        self.refresh()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802 - Qt naming
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self._activate()
        super().mouseReleaseEvent(e)


class Banner(QFrame):
    clicked = Signal()

    def __init__(self, kind: str, button: str) -> None:
        super().__init__()
        self.setObjectName({"warn": "Warn", "info": "Info"}.get(kind, "Err"))
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
    folderClicked = Signal(str)  # the folder of "photos" | "drive" | "notes"
    quitApp = Signal()
    openUpdate = Signal()

    WIDTH = 376

    def __init__(self, palette: dict[str, str]) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.c = palette
        self.setObjectName("Root")
        self.setWindowTitle(APP_NAME)
        self.setAccessibleName(APP_NAME)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(self.WIDTH)
        self.setFocusPolicy(Qt.StrongFocus)
        from .backdrop import glass_supported

        self.glass = glass_supported()  # Windows 11 with transparency effects: the desktop shows through
        if self.glass:
            self.setAttribute(Qt.WA_TranslucentBackground)
        self.state = "idle"
        self.paused = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QWidget()
        body.setObjectName("Root")
        self._body = body
        self._scroll = QScrollArea()  # a small screen scrolls the content; the buttons never leave it
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)  # as tall as the panel's own sizing says
        self._scroll.setFocusPolicy(Qt.NoFocus)
        self._scroll.setWidget(body)
        outer.addWidget(self._scroll, 1)
        root = QVBoxLayout(body)
        root.setContentsMargins(12, 12, 12, 8)
        root.setSpacing(8)

        # header
        head = QHBoxLayout()
        # the brand mark: the cloud in the accent colour on its tint, glowing under the pointer
        logo = LogoTile(36)  # the app's own icon, as on the taskbar
        self._brand = logo
        titles = QVBoxLayout()
        titles.setSpacing(0)
        app_title = QLabel(APP_NAME)
        app_title.setObjectName("PanelTitle")
        self.account = QLabel(t("Not signed in"))  # shown as the title's tooltip and in Settings
        self.account.setObjectName("Muted")
        self.account.hide()
        self._app_title = app_title
        status = QHBoxLayout()
        status.setSpacing(5)
        self.status_icon = QLabel()
        self.status_icon.setFixedSize(13, 13)
        self._spin = QTimer(self)  # turns the sync arrows next to "Syncing…" while a pass runs
        self._spin.setInterval(50)
        self._spin.timeout.connect(self._spin_tick)
        self._spin_angle = 0.0
        self._spin_base: QPixmap | None = None
        self.status_line = QLabel("")
        self.status_line.setObjectName("StatusLine")
        status.addWidget(self.status_icon, 0, Qt.AlignVCenter)
        status.addWidget(self.status_line, 1)
        titles.addWidget(app_title)
        titles.addLayout(status)
        self.gear = QToolButton()
        self.gear.setIcon(glyph("gear", palette["muted"]))
        self.gear.setIconSize(QSize(18, 18))
        self.gear.setFixedSize(34, 34)
        self.gear.setToolTip(t("Settings"))
        self.gear.setCursor(Qt.PointingHandCursor)
        self.gear.clicked.connect(self.openSettings)
        self.gear.setAccessibleName(t("Settings"))
        self.dot = StatusDot(palette)
        self.storage_ring = StorageRing(palette)
        self.storage_ring.hide()
        head.setSpacing(12)
        head.addWidget(logo)
        head.addLayout(titles, 1)
        self.dot.hide()  # the status line under the title says it now (kept for its state logic)
        head.addWidget(self.storage_ring, 0, Qt.AlignVCenter)
        from .notices import BellButton

        self.bell = BellButton(palette)  # this session's notifications (shown once the app attaches them)
        self.bell.clicked.connect(self.show_notices)
        self.bell.hide()
        self.notices_page = None
        self.thumbs = Thumbs(self)  # small pictures of the photos named in the activity list
        self.thumbs.ready.connect(self._thumb_ready)
        head.addWidget(self.bell, 0, Qt.AlignVCenter)
        head.addWidget(self.gear, 0, Qt.AlignVCenter)
        root.addLayout(head)

        # status card (when it says "Sign-in required", clicking it signs in)
        flyout = self

        class _Card(SoftCard):
            def mouseReleaseEvent(self, e) -> None:  # noqa: N802 - Qt naming
                if flyout.state == "auth" and e.button() == Qt.LeftButton:
                    flyout.login.emit()
                super().mouseReleaseEvent(e)

            def enterEvent(self, e) -> None:  # noqa: N802 - Qt naming
                if flyout.state == "auth":  # it can be clicked: it shows it, like the other rows
                    self.setProperty("hot", True)
                    self.style().unpolish(self)
                    self.style().polish(self)
                super().enterEvent(e)

            def leaveEvent(self, e) -> None:  # noqa: N802 - Qt naming
                if self.property("hot"):
                    self.setProperty("hot", False)
                    self.style().unpolish(self)
                    self.style().polish(self)
                super().leaveEvent(e)

        card = _Card()
        card.setObjectName("Card")
        self.status_card = card
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(8)
        card.setFixedHeight(72)  # one height in every state: the panel never moves
        row = QHBoxLayout()
        row.setSpacing(12)
        self.badge = StateBadge(palette)
        row.addWidget(self.badge, 0, Qt.AlignVCenter)
        txt = QVBoxLayout()
        txt.setSpacing(1)
        txt.addStretch(1)
        self.status_title = QLabel(STATE_TITLES["idle"])
        self.status_title.setObjectName("HeroTitle")
        self.status_text = TwoLineLabel("")  # two lines at most; the whole text in its tooltip
        self.status_text.setObjectName("HeroText")
        txt.addWidget(self.status_title)
        txt.addWidget(self.status_text)
        txt.addStretch(1)
        row.addLayout(txt, 1)
        self.review_btn = QPushButton(t("Review"))  # a large deletion waits: decided here, never by a card
        self.review_btn.setCursor(Qt.PointingHandCursor)
        self.review_btn.clicked.connect(self.reviewPending)
        self.review_btn.hide()
        row.addWidget(self.review_btn, 0, Qt.AlignVCenter)
        cl.addLayout(row)
        self._progress: float | None = None
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
        self.update_banner = Banner("info", t("Update"))
        self.update_banner.clicked.connect(self.openUpdate)
        self.update_banner.hide()
        root.addWidget(self.update_banner)

        # services
        services = SoftCard()
        services.setObjectName("Card")
        self.services_card = services
        sl = QVBoxLayout(services)
        sl.setContentsMargins(0, 0, 0, 2)  # (room for the card's edge and its shadow)
        sl.setSpacing(0)
        self.row_photos = ServiceRow("photo", t("Photos"), palette)
        self.row_drive = ServiceRow("folder", t("iCloud Drive"), palette)
        self.row_photos.clicked.connect(lambda: self.serviceClicked.emit("photos"))
        self.row_drive.clicked.connect(lambda: self.serviceClicked.emit("drive"))
        self.row_notes = ServiceRow("note", t("Notes"), palette)
        for row in (self.row_photos, self.row_drive, self.row_notes):
            row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)  # never squeezed below their text
        self.row_notes.clicked.connect(lambda: self.serviceClicked.emit("notes"))
        for i, row in enumerate((self.row_photos, self.row_drive, self.row_notes)):
            if i:
                sep = QFrame()
                sep.setObjectName("Sep")
                inset = QHBoxLayout()  # the line runs the card's full width (1.1 design)
                inset.setContentsMargins(0, 0, 0, 0)
                inset.addWidget(sep)
                sl.addLayout(inset)
            sl.addWidget(row)
        root.addWidget(services)

        self.storage_bar = StorageBar(palette)
        self.storage_bar.hide()
        root.addWidget(self.storage_bar)

        # activity
        sec = QHBoxLayout()
        sec.setContentsMargins(4, 6, 4, 0)
        lbl = QLabel(t("Recent activity"))
        lbl.setObjectName("ListHead")
        self.last_sync = QLabel("")
        self.last_sync.setObjectName("Hint")
        self.clear_btn = QPushButton(t("Clear"))
        self.clear_btn.setObjectName("ListLink")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.setToolTip(t("Clear the activity list. Your files are not touched."))
        self.clear_btn.clicked.connect(self._clear_activity)
        self.clear_btn.hide()
        sec.addWidget(lbl)
        sec.addStretch(1)
        sec.addWidget(self.clear_btn)
        self.last_sync.hide()  # each service row now shows its own "checked …" time
        root.addLayout(sec)
        self.list = RowScroll()  # one wheel notch, one row
        self.list.setWidgetResizable(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setFocusPolicy(Qt.NoFocus)  # its rows take the focus themselves
        self.list.setStyleSheet("QScrollBar:vertical { width: 6px; margin: 0; }")  # a slim bar, only when it scrolls
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
        self._list_pad = QWidget()  # what is left under the last whole row: the panel keeps one height
        self._list_pad.setFixedHeight(0)
        root.addWidget(self._list_pad)
        self.list.hide()

        # footer: one primary button, then icon-only buttons (each with a tooltip and an accessible name)
        bar = QFrame()
        bar.setObjectName("BottomBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 10, 12, 10)
        bl.setSpacing(8)

        def bar_button(text: str, slot, tip: str = "", kind=QToolButton) -> QToolButton:
            b = kind()
            b.setObjectName("Bar")
            b.setToolButtonStyle(Qt.ToolButtonIconOnly)
            b.setIconSize(QSize(18, 18))
            b.setText(text)
            b.setToolTip(tip or text)
            b.setAccessibleName(tip or text)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            bl.addWidget(b)
            return b

        self.checked_label = QLabel("")  # "Last checked / 14:32"
        self.checked_label.setObjectName("Legend")
        self.checked_label.setMinimumWidth(0)
        self.checked_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bl.addWidget(self.checked_label, 1)
        self.sync_btn = bar_button(t("Sync now"), self._on_sync_clicked, kind=CenteredButton)
        self.sync_btn.setProperty("primary", True)
        self.sync_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.sync_btn.setIconSize(QSize(16, 16))
        self.sync_btn.setFixedSize(132, 34)  # Stop takes Sync now's place: the same size, the same spot
        self.open_btn = bar_button(t("Open folder"), self._open_folder_menu)  # (iCloud Drive's, when it syncs)
        self.folder_btn = bar_button(t("Find My"), self.findMy, t("Find My devices…"))  # (name kept: other code refers to it)
        self.more_btn = bar_button(t("More"), self._more_menu)
        # Kept as objects (other code and the tests use them); their actions live in the "More" menu now.
        self.web_btn = bar_button(t("iCloud.com"), lambda: _open_url("https://www.icloud.com"))
        self.pause_btn = bar_button(t("Pause"), self.togglePause)
        self.web_btn.hide()
        self.pause_btn.hide()
        self._bottom = bar
        outer.addWidget(bar)
        self._bar_icons()
        self.set_paused(False)
        self._glass_style()

    def _glass_style(self) -> None:
        """Over Windows' acrylic: everything see-through, a light tint over the whole panel."""
        if not self.glass:
            self.setStyleSheet("")
            return
        from .backdrop import glass_colours
        from .theme import accent_edges, accent_fill

        g = glass_colours(self.c, QColor(self.c["bg"]).lightness() < 128)
        self.setStyleSheet(
            f"QWidget#Root {{ background: transparent; }}"
            f"QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}"
            f"SoftCard {{ qproperty-fill: {g['card']}; qproperty-stroke: {g['stroke']}; qproperty-shade: {g['shade']}; }}"
            f"SoftCard[hot=\"true\"] {{ qproperty-fill: {g['hover']}; }}"
            f"QFrame#BottomBar {{ background: {g['foot']}; border-top: 1px solid {g['stroke']}; }}"
            f"QFrame#Row:hover, QFrame#ActRow[link=\"true\"]:hover {{ background: {g['hover']}; }}"
            f"QToolButton#Bar {{ background: {g['control']}; border-color: {g['stroke2']}; }}"
            f"QToolButton#Bar[wide=\"true\"] {{ background: {g['control']}; border-color: {g['stroke2']}; }}"
            # the main button: the accent, see-through enough for the blur to show in it
            f"QToolButton#Bar[primary=\"true\"] {{ background: {accent_fill(self.c['btn'], 0.68)};"
            f" {accent_edges(self.c['btn'], 0.75)} }}"
            f"QToolButton#Bar[primary=\"true\"]:hover {{ background: {accent_fill(self.c['btn_hover'], 0.82)};"
            f" {accent_edges(self.c['btn_hover'], 0.85)} }}"
            f"QFrame#Sep {{ background: {g['stroke']}; }}")

    def paintEvent(self, e) -> None:  # noqa: N802
        if self.glass:
            # Windows draws the blur and its tint behind. A see-through window lets the mouse fall
            # through wherever it is fully transparent (the wheel scrolled the browser behind the
            # panel): an all but invisible fill keeps every pixel the panel's own.
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(0, 0, 0, 1))
            return
        super().paintEvent(e)

    def _more_menu(self) -> None:
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.addAction(glyph("globe", self.c["muted"]), t("Open iCloud.com"), self.web_btn.click)
        pause = menu.addAction(glyph("play" if self.paused else "pause", self.c["muted"]),
                               t("Resume syncing") if self.paused else t("Pause all syncing"), self.togglePause.emit)
        pause.setEnabled(self.state not in ("auth", "setup"))
        menu.exec(self.more_btn.mapToGlobal(self.more_btn.rect().topRight())
                  - QPoint(menu.sizeHint().width(), menu.sizeHint().height() + 4))

    def _bar_icons(self) -> None:
        self.open_btn.setIcon(glyph("folder", self.c["text"]))
        self.web_btn.setIcon(glyph("globe", self.c["text"]))
        self.folder_btn.setIcon(glyph("pin", self.c["text"]))
        self.more_btn.setIcon(glyph("more", self.c["text"]))

    def _open_folder_menu(self) -> None:
        """Opens the iCloud Drive folder straight away (the photos folder while Drive isn't synced):
        Photos and Notes have their own windows from their rows."""
        s = getattr(self, "_stats", {}) or {}
        self.folderClicked.emit("drive" if s.get("drive_enabled") else "photos")

    def apply_palette(self, palette: dict[str, str]) -> None:
        """Called when Windows switches light/dark: re-tint the hand-drawn icons."""
        self.c = palette
        self._glass_style()
        self.gear.setIcon(glyph("gear", palette["muted"]))
        self.bell.apply_palette(palette)
        if self.notices_page is not None:
            self.notices_page.apply_palette(palette)
        self._brand.set_palette(palette, palette["accent_fg"])
        self.dot.c = palette
        self.badge.set_palette(palette)
        self.storage_ring.c = palette
        self.storage_ring.update()
        self.storage_bar.apply_palette(palette)
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
        log.info("Panel: main button clicked (state %s)", self.state)
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
        self.pause_btn.setIcon(glyph("play" if paused else "pause", self.c["muted"]))
        self.pause_btn.setText(t("Resume") if paused else t("Pause"))
        self.pause_btn.setToolTip(t("Resume syncing") if paused else t("Pause syncing"))
        self.pause_btn.setAccessibleName(self.pause_btn.toolTip())

    def _dot_state(self) -> str:
        return "attention" if self.state == "idle" and getattr(self, "_pending_text", "") else self.state

    def set_state(self, state: str, text: str) -> None:
        if state == "syncing" and self.state != "syncing":
            from datetime import datetime as _dt

            self._started = _dt.now().strftime("%H:%M")  # "Started at 09:42" in the footer
        self.state = state
        if hasattr(self, "checked_label"):
            self._footer_time()
        self.dot.set_state(self._dot_state())
        self.status_title.setText(STATE_TITLES.get(state, state))
        self.status_text.setText("" if text.rstrip(".…") == STATE_TITLES.get(state, "").rstrip(".…")
                                 else short_text(text, 260 if state in ("error", "auth") else 110))
        self.status_text.setVisible(bool(self.status_text.text()))
        self._dot_tip()
        self.status_card.setVisible(True)  # always there, one height: the panel never jumps
        self.status_card.setCursor(Qt.PointingHandCursor if state == "auth" else Qt.ArrowCursor)
        syncing = state == "syncing"
        self.bar.hide()  # the badge's ring shows the progress
        self.bar_label.hide()
        if not syncing:
            self._progress = None
        self._render_hero()
        self._render_services()
        self.auth_banner.hide()  # the status card and the main button already say "Sign in"
        if state == "auth":
            self.auth_banner.text.setText(t("Signing in to iCloud is required to sync."))
        name, label = {"syncing": ("close", t("Stop")), "setup": ("folder", t("Set up")),
                       "auth": ("cloud", t("Sign in")), "paused": ("play", t("Resume"))}.get(
            state, ("sync", t("Sync now")))
        self.sync_btn.setText(label)
        self.sync_btn.setToolTip(t("Resume syncing") if state == "paused" else label)
        self.sync_btn.setAccessibleName(self.sync_btn.toolTip())
        quiet = state == "syncing"  # Stop is a quiet button in the same place; the rest is the accent
        if bool(self.sync_btn.property("wide")) != quiet:
            self.sync_btn.setProperty("primary", not quiet)
            self.sync_btn.setProperty("wide", quiet)
            self.sync_btn.style().unpolish(self.sync_btn)
            self.sync_btn.style().polish(self.sync_btn)
        self.sync_btn.setIcon(glyph("stop" if quiet else name, self.c["text"] if quiet else self.c["accent_text"]))
        self.sync_btn.set_glyph("stop" if quiet else name)  # drawn centred with the text, in its colour
        self.sync_btn.setEnabled(True)  # paused: this button is the way to continue
        self._status_line()
        self._size_list()

    def _render_hero(self) -> None:
        """The status card: its badge, and a quiet second line when all is well."""
        if self.state != "syncing" and self.status_text.objectName() != "HeroText":
            self.status_text.setObjectName("HeroText")
            self.status_text.style().unpolish(self.status_text)
            self.status_text.style().polish(self.status_text)
        state = self._dot_state()
        self.badge.set_state(state, self._progress if self.state == "syncing" else None)
        pending = state == "attention"
        self.review_btn.setVisible(pending)
        if pending:
            self.status_title.setText(t("Deletion needs your OK"))
            self.status_text.setText(getattr(self, "_pending_short", "") or self._pending_text)
            self.status_text.setToolTip(self._pending_text)
            self.status_text.show()
        elif self.state == "idle":
            self.status_title.setText(t("Up to date"))  # (the line under the name says "in sync")
            s = getattr(self, "_stats", {}) or {}
            names = [t("Photos")] + ([t("iCloud Drive")] if s.get("drive_enabled") else []) + \
                ([t("Notes")] if s.get("notes_enabled") else [])
            if len(names) == 1:
                self.status_text.setText(t("Your photos are in step with iCloud."))
            else:
                self.status_text.setText(t("{names} and {last} are in step with iCloud.",
                                           names=", ".join(names[:-1]), last=names[-1]))
            self.status_text.show()

    def _status_line(self) -> None:
        state = self._dot_state()
        title = STATE_TITLES.get(self.state, self.state)
        if state == "attention":
            title = t("Waiting for your OK")
        icon = {"idle": "check", "syncing": "sync", "paused": "pause", "error": "error", "auth": "error",
                "offline": "cloud", "setup": "folder", "attention": "error"}.get(state, "check")
        color = state_color(self.c, state)
        pix = _status_dot(color, max(1.0, self.devicePixelRatioF()))
        self.status_icon.setPixmap(pix)
        if False:  # (the dot doesn't spin; the badge in the status card shows the progress)
            self._spin_base = pix
            if not self._spin.isActive():
                self._spin.start()
        else:
            self._spin.stop()
            self._spin_base = None
            self._spin_angle = 0.0
        self.status_line.setText(title)
        self.status_line.setStyleSheet(f"color: {color};")

    def _spin_tick(self) -> None:
        if self._spin_base is None or not self.isVisible():
            return  # hidden panel: nothing to draw
        self._spin_angle = (self._spin_angle + 18) % 360
        dpr = self._spin_base.devicePixelRatio()
        canvas = QPixmap(round(13 * dpr), round(13 * dpr))
        canvas.setDevicePixelRatio(dpr)
        canvas.fill(Qt.transparent)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(6.5, 6.5)
        p.rotate(self._spin_angle)
        p.translate(-6.5, -6.5)
        p.drawPixmap(0, 0, self._spin_base)
        p.end()
        self.status_icon.setPixmap(canvas)
        self.badge.spin(self._spin_angle * 1.6)

    def set_progress(self, done: int, total: int, text: str) -> None:
        if self.state != "syncing" or total <= 0:
            return
        self.bar.setValue(int(1000 * min(done, total) / total))
        self._progress = min(done, total) / total
        self.badge.set_state("syncing", self._progress)
        if self.status_text.objectName() != "HeroMono":
            self.status_text.setObjectName("HeroMono")  # the numbers: "812 of 1,284 · 4.2 MB/s · ~3 min"
            self.status_text.style().unpolish(self.status_text)
            self.status_text.style().polish(self.status_text)
        if text and " · " in text:  # "Downloading photos · 812 of 1,284": the action above, the numbers below
            head, _, rest = text.partition(" · ")
            self.status_title.setText(head)
            text = rest
        text = break_at_dots(text) if text else text
        if text and text != self.status_text.text():
            lines_before = self.status_text.heightForWidth(max(1, self.status_text.width()))
            self.status_text.setText(text)  # one line of text: the status line itself
            self.status_text.show()
            if self.isVisible() and self.status_text.heightForWidth(max(1, self.status_text.width())) != lines_before:
                QTimer.singleShot(0, self._size_list)  # wraps to more or fewer lines: fit again
            self.dot.setToolTip(STATE_TITLES.get(self.state, self.state) + "\n" + text.replace(chr(0xA0), " "))


    def set_update(self, version: str | None) -> None:
        """A newer Skyfold is out (None: not, or already installed)."""
        if version:
            self.update_banner.text.setText(t("Skyfold {version} is available.", version=version))
        self.update_banner.setVisible(bool(version))
        if self.isVisible():
            self._size_list()

    def set_stats(self, stats: dict) -> None:
        self._stats = stats
        st = stats.get("storage")
        if st and st.get("total"):
            used, total = st["used"], st["total"]
            self.storage_ring.set_fraction(used / total)
            lines = [t("iCloud storage"),
                     t("{used} of {total}", used=storage_text(used), total=storage_text(total)),
                     ""]
            lines += [f"{p[0]}: {storage_text(p[1])}" for p in st.get("parts", [])]
            self.storage_ring.setToolTip("\n".join(lines).strip())
            self.storage_ring.setAccessibleName(t("iCloud storage") + f" {round(100 * used / total)}%")
            self.storage_bar.set(used, total, list(st.get("parts", [])))
            self.storage_bar.show()
        else:
            self.storage_bar.hide()
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

        def checked(ts) -> str:
            return t("checked {when}", when=fmt_time(ts)) if ts else ""

        if not when:
            detail += " · " + t("first sync in progress")
        waiting = {"auth": t("Waiting for sign-in"), "setup": t("Not set up yet")}.get(self.state)
        if waiting and not when:
            detail = waiting
        self.row_photos.set(detail, "idle" if self.state == "idle" else self.state, checked(when))
        self.folder_btn.setEnabled(self.state not in ("auth", "setup"))
        self.pause_btn.setEnabled(self.state not in ("auth", "setup"))
        if s.get("drive_enabled"):
            when = s.get("drive_last_sync")
            detail = t("{files} files · {size}", files=n(s.get("drive_files", 0)), size=fmt_bytes(s.get("drive_bytes", 0)))
            if not when:
                detail += " · " + t("first sync in progress")
            if waiting and not when:
                detail = waiting
            self.row_drive.set(detail, "idle" if self.state == "idle" else self.state, checked(when))
        else:
            self.row_drive.set(t("Off · click to turn on"), None)
        if s.get("notes_enabled"):
            when = s.get("notes_last_sync")
            detail = t("{n} notes", n=n(s.get("notes_count", 0)))
            if not when:
                detail += " · " + t("first read pending")
            if waiting and not when:
                detail = waiting
            self.row_notes.set(detail, "idle" if self.state == "idle" else self.state, checked(when))
        else:
            self.row_notes.set(t("Off · click to turn on"), None)

    def _footer_time(self) -> None:
        """Beside the main button: when Skyfold last checked, or, while syncing, when this pass began."""
        ts = getattr(self, "_last_sync_ts", None)
        started = getattr(self, "_started", None)
        if self.state == "syncing" and started:
            self.checked_label.setText(t("Started") + "\n" + t("at {time}", time=started))
        else:
            self.checked_label.setText(t("Last checked") + "\n" + fmt_time(ts) if ts else "")

    def refresh_times(self) -> None:
        ts = getattr(self, "_last_sync_ts", None)
        self.last_sync.setText(t("Last check: {when}", when=fmt_time(ts)) if ts else "")
        self._footer_time()
        self.checked_label.setToolTip(self.last_sync.text())
        if self.state == "idle":
            self._render_hero()
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
            self._pending_short = t(" and ").join(parts)
            self.pending_banner.text.setText(self._pending_text)  # (said by the status card now)
        self.dot.set_state(self._dot_state())
        self._dot_tip()
        self._status_line()
        if not pending and self.state in STATE_TITLES:
            self.status_title.setText(STATE_TITLES[self.state])
        self._render_hero()
        self._size_list()

    MAX_ROWS_VISIBLE = 6
    TARGET_HEIGHT = 624  # the panel's height (less on a small screen): the same in every state
    MIN_LIST_HEIGHT = 104  # the panel keeps a calm, steady height instead of jumping with each entry
    HELD_LIST_MIN = 56  # while open, the list may shrink this far to keep the panel's height

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
        if Thumbs.wanted(target):
            self.thumbs.request(target)
        while self._rows.count() - 1 > 60:  # keep the newest 60 (the last item is the stretch)
            old = self._rows.takeAt(self._rows.count() - 2).widget()
            old.deleteLater()
        self.list.show()
        self.empty.hide()
        self.clear_btn.show()
        QTimer.singleShot(0, self._size_list)  # after styles are applied, so heights are exact

    def _thumb_ready(self, target: str, img) -> None:
        for row in self._activity_rows():
            if row.target == target:
                row.set_thumb(img)

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
        self._fit_width()
        self.services_card.show()
        self.list.setFixedHeight(max(height, self.MIN_LIST_HEIGHT if rows else 1) + 2)
        # One height in every state: the list (or the empty-list line) fills what the rest leaves.
        self._list_pad.setFixedHeight(0)
        other = self._needed_height() - (self.list.height() if self.list.isVisible() else self.empty.height())
        fill = max(self.MIN_LIST_HEIGHT, self.TARGET_HEIGHT - other)
        # whole rows only, never one cut in half: the list is a whole number of rows tall (measured,
        # since the row height follows the font and the display scale)
        if rows:
            self._row_step = max(30, rows[0].sizeHint().height())
        step = getattr(self, "_row_step", 42)
        shown = max(2, round(fill / step)) * step  # (the nearest whole number of rows: 3, not 2.9 cut to 2)
        if self.list.isVisible():
            self.list.setFixedHeight(shown)
        else:
            self.empty.setFixedHeight(shown)  # the same height with or without entries
        room = self._limit() - (self._needed_height() - self.list.height())
        if self.list.height() > room:  # the list gives way first; below that the panel content scrolls
            self.list.setFixedHeight(max(room, min(self.list.height(), 120)))
        self._fit()

    def refresh_activity_times(self) -> None:
        for w in self._activity_rows():
            w.refresh()

    # placement -----------------------------------------------------------------
    def _screen(self):
        """The screen the panel opens on: the tray icon's, not wherever the window was last."""
        anchor = getattr(self, "_anchor", None)
        screen = QGuiApplication.screenAt(anchor.center()) if anchor else None
        return screen or self.screen() or QGuiApplication.primaryScreen()

    def _limit(self) -> int:
        return self._screen().availableGeometry().height() - 24

    def _fit_width(self) -> None:
        """At most 376 px, never wider than the screen (e.g. a small display at 225 %)."""
        w = max(200, min(Flyout.WIDTH, self._screen().availableGeometry().width() - 24))
        if w != self.WIDTH or self.width() != w:
            self.WIDTH = w
            self.setFixedWidth(w)
            # a very narrow screen: the primary button keeps its icon only (its tooltip says the rest)
            self.sync_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if w >= 300 else Qt.ToolButtonIconOnly)

    def _needed_height(self) -> int:
        lay = self._body.layout()
        lay.activate()
        body = lay.totalHeightForWidth(self.WIDTH) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        bar = self._bottom.layout()
        bar.activate()
        return body + bar.totalSizeHint().height()

    def _fit(self) -> None:
        self._fit_width()
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
        screen = self._screen()
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
        if anchor is not None and anchor.isValid() and not anchor.isEmpty():
            self._anchor = anchor  # sized for the screen it opens on
        self.refresh_times()
        self.refresh_activity_times()
        self._size_list()
        self._place(anchor if anchor and anchor.isValid() and not anchor.isEmpty() else None)
        if self.glass:
            from .backdrop import blur_behind, glass_colours

            dark = QColor(self.c["bg"]).lightness() < 128
            r, g, b, a = (int(x) for x in glass_colours(self.c, dark)["tint"][5:-1].split(","))
            blur_behind(self, QColor(r, g, b, a))  # Windows blurs the desktop behind the panel and tints it
        self.show()
        round_corners(self)
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.OtherFocusReason)  # the panel itself, not its first button
        self._held = None
        QTimer.singleShot(0, self._settle)  # final size known: keep it on screen

    def _settle(self) -> None:
        self._fit()
        self._place()
        self._held = self.height() if self.isVisible() else None  # kept while the panel is open

    def hideEvent(self, e) -> None:  # noqa: N802
        self._held = None  # opened again: its natural height
        self.show_home()  # opened again: the panel itself, not the notifications
        super().hideEvent(e)

    # notifications ----------------------------------------------------------------
    def attach_notices(self, log, resolve) -> None:
        """Show the bell for this session's notifications (a NoticeLog) and their page; resolve(key)
        gives a notification button's (label, action, applies now)."""
        from .notices import NoticesPage

        page = NoticesPage(log, self.c, resolve)
        page.hide()
        page.back.connect(self.show_home)
        self.layout().insertWidget(0, page, 1)
        self.notices_page = page
        self._notices = log
        log.on_change(lambda: self.bell.set_count(log.unseen))
        self.bell.set_count(log.unseen)
        self.bell.show()

    def show_notices(self) -> None:
        """The notifications page in place of the panel's content; the panel keeps its size."""
        if self.notices_page is None:
            return
        self._scroll.hide()
        self._bottom.hide()
        self.notices_page.show()
        self._notices.mark_seen()
        self.notices_page.setFocus(Qt.OtherFocusReason)

    def show_home(self) -> None:
        if self.notices_page is None or not self.notices_page.isVisible():
            return
        self.notices_page.hide()
        self._scroll.show()
        self._bottom.show()
        self.setFocus(Qt.OtherFocusReason)

    def event(self, e) -> bool:  # noqa: N802
        if e.type() == QEvent.WindowDeactivate:
            self._hidden_at = time.monotonic()
            self.hide()
        return super().event(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key_Escape and self.notices_page is not None and self.notices_page.isVisible():
            self.show_home()  # Escape on the notifications goes back first
        elif e.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)
