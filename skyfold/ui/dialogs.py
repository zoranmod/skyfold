"""Sign-in, settings and deletion-confirmation dialogs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
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
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)

from .. import auth
from ..config import APP_IN_CLOUD, APP_NAME, FOLDER_STRUCTURES, PRIVATE_DIR, VERSION, Settings, check_photos_dir, default_photos_dir
from ..i18n import LANGUAGES, language, t
from .icons import app_icon, draw_logo, glyph


# async helper ------------------------------------------------------------------
class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))
        else:
            self.signals.done.emit(result)


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
        self._on_done(result)

    @Slot(str)
    def failed(self, msg: str) -> None:
        self._on_fail(msg)


def run_async(fn: Callable[[], Any], on_done: Callable[[Any], None], on_fail: Callable[[str], None]) -> _Task:
    task = _Task(fn)
    task.setAutoDelete(False)  # we keep a reference; Qt must not delete it under us
    task.relay = _Relay(on_done, on_fail)
    task.signals.done.connect(task.relay.done, Qt.QueuedConnection)
    task.signals.failed.connect(task.relay.failed, Qt.QueuedConnection)
    QThreadPool.globalInstance().start(task)
    return task


def _open_url(url: str) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl(url))


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


def _box(parent, icon, title: str, text: str, yes_no: bool) -> int:
    """QMessageBox with the buttons in the app's language (Qt's own Yes/No/OK stay English)."""
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    if yes_no:
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.button(QMessageBox.Yes).setText(t("Yes"))
        box.button(QMessageBox.No).setText(t("No"))
        box.setDefaultButton(QMessageBox.Yes)
    else:
        box.setStandardButtons(QMessageBox.Ok)
        box.button(QMessageBox.Ok).setText(t("OK"))
    return box.exec()


def ask(parent, title: str, text: str) -> int:
    """Returns QMessageBox.Yes or QMessageBox.No."""
    return _box(parent, QMessageBox.Question, title, text, True)


def inform(parent, title: str, text: str) -> None:
    _box(parent, QMessageBox.Information, title, text, False)


def warn(parent, title: str, text: str) -> None:
    _box(parent, QMessageBox.Warning, title, text, False)


def _label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


class _Dialog(QDialog):
    def __init__(self, title: str) -> None:
        # A normal top-level window: own taskbar button (so it can be minimised and found again),
        # and it goes behind other windows like any app instead of floating on top.
        super().__init__(None, Qt.Window | Qt.WindowTitleHint | Qt.WindowMinimizeButtonHint
                         | Qt.WindowCloseButtonHint)
        self.setWindowTitle(title)
        self.setWindowIcon(app_icon())
        self.setObjectName("Root")


class Toggle(QCheckBox):
    """Windows 11 style switch drawn by hand (QSS cannot draw the knob)."""

    PAD = 4  # room around the switch for the focus ring, so it is never clipped

    def __init__(self, text: str, palette: dict[str, str]) -> None:
        super().__init__(text)
        self.c = palette
        self.setMinimumHeight(30)
        self.setAttribute(Qt.WA_Hover)

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = self.fontMetrics()
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
        x = self.PAD
        track = QRectF(x + 0.5, y + 0.5, w - 1, h - 1)
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["accent_hover"] if hover else c["accent"]))
        else:
            p.setPen(QPen(QColor(c["text"] if hover else c["border_strong"]), 1.2))
            p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(track, h / 2, h / 2)
        knob = 14 if on else 12
        cx = x + (w - h / 2 if on else h / 2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff") if on else QColor(c["muted"]))
        p.drawEllipse(QPointF(cx, y + h / 2), knob / 2, knob / 2)
        if self.hasFocus():
            p.setPen(QPen(QColor(c["accent"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(track.adjusted(-2.5, -2.5, 2.5, 2.5), h / 2 + 2, h / 2 + 2)
        p.setPen(QColor(c["text"]))
        p.drawText(QRectF(x + w + 12, 0, self.width() - x - w - 12, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())


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


def _field(w: QLineEdit) -> QLineEdit:
    w.setFixedHeight(32)
    return w


# login ------------------------------------------------------------------------
class LoginDialog(_Dialog):
    loggedIn = Signal(object, str)  # api, apple_id

    def __init__(self, apple_id: str = "") -> None:
        super().__init__(t("Sign in – {app}", app=APP_NAME))
        self.setFixedWidth(420)
        self.api: Any = None
        self._task: Any = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 26, 28, 22)
        outer.setSpacing(14)
        outer.addWidget(_logo(48))
        title = _label(t("Sign in to iCloud"), "Title")
        title.setAlignment(Qt.AlignCenter)
        outer.addWidget(title)

        self.pages = QStackedWidget()
        outer.addWidget(self.pages)

        # page 1: credentials
        p1 = QWidget()
        l1 = QVBoxLayout(p1)
        l1.setContentsMargins(0, 0, 0, 0)
        l1.setSpacing(8)
        sub = _label(t("Use the Apple ID you use on your iPhone and iPad."), "Muted", True)
        sub.setAlignment(Qt.AlignCenter)
        l1.addWidget(sub)
        l1.addSpacing(6)
        self.email = _field(QLineEdit(apple_id))
        self.email.setPlaceholderText(t("Apple ID (email)"))
        self.password = _field(QLineEdit())
        self.password.setPlaceholderText(t("Password"))
        self.password.setEchoMode(QLineEdit.Password)
        self.password.returnPressed.connect(self._submit_credentials)
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
        self.code.setMaxLength(7)
        self.code.setAlignment(Qt.AlignCenter)
        self.code.setStyleSheet("font-size: 20pt; letter-spacing: 8px; padding: 10px;")
        self.code.returnPressed.connect(self._submit_code)
        self.code.textChanged.connect(lambda t: len(t.replace(" ", "")) == 6 and self._submit_code())
        l2.addWidget(self.code_info)
        l2.addWidget(self.code)
        self.pages.addWidget(p2)

        self.error = _label("", "ErrorText", True)
        self.error.hide()
        outer.addWidget(self.error)

        btns = QHBoxLayout()
        self.cancel = QPushButton(t("Cancel"))
        self.cancel.clicked.connect(self.reject)
        self.ok = QPushButton(t("Sign in"))
        self.ok.setObjectName("Primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._next)
        btns.addWidget(self.cancel)
        btns.addStretch(1)
        btns.addWidget(self.ok)
        outer.addLayout(btns)
        (self.password if apple_id else self.email).setFocus()

    def _busy(self, busy: bool, text: str | None = None) -> None:
        self.ok.setEnabled(not busy)
        self.email.setEnabled(not busy)
        self.password.setEnabled(not busy)
        self.code.setEnabled(not busy)
        if text:
            self.ok.setText(text)

    def _shrink(self) -> None:
        lay = self.layout()
        lay.activate()
        h = lay.totalHeightForWidth(self.width()) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        self.resize(self.width(), h)

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
        email, pw = self.email.text().strip(), self.password.text()
        if not email or not pw:
            self._show_error(t("Enter your Apple ID and password."))
            return
        self._show_error("")
        self._busy(True, t("Signing in…"))

        def done(step: auth.LoginStep) -> None:
            if not self.isVisible():
                return  # cancelled meanwhile: this sign-in is not used
            self.password.clear()
            self.api = step.api
            if step.needs_code:
                self.code_info.setText(step.notice)
                # the stack keeps the tallest page's height: take the sign-in page out
                self._page1 = self.pages.widget(0)
                self.pages.removeWidget(self._page1)
                self._page1.hide()
                self.pages.setCurrentIndex(0)
                self._shrink()
                self._busy(False, t("Confirm"))
                self.code.setFocus()
            else:
                self._finish()

        def fail(msg: str) -> None:
            self._busy(False, t("Sign in"))
            self._show_error(msg)
            self.password.selectAll()
            self.password.setFocus()

        self._task = run_async(lambda: auth.start_login(email, pw), done, fail)

    def _submit_code(self) -> None:
        code = self.code.text().replace(" ", "")
        if len(code) != 6 or not self.ok.isEnabled():
            return
        self._show_error("")
        self._busy(True, t("Checking…"))

        def fail(msg: str) -> None:
            self._busy(False, t("Confirm"))
            self.code.clear()
            self.code.setFocus()
            self._show_error(msg)

        self._task = run_async(lambda: auth.submit_code(self.api, code), lambda _r: self._finish(), fail)

    def _finish(self) -> None:
        if not self.isVisible():
            return  # the dialog was cancelled while Apple was still answering
        self.loggedIn.emit(self.api, self.email.text().strip())
        self.accept()


# settings -----------------------------------------------------------------------
POLL_PRESETS = (1, 2, 5, 10, 15, 30, 60)


class SettingsDialog(_Dialog):
    """Left navigation (General · Photos · iCloud Drive · About) with one scrolling page each."""

    signOut = Signal()
    restoreExcluded = Signal()

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
        self.resize(*_fit_screen(760, 600))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        main = QHBoxLayout()
        main.setSpacing(0)
        outer.addLayout(main, 1)

        nav_pane = QWidget()
        nav_pane.setObjectName("NavPane")
        nav_pane.setAttribute(Qt.WA_StyledBackground, True)
        nav_pane.setFixedWidth(190)
        nl = QVBoxLayout(nav_pane)
        nl.setContentsMargins(12, 20, 12, 12)
        nl.setSpacing(12)
        title = _label(t("Settings"), "Title")
        title.setContentsMargins(8, 0, 0, 0)
        nl.addWidget(title)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setIconSize(QSize(18, 18))
        nl.addWidget(self.nav, 1)
        main.addWidget(nav_pane)

        self.stack = QStackedWidget()
        main.addWidget(self.stack, 1)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        self._build_general()
        self._build_photos()
        self._build_drive()
        self._build_notes()
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

    # page helpers ---------------------------------------------------------------------
    def _page(self, name: str, glyph_name: str) -> QVBoxLayout:
        item = QListWidgetItem(glyph(glyph_name, self.c["muted"]), name)
        self.nav.addItem(item)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("Root")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(8)
        scroll.setWidget(body)
        self.stack.addWidget(scroll)
        return lay

    def _card(self, parent: QVBoxLayout, title: str) -> QVBoxLayout:
        parent.addSpacing(8)
        parent.addWidget(_label(title, "Section"))
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)
        parent.addWidget(card)
        return lay

    @staticmethod
    def _row(text: str, w: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(QLabel(text), 1, Qt.AlignVCenter)
        w.setFixedWidth(250)
        row.addWidget(w, 0, Qt.AlignVCenter)
        return row

    def _toggle(self, parent: QVBoxLayout, text: str, value: bool) -> Toggle:
        cb = Toggle(text, self.c)
        cb.setChecked(value)
        cb.setCursor(Qt.PointingHandCursor)
        parent.addWidget(cb)
        return cb

    @staticmethod
    def _hint(parent: QVBoxLayout, text: str) -> QLabel:
        lbl = _label(text, "Hint", True)
        lbl.setContentsMargins(56, 0, 0, 2)  # lines up with the toggle's text (Toggle.PAD + 52)
        parent.addWidget(lbl)
        return lbl

    def _folder_row(self, parent: QVBoxLayout, value: str, title: str) -> QLineEdit:
        row = QHBoxLayout()
        row.setSpacing(8)
        edit = _field(QLineEdit(value))
        browse = QPushButton(t("Change…"))
        browse.clicked.connect(lambda: self._browse(edit, title))
        row.addWidget(edit, 1)
        row.addWidget(browse)
        parent.addLayout(row)
        return edit

    def _browse(self, edit: QLineEdit, title: str) -> None:
        d = QFileDialog.getExistingDirectory(self, title, edit.text())
        if d:
            edit.setText(str(Path(d)))

    # pages ------------------------------------------------------------------------------
    def _build_general(self) -> None:
        lay = self._page(t("General"), "gear")
        acc = self._card(lay, t("ACCOUNT"))
        row = QHBoxLayout()
        who = QVBoxLayout()
        who.setSpacing(2)
        who.addWidget(_label(self.s.apple_id or t("Not signed in"), "H2"))
        who.addWidget(_label("iCloud", "Muted"))
        row.addLayout(who, 1)
        out = QPushButton(t("Sign out"))
        out.setObjectName("Danger")
        out.setEnabled(bool(self.s.apple_id))
        out.clicked.connect(self._sign_out)
        row.addWidget(out, 0, Qt.AlignVCenter)
        acc.addLayout(row)

        sync = self._card(lay, t("SYNC"))
        self.poll = Combo()
        for m in POLL_PRESETS:
            self.poll.addItem(t("{n} min", n=m), m)
        idx = min(range(len(POLL_PRESETS)), key=lambda i: abs(POLL_PRESETS[i] - self.s.poll_minutes))
        self.poll.setCurrentIndex(idx)
        sync.addLayout(self._row(t("Check iCloud every"), self.poll))
        sync.addWidget(_label(t("Apple doesn't notify apps about changes, so iCloud is checked at intervals. Each check only "
                                "reads what changed; the whole library is compared once a day."), "Hint", True))
        self.limit = Combo()
        for mb in (0, 1, 2, 5, 10, 20, 50):
            self.limit.addItem(t("No limit") if not mb else t("{n} MB/s", n=mb), mb)
        self.limit.setCurrentIndex(max(0, self.limit.findData(self.s.speed_limit_mb)))
        sync.addLayout(self._row(t("Download speed limit"), self.limit))
        sync.addWidget(_label(t("Keeps some of your connection free while Skyfold downloads, for example during "
                                "video calls."), "Hint", True))
        self.threshold = Spin(1, 10000, self.s.delete_threshold)
        sync.addLayout(self._row(t("Ask before deleting more than"), self.threshold))
        self.instant = self._toggle(sync, t("Send deletions on this PC right away (watch the folder)"),
                                    self.s.instant_local_deletes)

        sysc = self._card(lay, t("SYSTEM"))
        self.autostart = self._toggle(sysc, t("Start with Windows"), self.s.start_with_windows)
        self.notif = self._toggle(sysc, t("Show notifications"), self.s.notifications)
        self.lang = Combo()
        for code, name in LANGUAGES.items():
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(list(LANGUAGES).index(self.s.language) if self.s.language in LANGUAGES else 0)
        sysc.addLayout(self._row(t("Language"), self.lang))
        from .theme import THEMES

        self.theme = Combo()
        for code, name in THEMES.items():
            self.theme.addItem(t(name), code)
        self.theme.setCurrentIndex(list(THEMES).index(self.s.theme) if self.s.theme in THEMES else 0)
        sysc.addLayout(self._row(t("Theme"), self.theme))
        lay.addStretch(1)

    def _build_photos(self) -> None:
        lay = self._page(t("Photos"), "photo")
        fold = self._card(lay, t("PHOTOS FOLDER"))
        self.dir = self._folder_row(fold, self.s.photos_dir, t("Choose a folder for photos"))
        self.structure = Combo()
        for key, label in FOLDER_STRUCTURES.items():
            self.structure.addItem(t(label), key)
        self.structure.setCurrentIndex(list(FOLDER_STRUCTURES).index(self.s.folder_structure))
        fold.addLayout(self._row(t("Layout"), self.structure))
        fold.addWidget(_label(t("The layout applies to new photos; existing ones stay where they are."), "Hint", True))

        media = self._card(lay, t("DOWNLOAD"))
        self.videos = self._toggle(media, t("Download videos"), self.s.include_videos)
        self.live = self._toggle(media, t("Download the video part of Live Photos (.MOV)"), self.s.include_live_video)
        self.albums = self._toggle(media, t("Show albums as folders (Albums\\…)"), self.s.albums_as_folders)
        self._hint(media, t("Album folders use no extra disk space. They mirror iCloud; deleting there doesn't delete photos."))

        dele = self._card(lay, t("DELETING"))
        self.del_pc = self._toggle(dele, t("Deleted on the phone → delete on this PC too"), self.s.delete_cloud_to_pc)
        self._hint(dele, t("The file goes to the Recycle Bin, where it can be restored."))
        self.del_cloud = self._toggle(dele, t("Deleted on this PC → delete in iCloud too"), self.s.delete_pc_to_cloud)
        self._hint(dele, t("The photo goes to 'Recently Deleted' on the phone and can be restored for 30 days."))
        restore = QPushButton(t("Bring back photos I deleted only on this PC"))
        restore.setObjectName("Link")
        restore.setCursor(Qt.PointingHandCursor)
        restore.setToolTip(t("Downloads again the photos deleted on this PC while deleting in iCloud was off."))
        restore.clicked.connect(self._restore_excluded)
        dele.addWidget(restore, 0, Qt.AlignLeft)
        lay.addStretch(1)

    def _build_drive(self) -> None:
        lay = self._page(t("iCloud Drive"), "folder")
        head = QHBoxLayout()
        head.addWidget(_label(t("iCloud Drive"), "H2"), 1)
        self.drive_on = Toggle(t("Enabled"), self.c)
        self.drive_on.setChecked(self.s.drive_enabled)
        self.drive_on.setCursor(Qt.PointingHandCursor)
        head.addWidget(self.drive_on)
        lay.addLayout(head)
        lay.addWidget(_label(t("Keeps a folder on this PC and your iCloud Drive the same, in both directions."),
                             "Muted", True))

        self.drive_cards: list[QFrame] = []
        fold = self._card(lay, t("DRIVE FOLDER"))
        self.drive_cards.append(fold.parentWidget())
        self.drive_dir = self._folder_row(fold, self.s.drive_dir, t("Choose a folder for iCloud Drive"))
        fold.addWidget(_label(t("Files are downloaded to this PC; there are no online-only placeholders."),
                              "Hint", True))

        which = self._card(lay, t("FOLDERS"))
        self.drive_cards.append(which.parentWidget())
        row = QHBoxLayout()
        self.folders_text = _label("", None, True)
        row.addWidget(self.folders_text, 1)
        choose = QPushButton(t("Choose folders…"))
        choose.setCursor(Qt.PointingHandCursor)
        choose.clicked.connect(self._choose_folders)
        row.addWidget(choose)
        which.addLayout(row)
        which.addWidget(_label(t("Folders you leave out stay in iCloud untouched and aren't downloaded."), "Hint", True))
        self._show_folders()

        dele = self._card(lay, t("DELETING"))
        self.drive_cards.append(dele.parentWidget())
        self.drive_del_pc = self._toggle(dele, t("Deleted in iCloud → delete on this PC too"),
                                         self.s.drive_delete_cloud_to_pc)
        self._hint(dele, t("The file goes to the Recycle Bin, where it can be restored."))
        self.drive_del_cloud = self._toggle(dele, t("Deleted on this PC → delete in iCloud too"),
                                            self.s.drive_delete_pc_to_cloud)
        self._hint(dele, t("The file goes to 'Recently Deleted' in iCloud Drive and can be restored for 30 days."))

        info = self._card(lay, t("GOOD TO KNOW"))
        self.drive_cards.append(info.parentWidget())
        for line in (t("When a file changed on both sides, both versions are kept; the PC one gets "
                       "\"(PC conflict …)\" in its name."),
                     t("Renamed or moved files and folders are renamed or moved on the other side too.")):
            info.addWidget(_label("•  " + line, "Muted", True))
        lay.addStretch(1)
        self.drive_on.toggled.connect(self._drive_enabled_changed)
        self._drive_enabled_changed(self.drive_on.isChecked())

    def _build_notes(self) -> None:
        lay = self._page(t("Notes"), "note")
        head = QHBoxLayout()
        head.addWidget(_label(t("Apple Notes"), "H2"), 1)
        self.notes_on = Toggle(t("Enabled"), self.c)
        self.notes_on.setChecked(self.s.notes_enabled)
        self.notes_on.setCursor(Qt.PointingHandCursor)
        head.addWidget(self.notes_on)
        lay.addLayout(head)
        lay.addWidget(_label(t("Keeps a copy of all your notes on this PC as web pages, updated automatically. "
                               "Editing stays on your iPhone, iPad or Mac."), "Muted", True))
        fold = self._card(lay, t("NOTES FOLDER"))
        self.notes_card = fold.parentWidget()
        self.notes_dir = self._folder_row(fold, self.s.notes_dir, t("Choose a folder for your notes"))
        fold.addWidget(_label(t("Open Notes.html in that folder to browse and search all notes in your web browser."),
                              "Hint", True))
        info = self._card(lay, t("GOOD TO KNOW"))
        for line in (t("Each note is an HTML file with its pictures and attachments, in the same folders as in Notes."),
                     t("Notes deleted on your devices are moved to the Recycle Bin here."),
                     t("Locked notes can't be read; they are only listed.")):
            info.addWidget(_label("•  " + line, "Muted", True))
        lay.addStretch(1)
        self.notes_on.toggled.connect(self.notes_card.setEnabled)
        self.notes_card.setEnabled(self.notes_on.isChecked())

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
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(_logo(40))
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(_label(APP_NAME, "Title"))
        col.addWidget(_label(t("Version {version}", version=VERSION), "Muted"))
        row.addLayout(col, 1)
        lay.addLayout(row)
        where = self._card(lay, t("WHERE YOUR DATA IS"))
        where.addWidget(_label(t("Settings: the 'data' folder next to the app."), None, True))
        where.addWidget(_label(t("Sign-in and sync database: {path}", path=PRIVATE_DIR), None, True))
        if APP_IN_CLOUD:
            where.addWidget(_label(t("The app is in {service}, so the sign-in and database are kept only on this PC.",
                                     service=APP_IN_CLOUD), "Hint", True))
        links = self._card(lay, t("HELP"))
        for text, url in ((t("Source code and bug reports (GitHub)"), "https://github.com/zoranmod/skyfold"),
                          (t("License: GNU GPL v3"), "https://www.gnu.org/licenses/gpl-3.0.html")):
            b = QPushButton(text)
            b.setObjectName("Link")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, u=url: _open_url(u))
            links.addWidget(b, 0, Qt.AlignLeft)
        log_btn = QPushButton(t("Show the log file"))
        log_btn.setObjectName("Link")
        log_btn.setCursor(Qt.PointingHandCursor)
        log_btn.clicked.connect(_show_log)
        links.addWidget(log_btn, 0, Qt.AlignLeft)
        lay.addWidget(_label(t("Skyfold is not made by Apple. It uses the same web services as iCloud.com."),
                             "Hint", True))
        lay.addStretch(1)

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
              "and the old folder stays untouched and is no longer synced. Continue?"))
        return True if ans == QMessageBox.Yes else None

    def _save(self) -> None:
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
        s.include_videos = self.videos.isChecked()
        s.include_live_video = self.live.isChecked()
        s.albums_as_folders = self.albums.isChecked()
        s.delete_cloud_to_pc = self.del_pc.isChecked()
        s.delete_pc_to_cloud = self.del_cloud.isChecked()
        s.instant_local_deletes = self.instant.isChecked()
        s.delete_threshold = self.threshold.value()
        s.start_with_windows = self.autostart.isChecked()
        s.notifications = self.notif.isChecked()
        s.language = self.lang.currentData()
        s.theme = self.theme.currentData()
        s.drive_enabled = self.drive_on.isChecked()
        s.drive_dir = drive_dir
        s.drive_delete_cloud_to_pc = self.drive_del_pc.isChecked()
        s.drive_delete_pc_to_cloud = self.drive_del_cloud.isChecked()
        s.drive_folders = self.drive_folders
        s.notes_enabled = self.notes_on.isChecked()
        s.notes_dir = notes_dir
        self.accept()

    def result_settings(self) -> Settings:
        return self.s


# iCloud Drive folders -----------------------------------------------------------------
class DriveFoldersDialog(_Dialog):
    """Tick the iCloud Drive folders to sync. Folders are read from iCloud as they are opened;
    sizes come from the last full Drive read, when there is one."""

    PATH = Qt.UserRole
    LOADED = Qt.UserRole + 1

    def __init__(self, selection: list[str] | None, palette: dict[str, str], get_api,
                 sizes: dict[str, tuple[int, int]] | None = None) -> None:
        super().__init__(t("Choose iCloud Drive folders – {app}", app=APP_NAME))
        self.c, self.get_api, self.sizes = palette, get_api, sizes or {}
        self.initial = list(selection) if selection is not None else None
        self._items: dict[str, Any] = {}  # path -> DriveItem, for reading deeper levels
        self._tasks: list = []
        self._updating = False
        self.resize(*_fit_screen(560, 580))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(10)
        lay.addWidget(_label(t("What should be synced?"), "Title"))
        lay.addWidget(_label(t("Checked folders are downloaded and kept in sync. Unchecked folders stay in iCloud, "
                               "untouched. Files directly in iCloud Drive (outside any folder) are always synced."),
                             "Muted", True))
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.itemExpanded.connect(self._expand)
        self.tree.itemChanged.connect(self._changed)
        lay.addWidget(self.tree, 1)
        self.info = _label(t("Reading your iCloud Drive folders…"), "Muted", True)
        lay.addWidget(self.info)
        btns = QHBoxLayout()
        cancel = QPushButton(t("Cancel"))
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton(t("Save"))
        self.ok.setObjectName("Primary")
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self.accept)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(self.ok)
        lay.addLayout(btns)
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
            listed = ICloudDrive(api).walk(folders=[folder])
            return sorted((it for p, it in listed.items() if it.is_dir and p != path
                           and (p.rsplit("/", 1)[0] if "/" in p else "") == path),
                          key=lambda it: it.path.casefold())

        def done(children) -> None:
            self.add_children(parent, children)

        def fail(msg: str) -> None:
            self.info.setText(t("Couldn't read iCloud Drive: {error}", error=msg))

        self._tasks.append(run_async(fetch, done, fail))

    def add_children(self, parent, children) -> None:
        from ..ui_text import count_text, size_text

        self._updating = True
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
            node.addChild(QTreeWidgetItem([t("Loading…"), ""]))  # makes it expandable
            if parent is not None:
                parent.addChild(node)
            else:
                self.tree.addTopLevelItem(node)
        if parent is not None and inherited == Qt.PartiallyChecked:
            # the earlier choice inside may name folders that no longer exist: show what is real
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

    def _expand(self, item) -> None:
        if not item.data(0, self.LOADED):
            item.setData(0, self.LOADED, True)
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
            parent.setCheckState(0, kids[0] if kids and all(k == kids[0] for k in kids) else Qt.PartiallyChecked)
            parent = parent.parent()
        self._updating = False

    def selection(self) -> list[str] | None:
        """None when every folder is checked (= all of iCloud Drive, including future folders)."""
        tops = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        if not tops:
            return self.initial  # nothing to choose from: keep what was set
        if all(it.checkState(0) == Qt.Checked for it in tops):
            return None
        out: list[str] = []

        def visit(it) -> None:
            path, state = it.data(0, self.PATH), it.checkState(0)
            if path is None or state == Qt.Unchecked:
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
        return out


# pending deletions ------------------------------------------------------------------
class PendingDialog(_Dialog):
    decided = Signal(str, list)  # "approve"|"reject", ids

    def __init__(self, pending, palette: dict[str, str]) -> None:
        super().__init__(t("Confirm deletion – {app}", app=APP_NAME))
        self.setMinimumSize(*_fit_screen(480, 360))
        self.resize(*_fit_screen(560, 520))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(glyph("trash", palette["warn_text"], 28).pixmap(QSize(28, 28)))
        head.addWidget(ic)
        head.addWidget(_label(t("A large deletion is waiting for your OK"), "Title"), 1)
        lay.addLayout(head)
        lay.addWidget(_label(
            t("For safety, when more items than allowed in Settings are deleted at once, Skyfold asks first. "
            "Nothing is deleted permanently."), "Muted", True))

        drive = getattr(pending, "service", "photos") == "drive"
        if pending.to_pc:
            self._section(lay, pending, pending.to_pc,
                          t("{n} files or folders were deleted in iCloud Drive", n=len(pending.to_pc)) if drive
                          else t("{n} photos were deleted in iCloud", n=len(pending.to_pc)),
                          t("Delete on this PC too"), t("Keep on this PC"))
        if pending.to_cloud:
            self._section(lay, pending, pending.to_cloud,
                          t("{n} files or folders were deleted on this PC", n=len(pending.to_cloud)) if drive
                          else t("{n} photos were deleted on this PC", n=len(pending.to_cloud)),
                          t("Delete in iCloud too"), t("Bring them back to this PC"))


    def _section(self, lay, pending, ids, title, yes, no) -> None:
        if getattr(self, "_sections", 0):  # a second list: set it clearly apart from the first
            lay.addSpacing(10)
            sep = QFrame()
            sep.setObjectName("Sep")
            lay.addWidget(sep)
            lay.addSpacing(4)
        self._sections = getattr(self, "_sections", 0) + 1
        lay.addWidget(_label(title, "H2"))
        lst = QListWidget()
        for i in ids[:500]:
            lst.addItem(pending.names.get(i, i))
        if len(ids) > 500:
            lst.addItem(t("… and {n} more", n=len(ids) - 500))
        lst.setTextElideMode(Qt.ElideMiddle)
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lst.setMinimumHeight(80)
        for k in range(lst.count()):
            lst.item(k).setToolTip(lst.item(k).text())
        lay.addWidget(lst, 1)
        row = QHBoxLayout()
        if self._sections == 1:  # one "Decide later" for the whole window, in the first row
            later = QPushButton(t("Decide later"))
            later.clicked.connect(self.reject)
            row.addWidget(later)
        b_no = QPushButton(no)
        b_yes = QPushButton(yes)
        b_yes.setObjectName("Danger")
        b_no.setObjectName("Primary")
        self._open_sections = getattr(self, "_open_sections", 0) + 1
        b_no.clicked.connect(lambda: self._decide("reject", ids, (b_no, b_yes)))
        b_yes.clicked.connect(lambda: self._decide("approve", ids, (b_no, b_yes)))
        row.addStretch(1)
        row.addWidget(b_no)
        row.addWidget(b_yes)
        lay.addLayout(row)

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
class LayoutOption(QFrame):
    """A selectable card: radio dot, title and an example path."""

    clicked = Signal()

    def __init__(self, title: str, example: str) -> None:
        super().__init__()
        self.setObjectName("Option")
        self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 9, 12, 9)
        row.setSpacing(12)
        self.dot = QLabel()
        self.dot.setFixedSize(18, 18)
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(_label(title))
        text.addWidget(_label(example, "Hint"))
        row.addWidget(self.dot)
        row.addLayout(text, 1)
        self.set_selected(False)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.dot.setObjectName("DotOn" if on else "DotOff")
        for w in (self, self.dot):
            w.style().unpolish(w)
            w.style().polish(w)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(e)


class FolderSetupDialog(_Dialog):
    def __init__(self, current: str, palette: dict[str, str]) -> None:
        super().__init__(t("Welcome – {app}", app=APP_NAME))
        self.c = palette
        self.setFixedWidth(500)
        # Scrollable body with the buttons always visible: fits a 1366x768 screen at any scale.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("Root")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(28, 22, 28, 8)
        lay.setSpacing(10)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self._body = body
        lay.addWidget(_logo(40))
        title = _label(t("Where should your photos go?"), "Title")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)
        sub = _label(
            t("Choose the folder your iPhone and iPad gallery will sync to. It's separate from the program; "
            "pick a drive with enough space."),
            "Muted", True)
        sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(sub)
        lay.addSpacing(4)

        row = QHBoxLayout()
        self.path = QLineEdit(current or default_photos_dir())
        self.path.textChanged.connect(self._validate)
        browse = QPushButton(t("Choose…"))
        browse.setCursor(Qt.PointingHandCursor)
        browse.clicked.connect(self._browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        lay.addLayout(row)

        self.msg = _label("", None, True)
        self.msg.hide()
        lay.addWidget(self.msg)

        lay.addSpacing(6)
        lay.addWidget(_label(t("HOW SHOULD PHOTOS BE ORGANISED?"), "Section"))
        self.layout_choice = "flat"
        self.options: dict[str, LayoutOption] = {}
        examples = {
            "flat": (t("All in one folder"), "IMG_4411.HEIC, IMG_4412.HEIC …"),
            "year": (t("By year"), "2025\\IMG_4411.HEIC"),
            "year_month": (t("By year and month"), "2025\\11\\IMG_4411.HEIC"),
        }
        for key, (title, example) in examples.items():
            opt = LayoutOption(title, example)
            opt.clicked.connect(lambda k=key: self._pick_layout(k))
            self.options[key] = opt
            lay.addWidget(opt)
        self._pick_layout("flat")

        lay.addWidget(_label(
            t("An empty folder works best. Folder and layout can be changed later in Settings."),
            "Hint", True))

        lay.addSpacing(6)
        lay.addWidget(_label(t("BEFORE YOU START"), "Section"))
        lay.addWidget(_label(
            t("Skyfold syncs in both directions, deletions included. It is free software provided "
              "without any warranty (GNU GPL v3): you use it at your own risk, and its authors are not "
              "liable for lost or changed files. Keep your own backup of anything important."),
            "Muted", True))
        self.agree = Toggle(t("I understand and accept this"), palette)
        self.agree.toggled.connect(lambda _on: self._validate())
        lay.addWidget(self.agree)

        btns = QHBoxLayout()
        quit_btn = QPushButton(t("Later"))
        quit_btn.clicked.connect(self.reject)
        self.ok = QPushButton(t("Continue"))
        self.ok.setObjectName("Primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._accept)
        btns.addWidget(quit_btn)
        btns.addStretch(1)
        btns.addWidget(self.ok)
        btns.setContentsMargins(28, 10, 28, 18)
        outer.addLayout(btns)
        self._validate()
        self._fit_height()

    def _fit_height(self) -> None:
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        room = int(screen.availableGeometry().height() * 0.9) if screen else 800
        self._body.adjustSize()
        self.setFixedHeight(min(self._body.sizeHint().height() + 70, room))

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

    def _validate(self) -> bool:
        error, warning = check_photos_dir(self.path.text())
        text = error or warning or ""
        color = self.c["err_text"] if error else self.c["warn_text"]
        self.msg.setStyleSheet(f"color: {color};")
        self.msg.setText(text)
        self.msg.setVisible(bool(text))
        self.ok.setEnabled(error is None and self.agree.isChecked())
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
            self.msg.setStyleSheet(f"color: {self.c['err_text']};")
            self.msg.setText(t("Can't create the folder: {error}", error=exc))
            self.msg.show()
            return
        self.accept()


# Find My ------------------------------------------------------------------------------------
class DeviceRow(QFrame):
    def __init__(self, dev, palette: dict[str, str], get_api) -> None:
        super().__init__()
        self.setObjectName("Row")
        self.dev, self.c, self.get_api = dev, palette, get_api
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(12)
        icon = QLabel()
        icon.setPixmap(glyph("device", palette["accent_fg"], 22).pixmap(QSize(22, 22)))
        col = QVBoxLayout()
        col.setSpacing(1)
        name = QLabel(dev.name)
        name.setStyleSheet("font-weight: 600;")
        name.setWordWrap(True)
        bits = [dev.model] if dev.model else []
        if dev.battery is not None:
            bits.append(f"{round(dev.battery * 100)}%" + (" " + t("charging") if dev.charging else ""))
        bits.append(t("online") if dev.online else t("offline"))
        if dev.located_at:
            from ..worker import fmt_time

            bits.append(t("located {when}", when=fmt_time(dev.located_at)))
        detail = _label(" · ".join(bits), "Muted", True)
        col.addWidget(name)
        col.addWidget(detail)
        lay.addWidget(icon, 0, Qt.AlignVCenter)
        lay.addLayout(col, 1)
        self.map_btn = QPushButton(t("Map"))
        self.map_btn.setToolTip(t("Opens Google Maps in your browser with the device's last location."))
        self.map_btn.setObjectName("Link")
        self.map_btn.setCursor(Qt.PointingHandCursor)
        self.map_btn.setEnabled(dev.lat is not None)
        self.map_btn.clicked.connect(self._map)
        self.sound_btn = QPushButton(t("Play sound"))
        self.sound_btn.setCursor(Qt.PointingHandCursor)
        self.sound_btn.setEnabled(dev.can_sound)
        self.sound_btn.clicked.connect(self._sound)
        lay.addWidget(self.map_btn)
        lay.addWidget(self.sound_btn)

    def _map(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        d = self.dev
        QDesktopServices.openUrl(QUrl(f"https://www.google.com/maps/search/?api=1&query={d.lat},{d.lon}"))

    def _sound(self) -> None:
        from .. import extras

        api = self.get_api()
        if api is None:
            return
        self.sound_btn.setEnabled(False)
        self.sound_btn.setText(t("Sending…"))

        def done(_r) -> None:
            self.sound_btn.setText(t("Sound sent ✓"))

        def fail(msg: str) -> None:
            self.sound_btn.setEnabled(True)
            self.sound_btn.setText(t("Play sound"))
            warn(self, t("Find My"), msg)

        self._task = run_async(lambda: extras.play_sound(api, self.dev.id), done, fail)


class FindMyDialog(_Dialog):
    """Your Apple devices with battery, last location (opens a map) and 'play sound'."""

    signInAgain = Signal()

    def __init__(self, palette: dict[str, str], get_api) -> None:
        super().__init__(t("Find My – {app}", app=APP_NAME))
        self.c, self.get_api = palette, get_api
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(_label(t("Find My"), "Title"), 1)
        self.refresh_btn = QPushButton(t("Refresh"))
        self.refresh_btn.clicked.connect(self.load)
        head.addWidget(self.refresh_btn)
        lay.addLayout(head)
        self.info = _label(t("Locating your devices…"), "Muted", True)
        lay.addWidget(self.info)
        self.signin_btn = QPushButton(t("Sign in again"))
        self.signin_btn.setObjectName("Primary")
        self.signin_btn.clicked.connect(self._sign_in_again)
        self.signin_btn.hide()
        lay.addWidget(self.signin_btn, 0, Qt.AlignLeft)
        self.card = QFrame()
        self.card.setObjectName("Card")
        self.rows = QVBoxLayout(self.card)
        self.rows.setContentsMargins(6, 6, 6, 6)
        self.rows.setSpacing(0)
        self.card.hide()
        self._devices_scroll = QScrollArea()
        self._devices_scroll.setWidgetResizable(True)
        self._devices_scroll.setFrameShape(QFrame.NoFrame)
        self._devices_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._devices_scroll.setWidget(self.card)
        self._devices_scroll.setMaximumHeight(_fit_screen(10_000, 600)[1] - 220)
        self._devices_scroll.hide()
        lay.addWidget(self._devices_scroll)
        lay.addWidget(_label(t("The location opens in Google Maps in your browser. "
                               "Lost device? Use iCloud.com for Lost Mode or erasing."), "Hint", True))
        close = QPushButton(t("Close"))
        close.clicked.connect(self.accept)
        lay.addWidget(close, 0, Qt.AlignRight)
        self.load()

    def _sign_in_again(self) -> None:
        self.signInAgain.emit()
        self.accept()

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
            self.refresh_btn.setEnabled(True)
            while self.rows.count():
                w = self.rows.takeAt(0).widget()
                if w:
                    w.deleteLater()
            for i, d in enumerate(devs):
                if i:
                    sep = QFrame()
                    sep.setObjectName("Sep")
                    self.rows.addWidget(sep)
                self.rows.addWidget(DeviceRow(d, self.c, self.get_api))
            self.card.setVisible(bool(devs))
            self._devices_scroll.setVisible(bool(devs))
            self.info.setVisible(not devs)
            self.info.setText(t("No devices found."))
            self.adjustSize()

        def fail(msg: str) -> None:
            self.refresh_btn.setEnabled(True)
            self.info.setText(msg)
            self.info.show()
            if msg == extras.sign_in_again_text():
                self.signin_btn.show()

        first, self._loaded = not getattr(self, "_loaded", False), True
        self._task = run_async(lambda: extras.devices(api, refresh=not first), done, fail)
