"""Independent, offline expected-safety contracts. Run with the review checkout on sys.path."""
import hashlib
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from skyfold import notes_engine as ne, updater as up
from tests.test_updater import DL, build_zip, fake_get, rel, release_json
from tests.test_notes_quality_codex import (
    OfflineCloudKit, asset, env, field, note_record, png, previews, qt_app, rec, token,
)
from tests.test_drive import env as drive_env, write


def package():
    return build_zip({"Skyfold/Skyfold.exe": b"inert", "Skyfold/_internal/lib.dll": b"inert"})


def offline_release(tmp_path):
    z = package()
    return up.download(rel(), tmp_path, get=fake_get(z, hashlib.sha256(z).hexdigest()))


def test_staging_never_deletes_a_preexisting_unowned_folder(tmp_path):
    stage = tmp_path / up.STAGE
    stage.mkdir()
    keep = stage / "owner-draft.txt"
    keep.write_bytes(b"owner data")
    offline_release(tmp_path)
    assert keep.is_file() and keep.read_bytes() == b"owner data"


def test_interrupted_download_leaves_no_partial_archive(tmp_path):
    z = package()
    with pytest.raises(up.UpdateError, match="cancelled"):
        up.download(rel(), tmp_path, get=fake_get(z, hashlib.sha256(z).hexdigest()),
                    cancelled=lambda: True)
    assert not list(tmp_path.rglob(up.ZIP_NAME))


def test_cancel_after_last_chunk_prevents_staging(tmp_path):
    z = package()
    cancelled = threading.Event()
    with pytest.raises(up.UpdateError, match="cancelled"):
        up.download(rel(), tmp_path, progress=lambda *_: cancelled.set(),
                    get=fake_get(z, hashlib.sha256(z).hexdigest()), cancelled=cancelled.is_set)


def test_response_is_released_when_transfer_stops(tmp_path):
    z = package()
    closed = []
    def get(url, **kwargs):
        response = fake_get(z, hashlib.sha256(z).hexdigest())(url, **kwargs)
        response.close = lambda: closed.append(url)
        return response
    with pytest.raises(up.UpdateError):
        up.download(rel(), tmp_path, get=get, cancelled=lambda: True)
    assert rel().zip_url in closed


def test_release_assets_are_bound_to_the_selected_tag():
    data = release_json()
    data["assets"][1]["browser_download_url"] = DL.replace("v9.9.9", "v9.9.8") + up.ZIP_NAME + ".sha256"
    assert up.latest_release(lambda *_: SimpleNamespace(json=lambda: data)) is None


def test_release_metadata_fails_closed_on_invalid_schema():
    data = release_json(assets=[None])
    assert up.latest_release(lambda *_: SimpleNamespace(json=lambda: data)) is None


def test_network_client_requires_an_explicit_redirect_policy(monkeypatch):
    import requests

    calls = []
    def get(url, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None)
    monkeypatch.setattr(requests, "get", get)
    up._get(up.API)
    assert not calls or calls[0]["allow_redirects"] is False


@pytest.mark.xfail(strict=True, reason="by design: the app passes verify=provenance.verify (see "
                   "tests/test_provenance.py); download() without it is the plain checksum step that the "
                   "other probes here rely on")
def test_installer_requires_build_provenance_before_staging(tmp_path):
    # A correct ZIP checksum alone carries no workflow identity; the ZIP contains inert bytes.
    with pytest.raises(up.UpdateError):
        offline_release(tmp_path)


def test_second_download_does_not_invalidate_the_first(tmp_path):
    first = offline_release(tmp_path)
    other = build_zip({"Skyfold/Skyfold.exe": b"second inert", "Skyfold/_internal/lib.dll": b"inert"})
    second = up.download(rel(), tmp_path, get=fake_get(other, hashlib.sha256(other).hexdigest()))
    assert first != second and (first / "Skyfold.exe").read_bytes() == b"inert"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell")
def test_a_failed_restart_keeps_or_restores_the_previous_build(tmp_path, monkeypatch):
    app = tmp_path / "A space - d'écho"
    (app / "_internal").mkdir(parents=True)
    (app / "_internal" / "old.dll").write_bytes(b"old")
    (app / "Skyfold.exe").write_bytes(b"old")
    (app / "data").mkdir()
    (app / "data" / "settings.json").write_bytes(b"unchanged")
    new = offline_release(app)  # deliberately non-runnable marker bytes, same as the existing swap test
    assert new.is_relative_to(tmp_path)
    log = tmp_path / "installer.log"
    monkeypatch.setattr("skyfold.config.LOG_FILE", log)
    launched = []
    def popen(args, **kwargs):
        assert Path(args[8]) == app and Path(args[9]) == new
        launched.append(subprocess.Popen(args, **kwargs))
    up.start_install(new, app, pid=999999, popen=popen)
    assert launched[0].wait(timeout=30) == 0
    assert (app / "data" / "settings.json").read_bytes() == b"unchanged"
    assert "Could not start Skyfold again" in log.read_text(encoding="utf-8", errors="replace")
    assert (app / "Skyfold.exe").read_bytes() == b"old" or list(app.glob("Skyfold.exe.old-*"))


def notes_case(env, records, blobs, uti):
    sync, fake, root, _ = env
    raw = OfflineCloudKit(records, blobs)
    adapter = ne.ICloudNotes(SimpleNamespace(notes=SimpleNamespace(
        _raw=raw, _lookup_note_record=lambda nid: note_record(uti))))
    fake.export = adapter.export
    fake.put("n1", "Attachment")
    return sync, root, raw


def test_legacy_tiff_is_browser_readable_after_real_export(env, tmp_path):
    uti = "com.apple.notes.sketch"
    full = png(tmp_path / "full.tiff", 850, 650, "TIFF")
    sync, root, raw = notes_case(env, [rec(uti, FallbackImage=asset("full"), **previews())],
                                 {token("full")["downloadURL"]: full.read_bytes()}, uti)
    result = sync.run()
    assert result.exported == 1
    page = root / "Notes" / "Attachment.html"
    src = ne.IMG_TAG.search(page.read_text(encoding="utf-8")).group(1)
    assert Path(unquote(src)).suffix.lower() == ".png"


def test_unavailable_scan_pdf_keeps_a_local_preview(env, tmp_path):
    uti = "com.apple.paper.doc.scan"
    preview = png(tmp_path / "preview.png", 320, 240).read_bytes()
    sync, root, raw = notes_case(
        env, [rec(uti, FallbackPDF=asset("full.pdf"), **previews())],
        {token(name)["downloadURL"]: preview for name in ("small", "large")}, uti)
    result = sync.run()
    page = root / "Notes" / "Attachment.html"
    assert result.exported == 1 and page.is_file()
    match = ne.IMG_TAG.search(page.read_text(encoding="utf-8"))
    assert match and (page.parent / unquote(match.group(1))).is_file()


@pytest.mark.parametrize("quick", [True, False], ids=["quick", "full-control"])
def test_held_file_returned_under_new_name_does_not_duplicate_in_drive(drive_env, quick):
    s, cloud, root, _ = drive_env
    s.delete_threshold = 1
    cloud.put("A/a.txt", b"moved file")
    cloud.put("A/b.txt", b"another file with a distinct size")
    cloud.put("B/anchor.txt", b"anchor")
    s.run()
    moved = root / "A/a.txt"
    outside = root.parent / "a.txt"
    moved.rename(outside)
    (root / "A/b.txt").unlink()
    question = s.run()
    assert question.pending_to_cloud == ["A/a.txt", "A/b.txt"]
    outside.rename(root / "B/a.txt")
    write(root, "A/new.txt", b"unrelated save in A")
    s.run(changed={"A/new.txt", "B/a.txt"} if quick else None)
    assert "B/a.txt" in cloud.items and "A/a.txt" not in cloud.items
    assert not cloud.trashed


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell")
def test_installer_aborts_if_original_process_does_not_exit(tmp_path, monkeypatch):
    app = tmp_path / "App"
    (app / "_internal").mkdir(parents=True)
    (app / "Skyfold.exe").write_bytes(b"old")
    (app / "_internal" / "old.dll").write_bytes(b"old")
    new = offline_release(app)
    monkeypatch.setattr("skyfold.config.LOG_FILE", tmp_path / "log.txt")
    # Shorten only the real wait. Pytest stays alive for the entire swap.
    monkeypatch.setattr(up, "_SCRIPT", up._SCRIPT.replace("-Timeout 120", "-Timeout 1"))
    launched = []
    up.start_install(new, app, pid=os.getpid(), popen=lambda *a, **k: launched.append(subprocess.Popen(*a, **k)))
    launched[0].wait(timeout=20)
    assert (app / "Skyfold.exe").read_bytes() == b"old"
    assert (app / "_internal" / "old.dll").read_bytes() == b"old"


def test_asset_download_does_not_use_ambient_netrc_credentials(monkeypatch):
    import requests

    sent = []
    def send(session, request, **kwargs):
        sent.append(request)
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        response._content = b"{}"
        response._content_consumed = True
        return response
    monkeypatch.setattr(requests.sessions, "get_netrc_auth", lambda *_a, **_k: ("fixture", "fake"))
    monkeypatch.setattr(requests.sessions.Session, "send", send)
    up._get(up.API)
    assert sent and "Authorization" not in sent[0].headers


def test_unrelated_identifier_field_does_not_change_another_attachment():
    from pyicloud.services.notes.rendering import exporter
    from pyicloud.services.notes.rendering.ck_datasource import CloudKitNoteDataSource
    from pyicloud.services.notes.rendering.options import ExportConfig

    ds = CloudKitNoteDataSource(_config=ExportConfig())
    a = rec(name="a", AttachmentIdentifier=field("STRING", "inline-a"),
            Identifier=field("STRING", "b"), Media=field("REFERENCE", {"recordName": "media"}), **previews())
    b = rec(name="b", PrimaryAsset=asset("own-image"))
    media_map = exporter._hydrate_attachment_records(ds, SimpleNamespace(records=[a, b]), ExportConfig())
    media = rec(name="media", Image=asset("a-image"))
    exporter._wire_media_to_parent(ds, media_map, media, exporter._media_field_url(media), ExportConfig())
    assert ds.get_primary_asset_url("b") == token("own-image")["downloadURL"]


def test_shared_original_failure_uses_each_attachments_own_preview(env, tmp_path):
    import base64
    import gzip
    from pyicloud.common.cloudkit import CKRecord
    from pyicloud.services.notes.protobuf import notes_pb2 as pb

    s, fake, root, _ = env
    recs = [rec(name=k, Media=field("REFERENCE", {"recordName": "media"}),
                PreviewImages=field("ASSETID_LIST", [token(k)])) for k in ("a", "b")]
    recs.append(rec(name="media", Image=asset("full")))
    pixels = {k: png(tmp_path / (k + ".png"), w, 100).read_bytes() for k, w in (("a", 123), ("b", 456))}
    raw = OfflineCloudKit(recs, {token(k)["downloadURL"]: blob for k, blob in pixels.items()})
    proto = pb.NoteStoreProto()
    proto.document.note.note_text = "\ufffc\ufffc"
    for k in ("a", "b"):
        run = proto.document.note.attribute_run.add()
        run.length = 1
        run.attachment_info.attachment_identifier = k
        run.attachment_info.type_uti = "public.jpeg"
    note = CKRecord(recordName="n1", recordType="Note", fields={
        "TextDataEncrypted": field("ENCRYPTED_BYTES", base64.b64encode(gzip.compress(proto.SerializeToString())).decode()),
        "Attachments": field("REFERENCE_LIST", [{"recordName": k} for k in ("a", "b")])})
    fake.export = ne.ICloudNotes(SimpleNamespace(notes=SimpleNamespace(
        _raw=raw, _lookup_note_record=lambda _: note))).export
    fake.put("n1", "Shared")
    assert s.run().exported == 1
    from PySide6.QtGui import QImage
    page = root / "Notes/Shared.html"
    sources = [m.group(1) for m in ne.IMG_TAG.finditer(page.read_text(encoding="utf-8"))]
    assert [QImage(str(page.parent / unquote(src))).width() for src in sources] == [123, 456]


def test_unknown_pc_file_identity_cannot_be_used_as_a_confirmed_shape(drive_env, monkeypatch):
    from skyfold import drive_engine as drive

    sync, cloud, root, _ = drive_env
    cloud.put("file.txt", b"payload")
    sync.run()
    real = os.lstat
    def lstat(path, *args, **kwargs):
        st = real(path, *args, **kwargs)
        if Path(path) == root / "file.txt":
            return SimpleNamespace(st_mode=st.st_mode, st_dev=st.st_dev, st_ino=0)
        return st
    monkeypatch.setattr(drive.os, "lstat", lstat)
    assert sync._pc_shape("file.txt") is None


def test_update_dialog_can_retry_when_installer_cannot_launch(monkeypatch, tmp_path, qt_app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from skyfold import app as appmod
    from skyfold.ui import dialogs
    from tests.test_update_ui import _wait

    warnings = []
    monkeypatch.setattr(up, "can_install", lambda: True)
    monkeypatch.setattr(up, "download", lambda *_a, **_kw: tmp_path)
    def fail_launch(_):
        raise OSError(5, "synthetic installer failure")
    monkeypatch.setattr(up, "start_install", fail_launch)
    monkeypatch.setattr(appmod, "warn", lambda *_args: warnings.append(True))
    shown = []
    ctl = SimpleNamespace(_release=rel(), _show_dialog=lambda _, dialog: shown.append(dialog))
    appmod.Controller.open_update(ctl)
    d = shown[0]
    try:
        d.show()
        QTest.mouseClick(d.go, Qt.LeftButton)
        _wait(qt_app, lambda: warnings)
        assert warnings and d.go.isEnabled()
    finally:
        d.close()


def test_small_photo_quality_refresh_is_one_shot(env, tmp_path):
    s, fake, root, _ = env
    s.state.set("quality", "2")
    fake.put("n1", "Small photo", body='<img data-uti="public.jpeg" src="../_assets/n1/img.png">')
    assets = root / "_assets" / "n1"
    assets.mkdir(parents=True)
    png(assets / "img.png", 320, 240)
    assert s.run().exported == 1
    # An earlier migration pass may perform the one-time orphan sweep first.
    s.state.set("orphan_sweep", "1")
    assert s.run().exported == 1
    assert s.state.get("quality") == ne.QUALITY and s.run().exported == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows NTFS")
def test_notes_refuses_an_asset_directory_that_resolves_outside_library(env, tmp_path):
    from tests.test_review3_fixes import _junction

    s, fake, root, _ = env
    sibling = tmp_path / "another-folder"
    sibling.mkdir()
    assets = root / "_assets"
    assets.mkdir(parents=True)
    _junction(sibling, assets / "n1")
    attempted = []

    class Guarded(OfflineCloudKit):
        def download_asset_to(self, url, directory):
            attempted.append(Path(directory).resolve())
            raise OSError("probe: stop any writes at the fake downloader boundary")

    raw = Guarded([rec(PrimaryAsset=asset("photo.jpg"))], {})
    fake.export = ne.ICloudNotes(SimpleNamespace(notes=SimpleNamespace(
        _raw=raw, _lookup_note_record=lambda _: note_record()))).export
    fake.put("n1", "Photo")
    s.run()
    assert all(p.is_relative_to(assets.resolve()) for p in attempted)
    assert not list(sibling.iterdir())


@pytest.mark.parametrize("frozen,selftest,armed", [(False, False, False), (True, True, False), (True, False, True)])
def test_automatic_update_timer_is_only_armed_for_packaged_app(monkeypatch, qt_app, frozen, selftest, armed):
    from skyfold import app as appmod
    from skyfold.config import Settings

    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(sys, "argv", ["Skyfold.exe"] + (["--selftest", "fake-report.txt"] if selftest else []))
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: Settings()))
    monkeypatch.setattr(appmod.SyncWorker, "start", lambda self: None)
    monkeypatch.setattr(up, "latest_release", lambda: pytest.fail("automatic network work started in test"))
    scheduled = []
    monkeypatch.setattr(appmod.QTimer, "singleShot", lambda msec, *_a: scheduled.append(msec))
    ctl = appmod.Controller(qt_app)
    try:
        assert ctl.update_clock.isActive() is armed
        assert (60_000 in scheduled) is armed
        assert ctl.update_clock.interval() == 24 * 3600 * 1000
    finally:
        ctl.update_clock.stop()
        ctl.clock.stop()
        ctl.anim.stop()
        ctl.thread.quit()
        assert ctl.thread.wait(3000)
        ctl.tray.hide()
        ctl.flyout.close()
