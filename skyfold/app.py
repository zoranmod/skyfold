"""Tray application: wires the flyout, dialogs and the background worker together."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QCursor, QDesktopServices
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import auth
from .config import APP_ID, APP_NAME, VERSION, LOG_FILE, Settings, migrate_private_files, set_start_with_windows
from .i18n import set_language, t
from .ui import theme
from .ui.dialogs import ask, inform, warn
from .ui.dialogs import FindMyDialog, FolderSetupDialog, LoginDialog, PendingDialog, SettingsDialog, make_popup_round
from .ui.flyout import STATE_TITLES, Flyout
from .ui.icons import app_icon, tray_icon
from .worker import SyncWorker

log = logging.getLogger("skyfold")


def setup_logging() -> None:
    try:
        migrate_private_files()
    except OSError:
        pass
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_RedactEmails())  # Apple's error texts can contain the Apple ID: keep it out of the log
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    for noisy in ("urllib3", "pyicloud", "watchdog"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class _RedactEmails(logging.Filter):
    """Users attach the log to bug reports: never let an email address (the Apple ID), a cookie or a
    token into it."""

    import re as _re

    _EMAIL = _re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
    # X-APPLE-WEBAUTH-…=value, scnt/session/token headers, and any long token-like run of characters
    _SECRET = _re.compile(r"(?i)((?:x-apple-[\w-]+|scnt|session[\w-]*|[\w-]*token[\w-]*|cookie|dsid)"
                          r"[\"']?\s*[=:]\s*[\"']?)[^\s;,\"'&]+")
    _LONG = _re.compile(r"(?=[\w+=-]*\d)(?=[\w+=-]*[A-Za-z])[\w+=-]{40,}")  # letters and digits mixed

    @classmethod
    def clean(cls, text: str) -> str:
        if "@" in text:
            text = cls._EMAIL.sub("<apple-id>", text)
        text = cls._SECRET.sub(r"\1<redacted>", text)
        return cls._LONG.sub("<redacted>", text)

    def filter(self, record: logging.LogRecord) -> bool:
        text = record.getMessage()
        clean = self.clean(text)
        if clean != text:
            record.msg, record.args = clean, ()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.clean(record.exc_text)
        return True


def _instance_name() -> str:
    """One Skyfold per Windows user; the name includes the user so nobody else can take it."""
    import getpass

    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return f"{APP_ID}-{''.join(c for c in user if c.isalnum()) or 'user'}"


class Requests(QObject):
    """Signals into the worker; Qt queues them onto the worker thread automatically."""

    syncNow = Signal(bool)
    applySettings = Signal(object, bool, bool)  # settings, reset photos state, reset drive state
    setApi = Signal(object)
    signOut = Signal()
    resolvePending = Signal(str, list)
    restoreExcluded = Signal()
    fullCheck = Signal()
    clearActivity = Signal()
    trashDrivePaths = Signal(list)


class Controller(QObject):
    """Lives on the UI thread. Being a QObject matters: worker signals connected to its
    methods are queued onto the UI thread instead of running inside the worker thread."""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.settings = Settings.load()
        self.c = theme.palette(self.settings.theme)
        app.setStyleSheet(theme.stylesheet(self.c))
        app.setPalette(theme.qpalette(self.c))
        app.setWindowIcon(app_icon())
        self.state = "idle"
        self._spin = 0
        self._dialogs: dict[str, object] = {}
        self._pending = None
        self.server = None  # single-instance server, set by main()

        # worker thread
        self.thread = QThread()
        self.worker = SyncWorker(self.settings)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.req = Requests()
        self.req.syncNow.connect(self.worker.sync_now)
        self.req.applySettings.connect(self.worker.apply_settings)
        self.req.setApi.connect(self.worker.set_api)
        self.req.signOut.connect(self.worker.sign_out)
        self.req.resolvePending.connect(self.worker.resolve_pending)
        self.req.restoreExcluded.connect(self.worker.restore_excluded)
        self.req.fullCheck.connect(self.worker.full_check)
        self.req.clearActivity.connect(self.worker.clear_activity)
        self.req.trashDrivePaths.connect(self.worker.trash_drive_paths)

        # UI
        self.flyout = Flyout(self.c)
        self.flyout.set_account(self.settings.apple_id)
        self.flyout.set_paused(self.settings.paused)
        self.tray = QSystemTrayIcon(tray_icon("idle"))
        self.tray.setToolTip(APP_NAME)
        self.tray.activated.connect(self._on_tray)
        self.tray.messageClicked.connect(self._on_message_clicked)
        self._build_menu()
        self.tray.show()

        self.anim = QTimer()
        self.anim.setInterval(120)
        self.anim.timeout.connect(self._animate)
        self.clock = QTimer()
        self.clock.setInterval(30_000)
        self.clock.timeout.connect(self.flyout.refresh_times)
        self.clock.start()
        try:
            app.styleHints().colorSchemeChanged.connect(self._on_theme_changed)
        except AttributeError:  # Qt < 6.5
            pass

        w, f = self.worker, self.flyout
        # Everything the worker emits touches widgets, so it must run on the UI thread:
        # every connection is explicitly queued, and only to UI-thread QObjects.
        q = Qt.QueuedConnection
        w.stateChanged.connect(self._on_state, q)
        w.progress.connect(self._on_progress, q)
        w.activity.connect(f.add_activity, q)
        w.statsChanged.connect(f.set_stats, q)
        w.pendingChanged.connect(self._on_pending, q)
        w.notify.connect(self._notify, q)
        w.accountChanged.connect(f.set_account, q)
        w.accountChanged.connect(self._forget_login_api, q)
        f.syncNow.connect(lambda: self.req.syncNow.emit(True))
        f.clearActivity.connect(lambda: self.req.clearActivity.emit())
        # Cancel must not wait in the busy worker's queue: set the flag right here.
        f.cancelSync.connect(lambda: self.worker.cancel())
        f.openFolder.connect(lambda: self.open_folder("photos"))
        f.togglePause.connect(self.toggle_pause)
        f.openSettings.connect(lambda: self.open_settings(0))
        f.login.connect(self.open_login)
        f.reviewPending.connect(self.open_pending)
        f.setup.connect(self.open_setup)
        f.serviceClicked.connect(self._on_service)
        f.findMy.connect(self.open_find_my)

        self.thread.start()

    # plumbing ---------------------------------------------------------------
    def _build_menu(self) -> None:
        m = QMenu()
        make_popup_round(m)  # rounded like the rest of the app
        self.m_status = QAction(t("Everything is in sync"))
        self.m_status.setEnabled(False)
        m.addAction(self.m_status)
        m.addSeparator()
        m.addAction(t("Show"), lambda: self.flyout.show_at(self.tray.geometry()))
        m.addAction(t("Sync now"), lambda: self.req.syncNow.emit(True))
        m.addAction(t("Full library check"), lambda: self.req.fullCheck.emit())
        self.m_pause = m.addAction(t("Pause"), self.toggle_pause)
        m.addAction(t("Find My devices…"), self.open_find_my)
        m.addSeparator()
        m.addAction(t("Settings…"), self.open_settings)
        m.addAction(t("Open log"), self.open_log)
        m.addSeparator()
        m.addAction(t("Quit"), self.quit)
        self.menu = m
        self.tray.setContextMenu(m)
        self._sync_pause_label()

    def _sync_pause_label(self) -> None:
        self.m_pause.setText(t("Resume syncing") if self.settings.paused else t("Pause"))

    # events -------------------------------------------------------------------
    def _on_tray(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.flyout.toggle(self.tray.geometry())

    def _on_message_clicked(self) -> None:
        if self.state == "setup":
            self.open_setup()
        elif self.state == "auth":
            self.open_login()
        elif self._pending:
            self.open_pending()
        else:
            self.flyout.show_at(self.tray.geometry())

    def _on_state(self, state: str, text: str) -> None:
        self.state = state
        self.flyout.set_state(state, text)
        title = STATE_TITLES.get(state, state)
        self.m_status.setText(title)
        self.tray.setToolTip(f"{APP_NAME}\n{text or title}"[:120])
        if state == "syncing":
            if not self.anim.isActive():
                self.anim.start()
        else:
            self.anim.stop()
            self.tray.setIcon(tray_icon("paused" if self._pending and state == "idle" else state))

    def _on_theme_changed(self, *_args) -> None:
        self.c = theme.palette(self.settings.theme)
        self.app.setStyleSheet(theme.stylesheet(self.c))
        self.app.setPalette(theme.qpalette(self.c))
        self.flyout.apply_palette(self.c)
        # the tray mark is black or white depending on the taskbar colour
        QTimer.singleShot(500, lambda: self._on_state(self.state, self.flyout.status_text.text()))

    def _on_progress(self, done, total, text: str) -> None:
        self.flyout.set_progress(int(done), int(total), text)

    def _animate(self) -> None:
        self._spin = (self._spin + 1) % 8
        self.tray.setIcon(tray_icon("syncing", self._spin))

    def _on_pending(self, pending) -> None:
        self._pending = pending
        self.flyout.set_pending(pending)
        if self.state != "syncing":
            self._on_state(self.state, self.flyout.status_text.text())

    def _notify(self, title: str, text: str) -> None:
        important = title in (t("Sign-in required"), t("Confirmation needed"))
        if self.settings.notifications or important:
            self.tray.showMessage(title, text, app_icon(), 6000)

    # actions --------------------------------------------------------------------
    def open_folder(self, which: str = "photos") -> None:
        p = Path({"drive": self.settings.drive_dir, "notes": self.settings.notes_dir}.get(which, self.settings.photos_dir))
        p.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def _on_service(self, which: str) -> None:
        """Click on a service row: open its folder, or Settings when the service is off."""
        if which == "drive" and not self.settings.drive_enabled:
            self.open_settings(page=2)
        elif which == "notes":
            index = Path(self.settings.notes_dir) / "Notes.html"
            if not self.settings.notes_enabled:
                self.open_settings(page=3)
            elif index.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(index)))
            else:
                self.open_folder("notes")
        else:
            self.open_folder(which)

    def toggle_pause(self) -> None:
        self.settings.paused = not self.settings.paused
        if self.settings.paused:
            self.worker.cancel()  # thread-safe; the worker thread may be busy for a long time
        self.settings.save()
        self.flyout.set_paused(self.settings.paused)
        self._sync_pause_label()
        self.req.applySettings.emit(self.settings.copy(), False, False)

    def _show_dialog(self, key: str, dlg) -> None:
        old = self._dialogs.get(key)
        if old is not None and old.isVisible():
            old.raise_()
            old.activateWindow()
            return
        self._dialogs[key] = dlg
        self.flyout.hide()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def open_login(self) -> None:
        dlg = LoginDialog(self.settings.apple_id)

        def logged_in(api, apple_id: str) -> None:
            if self.settings.apple_id and apple_id.lower() != self.settings.apple_id.lower():
                turned_off = [name for on, name in ((self.settings.drive_enabled, t("iCloud Drive")),
                                                    (self.settings.notes_enabled, t("Notes"))) if on]
                self.settings.drive_enabled = False
                self.settings.notes_enabled = False
                self.req.applySettings.emit(self.settings.copy(), True, False)  # new account → fresh state
                if turned_off:
                    QTimer.singleShot(300, lambda: inform(
                        None, APP_NAME, t("You signed in with another Apple ID. Turned off: {services}. Their folders hold the "
                                          "other account's files; choose a new, empty folder in Settings to turn them "
                                          "on again.", services=", ".join(turned_off))))
            self._login_api = api  # Find My uses it right away, even while the worker is still busy
            auth.commit_login(api)  # the old session must not overwrite this one on disk
            self.worker.cancel()  # so the worker switches to the new sign-in now, not after a long pass
            self.settings.apple_id = apple_id
            self.settings.save()
            self.req.applySettings.emit(self.settings.copy(), False, False)
            self.req.setApi.emit(api)
            self.flyout.set_account(apple_id)
            if getattr(self, "_find_my_after_login", False):
                self._find_my_after_login = False
                QTimer.singleShot(800, self.open_find_my)  # once the new session is in place
            else:
                self.flyout.show_at(self.tray.geometry())

        dlg.loggedIn.connect(logged_in)
        self._show_dialog("login", dlg)

    def open_settings(self, page: int = 0) -> None:
        dlg = SettingsDialog(self.settings, self.c, storage=getattr(self.worker, "_storage", None),
                             get_api=lambda: self.worker.api)
        dlg.nav.setCurrentRow(page)

        def saved() -> None:
            new = dlg.result_settings()
            if new.start_with_windows != self.settings.start_with_windows:
                try:
                    set_start_with_windows(new.start_with_windows)
                except OSError as exc:
                    warn(None, APP_NAME, t("Could not set up start with Windows: {error}", error=exc))
                    new.start_with_windows = self.settings.start_with_windows
            new.paused = self.settings.paused
            language_changed = new.language != self.settings.language
            theme_changed = new.theme != self.settings.theme
            self.settings = new
            self.settings.save()
            self.req.applySettings.emit(self.settings.copy(), dlg.reset_state, dlg.reset_drive)
            if dlg.drive_trash:
                self.req.trashDrivePaths.emit(list(dlg.drive_trash))
            if theme_changed:
                self._on_theme_changed()  # applies at once, no restart needed
            if language_changed:
                # labels are built once at start-up, so a new language needs a restart
                set_language(new.language)
                ans = ask(None, APP_NAME, t("Restart to change the language?"))
                if ans == QMessageBox.Yes:
                    self.restart()

        dlg.accepted.connect(saved)
        dlg.signOut.connect(self.sign_out)
        dlg.restoreExcluded.connect(lambda: self.req.restoreExcluded.emit())
        self._show_dialog("settings", dlg)

    def _forget_login_api(self, *_args) -> None:
        """The worker now holds the newest sign-in (or renewed it): Find My follows the worker."""
        self._login_api = None

    def sign_out(self) -> None:
        self._login_api = None
        self.worker.cancel()  # stop the running pass now; the rest happens on the worker thread
        self.req.signOut.emit()
        self.flyout.set_account("")

    def open_log(self) -> None:
        """Select the log file in Explorer (robust even if no app is set up for .log files)."""
        import subprocess

        if LOG_FILE.exists():
            subprocess.Popen([os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe"), "/select,", str(LOG_FILE)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_FILE.parent)))

    def open_find_my(self) -> None:
        dlg = FindMyDialog(self.c, lambda: getattr(self, "_login_api", None) or self.worker.api)

        def again() -> None:
            self._find_my_after_login = True
            QTimer.singleShot(150, self.open_login)

        dlg.signInAgain.connect(again)
        self._show_dialog("findmy", dlg)

    def open_pending(self) -> None:
        if not self._pending:
            return
        dlg = PendingDialog(self._pending, self.c)
        dlg.decided.connect(lambda d, ids: self.req.resolvePending.emit(d, ids))
        self._show_dialog("pending", dlg)

    def quit(self) -> None:
        self.worker.cancel()
        self.worker._stop_watchers()
        self.tray.hide()
        self.thread.quit()
        self.thread.wait(3000)
        self.app.quit()

    def restart(self) -> None:
        """Start a fresh copy and quit this one (used after changing the language)."""
        from PySide6.QtCore import QProcess

        if self.server is not None:
            self.server.close()  # free the single-instance name for the new copy
        if getattr(sys, "frozen", False):
            QProcess.startDetached(sys.executable, ["--tray"])
        else:
            QProcess.startDetached(sys.executable, [str(Path(__file__).resolve().parent.parent / "run.py"), "--tray"])
        self.quit()

    def open_setup(self) -> None:
        """First run: ask where the gallery should live before anything is downloaded."""
        dlg = FolderSetupDialog(self.settings.photos_dir, self.c)

        def chosen() -> None:
            self.settings.photos_dir = dlg.chosen_path()
            self.settings.folder_structure = dlg.chosen_layout()
            self.settings.photos_dir_confirmed = True
            self.settings.save()
            self.req.applySettings.emit(self.settings.copy(), False, False)
            if not self.settings.apple_id:
                QTimer.singleShot(200, self.open_login)
            else:
                self.req.syncNow.emit(True)

        dlg.accepted.connect(chosen)
        self._show_dialog("setup", dlg)

    def start(self, show: bool) -> None:
        if self.settings.start_with_windows:
            try:  # the app may have been moved or renamed: point Windows at the current exe
                set_start_with_windows(True)
            except OSError as exc:
                log.warning("Could not refresh autostart: %s", exc)
        if not self.settings.photos_dir_confirmed:
            QTimer.singleShot(400, self.open_setup)
        elif not self.settings.apple_id:
            QTimer.singleShot(400, self.open_login)
        elif show:
            QTimer.singleShot(300, lambda: self.flyout.show_at(self.tray.geometry()))


def main() -> int:
    from ._stdio import ensure_std_streams

    ensure_std_streams()
    if sys.platform == "win32":
        import ctypes

        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:  # noqa: BLE001
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")

    # single instance: a second launch just pops the running one open
    sock = QLocalSocket()
    instance = _instance_name()
    sock.connectToServer(instance)
    if sock.waitForConnected(300):
        sock.write(b"quit" if "--quit" in sys.argv else b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        return 0
    if "--quit" in sys.argv:
        return 0  # nothing running
    QLocalServer.removeServer(instance)
    server = QLocalServer()
    if not server.listen(instance):
        logging.getLogger(__name__).warning("Single-instance server: %s", server.errorString())

    setup_logging()
    log.info("Starting %s %s from %s (exe %s)", APP_NAME, VERSION, os.getcwd(), sys.executable)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, APP_NAME, t("The system tray is not available."))
        return 1

    ctl = Controller(app)
    ctl.server = server

    def on_conn() -> None:
        conn = server.nextPendingConnection()
        if conn:
            def on_message() -> None:
                if bytes(conn.readAll()).strip() == b"quit":  # `Skyfold.exe --quit`: close cleanly
                    log.info("Quit requested by another Skyfold process")
                    ctl.quit()
                else:
                    ctl.flyout.show_at(ctl.tray.geometry())

            conn.readyRead.connect(on_message)

    server.newConnection.connect(on_conn)
    ctl.start(show="--tray" not in sys.argv)
    return app.exec()
