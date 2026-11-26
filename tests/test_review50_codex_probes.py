"""Offline Windows probes for the release worker and the 1.0.21 Settings dialog."""

import os
import threading
import time

from PySide6.QtCore import QMetaObject, QObject, QRect, Qt, Slot
from PySide6.QtGui import QGuiApplication

from skyfold import worker
from skyfold.config import Settings
from skyfold.drive_engine import unsynced_local_dirs
from skyfold.remote import RemoteUnavailable
from skyfold.ui import dialogs, theme
from tests.test_drive import FakeDrive, env as drive_env, write
from tests.test_notes import env as notes_env
from tests.test_review27_codex_ui_probes import qapp
from tests.test_worker import pump, qt


class PanelReceiver(QObject):
    def __init__(self):
        super().__init__()
        self.states = []

    @Slot(str, str)
    def receive(self, state, text):
        self.states.append((state, text))


class Barrier(QObject):
    @Slot()
    def reached(self):
        pass


def test_slow_drive_fixture_observes_the_same_final_state_as_the_panel(qapp, qt, monkeypatch, tmp_path):
    _, w, th, driver, photos, root, recorded, _, _ = qt
    gate = threading.Event()
    drive_entered = threading.Event()
    drive_finished = threading.Event()
    worker_thread = []
    second = root / "2025/11/IMG_0002.JPG"
    drive = FakeDrive()
    drive.put("Docs/a.txt", b"a")
    walk = drive.walk

    def gated_walk(*args, **kwargs):
        drive_entered.set()
        assert gate.wait(10)
        return walk(*args, **kwargs)

    def storage(force=False):
        if second.exists():
            # Finish Drive while Photos still owns the QThread, before the cycle's final idle.
            worker_thread.append(threading.get_ident())
            gate.set()
            w._drive_thread.join(5)
            if not w.drive_running():
                drive_finished.set()

    panel = PanelReceiver()
    emissions = []
    callbacks = []

    def record_send(state, text):
        emissions.append((threading.get_ident(), state, text))

    w.stateChanged.connect(record_send, Qt.DirectConnection)
    w.stateChanged.connect(lambda state, text: callbacks.append((threading.get_ident(), state, text)))
    w.stateChanged.connect(panel.receive, Qt.QueuedConnection)
    barrier = Barrier()
    barrier.moveToThread(th)
    th.finished.connect(barrier.deleteLater)
    monkeypatch.setattr(drive, "walk", gated_walk)
    monkeypatch.setattr(worker, "ICloudDrive", lambda _: drive)
    monkeypatch.setattr(w, "_refresh_storage", storage)
    w.settings.drive_enabled = True
    w.settings.drive_dir = str(tmp_path / "Drive")
    th.start()
    try:
        assert pump(lambda: drive_entered.is_set() and (root / "2025/11/IMG_0001.HEIC").exists())
        photos.add("A2", second.name, b"new")
        driver.syncNow.emit(False)
        assert pump(drive_finished.is_set)
        assert QMetaObject.invokeMethod(barrier, "reached", Qt.BlockingQueuedConnection)
        qapp.processEvents()
        assert not w.busy and not w.drive_running()
        assert w._drive_ui[0] == w._last_state[0] == "idle"
        assert (tmp_path / "Drive/Docs/a.txt").read_bytes() == b"a"
        assert panel.states[-1] == w._last_state
        assert emissions[-1][1:] == panel.states[-1]
        assert len({tid for tid, _, _ in emissions}) == 2
        assert {tid for tid, _, _ in callbacks} == set(worker_thread)
        assert recorded[-1] == panel.states[-1], (recorded, panel.states)
    finally:
        gate.set()


def test_drive_detects_replacement_with_preserved_timestamp(drive_env):
    sync, cloud, root, _ = drive_env
    cloud.put("plan.txt", b"old plan")
    sync.run()
    target = root / "plan.txt"
    timestamp = target.stat().st_mtime_ns
    newer = write(root, "replacement.tmp", b"new plan")
    os.utime(newer, ns=(timestamp, timestamp))
    os.replace(newer, target)
    sync.run(changed={"plan.txt"})
    sync.run()
    assert cloud.items["plan.txt"]["data"] == b"new plan"


def test_recent_drive_save_is_scheduled_for_another_pass(drive_env, monkeypatch):
    sync, cloud, root, _ = drive_env
    sync.run()
    created = write(root, "invoice.txt", b"invoice")
    # A five-second coarse Qt timer may legally fire up to 250 ms early.
    now = created.stat().st_mtime + 4.85
    monkeypatch.setattr(time, "time", lambda: now)
    result = sync.run(changed={"invoice.txt"})
    assert "invoice.txt" in cloud.items or result.followup


def test_deleted_note_attachment_is_retried_after_viewer_closes(notes_env, monkeypatch):
    sync, cloud, root, _ = notes_env
    cloud.put("n1", "Receipt", body='<img src="../_assets/n1/scan.png">')
    attach = write(root, "_assets/n1/scan.png", b"private scan")
    sync.run()
    assert sync.state.get("assets:n1")
    trash = sync._trash
    opened = True

    def recycle(path):
        if path == attach and opened:
            raise PermissionError("open in a viewer")
        trash(path)

    monkeypatch.setattr(sync, "_trash", recycle)
    cloud.delete("n1")
    sync.run()
    assert attach.exists()
    opened = False
    sync.run()
    sync.run(full=True)
    assert not attach.exists()


def test_cloud_note_edit_downloads_after_its_change_token_expires(notes_env, monkeypatch):
    from pyicloud.services.notes.client import NotesApiError

    sync, cloud, root, _ = notes_env
    cloud.put("n1", "Shopping", body="first version")
    sync.run()
    expired = cloud.cursor()
    cloud.put("n1", "Shopping", body="second version")
    changes = cloud.changes

    def feed(since):
        if since == expired:
            raise NotesApiError("change token expired", payload={"serverErrorCode": "CHANGE_TOKEN_EXPIRED"})
        return changes(since)

    monkeypatch.setattr(cloud, "changes", feed)
    for _ in range(2):
        try:
            sync.run()
        except NotesApiError:
            pass
    assert "second version" in (root / "Notes/Shopping.html").read_text(encoding="utf-8")


def test_busy_photo_upload_is_visible_in_panel_after_cycle(qapp, qt, monkeypatch):
    _, w, th, driver, cloud, root, _, _, _ = qt
    w.settings.photos_upload = True
    panel = PanelReceiver()
    w.stateChanged.connect(panel.receive, Qt.QueuedConnection)
    barrier = Barrier()
    barrier.moveToThread(th)
    th.finished.connect(barrier.deleteLater)
    attempted = threading.Event()

    def busy(*args, **kwargs):
        attempted.set()
        raise RemoteUnavailable("registration busy")

    monkeypatch.setattr(cloud, "upload", busy)
    th.start()
    assert pump(lambda: ("idle", "Everything is in sync.") in panel.states)
    write(root, "unsent.mov", b"video", age=300)
    cloud.add("phone2", "phone2.jpg", b"new on iPhone")
    driver.syncNow.emit(False)
    assert pump(attempted.is_set)
    assert QMetaObject.invokeMethod(barrier, "reached", Qt.BlockingQueuedConnection)
    qapp.processEvents()
    assert (root / "2025/11/phone2.jpg").exists()
    assert panel.states[-1] != ("idle", "Everything is in sync.")


def test_drive_type_conflict_does_not_trash_cloud_children(drive_env):
    sync, cloud, root, _ = drive_env
    cloud.put("Docs/report.txt", b"report")
    sync.run()
    (root / "Docs/report.txt").unlink()
    (root / "Docs").rmdir()
    write(root, "Docs", b"replacement file")
    result = sync.run()
    assert result.errors
    assert "Docs/report.txt" in cloud.items, cloud.trashed


def test_recycling_deselected_drive_copies_keeps_unuploaded_work(qapp, drive_env):
    sync, cloud, root, _ = drive_env
    cloud.put("Projects/synced.txt", b"synced")
    cloud.put("Keep", None)
    sync.run()
    draft = write(root, "Projects/draft.txt", b"only copy")
    sync.folders = ["Keep"]
    deselected = unsynced_local_dirs(root, None, sync.folders)
    w = worker.SyncWorker(Settings())
    w.drive = sync
    w.trash_drive_paths(deselected)
    assert "Projects/draft.txt" not in cloud.items
    assert draft.exists() and draft.read_bytes() == b"only copy"


def test_settings_longest_page_fits_with_the_application_stylesheet(qapp, monkeypatch):
    screen = QGuiApplication.primaryScreen()
    monkeypatch.setattr(type(screen), "availableGeometry", lambda _: QRect(0, 0, 1920, 1040))
    previous = qapp.styleSheet()
    qapp.setStyleSheet(theme.stylesheet(theme.LIGHT))
    dlg = dialogs.SettingsDialog(Settings(), theme.LIGHT)
    try:
        dlg.show()
        for i in range(dlg.stack.count()):
            if dlg.PAGES[i] == "general":
                continue  # (1.1: General holds the Sync group again, as in the design, and may scroll)
            dlg.stack.setCurrentIndex(i)
            qapp.processEvents()
            page = dlg.stack.currentWidget()
            assert page.verticalScrollBar().maximum() == 0, (i, dlg.size(), page.verticalScrollBar().maximum())
    finally:
        dlg.close()
        qapp.setStyleSheet(previous)
