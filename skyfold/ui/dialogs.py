"""Sign-in, settings and deletion-confirmation dialogs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import logging
import time
from typing import Any

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import (QColor, QFont,
                           QPainter, QPalette, QPen, QPixmap)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QListView,
    QListWidgetItem,
    QStyledItemDelegate,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)

from .. import auth
from ..config import APP_IN_CLOUD, APP_NAME, FOLDER_STRUCTURES, PRIVATE_DIR, VERSION, Settings, check_photos_dir, default_photos_dir, \
    starts_with_windows
from ..i18n import LANGUAGES, language, t
from ..safelog import brief, describe
from .icons import app_icon, draw_logo, glyph
from .panel_parts import SoftCard
from .safe_text import plain_tip

log = logging.getLogger(__name__)


# async helper ------------------------------------------------------------------
class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


_EPOCH = [0]  # raised on Quit: tasks made before that never start


def stop_tasks() -> None:
    """Quit: background tasks still waiting for a free thread are dropped when their turn comes."""
    _EPOCH[0] += 1


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = _Signals()
        self.epoch = _EPOCH[0]

    def run(self) -> None:
        if self.epoch != _EPOCH[0]:
            self.fn = None
            return  # the app is quitting
        try:
            result = self.fn()
        except Exception as exc:  # noqa: BLE001
            log.warning("Background request failed: %s", describe(exc))
            self.signals.failed.emit(brief(exc))
        else:
            self.signals.done.emit(result)
        finally:
            self.fn = None  # release captured passwords/codes as soon as the request ends


class _Relay(QObject):
    """Created on the UI thread; its slots therefore always run on the UI thread.

    Connecting a pool-thread signal straight to a plain function would run that function
    in the pool thread, and touching widgets from there freezes the app.
    """

    def __init__(self, on_done: Callable[[Any], None], on_fail: Callable[[str], None]) -> None:
        super().__init__()
        self._on_done = on_done
        self._on_fail = on_fail

    @Slot(object)
    def done(self, result: Any) -> None:
        try:
            self._on_done(result)
        except Exception as exc:  # a UI callback must release the busy state too
            log.error("Background result callback failed: %s", describe(exc))
            self.failed(brief(exc))

    @Slot(str)
    def failed(self, msg: str) -> None:
        try:
            self._on_fail(msg)
        except Exception as exc:
            log.error("Background failure callback failed: %s", describe(exc))


def run_async(fn: Callable[[], Any], on_done: Callable[[Any], None], on_fail: Callable[[str], None]) -> _Task:
    task = _Task(fn)
    task.setAutoDelete(False)  # we keep a reference; Qt must not delete it under us
    task.relay = _Relay(on_done, on_fail)
    task.signals.done.connect(task.relay.done, Qt.QueuedConnection)
    task.signals.failed.connect(task.relay.failed, Qt.QueuedConnection)
    try:
        QThreadPool.globalInstance().start(task)
    except Exception as exc:
        task.fn = None
        log.error("Background request could not start: %s", describe(exc))
        task.relay.failed(brief(exc))
    return task


def _open_url(url: str) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl(url))


class _ReleaseNotes(QTextBrowser):
    """The "What's new" text: links open in the browser when clicked, but nothing is loaded by
    itself (no picture from the internet or from a network share)."""

    def loadResource(self, kind, url):  # noqa: N802
        # An empty answer is not enough: Qt then loads the file itself (a picture on a network share
        # makes Windows connect to that computer). Every picture is a blank pixel, nothing else loads.
        from PySide6.QtGui import QImage, QTextDocument

        if kind == QTextDocument.ImageResource:
            blank = QImage(1, 1, QImage.Format_ARGB32)
            blank.fill(0)
            return blank
        return b""

    def style_notes(self, palette: dict[str, str]) -> None:
        """Readable as in the 1.1 design: body text 14 px with air between the lines, headings 600
        in Segoe UI Variable Display (an all-capitals heading is set in sentence case), links in the
        accent; on the card under it (no frame or background of its own)."""
        from PySide6.QtGui import QTextBlockFormat, QTextCharFormat, QTextCursor

        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        doc = self.document()
        doc.setDocumentMargin(12)
        cur = QTextCursor(doc)
        cur.beginEditBlock()
        block = doc.begin()
        first = True
        while block.isValid():
            level = block.blockFormat().headingLevel()
            text = block.text()
            if level and len(text) > 3 and text.isupper():  # sentence case, as the design's headings
                c2 = QTextCursor(block)
                c2.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
                c2.insertText(text[0] + text[1:].lower(), c2.charFormat())
            bf = QTextBlockFormat()
            bf.setLineHeight(130 if level else 145, 1)  # (1: proportional height)
            if level:
                bf.setTopMargin(0 if first else 14)
                bf.setBottomMargin(4)
            else:
                bf.setBottomMargin(4)
            c1 = QTextCursor(block)
            c1.mergeBlockFormat(bf)
            c1.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            cf = QTextCharFormat()
            if level:
                cf.setFontFamilies(["Segoe UI Variable Display", "Segoe UI"])
                cf.setFontPointSize({1: 15, 2: 12}.get(level, 11.25))  # 20 / 16 / 15 px
                cf.setFontWeight(QFont.DemiBold)
                cf.setProperty(QTextCharFormat.FontSizeAdjustment, 0)
            else:
                cf.setFontPointSize(10.5)  # 14 px
            c1.mergeCharFormat(cf)
            first = False
            block = block.next()
        # links in the accent (their own colour, whatever the theme)
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid() and frag.charFormat().isAnchor():
                    c3 = QTextCursor(doc)
                    c3.setPosition(frag.position())
                    c3.setPosition(frag.position() + frag.length(), QTextCursor.KeepAnchor)
                    lf = QTextCharFormat()
                    lf.setForeground(QColor(palette["accent_fg"]))
                    c3.mergeCharFormat(lf)
                it += 1
            block = block.next()
        cur.endEditBlock()


def _show_log() -> None:
    import os
    import subprocess

    from ..config import LOG_FILE

    if LOG_FILE.exists():
        subprocess.Popen([os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe"), "/select,", str(LOG_FILE)])
    else:
        _open_url(LOG_FILE.parent.as_uri())


def _fit_screen(w: int, h: int) -> tuple[int, int]:
    """A window size that fits the screen (e.g. a 1366x768 laptop at 125 %)."""
    from PySide6.QtGui import QGuiApplication

    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return w, h
    avail = screen.availableGeometry()
    return min(w, int(avail.width() * 0.92)), min(h, int(avail.height() * 0.88))


def _logo(size: int) -> QLabel:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    draw_logo(p, size * 2)
    p.end()
    pm.setDevicePixelRatio(2)
    lbl = QLabel()
    lbl.setPixmap(pm)
    lbl.setAlignment(Qt.AlignCenter)
    return lbl


def _palette_of(widget) -> dict[str, str]:
    """The palette a window was given (its own or a parent's), else the one the app shows now."""
    w = widget
    while w is not None:
        c = getattr(w, "c", None)
        if isinstance(c, dict) and "bg" in c and "accent" in c:
            return c
        w = w.parentWidget() if isinstance(w, QWidget) else None
    return _current_palette()


def _tile_pixmap(icon, c: dict[str, str], dpr: float = 2.0) -> QPixmap:
    """The message's sign as in the 1.1 design: a rounded tile tinted with the state's colour (warning:
    amber, error: red, question and information: the accent) and its line icon in that colour."""
    colour = {QMessageBox.Warning: c["warn"], QMessageBox.Critical: c["err"]}.get(icon, c["accent_fg"])
    size = 40
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    tint = QColor(colour)
    tint.setAlphaF(0.14 if QColor(c["bg"]).lightness() > 128 else 0.20)
    p.setPen(Qt.NoPen)
    p.setBrush(tint)
    p.drawRoundedRect(QRectF(0, 0, size, size), 10, 10)
    if icon == QMessageBox.Question:  # (the icon set has no question mark: drawn as text)
        f = QFont()
        f.setFamilies(["Segoe UI Variable Display", "Segoe UI"])
        f.setPixelSize(22)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(colour))
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "?")
    else:
        name = "info" if icon in (QMessageBox.Information, QMessageBox.NoIcon) else "error"
        glyph(name, colour, 22).paint(p, 9, 9, 22, 22)
    p.end()
    return pm


def style_message_box(box: QMessageBox, kind=None) -> None:
    """A QMessageBox in the 1.1 design, still a QMessageBox (its answers, roles and Escape work as
    before): 14 px text, the sign on a tile in the state's colour, the main answer highlighted and
    on the right, the buttons in a band along the bottom. kind: the box's icon (default: its own)."""
    c = _palette_of(box.parentWidget()) if box.parentWidget() is not None else _current_palette()
    kind = box.icon() if kind is None else kind
    primary = box.defaultButton() or (box.buttons()[0] if len(box.buttons()) == 1 else None)
    if primary is not None:
        primary.setObjectName("Primary")
    for b in box.buttons():
        b.setMinimumWidth(96)
        b.setCursor(Qt.PointingHandCursor)
    box.setObjectName("Message")
    box.setStyleSheet(
        # button-layout 3: the main answer on the right ("No  Yes"), as in Windows 11's own dialogs
        f"QMessageBox#Message {{ background: {c['bg']}; }}"
        "QMessageBox#Message QLabel#qt_msgbox_label { font-size: 10.5pt; min-width: 300px; max-width: 440px; }"
        f"QMessageBox#Message QLabel#qt_msgbox_informativelabel {{ font-size: 9.25pt; color: {c.get('muted2', c['muted'])}; }}")
    from PySide6.QtWidgets import QDialogButtonBox

    buttons = box.findChild(QDialogButtonBox)
    if buttons is not None:  # (set on the button box itself: a rule from the box loses to the app's sheet)
        buttons.setStyleSheet("QDialogButtonBox { button-layout: 3; }")
    label = box.findChild(QLabel, "qt_msgboxex_icon_label")
    if label is not None:  # the sign drawn in the design (box.icon() keeps telling what it is)
        label.setPixmap(_tile_pixmap(kind, c, max(2.0, box.devicePixelRatioF())))
        label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        label.setContentsMargins(0, 2, 8, 0)
    else:
        box.setIconPixmap(_tile_pixmap(kind, c))
    lay = box.layout()
    if lay is not None:
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setVerticalSpacing(18)


def _box(parent, icon, title: str, text: str, yes_no: bool) -> int:
    """QMessageBox with the buttons in the app's language (Qt's own Yes/No/OK stay English), in the
    1.1 design (style_message_box)."""
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.PlainText)  # (messages name files and folders from iCloud: never HTML)
    box.setText(text)
    if yes_no:
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.button(QMessageBox.Yes).setText(t("Yes"))
        box.button(QMessageBox.No).setText(t("No"))
        box.setDefaultButton(QMessageBox.Yes)
    else:
        box.setStandardButtons(QMessageBox.Ok)
        box.button(QMessageBox.Ok).setText(t("OK"))
        box.setDefaultButton(QMessageBox.Ok)
    style_message_box(box, icon)
    return box.exec()


def ask(parent, title: str, text: str) -> int:
    """Returns QMessageBox.Yes or QMessageBox.No."""
    return _box(parent, QMessageBox.Question, title, text, True)


def inform(parent, title: str, text: str) -> None:
    _box(parent, QMessageBox.Information, title, text, False)


def warn(parent, title: str, text: str) -> None:
    _box(parent, QMessageBox.Warning, title, text, False)


def _label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    lbl = QLabel()
    lbl.setTextFormat(Qt.PlainText)  # (names from iCloud are never read as HTML; markup opts in itself)
    lbl.setText(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


def _as_control(widget: QWidget, kind: str) -> None:
    """Marks a card that works like a button / radio button / group. Screen readers get its name and
    description (set by the caller) and Enter/Space work on it; there is no accessibility interface of
    our own: Qt owns and deletes those, and one written in Python could crash Skyfold."""
    widget.setProperty("a11y", kind)


class _Group(QWidget):
    """Controls that belong together (a class of its own: Qt answers for a plain QWidget itself)."""


def _checked_changed(widget: QWidget) -> None:
    """A radio card was chosen or unchosen: its description says so ("Chosen"), for screen readers."""
    widget.setAccessibleDescription(t("Chosen") if widget.property("selected") else "")


class _Dialog(QDialog):
    GLASS = False  # a window that shows Windows 11's material behind it (when Windows allows it)
    # acrylic: what is behind the window shows through, blurred, as in the 1.1 design and the tray
    # panel (Mica only tints with the wallpaper and looked like a plain colour)
    MATERIAL = "acrylic"

    def __init__(self, title: str) -> None:
        # A normal top-level window: own taskbar button (so it can be minimised and found again),
        # and it goes behind other windows like any app instead of floating on top.
        super().__init__(None, Qt.Window | Qt.WindowTitleHint | Qt.WindowMinimizeButtonHint
                         | Qt.WindowCloseButtonHint)
        self.setWindowTitle(title)
        self.setWindowIcon(app_icon())
        self.setObjectName("Root")
        self.glass = False
        if self.GLASS:
            from .backdrop import glass_supported

            # Windows 11 with transparency effects on: the window's own material shows through it
            # (the wallpaper's tint, as in Windows' own Settings); otherwise the theme's plain colour
            self.glass = glass_supported()
            if self.glass:
                self.setAttribute(Qt.WA_TranslucentBackground)

    def _dark(self) -> bool:
        return QColor(getattr(self, "c", {}).get("bg", "#202020")).lightness() < 128

    def _material(self) -> None:
        from .backdrop import apply_backdrop

        apply_backdrop(self, self.MATERIAL, self._dark())
        self._glass_style()

    def _glass_style(self) -> None:
        """Over the window's material: the background see-through, cards and the footer as light
        layers over it (as in the tray panel)."""
        if not self.glass:
            return
        from .backdrop import glass_colours
        from .theme import accent_edges, accent_fill

        dark = self._dark()
        g = glass_colours(self.c, dark)
        sel = "rgba(255, 255, 255, 22)" if dark else "rgba(255, 255, 255, 235)"
        hot = "rgba(255, 255, 255, 24)" if dark else "rgba(255, 255, 255, 235)"
        sheet = (
            "QDialog#Root, QWidget#Root, QWidget#NavPane, QStackedWidget, QScrollArea, QScrollArea > QWidget,"
            " QScrollArea > QWidget > QWidget { background: transparent; }"
            f"QWidget#Footer {{ background: {g['foot']}; border-top: 1px solid {g['stroke']}; }}"
            f"QFrame#Card {{ background: {g['card']}; border: 1px solid {g['stroke']}; }}"
            f"SoftCard {{ qproperty-fill: {g['card']}; qproperty-stroke: {g['stroke']}; qproperty-shade: {g['shade']}; }}"
            f"SoftCard[hot=\"true\"] {{ qproperty-fill: {hot}; }}"
            "QListWidget#Nav { background: transparent; }"
            f"QListWidget#Nav::item:hover {{ background: {g['hover']}; }}"
            f"QListWidget#Nav::item:selected {{ background: {sel}; }}"
            # controls as light layers too (the main button keeps its colour)
            f"QPushButton, QComboBox, QLineEdit {{ background: {g['control']}; border-color: {g['stroke2']}; }}"
            f"QPushButton:hover, QComboBox:hover {{ background: {g['hover']}; }}"
            # quiet links stay text only (the button layer above would put a box behind them)
            "QPushButton#Link, QPushButton#Link:hover, QPushButton#Link:pressed"
            " { background: transparent; border: none; }"
            f"QPushButton#Primary {{ background: {accent_fill(self.c['btn'], 0.80)}; {accent_edges(self.c['btn'], 0.85)} }}"
            f"QPushButton#Primary:hover {{ background: {accent_fill(self.c['btn_hover'], 0.92)};"
            f" {accent_edges(self.c['btn_hover'], 0.95)} }}")
        if sheet != self.styleSheet():  # (setting it again would announce another change: a loop)
            self.setStyleSheet(sheet)

    def paintEvent(self, e) -> None:  # noqa: N802
        if self.glass:
            # the theme's veil over Windows' material: dense enough for small text to stay readable
            # over a bright wallpaper (a see-through window doesn't paint its style sheet background)
            from PySide6.QtGui import QPainter as _P

            from .backdrop import glass_colours, rgba

            p = _P(self)
            p.fillRect(self.rect(), rgba(glass_colours(self.c, self._dark())["tint"]))
            return
        super().paintEvent(e)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if self.glass:
            self._material()
            return
        from .backdrop import match_frame

        match_frame(self)  # the title bar in the window's own colours (dark theme: a dark title bar)

    def changeEvent(self, e) -> None:  # noqa: N802
        super().changeEvent(e)
        from PySide6.QtCore import QEvent

        if e.type() in (QEvent.PaletteChange, QEvent.StyleChange) and self.isVisible():
            if self.glass:
                if e.type() == QEvent.PaletteChange:
                    self._material()  # (light or dark material, and the layers in the new colours)
                return
            from .backdrop import match_frame

            match_frame(self)  # the theme changed while it was open


class Toggle(QCheckBox):
    """Windows 11 style switch drawn by hand (QSS cannot draw the knob)."""

    PAD = 4  # room around the switch for the focus ring, so it is never clipped

    def __init__(self, text: str, palette: dict[str, str], state: bool = False) -> None:
        super().__init__(text)
        self.c = palette
        self.state = state  # (a setting card) "On"/"Off" written left of the switch, as in Windows' Settings
        self.setMinimumHeight(30)
        self.setAttribute(Qt.WA_Hover)

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = self.fontMetrics()
        if self.state:
            words = max(fm.horizontalAdvance(t("On")), fm.horizontalAdvance(t("Off")))
            return QSize(40 + 12 + 2 * self.PAD + words, max(30, fm.height() + 8))
        return QSize(52 + 2 * self.PAD + fm.horizontalAdvance(self.text()), max(30, fm.height() + 8))

    def hitButton(self, pos) -> bool:  # noqa: N802
        return self.rect().contains(pos)

    def paintEvent(self, _e) -> None:  # noqa: N802
        c = self.c
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.4)
        h, w = 20, 40
        y = (self.height() - h) / 2
        on = self.isChecked()
        hover = self.underMouse()
        x = self.width() - self.PAD - w if self.state else self.PAD
        track = QRectF(x + 0.5, y + 0.5, w - 1, h - 1)
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c.get("btn_hover", c["accent_hover"]) if hover else c.get("btn", c["accent"])))
        else:
            p.setPen(QPen(QColor(c["text"] if hover else c["border_strong"]), 1.2))
            p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(track, h / 2, h / 2)
        knob = 14 if on else 12
        cx = x + (w - h / 2 if on else h / 2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["accent_text"]) if on else QColor(c["muted"]))  # contrasts with the accent
        p.drawEllipse(QPointF(cx, y + h / 2), knob / 2, knob / 2)
        if self.hasFocus():
            p.setPen(QPen(QColor(c["accent"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(track.adjusted(-2.5, -2.5, 2.5, 2.5), h / 2 + 2, h / 2 + 2)
        if self.state:
            p.setPen(QColor(c.get("muted2", c["muted"])))
            p.drawText(QRectF(0, 0, x - 12, self.height()), Qt.AlignVCenter | Qt.AlignRight, t("On") if on else t("Off"))
            return
        p.setPen(QColor(c["text"]))
        p.drawText(QRectF(x + w + 12, 0, self.width() - x - w - 12, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())


def refresh_palette(palette: dict[str, str], skip: tuple = ()) -> None:
    """The theme changed: in every open window (except those in skip, refreshed by their owner), each
    widget that paints with a palette it was given re-renders with the new one. Only widgets that
    say how (an apply_palette method) are touched."""
    from PySide6.QtWidgets import QApplication

    for top in QApplication.topLevelWidgets():
        if top in skip:
            continue
        for w in [top, *top.findChildren(QWidget)]:
            if any(w is s or s.isAncestorOf(w) for s in skip):
                continue  # (also when that window sits inside another one)
            fn = getattr(w, "apply_palette", None)
            if callable(fn):
                fn(palette)
                w.update()


def make_popup_round(w: QWidget) -> None:
    """Drop the square native frame and shadow so the QSS border-radius shows. Call before first show."""
    w.setWindowFlags(w.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    w.setAttribute(Qt.WA_TranslucentBackground)


class Combo(QComboBox):
    """Combo box with a rounded popup that opens just under the field."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setView(QListView())  # QSS ::item styling works on QListView
        self.setItemDelegate(QStyledItemDelegate())  # Fusion's menu delegate ignores QSS
        self.setFixedHeight(32)
        self.setCursor(Qt.PointingHandCursor)
        make_popup_round(self.view().window())

    def showPopup(self) -> None:  # noqa: N802
        super().showPopup()
        pop = self.view().window()
        pop.move(self.mapToGlobal(self.rect().bottomLeft()) + QPoint(0, 4))


class Spin(QLineEdit):
    """Whole-number field that looks exactly like the other inputs.

    (A QSpinBox with hidden arrows mis-places its inner editor under our stylesheet, so the
    number ends up clipped; a plain line edit with a validator has no such sub-controls.)
    """

    def __init__(self, lo: int, hi: int, value: int, suffix: str = "") -> None:
        super().__init__(str(value))
        from PySide6.QtGui import QIntValidator

        self.lo, self.hi = lo, hi
        self.setValidator(QIntValidator(lo, hi, self))
        self.setFixedHeight(32)

    def value(self) -> int:
        try:
            return max(self.lo, min(self.hi, int(self.text())))
        except ValueError:
            return self.lo


def _free_text(folder: str) -> str:
    """'312 GB free on this drive' for a folder (or the nearest part of its path that exists)."""
    import shutil

    from ..ui_text import size_text

    if not folder:
        return ""
    probe = Path(folder)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return ""
    return t("{free} free on this drive", free=size_text(free))


def _field(w: QLineEdit) -> QLineEdit:
    w.setFixedHeight(32)
    return w


# login ------------------------------------------------------------------------
class LoginDialog(_Dialog):
    loggedIn = Signal(object, str)  # api, apple_id

    def __init__(self, apple_id: str = "") -> None:
        super().__init__(t("Sign in – {app}", app=APP_NAME))
        self.setFixedWidth(_fit_screen(480, 0)[0])
        self.api: Any = None
        self._task: Any = None
        self._cancelled = False
        self._completed = False
        self._completion_failed = False
        self._waiting = False  # either credentials or code verification is in flight
        self._cancel_pressed = False

        frame = QVBoxLayout(self)  # a scrolling body: fits a small screen at high scaling
        frame.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._login_body = QWidget()
        self._login_body.setObjectName("Root")
        self._scroll.setWidget(self._login_body)
        frame.addWidget(self._scroll)
        outer = QVBoxLayout(self._login_body)
        outer.setContentsMargins(0, 0, 0, 22)
        outer.setSpacing(14)
        from .theme import palette as _palette

        # the top of first run's last step: the mark, the title, the promise about the password, the steps
        self.hero = Hero(_palette("system"), 2)
        self.hero.set_text(t("Sign in to iCloud"),
                           t("Your password is only used to sign in, with Apple's two-factor code. It is never stored."), 2)
        outer.addWidget(self.hero)
        inner = QVBoxLayout()
        inner.setContentsMargins(36, 0, 36, 0)
        inner.setSpacing(14)
        outer.addLayout(inner)
        outer = inner

        self.pages = QStackedWidget()
        outer.addWidget(self.pages)

        # page 1: credentials
        p1 = QWidget()
        l1 = QVBoxLayout(p1)
        l1.setContentsMargins(0, 0, 0, 0)
        l1.setSpacing(8)
        self.email = _field(QLineEdit(apple_id))
        self.email.setPlaceholderText(t("Apple ID (email)"))
        self.password = _field(QLineEdit())
        self.password.setPlaceholderText(t("Password"))
        self.password.setEchoMode(QLineEdit.Password)
        self.password.returnPressed.connect(self._submit_credentials)
        for f in (self.email, self.password):
            f.setMinimumHeight(36)  # (the design's field height)
        l1.addWidget(self.email)
        l1.addWidget(self.password)
        note = _label(
            t("Your password is never stored. Only Apple's session, which lasts about two months, is kept."),
            "Hint",
            True,
        )
        l1.addWidget(note)
        self.pages.addWidget(p1)

        # page 2: 2FA code
        p2 = QWidget()
        l2 = QVBoxLayout(p2)
        l2.setContentsMargins(0, 0, 0, 0)
        l2.setSpacing(10)
        self.code_info = _label("", "Muted", True)
        self.code_info.setAlignment(Qt.AlignCenter)
        self.code = QLineEdit()
        self.code.setPlaceholderText("• • • • • •")
        self.code.setAccessibleName(t("Verification code"))  # (the dots say nothing to a screen reader)
        self.email.setAccessibleName(t("Apple ID (email)"))
        self.password.setAccessibleName(t("Password"))
        self.code.setMaxLength(7)
        self.code.setAlignment(Qt.AlignCenter)
        self.code.setStyleSheet("font-size: 20pt; letter-spacing: 8px; padding: 10px;")
        self.code.returnPressed.connect(self._submit_code)
        self.code.textChanged.connect(lambda t: len(t.replace(" ", "")) == 6 and self._submit_code())
        l2.addWidget(self.code_info)
        l2.addWidget(self.code)
        self.pages.addWidget(p2)

        self.wait_note = _label(t("Waiting for Apple… this may take a few minutes. You can close this window; "
                                  "it will return when Apple answers. Cancel stops sign-in."), "Hint", True)
        self.wait_note.setAlignment(Qt.AlignCenter)
        self.wait_note.hide()
        outer.addWidget(self.wait_note)
        self.error = _label("", "ErrorText", True)
        self.error.hide()
        outer.addWidget(self.error)

        btns = QHBoxLayout()
        self.cancel = QPushButton(t("Cancel"))
        self.cancel.setObjectName("Link")  # quiet, on the left, as Back in the design
        self.cancel.setCursor(Qt.PointingHandCursor)
        self.cancel.clicked.connect(self._cancel_sign_in)
        # Enter must never reach Cancel: when the field with the focus is locked while Apple answers,
        # Qt moves the focus to the next button and an "auto default" button takes over Enter.
        self.cancel.setAutoDefault(False)
        self.cancel.setFocusPolicy(Qt.StrongFocus)  # keyboard users can deliberately Tab here, then Space
        self.ok = QPushButton(t("Sign in"))
        self.ok.setObjectName("Primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._next)
        btns.addWidget(self.cancel)
        btns.addStretch(1)
        btns.addWidget(self.ok)
        outer.addLayout(btns)
        self.setTabOrder(self.password, self.ok)
        self.setTabOrder(self.ok, self.cancel)
        (self.password if apple_id else self.email).setFocus()
        self._shrink()

    def _busy(self, busy: bool, text: str | None = None) -> None:
        if busy:
            self.setFocus(Qt.OtherFocusReason)  # keep the focus off the buttons while fields are locked
        self.ok.setEnabled(not busy)
        self.email.setEnabled(not busy)
        self.password.setEnabled(not busy)
        self.code.setEnabled(not busy)
        if text:
            self.ok.setText(text)

    def _shrink(self) -> None:
        lay = self._login_body.layout()
        lay.activate()
        h = lay.totalHeightForWidth(self.width()) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        self.resize(self.width(), min(h + 2, _fit_screen(0, 100000)[1]))

    def _show_error(self, msg: str) -> None:
        self.error.setText(msg)
        self.error.setVisible(bool(msg))
        self._shrink()

    def _next(self) -> None:
        if getattr(self, "_page1", None) is None:  # still on the Apple ID / password page
            self._submit_credentials()
        else:
            self._submit_code()

    def _submit_credentials(self) -> None:
        if self._waiting or self._cancelled or self._completed or getattr(self, "_page1", None) is not None:
            return
        email, pw = self.email.text().strip(), self.password.text()
        if not email or not pw:
            self._show_error(t("Enter your Apple ID and password."))
            return
        self._show_error("")
        self._busy(True, t("Signing in…"))
        self.wait_note.show()
        self._shrink()
        self._waiting = True
        self.password.clear()

        def done(step: auth.LoginStep) -> None:
            self._waiting = False
            if self._cancelled:
                log.info("Sign-in: cancelled before Apple answered; result not used")
                return  # cancelled meanwhile: this sign-in is not used
            self.password.clear()
            self.api = step.api
            if step.needs_code:
                self._bring_forward()  # Apple has just sent the code: this window must be in sight
                self.code_info.setText(step.notice)
                # the stack keeps the tallest page's height: take the sign-in page out
                self._page1 = self.pages.widget(0)
                self.pages.removeWidget(self._page1)
                self._page1.hide()
                self.pages.setCurrentIndex(0)
                self.wait_note.hide()
                self._shrink()
                self._busy(False, t("Confirm"))
                self.code.setFocus()
            else:
                self._finish()

        def fail(msg: str) -> None:
            self._login_failed(msg)

        self._task = run_async(lambda: auth.start_login(email, pw), done, fail)

    def _submit_code(self) -> None:
        code = self.code.text().replace(" ", "")
        if len(code) != 6 or not self.ok.isEnabled() or self._waiting or self._cancelled or self._completed:
            return
        self._show_error("")
        self._busy(True, t("Checking…"))
        self._waiting = True
        self.wait_note.show()
        self._shrink()
        api = self.api  # the task belongs to this attempt, even if Cancel is clicked

        def fail(msg: str) -> None:
            if self._cancelled:
                self._waiting = False
                return
            self.code.clear()
            self._login_failed(msg)

        self._task = run_async(lambda: auth.submit_code(api, code), lambda _r: self._finish(), fail)

    def _login_failed(self, msg: str) -> None:
        """msg has already passed through safelog.brief; never display raw server errors."""
        self._waiting = False
        if self._cancelled or self._completed:
            return
        self._completion_failed = True
        log.warning("Sign-in failed: %s", msg)
        self.wait_note.hide()
        self._busy(False, t("Confirm") if getattr(self, "_page1", None) is not None else t("Sign in"))
        self._show_error(msg)
        self._bring_forward()

    def _bring_forward(self) -> None:
        """Shown, not minimised, in front, and its taskbar button flashing."""
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        if self.windowHandle() is not None:
            self.windowHandle().requestActivate()
        field = self.code if getattr(self, "_page1", None) is not None else (self.password if self.email.text() else self.email)
        if field.isEnabled():
            field.setFocus(Qt.OtherFocusReason)
        QApplication.alert(self)

    def _cancel_sign_in(self) -> None:
        self._cancel_pressed = True
        self.reject()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.cancel.hasFocus():
            # Cancel is never an Enter action, even when deliberately focused. Do not
            # let the event bubble into QDialog and activate its default Sign in button.
            event.accept()
            return
        super().keyPressEvent(event)

    def reject(self) -> None:
        if not self._cancel_pressed:
            # Preserve the entire attempt, including a code already sent by Apple.
            self.hide()
            return
        self._cancelled = True
        self.password.clear()
        self.code.clear()
        log.info("Sign-in cancelled by user")
        super().reject()

    def _finish(self) -> None:
        self._waiting = False
        if self._cancelled or self._completed:
            return  # the dialog was cancelled while Apple was still answering
        self._completion_failed = False
        self.loggedIn.emit(self.api, self.email.text().strip())
        if not self._completion_failed:
            self._completed = True
            self.code.clear()
            self.accept()


# settings -----------------------------------------------------------------------
POLL_PRESETS = (1, 2, 5, 10, 15, 30, 60)


def _nav_color(c: dict[str, str], glyph_name: str) -> str:
    """Settings pages for a service show its colour (as in the panel); the others stay quiet."""
    from .theme import service_color

    return service_color(c, glyph_name) or c["muted"]


class SettingsDialog(_Dialog):
    """Left navigation (General · Syncing · Photos · iCloud Drive · Notes · Bookmarks · About) with one
    page each, every option on its own card, as in Windows 11's Settings."""

    GLASS = True

    signOut = Signal()
    restoreExcluded = Signal()
    checkUpdates = Signal()

    PAGES = ("general", "photos", "drive", "notes", "bookmarks", "about")

    def __init__(self, settings: Settings, palette: dict[str, str], storage: dict | None = None,
                 get_api=None) -> None:
        super().__init__(t("Settings – {app}", app=APP_NAME))
        self.storage = storage or {}
        self.get_api = get_api or (lambda: None)
        self.drive_folders: list[str] | None = settings.drive_folders
        self.folders_chosen = False  # the user went through the folder chooser in this dialog
        self.drive_trash: list[str] = []  # PC folders to move to the Recycle Bin after saving
        self.s = settings.copy()
        self.c = palette
        self.reset_state = False
        self.reset_drive = False
        self.resize(*_fit_screen(900, 640))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        main = QHBoxLayout()
        main.setSpacing(0)
        outer.addLayout(main, 1)

        # the sidebar, as in Windows' Settings: who you are at the top, then the pages
        nav_pane = QWidget()
        nav_pane.setObjectName("NavPane")
        nav_pane.setAttribute(Qt.WA_StyledBackground, True)
        nav_pane.setFixedWidth(244)
        nl = QVBoxLayout(nav_pane)
        nl.setContentsMargins(8, 12, 8, 12)
        nl.setSpacing(4)
        nl.addWidget(self._who())
        from .settings_parts import NavDelegate

        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setItemDelegate(NavDelegate(self))
        self.nav.setAccessibleName(t("Settings"))
        nl.addWidget(self.nav, 1)
        main.addWidget(nav_pane)

        self.stack = QStackedWidget()
        main.addWidget(self.stack, 1)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        self._build_general()
        self._build_photos()
        self._build_drive()
        self._build_notes()
        self._build_bookmarks()
        self._build_about()
        self.nav.setCurrentRow(0)

        footer = QWidget()
        footer.setObjectName("Footer")
        footer.setAttribute(Qt.WA_StyledBackground, True)
        btns = QHBoxLayout(footer)
        btns.setContentsMargins(24, 12, 24, 12)
        cancel = QPushButton(t("Cancel"))
        cancel.clicked.connect(self.reject)
        save = QPushButton(t("Save"))
        save.setObjectName("Primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(save)
        outer.addWidget(footer)
        for b in self.findChildren(QPushButton):  # Enter in a field never presses a button by itself
            b.setAutoDefault(False)
            b.setDefault(False)
        self._fit_to_pages(nav_pane, footer)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            from PySide6.QtWidgets import QApplication

            w = QApplication.focusWidget()
            if isinstance(w, QPushButton) and w.window() is self:
                w.click()  # a button with the keyboard focus: Enter presses it
            # anywhere else Enter does nothing: saving can move folders or ask about deletions, and
            # Sign out sits on the same page as the fields (it must never answer an Enter)
            return
        super().keyPressEvent(e)

    def show_page(self, page: int | str) -> None:
        """Open on a page, by its number or its name in PAGES ("drive", "notes"…)."""
        index = self.PAGES.index(page) if isinstance(page, str) else int(page)
        self.nav.setCurrentRow(max(0, min(index, self.nav.count() - 1)))

    def _fit_to_pages(self, nav_pane: QWidget, footer: QWidget) -> None:
        """Opens big enough for the longest page, so nothing needs scrolling (as far as the screen allows)."""
        need_w, need_h = 900, 640
        for i in range(self.stack.count()):
            page = self.stack.widget(i)
            body = page.widget() if isinstance(page, QScrollArea) else page
            if body is None:
                continue
            hint = body.sizeHint()
            need_w = max(need_w, nav_pane.width() + hint.width() + 24)
            need_h = max(need_h, hint.height() + footer.sizeHint().height() + 4)
        self.resize(*_fit_screen(need_w, need_h))

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        if self.glass and self.isVisible():
            self._material()
        for i in range(self.nav.count()):
            item = self.nav.item(i)
            name = item.data(Qt.UserRole)
            if name:
                item.setIcon(glyph(name, _nav_color(palette, name)))

    # page helpers ---------------------------------------------------------------------
    def _who(self) -> QWidget:
        """The Apple ID at the top of the sidebar, with a round letter as its picture."""
        from .settings_parts import Avatar, ElidedLabel

        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(8, 4, 8, 14)
        row.setSpacing(12)
        who = self.s.apple_id or ""
        from .. import avatar

        picture = avatar.path_for(who) if who else None
        row.addWidget(Avatar(who or "?", 40, str(picture) if picture and picture.exists() else None), 0,
                      Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(0)
        name = ElidedLabel(who or t("Not signed in"))
        name.setObjectName("WhoName")
        col.addWidget(name)
        sub = "iCloud"
        if self.storage.get("total"):
            from .flyout import storage_text

            sub = t("iCloud · {size} plan", size=storage_text(self.storage["total"]))  # (as the storage bar says it)
        col.addWidget(_label(sub, "WhoSub"))
        row.addLayout(col, 1)
        return box

    def _page(self, name: str, glyph_name: str) -> QVBoxLayout:
        item = QListWidgetItem(glyph(glyph_name, _nav_color(self.c, glyph_name)), name)
        item.setData(Qt.UserRole, glyph_name)  # drawn again in the new colour when the theme changes
        self.nav.addItem(item)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("Root")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 6, 28, 12)
        lay.setSpacing(12)
        lay.addWidget(_label(name, "PageTitle"))  # a large page title, as in Windows' Settings
        scroll.setWidget(body)
        self.stack.addWidget(scroll)
        return lay

    @staticmethod
    def _group(parent: QVBoxLayout, title: str | None = None) -> QVBoxLayout:
        """A group of setting cards under a short title; returns the layout the cards go in (its
        widget, layout.parentWidget(), turns the whole group on and off)."""
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        if title:
            lbl = _label(title, "GroupLabel")
            lbl.setContentsMargins(2, 0, 0, 2)
            lay.addWidget(lbl)
        parent.addWidget(box)
        return lay

    def _setting(self, group: QVBoxLayout, icon, title, sub=None, control: QWidget | None = None) -> QFrame:
        """One setting as one card: its icon, its name, a line that explains it and its control on
        the right. icon: an icon name or a widget; title/sub: text or a label."""
        from .settings_parts import IconLabel

        card = SoftCard()
        card.setObjectName("SetCard")
        card.setMinimumHeight(62)  # (about the design's 64, so the Photos page still fits without scrolling)
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 9, 16, 9)
        row.setSpacing(16)
        row.addWidget(icon if isinstance(icon, QWidget) else IconLabel(icon, self.c, 20), 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(1)
        name = title if isinstance(title, QLabel) else _label(title, "SetTitle", True)
        col.addWidget(name)
        if sub:
            col.addWidget(sub if isinstance(sub, QLabel) else _label(sub, "SetSub", True))
        row.addLayout(col, 1)
        if control is not None:
            if not control.accessibleName():
                control.setAccessibleName(name.text())  # the card's name names the control (screen readers)
            name.setBuddy(control)
            row.addWidget(control, 0, Qt.AlignVCenter)
        group.addWidget(card)
        card.title_label = name
        return card

    def _switch(self, value: bool) -> Toggle:
        sw = Toggle("", self.c, state=True)
        sw.setChecked(value)
        sw.setCursor(Qt.PointingHandCursor)
        return sw

    @staticmethod
    def _wide(w: QWidget) -> QWidget:
        if isinstance(w, QComboBox):
            w.setSizeAdjustPolicy(QComboBox.AdjustToContents)  # as wide as its longest choice
        w.setMinimumWidth(180)
        w.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        return w

    def _info(self, group: QVBoxLayout, lines, icon: str = "info") -> QFrame:
        """A card of things worth knowing: an icon and a few short lines."""
        from .settings_parts import IconLabel

        card = SoftCard()
        card.setObjectName("SetCard")
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 13, 16, 13)
        row.setSpacing(16)
        row.addWidget(IconLabel(icon, self.c, 20), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(7)
        lines = list(lines)
        for line in lines:
            col.addWidget(_label(("•  " + line) if len(lines) > 1 else line, "InfoLine", True))
        row.addLayout(col, 1)
        group.addWidget(card)
        return card

    def _link_card(self, group: QVBoxLayout, icon: str, title: str, sub: str | None, action) -> QFrame:
        """A card that does something when clicked, with a chevron on the right."""
        from .settings_parts import ClickCard, IconLabel

        card = ClickCard()
        card.setMinimumHeight(56)
        _as_control(card, "button")  # a button for screen readers: its Press action clicks it, as Enter does
        card.setAccessibleName(title)
        if sub:
            card.setAccessibleDescription(sub)
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 10, 14, 10)
        row.setSpacing(16)
        row.addWidget(IconLabel(icon, self.c, 20), 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(_label(title, "SetTitle", True))
        if sub:
            col.addWidget(_label(sub, "SetSub", True))
        row.addLayout(col, 1)
        row.addWidget(IconLabel("chevron", self.c, 16), 0, Qt.AlignVCenter)
        card.clicked.connect(action)
        group.addWidget(card)
        return card

    def _service_head(self, lay: QVBoxLayout, glyph_name: str, title: str, sub: str, value: bool) -> Toggle:
        """The card at the top of a service's page: its coloured tile, what it does, and the switch."""
        from .glow import GlowTile
        from .theme import service_color

        tile = GlowTile(glyph_name, self.c, service_color(self.c, glyph_name) or self.c["accent"],
                        size=36, icon=19, radius=9)
        sw = self._switch(value)
        card = self._setting(lay, tile, title, sub, sw)
        card.setMinimumHeight(72)
        card.title_label.setObjectName("SetHead")
        return sw

    def _folder_card(self, group: QVBoxLayout, value: str, title: str, sub: str | None = None,
                     icon: str = "folder") -> QLineEdit:
        """A folder as a card: the path as its name (shortened in the middle when long), the free space
        on its drive below, and Change… on the right. Returns the field that holds the path."""
        from .settings_parts import ElidedLabel

        edit = _field(QLineEdit(value))
        edit.setAccessibleName(title)
        path = ElidedLabel(value)
        path.setObjectName("SetTitle")
        below = _label(sub or "", "SetSub", True)
        browse = QPushButton(t("Change…"))
        browse.setCursor(Qt.PointingHandCursor)
        browse.setAccessibleName(title)  # every "Change…" looks the same: a screen reader says which folder
        browse.clicked.connect(lambda: self._browse(edit, title))
        card = self._setting(group, icon, path, below, browse)
        edit.setParent(card)
        edit.hide()  # (the path is shown by the card; Change… picks another)

        def show(text: str) -> None:
            path.setText(text)
            if sub is None:
                below.setText(_free_text(text))
                below.setVisible(bool(below.text()))

        edit.textChanged.connect(show)
        show(value)
        return edit

    def _browse(self, edit: QLineEdit, title: str) -> None:
        d = QFileDialog.getExistingDirectory(self, title, edit.text())
        if d:
            edit.setText(str(Path(d)))

    # pages ------------------------------------------------------------------------------
    def _build_general(self) -> None:
        lay = self._page(t("General"), "gear")
        if self.storage.get("total"):  # the same storage bar as in the panel
            from .flyout import StorageBar

            card = SoftCard()
            card.setObjectName("SetCard")
            v = QVBoxLayout(card)
            v.setContentsMargins(18, 14, 18, 16)
            bar = StorageBar(self.c, large=True)
            bar.set(self.storage.get("used", 0), self.storage["total"], list(self.storage.get("parts", [])))
            v.addWidget(bar)
            lay.addWidget(card)

        sync = self._group(lay, t("Syncing"))
        self.poll = Combo()
        for m in POLL_PRESETS:
            self.poll.addItem(t("{n} min", n=m), m)
        idx = min(range(len(POLL_PRESETS)), key=lambda i: abs(POLL_PRESETS[i] - self.s.poll_minutes))
        self.poll.setCurrentIndex(idx)
        self._setting(sync, "clock", t("Check iCloud every"),
                      t("Apple doesn't notify apps about changes, so iCloud is checked at intervals. Each check only "
                        "reads what changed; the whole library is compared once a day."), self._wide(self.poll))
        self.limit = Combo()
        for mb in (0, 1, 2, 5, 10, 20, 50):
            self.limit.addItem(t("No limit") if not mb else t("{n} MB/s", n=mb), mb)
        self.limit.setCurrentIndex(max(0, self.limit.findData(self.s.speed_limit_mb)))
        self._setting(sync, "speed", t("Download speed limit"),
                      t("Keeps some of your connection free while Skyfold downloads, for example during "
                        "video calls."), self._wide(self.limit))
        self.threshold = Spin(1, 10000, self.s.delete_threshold)
        self.threshold.setFixedWidth(110)
        self._setting(sync, "shield", t("Ask before deleting more than"), t("Larger deletions wait for your OK."),
                      self.threshold)
        self.instant = self._switch(self.s.instant_local_deletes)
        self._setting(sync, "trash", t("Send deletions on this PC right away (watch the folder)"),
                      t("Otherwise they are sent with the next check."), self.instant)
        self.upbuf = Combo()
        self.upbuf.addItem(t("Automatic (recommended)"), 0)
        self.upbuf.addItem(t("Fixed 8 MB"), 8)
        self.upbuf.setCurrentIndex(max(0, self.upbuf.findData(self.s.upload_buffer_mb)))
        self._setting(sync, "upload", t("Upload send buffer"), None, self._wide(self.upbuf))

        win = self._group(lay, t("Windows"))
        self.autostart = self._switch(starts_with_windows(self.s.start_with_windows))
        self._setting(win, "power", t("Start with Windows"), t("Skyfold waits quietly in the system tray."),
                      self.autostart)
        self.notif = Combo()
        self.notif.addItem(t("Skyfold's own"), "skyfold")
        self.notif.addItem(t("Windows' own"), "windows")
        self.notif.addItem(t("Off"), "off")
        self.notif.setCurrentIndex(max(0, self.notif.findData(
            self.s.notification_style if self.s.notifications else "off")))
        self._setting(win, "bell", t("Notifications"),
                      t("Skyfold's own notifications follow the app's theme and stay away while Do Not Disturb "
                        "is on or a program runs full screen."), self._wide(self.notif))
        from .theme import THEMES

        self.theme = Combo()
        for code, name in THEMES.items():
            self.theme.addItem(t(name), code)
        self.theme.setCurrentIndex(list(THEMES).index(self.s.theme) if self.s.theme in THEMES else 0)
        self._setting(win, "palette", t("Theme"), t("Follows Windows, or always light, dark or OLED."),
                      self._wide(self.theme))
        self.lang = Combo()
        for code, name in LANGUAGES.items():
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(list(LANGUAGES).index(self.s.language) if self.s.language in LANGUAGES else 0)
        self._setting(win, "globe", t("Language"), None, self._wide(self.lang))
        self.updates = self._switch(self.s.check_updates)
        self._setting(win, "download", t("Check for new versions of Skyfold"), None, self.updates)

        acc = self._group(lay, t("Account"))
        out = QPushButton(t("Sign out"))
        out.setObjectName("Danger")
        out.setEnabled(bool(self.s.apple_id))
        out.clicked.connect(self._sign_out)
        self._setting(acc, "cloud", t("Sign out"),
                      t("Forgets this PC's sign-in to iCloud. Your files stay where they are."), out)
        lay.addStretch(1)


    def _build_photos(self) -> None:
        lay = self._page(t("Photos"), "photo")
        fold = self._group(lay, t("Photos folder"))
        self.dir = self._folder_card(fold, self.s.photos_dir, t("Choose a folder for photos"))
        self.structure = Combo()
        for key, label in FOLDER_STRUCTURES.items():
            self.structure.addItem(t(label), key)
        self.structure.setCurrentIndex(list(FOLDER_STRUCTURES).index(self.s.folder_structure))
        self._setting(fold, "layout", t("Layout"), t("The layout applies to new photos; existing ones stay where they are."),
                      self._wide(self.structure))

        view = self._group(lay, t("Photos window"))
        self.photos_window = self._switch(self.s.photos_window)
        self._setting(view, "window", t("Open the Photos window when I click Photos"),
                      t("Off: a click on Photos opens the photos folder in File Explorer instead."), self.photos_window)

        media = self._group(lay, t("Downloading"))
        self.videos = self._switch(self.s.include_videos)
        self._setting(media, "download", t("Download videos"), None, self.videos)
        self.live = self._switch(self.s.include_live_video)
        self._setting(media, "download", t("Download the video part of Live Photos (.MOV)"), None, self.live)
        self.albums = self._switch(self.s.albums_as_folders)
        self._setting(media, "folder", t("Show albums as folders (Albums\\…)"),
                      t("Album folders use no extra disk space. They mirror iCloud; deleting there doesn't delete photos."),
                      self.albums)
        self.upload = self._switch(self.s.photos_upload)
        self._setting(media, "upload", t("Upload photos and videos I add to this folder to iCloud Photos"), None,
                      self.upload)

        dele = self._group(lay, t("Deleting"))
        self.del_pc = self._switch(self.s.delete_cloud_to_pc)
        self._setting(dele, "trash", t("Deleted on the phone → delete on this PC too"),
                      t("The file goes to the Recycle Bin, where it can be restored."), self.del_pc)
        self.del_cloud = self._switch(self.s.delete_pc_to_cloud)
        self._setting(dele, "trash", t("Deleted on this PC → delete in iCloud too"),
                      t("The photo goes to 'Recently Deleted' on the phone and can be restored for 30 days."),
                      self.del_cloud)
        self._link_card(dele, "download", t("Bring back photos I deleted only on this PC"),
                        t("Downloads again the photos deleted on this PC while deleting in iCloud was off."),
                        self._restore_excluded)
        lay.addStretch(1)

    def _build_drive(self) -> None:
        lay = self._page(t("iCloud Drive"), "folder")
        self.drive_on = self._service_head(
            lay, "folder", t("iCloud Drive"),
            t("Keeps a folder on this PC and your iCloud Drive the same, in both directions."), self.s.drive_enabled)

        self.drive_cards: list[QWidget] = []
        fold = self._group(lay, t("Drive folder"))
        self.drive_cards.append(fold.parentWidget())
        self.drive_dir = self._folder_card(fold, self.s.drive_dir, t("Choose a folder for iCloud Drive"))

        which = self._group(lay, t("Folders"))
        self.drive_cards.append(which.parentWidget())
        self.folders_text = _label("", "SetTitle", True)
        choose = QPushButton(t("Choose folders…"))
        choose.setCursor(Qt.PointingHandCursor)
        choose.clicked.connect(self._choose_folders)
        self._setting(which, "sliders", self.folders_text,
                      t("Folders you leave out stay in iCloud untouched and aren't downloaded."), choose)
        self._show_folders()

        dele = self._group(lay, t("Deleting"))
        self.drive_cards.append(dele.parentWidget())
        self.drive_del_pc = self._switch(self.s.drive_delete_cloud_to_pc)
        self._setting(dele, "trash", t("Deleted in iCloud → delete on this PC too"),
                      t("The file goes to the Recycle Bin, where it can be restored."), self.drive_del_pc)
        self.drive_del_cloud = self._switch(self.s.drive_delete_pc_to_cloud)
        self._setting(dele, "trash", t("Deleted on this PC → delete in iCloud too"),
                      t("The file goes to 'Recently Deleted' in iCloud Drive and can be restored for 30 days."),
                      self.drive_del_cloud)

        info = self._group(lay, t("Good to know"))
        self.drive_cards.append(info.parentWidget())
        self._info(info, (t("Files are downloaded to this PC; there are no online-only placeholders."),
                          t("When a file changed on both sides, both versions are kept; the PC one gets "
                            "\"(PC conflict …)\" in its name."),
                          t("Renamed or moved files and folders are renamed or moved on the other side too.")))
        lay.addStretch(1)
        self.drive_on.toggled.connect(self._drive_enabled_changed)
        self._drive_enabled_changed(self.drive_on.isChecked())

    def _build_notes(self) -> None:
        lay = self._page(t("Notes"), "note")
        self.notes_on = self._service_head(
            lay, "note", t("Apple Notes"),
            t("Keeps a copy of all your notes on this PC as web pages, updated automatically. "
              "Editing stays on your iPhone, iPad or Mac."), self.s.notes_enabled)
        fold = self._group(lay, t("Notes folder"))
        self.notes_card = fold.parentWidget()
        self.notes_dir = self._folder_card(fold, self.s.notes_dir, t("Choose a folder for your notes"))
        info = self._group(lay, t("Good to know"))
        self._info(info, (t("Click Notes in the panel to read and search your notes in Skyfold. Notes.html in "
                            "that folder shows them in your web browser."),
                          t("Each note is an HTML file with its pictures and attachments, in the same folders as in Notes."),
                          t("Notes deleted on your devices are moved to the Recycle Bin here."),
                          t("Locked notes can't be read; they are only listed.")))
        lay.addStretch(1)
        self.notes_on.toggled.connect(self.notes_card.setEnabled)
        self.notes_card.setEnabled(self.notes_on.isChecked())

    def _build_bookmarks(self) -> None:
        lay = self._page(t("Bookmarks"), "bookmark")
        self.bookmarks_on = self._service_head(
            lay, "bookmark", t("Safari bookmarks"),
            t("Your Safari bookmarks from iPhone, iPad or Mac as a file that Firefox, Chrome and Edge "
              "can import, kept up to date. Read-only: nothing changes in iCloud."), self.s.bookmarks_enabled)
        fold = self._group(lay, t("Bookmarks file"))
        self.bookmarks_card = fold.parentWidget()
        self.bookmarks_dir = self._folder_card(
            fold, self.s.bookmarks_dir, t("Choose a folder for the bookmarks file"),
            t("Skyfold keeps \"Safari Bookmarks.html\" in this folder up to date: every 6 hours "
              "and when you click Sync now."))
        how = self._group(lay, t("Import it in your browser"))
        self._info(how, (t("Firefox: Ctrl+Shift+O → Import and Backup → Import Bookmarks from HTML…"),
                         t("Chrome or Edge: Bookmarks → Import bookmarks and settings → Bookmarks HTML file."),
                         t("Your bookmarks appear in a \"Safari Bookmarks\" folder. Importing again adds a second "
                           "copy, so delete the old folder first.")), icon="globe")
        info = self._group(lay, t("Good to know"))
        self._info(info, (t("One-way: bookmarks you add in your browser don't go to Safari."),
                          t("Deleted bookmarks, tab groups and the Reading List are left out.")))
        lay.addStretch(1)
        self.bookmarks_on.toggled.connect(self.bookmarks_card.setEnabled)
        self.bookmarks_card.setEnabled(self.bookmarks_on.isChecked())

    def drive_estimate(self) -> int | None:
        """Apple's "Docs" storage category: iCloud Drive plus app documents (a slight overestimate)."""
        for part in self.storage.get("parts", []):
            if len(part) > 2 and part[2] == "docs":
                return int(part[1])
        return None

    def _confirm_drive_size(self, drive_dir: str) -> bool:
        import shutil

        from ..engine import DISK_RESERVE
        from ..ui_text import size_text

        probe = Path(drive_dir)
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        try:
            free = shutil.disk_usage(probe).free
        except OSError:
            return True
        disk = (probe.anchor or str(probe)).rstrip("\\")
        est = self.drive_estimate()
        box = QMessageBox(self)
        box.setTextFormat(Qt.PlainText)  # (folder names from iCloud: never HTML)
        box.setWindowTitle(t("iCloud Drive"))
        box.setIcon(QMessageBox.Information)
        if est is None:
            box.setText(t("All of your iCloud Drive will be downloaded to {folder}.", folder=drive_dir))
            box.setInformativeText(t("{free} free on {disk}. Large Drives can take a while to download.",
                                     free=size_text(free), disk=disk))
        elif est > free - DISK_RESERVE:
            box.setIcon(QMessageBox.Warning)
            box.setText(t("Not enough space for iCloud Drive on {disk}.", disk=disk))
            box.setInformativeText(t("Your iCloud Drive holds up to about {size}, but only {free} is free there. "
                                     "Choose a folder on a disk with more space.",
                                     size=size_text(est), free=size_text(free)))
            box.setStandardButtons(QMessageBox.Ok)
            box.button(QMessageBox.Ok).setText(t("OK"))
            box.exec()
            return False
        else:
            box.setText(t("About {size} will be downloaded from iCloud Drive.", size=size_text(est)))
            box.setInformativeText(t("Folder: {folder}\n{free} free on {disk}, {left} left afterwards.\n\n"
                                     "The first download of a large Drive can take a few hours.",
                                     folder=drive_dir, free=size_text(free), disk=disk,
                                     left=size_text(max(0, free - est))))
        start = box.addButton(t("Turn on"), QMessageBox.AcceptRole)
        start.setObjectName("Primary")
        start.style().unpolish(start)
        start.style().polish(start)
        box.addButton(t("Cancel"), QMessageBox.RejectRole)
        box.setDefaultButton(start)
        box.exec()
        return box.clickedButton() is start

    def _show_folders(self) -> None:
        f = self.drive_folders
        if f is None:
            self.folders_text.setText(t("All of iCloud Drive"))
        elif not f:
            self.folders_text.setText(t("No folders (only files outside folders)"))
        else:
            names = ", ".join(f[:3]) + (" …" if len(f) > 3 else "")
            self.folders_text.setText(t("{n} chosen: {names}", n=len(f), names=names))

    def _choose_folders(self) -> bool:
        if self.get_api() is None:
            inform(self, t("iCloud Drive"), t("Sign in first."))
            return False
        from ..config import STATE_DB
        from ..drive_engine import folder_sizes

        dlg = DriveFoldersDialog(self.drive_folders, self.c, self.get_api,
                                 folder_sizes(STATE_DB.parent / "drive_cache.json"))
        if dlg.exec() != QDialog.Accepted:
            return False
        self.drive_folders = dlg.selection()
        self.folders_chosen = True
        self._show_folders()
        return True

    def _ask_what_to_sync(self) -> bool:
        """First time Drive is turned on: everything, or only some folders?"""
        if self.folders_chosen:
            return True
        box = QMessageBox(self)
        box.setTextFormat(Qt.PlainText)  # (folder names from iCloud: never HTML)
        box.setWindowTitle(t("iCloud Drive"))
        box.setIcon(QMessageBox.Question)
        box.setText(t("What should Skyfold sync from your iCloud Drive?"))
        box.setInformativeText(t("You can sync everything, or only the folders you need. "
                                 "This can be changed later in Settings."))
        everything = box.addButton(t("Everything"), QMessageBox.AcceptRole)
        some = box.addButton(t("Choose folders…"), QMessageBox.ActionRole)
        box.addButton(t("Cancel"), QMessageBox.RejectRole)
        box.setDefaultButton(everything)
        box.exec()
        if box.clickedButton() is everything:
            self.drive_folders = None
            return True
        if box.clickedButton() is some:
            return self._choose_folders()
        return False

    def _ask_about_unsynced(self, drive_dir: str) -> bool:
        """Folders that stop syncing: keep the PC copies (default) or move them to the Recycle Bin."""
        from ..drive_engine import unsynced_local_dirs

        gone = unsynced_local_dirs(Path(drive_dir), self.s.drive_folders, self.drive_folders)
        self.drive_trash = []
        if not gone:
            return True
        box = QMessageBox(self)
        box.setTextFormat(Qt.PlainText)  # (folder names from iCloud: never HTML)
        box.setWindowTitle(t("iCloud Drive"))
        box.setIcon(QMessageBox.Question)
        box.setText(t("{n} folders on this PC will no longer be synced.", n=len(gone)))
        box.setInformativeText(", ".join(gone[:6]) + (" …" if len(gone) > 6 else "") + "\n\n" + t(
            "They stay in iCloud either way. Keep the copies on this PC, or move them to the Recycle Bin?"))
        keep = box.addButton(t("Keep on this PC"), QMessageBox.AcceptRole)
        trash = box.addButton(t("Move to Recycle Bin"), QMessageBox.DestructiveRole)
        box.addButton(t("Cancel"), QMessageBox.RejectRole)
        box.setDefaultButton(keep)
        box.exec()
        if box.clickedButton() is trash:
            self.drive_trash = gone
            return True
        return box.clickedButton() is keep

    def _drive_enabled_changed(self, on: bool) -> None:
        for w in self.drive_cards:
            w.setEnabled(on)

    def _build_about(self) -> None:
        lay = self._page(t("About"), "info")
        head = SoftCard()
        head.setObjectName("SetCard")
        row = QHBoxLayout(head)
        row.setContentsMargins(16, 14, 16, 14)
        row.setSpacing(16)
        row.addWidget(_logo(44))
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(_label(APP_NAME, "SetHead"))
        col.addWidget(_label(t("Version {version}", version=VERSION), "SetSub"))
        row.addLayout(col, 1)
        check = QPushButton(t("Check for updates"))
        check.setCursor(Qt.PointingHandCursor)
        check.clicked.connect(self.checkUpdates)
        row.addWidget(check, 0, Qt.AlignVCenter)
        lay.addWidget(head)

        where = self._group(lay, t("Where your data is"))
        from .. import config

        # the folder really in use: next to the app, or the private folder when that can't be written
        def breakable(path) -> str:  # a long path wraps at its folders instead of widening the page
            return str(path).replace("\\", "\\" + chr(0x200B))  # (a zero-width space after each "\")

        lines = [t("Settings: {path}", path=breakable(config.DATA_DIR)),
                 t("Sign-in and sync database: {path}", path=breakable(PRIVATE_DIR))]
        if APP_IN_CLOUD:
            lines.append(t("The app is in {service}, so the sign-in and database are kept only on this PC.",
                           service=APP_IN_CLOUD))
        self._info(where, lines, icon="device")

        links = self._group(lay, t("Help"))
        self._link_card(links, "globe", t("Source code and bug reports (GitHub)"), "github.com/zoranmod/skyfold",
                        lambda: _open_url("https://github.com/zoranmod/skyfold"))
        self._link_card(links, "file", t("License: GNU GPL v3"), "gnu.org/licenses/gpl-3.0",
                        lambda: _open_url("https://www.gnu.org/licenses/gpl-3.0.html"))
        self._link_card(links, "search", t("Show the log file"), None, _show_log)
        self._diagnostics(lay)
        lay.addWidget(_label(t("Skyfold is not made by Apple. It uses the same web services as iCloud.com."),
                             "Hint", True))
        lay.addStretch(1)

    def _diagnostics(self, lay) -> None:
        """Connection diagnostics for a bug report: off at every start, 30 minutes when turned on."""
        from PySide6.QtCore import QTimer

        from .. import netdiag
        from .settings_parts import IconLabel

        group = self._group(lay, t("Connection diagnostics"))
        card = SoftCard()
        card.setObjectName("SetCard")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(16, 13, 16, 13)
        outer.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(16)
        top.addWidget(IconLabel("sliders", self.c, 20), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(1)
        state = _label("", "SetTitle", True)
        col.addWidget(state)
        col.addWidget(_label(t("For a bug report about syncing: records for {n} minutes what kind of "
                               "request Skyfold sent to Apple and how Apple answered, never names, file "
                               "names, addresses or passwords. The file stays on this PC until you "
                               "attach it to a report.", n=netdiag.MINUTES), "SetSub", True))
        top.addLayout(col, 1)
        outer.addLayout(top)
        row = QHBoxLayout()
        row.setContentsMargins(36, 0, 0, 0)
        row.setSpacing(8)
        toggle = QPushButton()
        show = QPushButton(t("Show the file"))
        remove = QPushButton(t("Delete"))
        for b in (toggle, show, remove):
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        row.addStretch(1)
        outer.addLayout(row)
        group.addWidget(card)

        def refresh() -> None:
            import time as _time

            end = netdiag.until()
            if end:
                state.setText(t("Recording until {time}.", time=_time.strftime("%H:%M", _time.localtime(end))))
                toggle.setText(t("Stop recording"))
            else:
                state.setText(t("Not recording."))
                toggle.setText(t("Record for {n} minutes", n=netdiag.MINUTES))
            has = bool(netdiag.files())
            show.setEnabled(has)
            remove.setEnabled(has)

        def flip() -> None:
            if netdiag.on():
                netdiag.stop()
            else:
                netdiag.start()
            refresh()

        def reveal() -> None:
            import os
            import subprocess

            got = netdiag.files()
            if got:
                subprocess.Popen([os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe"),
                                  "/select,", str(got[0])])

        def wipe() -> None:
            netdiag.delete()
            refresh()

        toggle.clicked.connect(flip)
        show.clicked.connect(reveal)
        remove.clicked.connect(wipe)
        timer = QTimer(self)
        timer.timeout.connect(refresh)
        timer.start(15_000)
        self._diag_refresh = refresh
        refresh()

    # actions ------------------------------------------------------------------------------
    def _sign_out(self) -> None:
        if ask(self, t("Sign out"), t("Sign out of iCloud? Photos on this PC stay.")) == QMessageBox.Yes:
            self.signOut.emit()
            self.reject()

    def _restore_excluded(self) -> None:
        self.restoreExcluded.emit()
        inform(self, APP_NAME, t("Photos deleted only on this PC will be downloaded again."))

    def _check_folder(self, new_dir: str, old_dir: str, title: str) -> bool | None:
        """Validate a folder change. Returns None to abort, True if it changed, False if not."""
        error, warning = check_photos_dir(new_dir)
        if error:
            warn(self, title, error)
            return None
        if Path(new_dir) == Path(old_dir):
            return False
        if warning and ask(self, title, warning + "\n\n" + t("Use this folder anyway?")) != QMessageBox.Yes:
            return None
        ans = ask(
            self, t("New folder"),
            t("You changed the folder.\n\nFiles won't be moved: the new location is filled from iCloud, "
              "and the old folder stays untouched and is no longer synced. Files in the new folder are synced "
              "both ways: deleting one there deletes it in iCloud too. Continue?"))
        return True if ans == QMessageBox.Yes else None

    def _save(self) -> None:
        self.drive_trash = []  # (worked out again from the choice saved now; an earlier attempt's is void)
        new_dir = self.dir.text().strip()
        changed = self._check_folder(new_dir, self.s.photos_dir, t("Photos folder"))
        if changed is None:
            return
        self.reset_state = changed
        drive_dir = self.drive_dir.text().strip()
        if self.drive_on.isChecked():
            if Path(drive_dir) == Path(new_dir) or Path(drive_dir) in Path(new_dir).parents or \
                    Path(new_dir) in Path(drive_dir).parents:
                warn(self, t("iCloud Drive"), t("The Drive folder and the photos folder must be separate."))
                return
            if self.s.drive_enabled:
                changed = self._check_folder(drive_dir, self.s.drive_dir, t("iCloud Drive"))
                if changed is None:
                    return
                self.reset_drive = changed
            else:  # turning Drive on: validate the folder and show what will be downloaded
                error, warning = check_photos_dir(drive_dir)
                if error:
                    warn(self, t("iCloud Drive"), error)
                    return
                if warning and ask(
                        self, t("iCloud Drive"), warning + "\n\n" + t("Use this folder anyway?")) != QMessageBox.Yes:
                    return
                if not self._ask_what_to_sync():
                    return
                if self.drive_folders is None and not self._confirm_drive_size(drive_dir):
                    return
            if self.s.drive_enabled and not self.reset_drive and self.drive_folders != self.s.drive_folders:
                if not self._ask_about_unsynced(drive_dir):
                    return
        notes_dir = self.notes_dir.text().strip()
        if self.notes_on.isChecked():
            error, _warning = check_photos_dir(notes_dir)
            if error:
                warn(self, t("Notes"), error)
                return
            for other in (new_dir, drive_dir):
                if Path(notes_dir) == Path(other) or Path(other) in Path(notes_dir).parents \
                        or Path(notes_dir) in Path(other).parents:
                    warn(self, t("Notes"), t("The notes folder must be separate from the other folders."))
                    return
        s = self.s
        s.photos_dir = new_dir
        s.folder_structure = self.structure.currentData()
        s.poll_minutes = self.poll.currentData()
        s.speed_limit_mb = self.limit.currentData()
        if self.upbuf.findData(s.upload_buffer_mb) >= 0 or self.upbuf.currentData() != 0:
            s.upload_buffer_mb = self.upbuf.currentData()  # (a hand-set size stays unless you change it)
        s.include_videos = self.videos.isChecked()
        s.include_live_video = self.live.isChecked()
        s.albums_as_folders = self.albums.isChecked()
        s.photos_upload = self.upload.isChecked()
        s.photos_window = self.photos_window.isChecked()
        s.delete_cloud_to_pc = self.del_pc.isChecked()
        s.delete_pc_to_cloud = self.del_cloud.isChecked()
        s.instant_local_deletes = self.instant.isChecked()
        s.delete_threshold = self.threshold.value()
        s.start_with_windows = self.autostart.isChecked()
        s.notifications = self.notif.currentData() != "off"
        if s.notifications:
            s.notification_style = self.notif.currentData()
        s.check_updates = self.updates.isChecked()
        s.language = self.lang.currentData()
        s.theme = self.theme.currentData()
        s.drive_enabled = self.drive_on.isChecked()
        s.drive_dir = drive_dir
        s.drive_delete_cloud_to_pc = self.drive_del_pc.isChecked()
        s.drive_delete_pc_to_cloud = self.drive_del_cloud.isChecked()
        s.drive_folders = self.drive_folders
        s.notes_enabled = self.notes_on.isChecked()
        s.bookmarks_enabled = self.bookmarks_on.isChecked()
        s.bookmarks_dir = self.bookmarks_dir.text().strip() or s.bookmarks_dir
        s.notes_dir = notes_dir
        self.accept()

    def result_settings(self) -> Settings:
        return self.s


# shared parts of the compact windows (Drive folders, update, Find My) -------------------------
_MONO = ["Cascadia Mono", "Cascadia Code", "Consolas"]


def _mono_font(px: float = 12.0) -> QFont:
    """Cascadia Mono for counts, sizes, times and versions (as in the 1.1 design)."""
    f = QFont()
    f.setFamilies(_MONO)
    f.setPixelSize(round(px))
    return f


def _footer_band(outer: QVBoxLayout) -> QHBoxLayout:
    """The band along the bottom of a compact window (as the deletion question's): a quiet link on
    the left, the answers on the right."""
    foot = QWidget()
    foot.setObjectName("Footer")
    foot.setAttribute(Qt.WA_StyledBackground, True)
    fl = QHBoxLayout(foot)
    fl.setContentsMargins(24, 12, 24, 12)
    fl.setSpacing(8)
    outer.addWidget(foot)
    return fl


def _quiet_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("Link")
    b.setCursor(Qt.PointingHandCursor)
    # (its own: over the glass a window's sheet gives every button a light layer; a link has none)
    b.setStyleSheet("QPushButton#Link { background: transparent; border: none; }"
                    "QPushButton#Link:focus { border: 2px solid palette(highlight); border-radius: 4px; }")
    return b


def _layer(c: dict[str, str]) -> str:
    """A light hover layer over a card (see-through, so it suits the glass and the plain theme)."""
    return "rgba(255, 255, 255, 20)" if QColor(c["bg"]).lightness() < 128 else "rgba(0, 0, 0, 10)"


# iCloud Drive folders -----------------------------------------------------------------
class DriveFoldersDialog(_Dialog):
    """Tick the iCloud Drive folders to sync. Folders are read from iCloud as they are opened;
    sizes come from the last full Drive read, when there is one.

    As in the 1.1 design: a compact header, the folder tree on a rounded card (sizes in Cascadia
    Mono), and the footer band with Cancel as a quiet link and Save as the main button."""

    GLASS = True
    PATH = Qt.UserRole
    LOADED = Qt.UserRole + 1

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette  # folders read later are drawn in the new colours
        self._tree_style()
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:  # the folders already shown take the new colours too
            it = stack.pop()
            if it.data(0, self.PATH) is not None:
                self._paint_item(it)
            stack.extend(it.child(i) for i in range(it.childCount()))

    def _tree_style(self) -> None:
        self.tree.setStyleSheet(
            "QTreeView { background: transparent; border: none; padding: 4px; }"
            "QTreeView::item { padding: 5px 2px; border-radius: 6px; }"
            f"QTreeView::item:hover, QTreeView::item:selected {{ background: {_layer(self.c)}; color: {self.c['text']}; }}")

    def _paint_item(self, node) -> None:
        from .theme import service_color

        node.setIcon(0, glyph("folder", service_color(self.c, "folder") or self.c["accent_fg"], 16))
        node.setForeground(1, QColor(self.c.get("muted2", self.c["muted"])))

    def __init__(self, selection: list[str] | None, palette: dict[str, str], get_api,
                 sizes: dict[str, tuple[int, int]] | None = None) -> None:
        super().__init__(t("Choose iCloud Drive folders – {app}", app=APP_NAME))
        self.c, self.get_api, self.sizes = palette, get_api, sizes or {}
        self.initial = list(selection) if selection is not None else None
        self._items: dict[str, Any] = {}  # path -> DriveItem, for reading deeper levels
        self._incomplete: set[str] = set()  # folders ("" = the top) whose list from iCloud left folders out
        self._noted = False  # the line below the tree says so
        self._tasks: list = []
        self._updating = False
        self.setMinimumWidth(480)
        self.resize(*_fit_screen(560, 600))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(24, 22, 24, 16)
        lay.setSpacing(4)
        outer.addWidget(body, 1)
        lay.addWidget(_label(t("What should be synced?"), "Title"))
        lay.addWidget(_label(t("Checked folders are downloaded and kept in sync. Unchecked folders stay in iCloud, "
                               "untouched. Files directly in iCloud Drive (outside any folder) are always synced."),
                             "Lead", True))
        lay.addSpacing(12)
        card = SoftCard()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(4, 4, 4, 4)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setFrameShape(QFrame.NoFrame)
        self.tree.setAccessibleName(t("iCloud Drive folders"))
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.itemExpanded.connect(self._expand)
        self.tree.itemChanged.connect(self._changed)
        self._tree_style()
        cl.addWidget(self.tree)
        lay.addWidget(card, 1)
        lay.addSpacing(6)
        self.info = _label(t("Reading your iCloud Drive folders…"), "Muted", True)
        lay.addWidget(self.info)
        fl = _footer_band(outer)
        cancel = _quiet_button(t("Cancel"))
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton(t("Save"))
        self.ok.setObjectName("Primary")
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self.accept)
        fl.addWidget(cancel)
        fl.addStretch(1)
        fl.addWidget(self.ok)
        self._load(None)

    # reading -------------------------------------------------------------------------------
    def _state_for(self, path: str):
        if self.initial is None or any(path == s or path.startswith(s + "/") for s in self.initial):
            return Qt.Checked
        if any(s.startswith(path + "/") for s in self.initial):
            return Qt.PartiallyChecked
        return Qt.Unchecked

    def _load(self, parent) -> None:
        from ..drive_remote import ROOT_ID, DriveItem, ICloudDrive

        api = self.get_api()
        if api is None:
            self.info.setText(t("Sign in first."))
            return
        path = parent.data(0, self.PATH) if parent is not None else ""
        folder = self._items.get(path) or DriveItem("", ROOT_ID, "root", "com.apple.CloudDocs", None, True)

        def fetch():
            drive = ICloudDrive(api)
            listed = drive.walk(folders=[folder])
            return sorted((it for p, it in listed.items() if it.is_dir and p != path
                           and (p.rsplit("/", 1)[0] if "/" in p else "") == path),
                          key=lambda it: it.path.casefold()), path in drive.incomplete

        def done(answer) -> None:
            children, incomplete = answer
            self.add_children(parent, children, incomplete=incomplete)

        def fail(msg: str) -> None:
            if parent is None:
                self.info.setText(t("Couldn't read iCloud Drive: {error}", error=msg))
            else:
                self.load_failed(parent, msg)

        self._tasks.append(run_async(fetch, done, fail))

    def load_failed(self, parent, msg: str) -> None:
        """A folder inside couldn't be read: say so in it, and read it again when it is opened again."""
        parent.setData(0, self.LOADED, False)
        if parent.childCount() and parent.child(0).data(0, self.PATH) is None:
            note = parent.child(0)  # (the "Loading…" line)
            note.setText(0, t("Couldn't read this folder. Close and open it again to try again."))
            note.setToolTip(0, plain_tip(msg))
        self.info.setText(t("Couldn't read iCloud Drive: {error}", error=msg))

    def add_children(self, parent, children, incomplete: bool = False) -> None:
        from ..ui_text import count_text, size_text

        self._updating = True
        path = parent.data(0, self.PATH) if parent is not None else ""
        if incomplete:
            self._incomplete.add(path)  # iCloud named fewer folders than it holds: Save never widens here
        else:
            self._incomplete.discard(path)
        if parent is not None:
            parent.takeChildren()
            parent.setData(0, self.LOADED, True)
            inherited = parent.checkState(0)
        else:
            inherited = Qt.PartiallyChecked
        for it in children:
            self._items[it.path] = it
            node = QTreeWidgetItem([it.path.rsplit("/", 1)[-1], ""])
            node.setData(0, self.PATH, it.path)
            node.setData(0, self.LOADED, False)
            node.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            node.setCheckState(0, inherited if inherited != Qt.PartiallyChecked else self._state_for(it.path))
            files, size = self.sizes.get(it.path, (0, 0))
            if files:
                node.setText(1, t("{files} files · {size}", files=count_text(files), size=size_text(size)))
                node.setFont(1, _mono_font(12))
                node.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            self._paint_item(node)
            node.addChild(QTreeWidgetItem([t("Loading…"), ""]))  # makes it expandable
            if parent is not None:
                parent.addChild(node)
            else:
                self.tree.addTopLevelItem(node)
        if parent is not None and inherited == Qt.PartiallyChecked and not incomplete:
            # the earlier choice inside may name folders that no longer exist: show what is real
            # (not from an incomplete list: the folders it left out may be the ones chosen)
            kids = [parent.child(i).checkState(0) for i in range(parent.childCount())]
            if kids and all(k == kids[0] for k in kids):
                parent.setCheckState(0, kids[0])
            elif not kids:
                parent.setCheckState(0, Qt.Unchecked)
        self._updating = False
        if parent is None:
            self.ok.setEnabled(True)
            self.info.setText(t("Open a folder to choose only some of the folders inside it.") if children
                              else t("There are no folders in your iCloud Drive."))
        self._update_ok()

    def _expand(self, item) -> None:
        if not item.data(0, self.LOADED):
            item.setData(0, self.LOADED, True)
            if item.childCount() and item.child(0).data(0, self.PATH) is None:  # (again, after a failed try)
                item.child(0).setText(0, t("Loading…"))
                item.child(0).setToolTip(0, "")
            self._load(item)

    # ticking -------------------------------------------------------------------------------
    def _changed(self, item, column: int) -> None:
        if self._updating or column != 0 or item.data(0, self.PATH) is None:
            return
        self._updating = True
        state = item.checkState(0)
        stack = [item.child(i) for i in range(item.childCount())]
        while stack:  # everything inside follows
            child = stack.pop()
            if child.data(0, self.PATH) is not None:
                child.setCheckState(0, state)
                stack.extend(child.child(i) for i in range(child.childCount()))
        parent = item.parent()
        while parent is not None:  # the folders above show "some"
            kids = [parent.child(i).checkState(0) for i in range(parent.childCount())
                    if parent.child(i).data(0, self.PATH) is not None]
            state = kids[0] if kids and all(k == kids[0] for k in kids) else Qt.PartiallyChecked
            path = parent.data(0, self.PATH)
            if path in self._incomplete and state != self._unseen_state(path):
                state = Qt.PartiallyChecked  # the folders the list left out are set otherwise
            parent.setCheckState(0, state)
            parent = parent.parent()
        self._updating = False
        self._update_ok()

    # an incomplete list ----------------------------------------------------------------------
    # When iCloud names fewer folders than a folder holds, the folders left out keep their current
    # setting: Save never adds one (never "all folders, also future ones"), and drops one only with
    # the whole folder around it.
    def _covered(self, path: str) -> bool:
        """Is everything in this folder synced now (so also the folders the list left out)?"""
        if self.initial is None:
            return True
        return bool(path) and any(path == s or path.startswith(s + "/") for s in self.initial)

    def _tops(self) -> list:
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    def _unseen_state(self, path: str):
        """How the folders inside path that the list left out are set now."""
        if self._covered(path):
            return Qt.Checked
        return Qt.PartiallyChecked if any(s.startswith(path + "/") for s in self.initial or []) else Qt.Unchecked

    def _drops_unseen(self) -> bool:
        """Would saving now leave out folders that the incomplete list didn't show (and that are synced
        now)? A list of chosen folders can't say "all of it except this one"."""
        for path in self._incomplete:
            if not self._covered(path):
                continue
            if path == "":
                if not all(it.checkState(0) == Qt.Checked for it in self._tops()):
                    return True
                continue
            node = self._node(path)  # (unticked as a whole: all of it goes, which a list can say)
            if node is not None and node.checkState(0) == Qt.PartiallyChecked:
                return True
        return False

    def _node(self, path: str):
        stack = self._tops()
        while stack:
            it = stack.pop()
            if it.data(0, self.PATH) == path:
                return it
            stack.extend(it.child(i) for i in range(it.childCount()))
        return None

    def _update_ok(self) -> None:
        if not self._incomplete:  # (Save is enabled once the first list is in; a complete list never stops it)
            if self._noted:  # a folder read again, completely this time
                self._noted = False
                self.ok.setEnabled(True)
                self.info.setText(t("Open a folder to choose only some of the folders inside it."))
            return
        self._noted = True
        stuck = self._drops_unseen()
        self.ok.setEnabled(not stuck)
        self.info.setText(t("iCloud didn't list every folder, so this choice can't be saved now: it would leave out "
                            "folders that aren't shown. Try again later.") if stuck else
                          t("iCloud didn't list every folder. Folders that aren't shown keep their current setting."))

    def selection(self) -> list[str] | None:
        """None when every folder is checked (= all of iCloud Drive, including future folders)."""
        tops = self._tops()
        if not tops:
            return self.initial  # nothing to choose from: keep what was set
        partial_root = "" in self._incomplete
        if all(it.checkState(0) == Qt.Checked for it in tops) and (not partial_root or self.initial is None):
            return None
        out: list[str] = []

        def shown_below(it) -> list[str]:
            return [it.child(i).data(0, self.PATH) for i in range(it.childCount())
                    if it.child(i).data(0, self.PATH) is not None]

        def unseen(path: str, shown: list[str]) -> list[str]:
            """The earlier choices inside path that the (incomplete) list didn't show."""
            return [s for s in (self.initial or []) if (s.startswith(path + "/") if path else True)
                    and not any(s == k or s.startswith(k + "/") for k in shown)]

        def visit(it) -> None:
            path, state = it.data(0, self.PATH), it.checkState(0)
            if path is None:
                return
            if path in self._incomplete:  # its list left folders out
                if state == Qt.Unchecked:
                    return  # unticked as a whole
                if state == Qt.Checked and self._covered(path):
                    out.append(path)  # as it was: all of it
                    return
                for i in range(it.childCount()):  # never more than the folders shown and chosen
                    visit(it.child(i))
                out.extend(unseen(path, shown_below(it)))
                return
            if state == Qt.Unchecked:
                return
            if state == Qt.Checked:
                out.append(path)
            elif it.childCount() and it.child(0).data(0, self.PATH) is not None:
                for i in range(it.childCount()):
                    visit(it.child(i))
            else:  # "some" inside a folder that wasn't opened: keep the earlier choice there
                out.extend(s for s in (self.initial or []) if s.startswith(path + "/"))

        for it in tops:
            visit(it)
        if partial_root:
            out.extend(unseen("", [it.data(0, self.PATH) for it in tops]))
        return list(dict.fromkeys(out))


# pending deletions ------------------------------------------------------------------
def _room_for(window: QWidget) -> int:
    """The tallest the inside of a window can be on its screen (above the taskbar, below the title bar)."""
    from PySide6.QtGui import QGuiApplication

    screen = window.screen() or QGuiApplication.primaryScreen()
    if screen is None:
        return 1 << 20
    title = window.frameGeometry().height() - window.height() if window.isVisible() else 0
    return screen.availableGeometry().height() - max(title, 32)


class _FitScroll(QScrollArea):
    """Scrolls its content only when the window can't be tall enough for it: it asks for the whole
    height of the content (at its width), and can give way to a small part of it."""

    LEAST = 96

    def __init__(self, content: QWidget) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.setWidget(content)
        self.viewport().setAutoFillBackground(False)
        content.setAutoFillBackground(False)

    def _content_height(self) -> int:
        w = self.widget()
        lay = w.layout()
        width = self.viewport().width() if self.isVisible() else w.sizeHint().width()
        if lay is not None and lay.hasHeightForWidth():
            return lay.totalHeightForWidth(max(width, 1))
        return w.sizeHint().height()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.widget().sizeHint().width(), self._content_height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(super().minimumSizeHint().width(), min(self.LEAST, self._content_height()))


class PendingDialog(_Dialog):
    """The deletion question, compact as in the 1.1 design: what was deleted where (as a title), a
    short explanation, the photos themselves in a small strip (or the first names), the whole list
    on request, and the two answers. Keeping is the highlighted answer: the safe one."""

    GLASS = True

    decided = Signal(str, list)  # "approve"|"reject", ids
    STRIP = 5

    def __init__(self, pending, palette: dict[str, str], root: Path | None = None, limit: int | None = None) -> None:
        super().__init__(t("Confirm deletion – {app}", app=APP_NAME))
        self.c, self.root, self.limit = palette, root, limit
        self.setMinimumWidth(460)
        self.resize(*_fit_screen(480, 260))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(0)
        outer.addWidget(body, 1)
        self._icon = QLabel()  # (kept for apply_palette; the title says it all)
        self._thumbs = None
        self._strips: dict[str, QLabel] = {}
        self._one = bool(pending.to_pc) != bool(pending.to_cloud)  # one question: answers in the footer
        self._answers = QHBoxLayout()
        self._answers.setSpacing(8)

        drive = getattr(pending, "service", "photos") == "drive"
        if pending.to_pc:
            self._section(lay, pending, pending.to_pc,
                          t("{n} files or folders were deleted in iCloud Drive",
                            n=getattr(pending, "files_to_pc", 0) or len(pending.to_pc)) if drive
                          else t("{n} photos were deleted in iCloud", n=len(pending.to_pc)),
                          t("Delete them on this PC too? They go to the Recycle Bin, where they can be restored."),
                          t("Delete on this PC too"), t("Keep on this PC"), pictures=not drive)
        if pending.to_cloud:
            self._section(lay, pending, pending.to_cloud,
                          t("{n} files or folders were deleted on this PC",
                            n=getattr(pending, "files_to_cloud", 0) or len(pending.to_cloud)) if drive
                          else t("{n} photos were deleted on this PC", n=len(pending.to_cloud)),
                          t("Delete them in iCloud too? They go to Recently Deleted, where they stay for 30 days."),
                          t("Delete in iCloud too"), t("Bring them back to this PC"), pictures=False)

        # the footer band, as in the design: "Decide later" quietly on the left
        foot = QWidget()
        foot.setObjectName("Footer")
        foot.setAttribute(Qt.WA_StyledBackground, True)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(24, 12, 24, 12)
        later = QPushButton(t("Decide later"))
        later.setObjectName("Link")
        later.setCursor(Qt.PointingHandCursor)
        later.clicked.connect(self.reject)
        fl.addWidget(later)
        fl.addStretch(1)
        fl.addLayout(self._answers)
        outer.addWidget(foot)
        self.adjustSize()
        self._fit(shrink=True)

    def _fit(self, shrink: bool = False) -> None:
        """Tall enough for the wrapped lines at this width, measured with the real fonts, but never
        taller than the screen: then the text and lists scroll and the answers stay in view."""
        for w in self.findChildren(QWidget):
            w.ensurePolished()
        # a list just shown or hidden: the layouts inside the scroll areas measure it now, not at the
        # next event (their old size made the window stay as it was and the list stay out of view)
        for s in self.findChildren(_FitScroll):
            inner = s.widget().layout()
            if inner is not None:
                for w in s.widget().findChildren(QWidget):
                    if w.layout() is not None:
                        w.layout().invalidate()
                inner.invalidate()
                inner.activate()
            s.updateGeometry()
        lay = self.layout()
        lay.invalidate()
        lay.activate()
        room = _room_for(self)
        self.setMaximumHeight(max(room, self.minimumSizeHint().height()))
        need = lay.totalHeightForWidth(self.width()) if lay.hasHeightForWidth() else lay.sizeHint().height()
        need = min(need, self.maximumHeight())
        if need > self.height() or (shrink and need < self.height()):
            self.resize(self.width(), need)
        if self.isVisible():  # a taller window may now reach below the screen: move it up
            screen = self.screen()
            if screen is not None:
                avail, frame = screen.availableGeometry(), self.frameGeometry()
                if frame.bottom() > avail.bottom():
                    self.move(self.x(), max(avail.top(), self.y() - (frame.bottom() - avail.bottom())))

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._fit()

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette

    def _section(self, lay, pending, ids, title, question, yes, no, pictures: bool) -> None:
        if getattr(self, "_sections", 0):  # a second question: set it clearly apart from the first
            lay.addSpacing(18)
            sep = QFrame()
            sep.setObjectName("Sep")
            lay.addWidget(sep)
            lay.addSpacing(16)
        self._sections = getattr(self, "_sections", 0) + 1
        # what the question is about scrolls when the screen is short; the answers stay in view
        outer_lay, content = lay, QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(_label(title, "Title", True))
        why = t("More than your limit ({n}) at once, so Skyfold asks first.", n=self.limit) if self.limit else \
            t("More than your limit at once, so Skyfold asks first.")
        lay.addWidget(_label(why + " " + question, "Lead", True))

        names = [pending.names.get(i, i) for i in ids]
        shown = False
        if pictures and self.root is not None:
            from .panel_parts import Thumbs

            strip = QHBoxLayout()
            strip.setContentsMargins(0, 6, 0, 2)
            strip.setSpacing(4)
            if self._thumbs is None:
                self._thumbs = Thumbs(self)
                self._thumbs.ready.connect(self._shot_ready)
            for name in names[:self.STRIP]:
                path = str(Path(self.root) / name)
                if not Thumbs.wanted(path):
                    continue
                shot = QLabel()
                shot.setFixedSize(56, 56)
                shot.setToolTip(plain_tip(name))
                shot.hide()  # shown once its picture is read
                strip.addWidget(shot)
                self._strips[path] = shot
                self._thumbs.request(path)
                shown = True
            if len(names) > self.STRIP:
                more = _label("+" + str(len(names) - self.STRIP), "Mono")
                strip.addWidget(more, 0, Qt.AlignVCenter)
            strip.addStretch(1)
            if shown:
                lay.addLayout(strip)
        if not shown:  # files, or photos that are no longer on this PC: the first few names
            first = names[:3]
            text = "\n".join(first) + ("\n" + t("… and {n} more", n=len(names) - 3) if len(names) > 3 else "")
            lay.addWidget(_label(text, "Mono", True))

        # the whole list, on request: every item the answer covers (a list view holds thousands)
        lst = QListWidget()
        lst.setUniformItemSizes(True)
        lst.addItems(names)
        lst.setTextElideMode(Qt.ElideMiddle)
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lst.setMinimumHeight(120)
        # The global help handler offers the full name only when this list elides it.
        lst.setProperty("tipForClippedItems", True)
        lst.hide()
        toggle = QPushButton(t("Show the list"))
        toggle.setObjectName("Link")
        toggle.setCursor(Qt.PointingHandCursor)

        def flip() -> None:
            lst.setVisible(not lst.isVisible())
            toggle.setText(t("Hide the list") if lst.isVisible() else t("Show the list"))
            self._fit(shrink=True)

            def settle() -> None:  # once Qt has laid the list out: measure again, then show it
                self._fit(shrink=True)
                if lst.isVisible():  # (where the window can't grow enough, the text scrolls to the list)
                    area = next((a for a in self.findChildren(_FitScroll) if a.isAncestorOf(lst)), None)
                    if area is not None:
                        area.ensureWidgetVisible(lst, 0, 0)

            QTimer.singleShot(0, self, settle)

        toggle.clicked.connect(flip)
        lay.addWidget(toggle, 0, Qt.AlignLeft)
        lay.addWidget(lst, 1)
        outer_lay.addWidget(_FitScroll(content), 1)

        row = self._answers if self._one else QHBoxLayout()
        row.setSpacing(8)  # (the two answers never touch)
        if not self._one:
            row.setContentsMargins(0, 14, 0, 0)
        b_no = QPushButton(no)
        b_yes = QPushButton(yes)
        b_yes.setObjectName("Danger")
        b_no.setObjectName("Primary")  # keeping: the safe answer is the highlighted one
        self._open_sections = getattr(self, "_open_sections", 0) + 1
        b_no.clicked.connect(lambda: self._decide("reject", ids, (b_no, b_yes)))
        b_yes.clicked.connect(lambda: self._decide("approve", ids, (b_no, b_yes)))
        if not self._one:
            row.addStretch(1)
        row.addWidget(b_yes)
        row.addWidget(b_no)
        if not self._one:
            outer_lay.addLayout(row)

    def _shot_ready(self, path: str, img) -> None:
        shot = self._strips.get(path)
        if shot is None or img.isNull():
            return
        from .panel_parts import rounded

        shot.setPixmap(rounded(img, 56, 6, max(1.0, self.devicePixelRatioF())))
        shot.show()
        self._fit()  # (the strip appears after the window opened: room for it)

    def _decide(self, decision: str, ids: list, buttons: tuple = ()) -> None:
        self.decided.emit(decision, list(ids))
        for b in buttons:
            b.setEnabled(False)
        if buttons:
            buttons[0].setText(t("Done"))
        self._open_sections = getattr(self, "_open_sections", 1) - 1
        if self._open_sections <= 0:
            self.accept()


# first run: where should the gallery live? -------------------------------------------
class LayoutPicture(QWidget):
    """A tiny drawing of how the photos folder will look: grey lines for photos, accent lines for
    folders, indented as in File Explorer."""

    LINES = {"flat": [("", 0, 0.78), ("", 0, 0.62), ("", 0, 0.70)],
             "year": [("f", 0, 0.32), ("", 10, 0.62), ("", 10, 0.70)],
             "year_month": [("f", 0, 0.32), ("f2", 10, 0.30), ("", 20, 0.66)]}

    def __init__(self, key: str, palette: dict[str, str]) -> None:
        super().__init__()
        self.key, self.c = key, palette
        self.setFixedHeight(46)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.c["tile"]))
        p.drawRoundedRect(r, 6, 6)
        y = 10.0
        for kind, indent, frac in self.LINES.get(self.key, self.LINES["flat"]):
            if kind:
                col = QColor(self.c["accent"])
                if kind == "f2":
                    col.setAlphaF(0.55)
            else:
                col = QColor(self.c["muted2"] if "muted2" in self.c else self.c["muted"])
                col.setAlphaF(0.55)
            p.setBrush(col)
            x = 12 + indent
            p.drawRoundedRect(QRectF(x, y, (r.width() - 24) * frac, 5), 2.5, 2.5)
            y += 10


class LayoutOption(QFrame):
    """A selectable card: a little picture of the folder layout, its name and an example path."""

    clicked = Signal()

    def __init__(self, title: str, example: str, key: str = "flat", palette: dict[str, str] | None = None) -> None:
        super().__init__()
        self.setObjectName("Option")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)  # Tab reaches it; Space or Enter chooses it
        _as_control(self, "radio")  # a screen reader hears a radio button, and whether it is the chosen one
        self.setAccessibleName(f"{title}: {example}")
        col = QVBoxLayout(self)
        col.setContentsMargins(12, 12, 12, 12)
        col.setSpacing(4)
        from .theme import palette as _palette

        self.picture = LayoutPicture(key, palette or _palette("system"))
        col.addWidget(self.picture)
        col.addSpacing(6)
        name = _label(title, "OptionTitle", True)
        col.addWidget(name)
        ex = _label(example, "Mono")
        col.addWidget(ex)
        self.dot = QLabel()  # (kept for older callers; the card's border shows the choice)
        self.dot.hide()
        self.set_selected(False)

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.picture.c = palette
        self.picture.update()

    def set_selected(self, on: bool) -> None:
        was = bool(self.property("selected"))
        self.setProperty("selected", on)
        self.dot.setObjectName("DotOn" if on else "DotOff")
        for w in (self, self.dot):
            w.style().unpolish(w)
            w.style().polish(w)
        if was != on:
            _checked_changed(self)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(e)


class StepDots(QWidget):
    """Where you are in first run: three short bars, the steps done and the current one in the accent."""

    def __init__(self, palette: dict[str, str], steps: int = 3, current: int = 0) -> None:
        super().__init__()
        self.c, self.steps, self.current = palette, steps, current
        self.setFixedSize(steps * 22 + (steps - 1) * 6, 4)
        self.setAccessibleName(t("Step {n} of {total}", n=current + 1, total=steps))

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        self.update()

    def set_current(self, n: int) -> None:
        self.current = n
        self.setAccessibleName(t("Step {n} of {total}", n=n + 1, total=self.steps))
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        for i in range(self.steps):
            p.setBrush(QColor(self.c["accent"] if i <= self.current else self.c["track"]))
            p.drawRoundedRect(QRectF(i * 28, 0, 22, 4), 2, 2)


class Hero(QWidget):
    """The top of first run and signing in: the app's mark, a large title, one line about it and the
    step bars, over a soft glow of the accent."""

    def __init__(self, palette: dict[str, str], step: int) -> None:
        super().__init__()
        self.c = palette
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 30, 36, 14)
        lay.setSpacing(0)
        logo = _logo(50)
        logo.setFixedSize(60, 60)
        logo.setAlignment(Qt.AlignCenter)
        logo.setObjectName("HeroLogo")
        lay.addWidget(logo, 0, Qt.AlignHCenter)
        lay.addSpacing(16)
        self.title = _label("", "HeroBig", True)
        self.title.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.title)
        lay.addSpacing(6)
        self.text = _label("", "Lead", True)
        self.text.setAlignment(Qt.AlignCenter)
        self.text.setContentsMargins(24, 0, 24, 0)
        lay.addWidget(self.text)
        lay.addSpacing(16)
        self.dots = StepDots(palette, 3, step)
        lay.addWidget(self.dots, 0, Qt.AlignHCenter)

    def set_text(self, title: str, text: str, step: int) -> None:
        self.title.setText(title)
        self.text.setText(text)
        self.dots.set_current(step)

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        from PySide6.QtGui import QRadialGradient

        p = QPainter(self)
        g = QRadialGradient(self.width() / 2, 0, max(self.width() * 0.6, 1))
        glow = QColor(self.c["accent"])
        glow.setAlphaF(0.16)
        g.setColorAt(0, glow)
        glow.setAlphaF(0.0)
        g.setColorAt(0.7, glow)
        p.fillRect(self.rect(), g)


class FolderSetupDialog(_Dialog):
    """First run, as in the 1.1 design: step 1 where the photos go (the folder and its layout as three
    pictures), step 2 the plain-language agreement; signing in is step 3, in its own window."""

    GLASS = True

    def __init__(self, current: str, palette: dict[str, str]) -> None:
        super().__init__(t("Welcome – {app}", app=APP_NAME))
        self.c = palette
        self.setFixedWidth(_fit_screen(540, 0)[0])
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.hero = Hero(palette, 0)
        outer.addWidget(self.hero)
        # the steps scroll when the screen is small; the buttons always stay visible
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.steps = QStackedWidget()
        body = QWidget()
        body.setObjectName("Root")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(36, 6, 36, 20)
        bl.addWidget(self.steps)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self._body = body

        # step 1: the folder and its layout
        one = QWidget()
        lay = QVBoxLayout(one)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.path = QLineEdit(current or default_photos_dir())
        self.path.setObjectName("PathField")
        self.path.setAccessibleName(t("Photos folder"))
        from .theme import service_color

        self.path.addAction(glyph("folder", service_color(palette, "photo") or palette["accent"], 16),
                            QLineEdit.LeadingPosition)
        self.path.textChanged.connect(self._validate)
        browse = QPushButton(t("Browse…"))
        browse.setCursor(Qt.PointingHandCursor)
        browse.clicked.connect(self._browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        self.msg = _label("", None, True)
        self.msg.hide()
        lay.addWidget(self.msg)
        lay.addSpacing(8)
        lay.addWidget(_label(t("Layout"), "ListHead"))
        self.layout_choice = "flat"
        self.options: dict[str, LayoutOption] = {}
        examples = {
            "flat": (t("One folder"), "IMG_4411.HEIC"),
            "year": (t("By year"), "2025\\IMG_4411.HEIC"),
            "year_month": (t("By year and month"), "2025\\11\\IMG_4411.HEIC"),
        }
        group = _Group()  # the three cards are one choice: a radio group for screen readers
        _as_control(group, "group")
        group.setAccessibleName(t("Layout"))
        cards = QHBoxLayout(group)
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setSpacing(8)
        for key, (title, example) in examples.items():
            opt = LayoutOption(title, example, key, palette)
            opt.clicked.connect(lambda k=key: self._pick_layout(k))
            self.options[key] = opt
            cards.addWidget(opt, 1)
        lay.addWidget(group)
        self.layout_group = group
        self._pick_layout("flat")
        lay.addWidget(_label(t("An empty folder works best. Folder and layout can be changed later in Settings."),
                             "Hint", True))
        lay.addStretch(1)
        self.steps.addWidget(one)

        # step 2: before you start
        two = QWidget()
        lay2 = QVBoxLayout(two)
        lay2.setContentsMargins(0, 0, 0, 0)
        lay2.setSpacing(8)
        warn = SoftCard()
        warn.setObjectName("SetCard")
        wl = QHBoxLayout(warn)
        wl.setContentsMargins(16, 14, 16, 16)
        wl.setSpacing(14)
        from .glow import GlowTile

        wl.addWidget(GlowTile("shield", palette, palette["warn"], size=32, icon=17, radius=9), 0, Qt.AlignTop)
        wl.addWidget(_label(
            t("Skyfold is free software without any warranty (GNU GPL v3). You use it at your own risk, and its "
              "authors are not liable for lost or changed files. Keep your own backup of anything important."),
            "Lead", True), 1)
        lay2.addWidget(warn)
        agree_card = SoftCard()
        agree_card.setObjectName("SetCard")
        al = QHBoxLayout(agree_card)
        al.setContentsMargins(12, 8, 16, 8)
        self.agree = Toggle(t("I understand and accept this"), palette)
        self.agree.setCursor(Qt.PointingHandCursor)
        self.agree.toggled.connect(lambda _on: self._validate())
        al.addWidget(self.agree)
        lay2.addWidget(agree_card)
        lay2.addStretch(1)
        self.steps.addWidget(two)

        # the footer band: a quiet Later / Back on the left, Continue on the right
        foot = QWidget()
        foot.setObjectName("Footer")
        foot.setAttribute(Qt.WA_StyledBackground, True)
        btns = QHBoxLayout(foot)
        btns.setContentsMargins(36, 14, 36, 14)
        self.back = QPushButton(t("Later"))
        self.back.setObjectName("Link")
        self.back.setCursor(Qt.PointingHandCursor)
        self.back.clicked.connect(self._back)
        self.ok = QPushButton(t("Continue"))
        self.ok.setObjectName("Primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._next)
        btns.addWidget(self.back)
        btns.addStretch(1)
        btns.addWidget(self.ok)
        outer.addWidget(foot)
        self._show_step(0)
        self._fit_height()

    def _show_step(self, n: int) -> None:
        self.steps.setCurrentIndex(n)
        if n == 0:
            self.hero.set_text(t("Where should your photos go?"),
                               t("Choose a folder on a drive with enough space. Skyfold keeps it in step with iCloud."), 0)
            self.back.setText(t("Later"))
        else:
            self.hero.set_text(t("Before you start"), t("Skyfold syncs both ways, deletions included."), 1)
            self.back.setText(t("Back"))
        self._validate()

    def _back(self) -> None:
        if self.steps.currentIndex() == 0:
            self.reject()
        else:
            self._show_step(0)

    def _next(self) -> None:
        if self.steps.currentIndex() == 0:
            error, _warning = check_photos_dir(self.path.text())
            if error is None:
                self._show_step(1)
            return
        self._accept()

    def _fit_height(self) -> None:
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        room = int(screen.availableGeometry().height() * 0.9) if screen else 800
        self._body.adjustSize()
        want = self.hero.sizeHint().height() + max(self.steps.widget(i).sizeHint().height()
                                                    for i in range(self.steps.count())) + 26 + 62
        self.setFixedHeight(min(want, room))

    def chosen_path(self) -> str:
        return str(Path(self.path.text().strip()).expanduser())

    def chosen_layout(self) -> str:
        return self.layout_choice

    def _pick_layout(self, key: str) -> None:
        self.layout_choice = key
        for k, opt in self.options.items():
            opt.set_selected(k == key)

    def _browse(self) -> None:
        start = self.path.text() if Path(self.path.text()).exists() else str(Path.home() / "Pictures")
        d = QFileDialog.getExistingDirectory(self, t("Choose a folder for the gallery"), start)
        if d:
            self.path.setText(str(Path(d)))

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        key = getattr(self, "_msg_key", None)
        if key:
            self.msg.setStyleSheet(f"color: {palette[key]};")

    def _validate(self) -> bool:
        error, warning = check_photos_dir(self.path.text())
        text = error or warning or ""
        self._msg_key = "err_text" if error else "warn_text"
        self.msg.setStyleSheet(f"color: {self.c[self._msg_key]};")
        self.msg.setText(text)
        self.msg.setVisible(bool(text))
        on_agreement = getattr(self, "steps", None) is not None and self.steps.currentIndex() == 1
        self.ok.setEnabled(error is None and (self.agree.isChecked() or not on_agreement))
        if hasattr(self, "_body") and bool(text) != getattr(self, "_had_msg", False):
            self._had_msg = bool(text)
            self._fit_height()
        return error is None and self.agree.isChecked()

    def _accept(self) -> None:
        if not self._validate():
            return
        p = Path(self.chosen_path())
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._msg_key = "err_text"
            self.msg.setStyleSheet(f"color: {self.c['err_text']};")
            self.msg.setText(t("Can't create the folder: {error}", error=exc))
            self.msg.show()
            return
        self.accept()


# Find My ------------------------------------------------------------------------------------
class DeviceRow(SoftCard):
    """One device as a card: its icon on a tile tinted with the accent, the name, the battery and when
    it was located on a quieter line, and its two actions as small buttons."""

    def __init__(self, dev, palette: dict[str, str], get_api) -> None:
        super().__init__()
        from .panel_parts import TintTile

        self.setObjectName("Device")
        self.setMinimumHeight(64)
        self.dev, self.c, self.get_api = dev, palette, get_api
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(14)
        self._tile = TintTile(36, 9)
        tl = QVBoxLayout(self._tile)
        tl.setContentsMargins(0, 0, 0, 0)
        icon = QLabel()
        icon.setAlignment(Qt.AlignCenter)
        tl.addWidget(icon)
        self._icon = icon
        col = QVBoxLayout()
        col.setSpacing(1)
        name = _label(dev.name, "SetTitle", True)
        name.setTextFormat(Qt.PlainText)  # (names come from iCloud: never read as markup)
        plain, rich = self._details(dev)
        detail = _label(rich, "SetSub", True)
        detail.setTextFormat(Qt.RichText)
        detail.setTextInteractionFlags(Qt.NoTextInteraction)
        col.addWidget(name)
        col.addWidget(detail)
        lay.addWidget(self._tile, 0, Qt.AlignVCenter)
        lay.addLayout(col, 1)
        self.setAccessibleName(f"{dev.name}, {plain}")
        self.map_btn = QPushButton(t("Map"))
        self.map_btn.setToolTip(t("Opens Google Maps in your browser with the device's last location."))
        self.map_btn.setAccessibleName(f"{t('Map')}: {dev.name}")
        self.map_btn.setEnabled(dev.lat is not None)
        self.map_btn.clicked.connect(self._map)
        self.sound_btn = QPushButton(t("Play sound"))
        self.sound_btn.setAccessibleName(f"{t('Play sound')}: {dev.name}")
        self.sound_btn.setEnabled(dev.can_sound)
        self.sound_btn.clicked.connect(self._sound)
        for btn in (self.map_btn, self.sound_btn):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setIconSize(QSize(14, 14))
            btn.setStyleSheet("min-height: 28px; max-height: 28px; padding: 0 12px;")  # small, as in the design
            lay.addWidget(btn, 0, Qt.AlignVCenter)
        self.apply_palette(palette)

    @staticmethod
    def _details(dev) -> tuple[str, str]:
        """The quieter line: model, battery, online and when it was located (numbers and times in
        Cascadia Mono). Returns it as plain text (screen readers) and as markup."""
        import html

        mono = "<span style=\"font-family:'Cascadia Mono','Cascadia Code','Consolas'\">{}</span>"
        plain: list[str] = []
        rich: list[str] = []
        if dev.model:
            plain.append(dev.model)
            rich.append(html.escape(dev.model))
        if dev.battery is not None:
            pct = f"{round(dev.battery * 100)}%"
            extra = " " + t("charging") if getattr(dev, "charging", False) else ""
            plain.append(pct + extra)
            rich.append(mono.format(pct) + html.escape(extra))
        state = t("online") if dev.online else t("offline")
        plain.append(state)
        rich.append(html.escape(state))
        if dev.located_at:
            from ..worker import fmt_time

            when = fmt_time(dev.located_at)
            plain.append(t("located {when}", when=when))
            rich.append(html.escape(t("located {when}", when="\x00")).replace("\x00", mono.format(html.escape(when))))
        return " · ".join(plain), " · ".join(rich)

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        tint = QColor(palette["accent_fg"])
        tint.setAlphaF(0.14 if QColor(palette["bg"]).lightness() > 128 else 0.20)
        self._tile.set_fill(tint)
        self._icon.setPixmap(glyph("device", palette["accent_fg"], 20).pixmap(QSize(20, 20)))
        self.map_btn.setIcon(glyph("pin", palette["text"], 14))
        self.sound_btn.setIcon(glyph("bell", palette["text"], 14))

    def _map(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        d = self.dev
        QDesktopServices.openUrl(QUrl(f"https://www.google.com/maps/search/?api=1&query={d.lat},{d.lon}"))

    def _sound(self) -> None:
        from shiboken6 import isValid

        from .. import extras

        api = self.get_api()
        if api is None:
            return
        self.sound_btn.setEnabled(False)
        self.sound_btn.setText(t("Sending…"))

        def done(_r) -> None:
            if isValid(self) and self.isVisible():
                self.sound_btn.setText(t("Sound sent ✓"))

        def fail(msg: str) -> None:
            if not isValid(self) or not self.isVisible():
                return
            self.sound_btn.setEnabled(True)
            self.sound_btn.setText(t("Play sound"))
            warn(self, t("Find My"), msg)

        device_id = self.dev.id
        self._task = run_async(lambda: extras.play_sound(api, device_id), done, fail)


class _Progress(QObject):
    moved = Signal(int, int)  # bytes so far, total (emitted from the download thread)


def _current_palette() -> dict[str, str]:
    """The theme the app shows now (for a window that isn't handed one): told apart by the window
    colour the app's palette was set to (theme.qpalette)."""
    from .theme import DARK, LIGHT, OLED, palette

    bg = QApplication.palette().color(QPalette.Window).name().lower() if QApplication.instance() else ""
    for c in (OLED, DARK, LIGHT):
        if bg == c["bg"].lower():
            return c
    return palette("system")


class _GlowHead(QWidget):
    """The top of a compact window: a soft glow of the accent behind the mark (as Hero's)."""

    def __init__(self, palette: dict[str, str]) -> None:
        super().__init__()
        self.c = palette

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        from PySide6.QtGui import QRadialGradient

        p = QPainter(self)
        # around the mark, gone before the bottom edge (no visible seam with the body under it)
        g = QRadialGradient(50, 50, max(self.height(), 1))
        glow = QColor(self.c["accent"])
        glow.setAlphaF(0.16)
        g.setColorAt(0, glow)
        glow.setAlphaF(0.0)
        g.setColorAt(0.55, glow)
        p.fillRect(self.rect(), g)


class UpdateDialog(_Dialog):
    """A newer Skyfold: what's new (the release notes) and Update now / Later. As in the 1.1 design:
    the mark, a large title and the versions in Cascadia Mono, the notes readable on a card, and the
    footer band ("Later" quietly on the left)."""

    GLASS = True
    ready = Signal(object)  # the unpacked new version: the controller installs it and quits

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        self.notes.style_notes(palette)

    def __init__(self, rel, can_install: bool, palette: dict[str, str] | None = None) -> None:
        import html

        super().__init__(t("Update – {app}", app=APP_NAME))
        self.c = palette or _current_palette()
        self.rel = rel
        self.can_install = can_install
        self._task: Any = None
        self._cancel = False
        self.setMinimumWidth(500)
        self.resize(*_fit_screen(580, 600))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = _GlowHead(self.c)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(24, 24, 24, 8)
        hl.setSpacing(16)
        logo = _logo(44)
        logo.setFixedSize(52, 52)
        hl.addWidget(logo, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = _label(t("Skyfold {version} is available", version=rel.version), "Title", True)
        title.setTextFormat(Qt.PlainText)
        col.addWidget(title)
        mono = "<span style=\"font-family:'Cascadia Mono','Cascadia Code','Consolas'\">{}</span>"
        have = _label(html.escape(t("You have version {version}.", version="\x00")).replace(
            "\x00", mono.format(html.escape(VERSION))), "Lead", True)
        have.setTextFormat(Qt.RichText)
        have.setTextInteractionFlags(Qt.NoTextInteraction)
        have.setAccessibleName(t("You have version {version}.", version=VERSION))
        col.addWidget(have)
        hl.addLayout(col, 1)
        outer.addWidget(head)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(24, 8, 24, 16)
        lay.setSpacing(4)
        outer.addWidget(body, 1)
        lay.addWidget(_label(t("What's new"), "GroupLabel"))
        lay.addSpacing(4)
        card = SoftCard()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(6, 6, 6, 6)
        self.notes = _ReleaseNotes()
        # links in the release notes: only https pages open (in the browser); a file or network
        # path written into them (the notes aren't covered by the build attestation) never does
        self.notes.setOpenExternalLinks(False)
        self.notes.setOpenLinks(False)
        self.notes.anchorClicked.connect(
            lambda u: u.scheme() == "https" and _open_url(u.toString()))
        self.notes.setAccessibleName(t("What's new"))
        from ..updater import whats_new

        self.notes.setMarkdown(whats_new(rel.notes) or t("No description for this version."))
        self.notes.style_notes(self.c)
        cl.addWidget(self.notes)
        lay.addWidget(card, 1)
        lay.addSpacing(8)
        self.status = _label(
            t("Skyfold downloads the new version, checks it, closes and starts again. Your settings and "
              "synced files stay as they are.") if can_install else
            t("Download the new version from GitHub and replace your Skyfold folder with it (settings are kept)."),
            "Hint", True)
        lay.addWidget(self.status)
        fl = _footer_band(outer)
        self.later = _quiet_button(t("Later"))
        self.later.setAutoDefault(False)
        self.later.clicked.connect(self.reject)
        self.go = QPushButton(t("Update now") if can_install else t("Open download page"))
        self.go.setObjectName("Primary")
        self.go.setDefault(True)
        self.go.clicked.connect(self._go)
        fl.addWidget(self.later)
        fl.addStretch(1)
        fl.addWidget(self.go)

    def _go(self) -> None:
        from .. import updater

        if not self.can_install:
            _open_url(self.rel.page)
            self.accept()
            return
        self.go.setEnabled(False)
        self.status.setText(t("Downloading…"))
        progress = _Progress()
        progress.moved.connect(self._moved, Qt.QueuedConnection)
        self._progress = progress

        def done(new) -> None:
            if self._cancel:
                return
            self.status.setText(t("Installing… Skyfold closes and starts again."))
            self.ready.emit(new)

        def fail(msg: str) -> None:
            if self._cancel:
                return
            log.warning("Update failed: %s", msg)
            self.status.setText(t("The update couldn't be done: {error}", error=msg))
            self.go.setEnabled(True)

        from .. import provenance

        self._task = run_async(lambda: updater.download(self.rel, progress=progress.moved.emit,
                                                        cancelled=lambda: self._cancel,
                                                        verify=provenance.verify), done, fail)

    def install_failed(self, msg: str) -> None:
        """The new version couldn't be handed over: the window can try again."""
        self.status.setText(msg)
        self.go.setEnabled(True)

    def _moved(self, done: int, total: int) -> None:
        if total:
            self.status.setText(t("Downloading… {pct}%", pct=round(100 * done / total)))

    def reject(self) -> None:
        self._cancel = True
        super().reject()


class FindMyDialog(_Dialog):
    """Your Apple devices with battery, last location (opens a map) and 'play sound': each device a
    card, a compact header with Refresh, and the footer band."""

    GLASS = True
    signInAgain = Signal()

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette  # rows made later (Refresh) take the new colours too
        self.refresh_btn.setIcon(glyph("sync", palette["text"], 14))

    def __init__(self, palette: dict[str, str], get_api) -> None:
        super().__init__(t("Find My – {app}", app=APP_NAME))
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._closed = False
        self.c, self.get_api = palette, get_api
        self.setMinimumWidth(560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(24, 22, 24, 16)
        lay.setSpacing(4)
        outer.addWidget(body, 1)
        head = QHBoxLayout()
        head.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(_label(t("Find My"), "Title"))
        col.addWidget(_label(t("The location opens in Google Maps in your browser. "
                               "Lost device? Use iCloud.com for Lost Mode or erasing."), "Lead", True))
        head.addLayout(col, 1)
        self.refresh_btn = QPushButton(t("Refresh"))
        self.refresh_btn.setIconSize(QSize(14, 14))
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.load)
        head.addWidget(self.refresh_btn, 0, Qt.AlignTop)
        lay.addLayout(head)
        lay.addSpacing(12)
        self.info = _label(t("Locating your devices…"), "Muted", True)
        lay.addWidget(self.info)
        self.signin_btn = QPushButton(t("Sign in again"))
        self.signin_btn.setObjectName("Primary")
        self.signin_btn.clicked.connect(self._sign_in_again)
        self.signin_btn.hide()
        lay.addWidget(self.signin_btn, 0, Qt.AlignLeft)
        self.card = QWidget()
        self.card.setObjectName("Root")
        self.rows = QVBoxLayout(self.card)
        self.rows.setContentsMargins(0, 0, 0, 2)
        self.rows.setSpacing(4)
        self.card.hide()
        self._devices_scroll = QScrollArea()
        self._devices_scroll.setWidgetResizable(True)
        self._devices_scroll.setFrameShape(QFrame.NoFrame)
        self._devices_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._devices_scroll.setWidget(self.card)
        self._devices_scroll.setMaximumHeight(_fit_screen(10_000, 600)[1] - 220)
        self._devices_scroll.hide()
        lay.addWidget(self._devices_scroll)
        lay.addStretch(1)
        fl = _footer_band(outer)
        web = _quiet_button(t("Open iCloud.com"))
        web.clicked.connect(lambda: _open_url("https://www.icloud.com"))
        close = QPushButton(t("Close"))
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        fl.addWidget(web)
        fl.addStretch(1)
        fl.addWidget(close)
        self.apply_palette(palette)
        self.load()

    def _sign_in_again(self) -> None:
        self.signInAgain.emit()
        self.accept()

    def done(self, result: int) -> None:
        self._closed = True
        super().done(result)

    def load(self) -> None:
        from .. import extras

        self.signin_btn.hide()
        api = self.get_api()
        if api is None:
            self.info.setText(t("Sign in first."))
            self.signin_btn.setText(t("Sign in"))
            self.signin_btn.show()
            return
        self.refresh_btn.setEnabled(False)
        self.info.setText(t("Locating your devices…"))
        self.info.show()

        def done(devs) -> None:
            if self._closed:
                return  # a reply for the previous window or account
            self.refresh_btn.setEnabled(True)
            while self.rows.count():
                w = self.rows.takeAt(0).widget()
                if w:
                    w.deleteLater()
            for d in devs:
                self.rows.addWidget(DeviceRow(d, self.c, self.get_api))
            self._shown_at = time.strftime("%H:%M")  # (said when a later refresh fails)
            self.card.setVisible(bool(devs))
            self._devices_scroll.setVisible(bool(devs))
            self.info.setVisible(not devs)
            self.info.setText(t("No devices found."))
            self.adjustSize()

        def fail(msg: str) -> None:
            if self._closed:
                return
            self.refresh_btn.setEnabled(True)
            text = msg
            if self._devices_scroll.isVisibleTo(self) and getattr(self, "_shown_at", None):
                # the devices below are still the earlier answer: their battery and "online" may be old
                text = t("Couldn't refresh: {error}", error=msg) + "\n" + \
                    t("Shown as of the last refresh at {time}.", time=self._shown_at)
            self.info.setText(text)
            self.info.show()
            if msg == extras.sign_in_again_text():
                self.signin_btn.show()

        first, self._loaded = not getattr(self, "_loaded", False), True
        self._task = run_async(lambda: extras.devices(api, refresh=not first), done, fail)
