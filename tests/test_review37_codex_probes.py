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
from tests.test_engine import env as photos_env
from tests.test_notes import env as notes_env


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
    with pytest.raises(up.IncompleteRelease):  # refused, and since 1.0.18 not reported as "latest" either
        up.latest_release(lambda *_: SimpleNamespace(json=lambda: data))


def test_release_metadata_fails_closed_on_invalid_schema():
    data = release_json(assets=[None])
    assert up.latest_release(lambda *_: SimpleNamespace(json=lambda: data)) is None


def test_network_client_requires_an_explicit_redirect_policy(monkeypatch):
    import requests

    calls = []
    def get(*args, **kwargs):  # (the updater uses a Session; never the real network in tests)
        calls.append(kwargs)
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, headers={})
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(requests.Session, "get", lambda self, url, **kw: get(url, **kw))
    up._get(up.API)
    assert calls and calls[0]["allow_redirects"] is False


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


# Additional regressions for v1.0.14..e3eb54a. An old mtime represents a settled file.
def test_r37_switching_apple_id_does_not_upload_old_accounts_library(photos_env, qt_app, monkeypatch):
    from skyfold import app, auth, worker
    from tests.test_engine import FakeRemote

    original, first, root, _ = photos_env
    settings = original.settings
    settings.apple_id = "first@example.invalid"
    settings.photos_upload = settings.photos_dir_confirmed = True
    first.add("PRIVATE", "private.jpg", b"first account only")
    second = FakeRemote()
    w = worker.SyncWorker(settings)
    w.state = original.state
    # Substitute only the service boundaries, persistence, timers and window presentation.
    monkeypatch.setattr(worker, "ICloudRemote", lambda api: api)
    monkeypatch.setattr(auth, "_CURRENT", None)
    monkeypatch.setattr(auth, "commit_login", lambda api: None)
    monkeypatch.setattr(type(settings), "save", lambda self: None)
    monkeypatch.setattr(w, "_ensure_watchers", lambda: None)
    monkeypatch.setattr(w, "_refresh_storage", lambda **kw: None)
    monkeypatch.setattr(w, "_apply_timers", lambda: None)
    told = []
    monkeypatch.setattr(app, "inform", lambda *a, **k: told.append(a[2]))  # (a modal box would wait for a click)
    w.set_api(first)
    assert (root / w.state.files()[0].rel_path).read_bytes() == b"first account only"
    w.sign_out()
    dialogs = {}
    ctl = SimpleNamespace(
        settings=settings, worker=w, _dialogs=dialogs,
        req=SimpleNamespace(applySettings=SimpleNamespace(emit=w.apply_settings),
                            setApi=SimpleNamespace(emit=w.set_api)),
        tray=SimpleNamespace(geometry=lambda: None),
        flyout=SimpleNamespace(set_account=lambda name: None, show_at=lambda rect: None),
        _show_dialog=lambda key, dialog: dialogs.update({key: dialog}),
    )
    app.Controller._open_login(ctl)
    dialog = dialogs["login"]
    try:
        dialog.loggedIn.emit(second, "second@example.invalid")
        assert w.api is second
        assert list(second.assets.values()) == []
        assert w.settings.photos_upload is False
        from PySide6.QtTest import QTest
        QTest.qWait(400)
        from skyfold.i18n import t
        assert told and t("Photos") in told[0]  # (1.0.16: the new Apple ID chooses its own photos folder)
        assert w.settings.photos_dir_confirmed is False
    finally:
        dialog.close()


def test_r37_full_check_consumes_deletion_of_recently_uploaded_asset(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    source = write(root, "new.jpg", b"original", age=300)
    s.run()  # change feed confirms this upload before the next full library read
    (aid,) = remote.assets
    remote.remove(aid)
    s.run(force_full=True)  # first pass after deleting it on the phone
    s.run()
    s.run(force_full=True)
    # one-way: the PC file stays and isn't sent again
    assert source.exists() and not s.state.asset_ids() and remote.uploads == ["new.jpg"]


@pytest.mark.parametrize("checkpoint", ["before_send", "registered", "receipt_saved", "matched"])
def test_r37_upload_restart_does_not_replay_or_gain_unproven_ownership(photos_env, tmp_path, checkpoint):
    import json
    from skyfold.engine import Syncer
    from skyfold.state import State

    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    path = write(root, "library.jpg", b"photo", age=300)
    if checkpoint == "matched":
        s.run()
    elif checkpoint == "receipt_saved":
        assert s.upload_new() == 1
    else:
        # Reconstruct the two durable states that have no registration receipt.
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        st = path.stat()
        s.state.set("upload_pending:" + digest, json.dumps(dict(
            rel=path.name, digest=digest, asset=None, duplicate=False, identity=[st.st_dev, st.st_ino])))
        if checkpoint == "registered":
            remote.upload(path)
    reopened = Syncer(State(tmp_path / "s.db"), remote, s.settings)
    reopened.run(force_full=True)
    reopened.run(force_full=True)
    assert len(getattr(remote, "uploads", [])) == (0 if checkpoint == "before_send" else 1)
    assert list(root.rglob("*.jpg")) == [path]
    assert not reopened.state.files()  # one-way: never a synced photo


def test_r37_pending_rename_avoids_download_back_while_another_photo_is_missing(photos_env):
    from skyfold.remote import UploadReceipt

    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    remote.add("OLD", "old.jpg", b"unrelated")
    s.run()
    old = root / s.state.files()[0].rel_path
    source = write(root, "new.jpg", b"new photo", age=300)
    # Same indexing delay as test_upload_indexed_later_is_matched_not_downloaded.
    remote.upload = lambda path: UploadReceipt("NEW", False)
    s.run()
    renamed = source.rename(root / "holiday.jpg")
    old.unlink()
    remote.add("NEW", "new.jpg", b"new photo")
    s.run()
    assert list(root.rglob("*.jpg")) == [renamed]


def test_r37_restoring_recycled_upload_recreates_cloud_photo(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    p = write(root, "holiday.jpg", b"holiday", age=300)
    s.run()
    (aid,) = remote.assets
    remote.remove(aid)
    s.run()
    assert p.exists() and not remote.assets  # one-way: deleting it in iCloud leaves the PC file
    s.run(force_full=True)
    assert not remote.assets  # and it isn't sent back


def test_r37_kept_photo_stays_local_after_rename(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.settings.delete_cloud_to_pc = False
    remote.add("A", "old.jpg", b"family")
    s.run()
    p = root / s.state.files()[0].rel_path
    remote.remove("A")
    s.run()
    assert p.is_file() and not remote.assets
    p.rename(p.with_name("family.jpg"))
    s.run()
    assert not remote.assets


def test_r37_duplicate_copy_retains_read_only_association(photos_env):
    s, remote, root, trashed = photos_env
    remote.add("A", "phone.jpg", b"photo")
    s.settings.photos_upload = True
    s.run()
    synced = root / s.state.files()[0].rel_path
    copy = write(root, "backup.jpg", b"photo", age=300)
    s.run(force_full=True)
    assert remote.uploads == ["backup.jpg"] and list(remote.assets) == ["A"]
    synced.unlink()  # remove the synced original, leaving the known independent copy
    s.run()
    if "A" in remote.assets:
        remote.remove("A")  # remove it on the phone if the first deletion didn't propagate
    s.run(force_full=True)
    assert copy.is_file() and copy.name not in trashed


def test_r37_renaming_duplicate_does_not_resend_its_bytes(photos_env):
    s, remote, root, _ = photos_env
    remote.add("A", "phone.jpg", b"photo")
    s.settings.photos_upload = True
    s.run()
    copy = write(root, "copy.jpg", b"photo", age=300)
    s.run(force_full=True)
    assert remote.uploads == ["copy.jpg"]
    copy.rename(copy.with_name("renamed.jpg"))
    s.run()
    assert remote.uploads == ["copy.jpg"]


def test_r37_live_movie_stays_local_after_icloud_still_deletion(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    write(root, "IMG_0001.HEIC", b"still", age=300)
    movie = write(root, "IMG_0001.MOV", b"motion", age=300)
    s.run()
    assert remote.uploads == ["IMG_0001.HEIC"]
    (aid,) = remote.assets
    remote.remove(aid)
    s.run()
    assert movie.is_file() and not remote.assets
    s.run()
    assert remote.uploads == ["IMG_0001.HEIC"] and not remote.assets


def test_r37_newly_copied_photo_gets_the_advertised_settle_period(photos_env, tmp_path):
    import shutil

    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    camera = write(tmp_path / "card", "recent-import.jpg", b"camera photo", age=86400)
    target = root / camera.name
    target.write_bytes(b"previous contents")
    s.run()
    assert not getattr(remote, "uploads", [])
    shutil.copy2(camera, target)  # different bytes, but Explorer-style copying preserves the old mtime
    s.run()
    assert not getattr(remote, "uploads", [])


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only attribute")
def test_r37_readonly_export_does_not_block_other_notes(notes_env):
    import stat
    from tests.test_upload_restyle_safety import old_note

    syncer, page = old_note(notes_env)
    _, remote, root, _ = notes_env
    page.chmod(stat.S_IREAD)
    try:
        syncer.run()
        remote.put("B", "Another note")
        syncer.run()
        assert (root / "Notes/Another note.html").is_file()
    finally:
        for candidate in page.parent.glob("*.html"):
            candidate.chmod(stat.S_IWRITE)


def test_r37_drive_ready_waits_for_file_folder_clash_to_clear(drive_env, tmp_path):
    from tests.test_drive_first_notice import _worker

    drive, remote, root, _ = drive_env
    remote.put("Invoices", None)
    remote.put("Invoices/bill.pdf", b"bill")
    write(root, "Invoices", b"my file")
    worker, titles = _worker(tmp_path)
    worker.drive = drive
    problems = []
    assert worker._run_drive(problems) and problems
    early = list(titles)
    (root / "Invoices").rename(root / "invoices.txt")
    assert worker._run_drive([])
    assert (root / "Invoices/bill.pdf").is_file()
    assert (early, titles) == ([], ["iCloud Drive is ready"])


def test_r37_manual_sync_from_tray_while_paused_refreshes_bookmarks(photos_env):
    from tests.test_bookmarks_sync_now import _worker

    worker, bookmark_reads = _worker()
    worker.syncer, _, _, _ = photos_env
    worker.settings.paused = True
    worker.sync_now(True)  # the tray menu remains available while polling is paused
    assert bookmark_reads == [1]


@pytest.mark.skipif(os.name != "nt", reason="Windows replacement sharing semantics")
def test_r37_locked_session_keeps_previous_json_and_saves_on_next_request(tmp_path, monkeypatch):
    import json
    from skyfold import auth
    from tests.test_session_atomic_save import _service

    monkeypatch.setattr(auth, "_CURRENT", None)
    session = _service(tmp_path).session
    session.data["client_id"] = "offline-before"
    session._save_session_data()
    target = Path(session.session_path)
    with target.open("rb") as reader:
        session.data["client_id"] = "offline-after"
        session._save_session_data()
        assert json.load(reader)["client_id"] == "offline-before"
        assert not list(tmp_path.glob("*.tmp"))
    session._save_session_data()
    assert json.loads(target.read_text(encoding="utf-8"))["client_id"] == "offline-after"


@pytest.mark.parametrize(("status", "reason"), [(403, "Forbidden"), (429, "Too Many Requests")])
def test_r37_secondary_rate_limit_uses_releases_page(monkeypatch, status, reason):
    import requests

    calls = []

    def reply(_session, url, **kwargs):
        calls.append(url)
        r = requests.Response()
        r.url = url
        if url == up.API:
            r.status_code, r.reason = status, reason
            r.headers["X-RateLimit-Remaining"] = "41"
            r._content = b'{"message":"You have exceeded a secondary rate limit."}'
        elif url == up.PAGE:
            r.status_code = 302
            r.headers["Location"] = "https://github.com/zoranmod/skyfold/releases/tag/v1.0.15"
            r._content = b""
        else:
            r.status_code, r._content = 200, b"Release notes"
        r._content_consumed = True
        return r

    monkeypatch.setattr(requests.Session, "get", reply)
    release = up.latest_release()
    assert release.version == "1.0.15" and up.PAGE in calls
