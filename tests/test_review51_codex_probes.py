"""Release 1.0.22 regressions, using only temporary libraries and offline services."""

import base64
import gzip
import os
import shutil
from types import SimpleNamespace

import pytest
from pyicloud.common.cloudkit import CKRecord
from pyicloud.services.notes.client import NotesApiError
from pyicloud.services.notes.protobuf import notes_pb2
from PySide6.QtCore import QMetaObject, QTimer, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from skyfold import notes_engine, worker
from skyfold.config import Settings
from skyfold.drive_engine import unsynced_local_dirs
from skyfold.drive_remote import ICloudDrive, ROOT_ID
from skyfold.engine import Cancelled
from skyfold.remote import UploadRejected
from skyfold.state import State
from skyfold.ui import dialogs, theme
from tests.test_drive import env as drive_env, write
from tests.test_notes import env as notes_env
from tests.test_notes_quality_codex import OfflineCloudKit, field, note_record, png, rec
from tests.test_review27_codex_ui_probes import qapp
from tests.test_review50_codex_probes import Barrier
from tests.test_worker import pump, qt


@pytest.mark.xfail(reason="Apple's web API has no known in-place update keeping the document id (rclone also "
                          "trashes and re-creates); documented in README Limitations", strict=True)
def test_editing_a_shared_drive_document_preserves_the_document_id(drive_env):
    sync, cloud, root, _ = drive_env
    cloud.put("budget.xlsx", b"original")
    shared_id = cloud.items["budget.xlsx"]["id"]
    sync.run()
    write(root, "budget.xlsx", b"revised budget")
    assert sync.run().uploaded == 1
    assert cloud.items["budget.xlsx"]["data"] == b"revised budget"
    assert cloud.items["budget.xlsx"]["id"] == shared_id


def test_recycling_deselected_folder_checks_preserved_time_and_size(qapp, drive_env, tmp_path):
    sync, cloud, root, _ = drive_env
    cloud.put("Projects/plan.txt", b"old plan")
    sync.run()
    target = root / "Projects/plan.txt"
    backup = tmp_path / "restored.txt"
    backup.write_bytes(b"new plan")
    stamp = target.stat().st_mtime_ns
    os.utime(backup, ns=(stamp, stamp))
    shutil.copy2(backup, target)
    backup.unlink()
    assert cloud.items["Projects/plan.txt"]["data"] == b"old plan"
    w = worker.SyncWorker(Settings())
    w.drive = sync
    w.trash_drive_paths(unsynced_local_dirs(root, None, []))
    assert target.is_file() and target.read_bytes() == b"new plan"


def test_deleting_folder_keeps_cloud_only_case_twin(drive_env):
    sync, cloud, root, _ = drive_env
    cloud.put("Docs/Map.txt", b"public map")
    cloud.put("Docs/map.txt", b"different private map")
    sync.run()
    visible, = (root / "Docs").iterdir()
    hidden = next(p for p in cloud.items if p.startswith("Docs/") and p != "Docs/" + visible.name)
    only_cloud_copy = cloud.items[hidden]["data"]
    shutil.rmtree(root / "Docs")
    sync.run()
    assert hidden in cloud.items and cloud.items[hidden]["data"] == only_cloud_copy


def test_failed_pdf_download_remains_due_for_offline_export(qapp, notes_env):
    sync, cloud, root, _ = notes_env
    url = "https://example.icloud-content.com/receipt"
    uti = "com.apple.paper.doc.pdf"
    attachment = rec(uti, FallbackPDF=field("ASSETID", {"downloadURL": url, "size": 8}))
    raw = OfflineCloudKit([attachment], {})
    svc = SimpleNamespace(_raw=raw, _lookup_note_record=lambda nid: note_record(uti))
    cloud.export = notes_engine.ICloudNotes(SimpleNamespace(notes=svc)).export
    cloud.put("n1", "Receipt")
    sync.run()
    assert url in raw.downloads
    # Connectivity returns without a cloud edit.
    raw.blobs[url] = b"%PDF-1.7"
    sync.run()
    assert any(p.read_bytes() == raw.blobs[url] for p in (root / "_assets/n1").glob("*.pdf"))


def test_unpublished_note_attachments_are_retired_after_failed_exports(qapp, notes_env, tmp_path):
    sync, cloud, root, _ = notes_env
    msg = notes_pb2.NoteStoreProto()
    msg.document.note.note_text = "\ufffc\ufffc"
    attachments = []
    urls = []
    for aid in ("first", "second"):
        run = msg.document.note.attribute_run.add()
        run.length = 1
        run.attachment_info.attachment_identifier = aid
        run.attachment_info.type_uti = "public.jpeg"
        url = f"https://example.icloud-content.com/{aid}"
        urls.append(url)
        attachments.append(rec(name=aid, Image=field("ASSETID", {"downloadURL": url, "size": 100})))
    payload = base64.b64encode(gzip.compress(msg.SerializeToString())).decode()
    record = CKRecord(recordName="n1", recordType="Note", fields={
        "TextDataEncrypted": field("ENCRYPTED_BYTES", payload),
        "Attachments": field("REFERENCE_LIST", [{"recordName": a.recordName} for a in attachments]),
    })
    photo = png(tmp_path / "scan.jpg", 20, 10, "JPEG").read_bytes()
    raw = OfflineCloudKit(attachments, {urls[0]: photo})  # second download fails on both attempts
    svc = SimpleNamespace(_raw=raw, _lookup_note_record=lambda nid: record)
    cloud.export = notes_engine.ICloudNotes(SimpleNamespace(notes=svc)).export
    cloud.put("n1", "Scans")
    assert sync.run().errors
    sync.run()
    assert raw.downloads.count(urls[0]) == 2
    cloud.put("n1", "Scans", locked=True)
    sync.run()
    assert all(p.read_bytes() != photo for p in (root / "_assets/n1").glob("*") if p.is_file())


def test_expired_token_reconciles_an_absent_note_on_ordinary_polls(notes_env, monkeypatch):
    sync, cloud, root, _ = notes_env
    cloud.put("survivor", "Keep")
    cloud.put("removed", "Deleted")
    sync.run()
    page = root / "Notes/Deleted.html"
    assert page.exists()
    for _ in range(5):
        sync.run()  # finish first-install quality, style and index migrations before the expiry
    expired = cloud.cursor()
    cloud.delete("removed")
    changes = cloud.changes

    def feed(since):
        if since == expired:
            raise NotesApiError("change token expired", payload={"serverErrorCode": "CHANGE_TOKEN_EXPIRED"})
        return changes(since)

    monkeypatch.setattr(cloud, "changes", feed)
    assert sync.run().full
    for _ in range(3):
        sync.run()
    assert (root / "Notes/Keep.html").exists()
    assert not page.exists()


def test_drive_followup_waits_for_debounce_before_relisting(qapp, drive_env, tmp_path, monkeypatch):
    sync, cloud, root, _ = drive_env
    sync.run()
    write(root, "saving.txt", b"still being saved", age=0)
    w = worker.SyncWorker(Settings(drive_dir=str(root), drive_enabled=True))
    w.drive = sync
    w.state = State(tmp_path / "worker.db")
    w._drive_changes.add("saving.txt")
    walk = cloud.walk
    quick_queries = []

    def counted_walk(*args, **kwargs):
        if kwargs.get("folders") is not None:
            quick_queries.append(True)
            if len(quick_queries) == 3:
                w._drive_stopped = True  # bound a broken loop without relying on wall time
        return walk(*args, **kwargs)

    monkeypatch.setattr(cloud, "walk", counted_walk)
    w._drive_loop()  # no Qt timer event is delivered inside this call
    assert len(quick_queries) == 1


def test_panel_reports_photo_waiting_during_file_retry_backoff(qapp, qt, monkeypatch):
    app, w, thread, driver, cloud, root, states, _, _ = qt
    w.settings.photos_upload = True
    barrier = Barrier()
    barrier.moveToThread(thread)
    thread.finished.connect(barrier.deleteLater)
    attempts = []

    def refused(path, **kwargs):
        attempts.append(path.name)
        raise UploadRejected("file refused")

    monkeypatch.setattr(cloud, "upload", refused)
    thread.start()
    assert pump(lambda: states and states[-1][0] == "idle")
    write(root, "offline.mov", b"new movie", age=300)
    driver.syncNow.emit(False)
    assert pump(lambda: attempts)
    assert QMetaObject.invokeMethod(barrier, "reached", Qt.BlockingQueuedConnection)
    app.processEvents()
    driver.syncNow.emit(False)
    assert QMetaObject.invokeMethod(barrier, "reached", Qt.BlockingQueuedConnection)
    app.processEvents()
    assert attempts == ["offline.mov"]
    assert states[-1] != ("idle", "Everything is in sync.")


def test_settings_retry_forgets_a_withdrawn_drive_recycle_choice(qapp, drive_env, tmp_path, monkeypatch):
    sync, cloud, root, _ = drive_env
    cloud.put("Projects/plan.txt", b"synced")
    sync.run()
    photos_root = tmp_path / "Photos"
    photos_root.mkdir()
    settings = Settings(photos_dir=str(photos_root), photos_dir_confirmed=True, drive_enabled=True,
                        drive_dir=str(root), notes_enabled=True, notes_dir=str(tmp_path / "Notes"))
    dlg = dialogs.SettingsDialog(settings, theme.LIGHT)
    warnings = []
    monkeypatch.setattr(dialogs, "warn", lambda parent, title, message: warnings.append(message))

    def choose_recycle():
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox)
        next(b for b in box.buttons() if box.buttonRole(b) == QMessageBox.DestructiveRole).click()

    try:
        dlg.drive_folders = []
        dlg.notes_dir.setText(str(root / "BadOverlap"))
        QTimer.singleShot(0, choose_recycle)
        dlg._save()
        assert warnings and not dlg.result()
        assert dlg.drive_trash == ["Projects"]
        dlg.drive_folders = None  # user changes their mind before fixing the other field
        dlg.notes_dir.setText(settings.notes_dir)
        dlg._save()
        assert dlg.result() == dialogs.QDialog.Accepted
        assert dlg.result_settings().drive_folders is None
        w = worker.SyncWorker(dlg.result_settings())
        w.drive = sync
        w.trash_drive_paths(dlg.drive_trash)  # the same cleanup list passed by open_settings.saved
        sync.run()
        assert "Projects/plan.txt" in cloud.items, cloud.trashed
        assert (root / "Projects/plan.txt").exists()
    finally:
        dlg.close()


def test_stop_during_drive_listing_stops_scheduling_new_folder_requests(qapp, drive_env, monkeypatch):
    sync, _, root, _ = drive_env
    api = ICloudDrive(SimpleNamespace(drive=object()))
    w = worker.SyncWorker(Settings(drive_enabled=True, drive_dir=str(root)))
    w.drive = sync
    sync.remote = api
    sync.cb = worker._Bridge(w, "drive")
    calls = []

    def folders(ids):
        ident, = ids
        calls.append(ident)
        if ident == "folder1":
            w.cancel()  # Stop during this request; later descendants aren't even known yet
        depth = 0 if ident == ROOT_ID else int(ident.removeprefix("folder"))
        items = [{"name": "Level", "drivewsid": f"folder{depth + 1}", "type": "FOLDER"}] if depth < 6 else []
        return [{"drivewsid": ident, "items": items, "numberOfItems": len(items)}]

    monkeypatch.setattr(api, "_folders", folders)
    with pytest.raises(Cancelled):
        sync.run()
    assert calls == [ROOT_ID, "folder1"]
