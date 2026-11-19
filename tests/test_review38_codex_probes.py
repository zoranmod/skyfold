"""Offline review of 975c07c. The old timestamps represent settled camera imports."""
import hashlib
import io
import json
import os
import stat
from types import SimpleNamespace

import pytest

from skyfold import engine, notes_engine, updater, worker
from skyfold.remote import UploadReceipt
from tests.test_drive import env as drive_env, write
from tests.test_engine import FakeRemote, env as photos_env
from tests.test_notes import env as notes_env


def test_undoing_a_rename_does_not_delete_the_uploaded_cloud_photo(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    original = write(root, "beach.jpg", b"beach", age=300)
    s.run()
    (aid,) = s.state.asset_ids()
    moved = original.rename(root / "coast.jpg")
    s.run()
    assert s.state.files()[0].rel_path == moved.name
    moved.rename(original)
    s.run()
    assert aid in remote.assets and not remote.deleted


def test_renamed_independent_copy_is_not_recycled_as_the_original(photos_env):
    s, remote, root, trashed = photos_env
    remote.add("A", "phone.jpg", b"family")
    s.settings.photos_upload = True
    s.run()
    original = root / s.state.files()[0].rel_path
    duplicate = write(root, "copy.jpg", b"family", age=300)
    s.run(force_full=True)
    assert remote.uploads == [duplicate.name]
    # Organize the two files before the next poll.
    kept = duplicate.rename(root / "family-backup.jpg")
    original.unlink()
    s.run()
    if "A" in remote.assets:
        remote.remove("A")
    s.run(force_full=True)
    assert kept.exists() and kept.name not in trashed


def test_restore_after_pc_initiated_deletion_can_upload_again(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    p = write(root, "holiday.jpg", b"holiday", age=300)
    s.run()
    p.unlink()  # Explorer deletion propagated to the phone
    s.run()
    assert remote.deleted and not remote.assets
    write(root, "recovered.jpg", b"holiday", age=300)
    s.run(force_full=True)
    assert any(a.filename == "recovered.jpg" for a in remote.assets.values())


def test_recycling_primary_retains_known_duplicate_after_rename(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    remote.add("A", "phone.jpg", b"family")
    s.run()
    copy = write(root, "backup.jpg", b"family", age=300)
    s.run(force_full=True)
    assert remote.uploads == [copy.name]
    remote.remove("A")
    s.run()
    assert copy.exists() and not remote.assets
    copy.rename(root / "archived.jpg")
    s.run()
    assert not remote.assets


def test_kept_photo_from_previous_build_stays_local_after_upgrade(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    p = write(root, "family.jpg", b"old export", age=300)
    # e3eb54a's complete durable representation for a copy kept after cloud deletion.
    s.state.set("upload_done:family.jpg", hashlib.sha256(p.read_bytes()).hexdigest())
    s.run(force_full=True)
    assert not remote.assets
    p.rename(root / "family-archive.jpg")
    s.run()
    assert not remote.assets


def test_live_sidecar_does_not_blacklist_a_separate_standalone_import(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    write(root, "IMG.HEIC", b"still", age=300)
    write(root, "IMG.MOV", b"motion", age=300)
    s.run()
    assert remote.uploads == ["IMG.HEIC"]
    # The user explicitly imports the motion as an independently named video.
    write(root, "Videos/standalone.mov", b"motion", age=300)
    s.run()
    assert remote.uploads == ["IMG.HEIC", "standalone.mov"]


@pytest.mark.parametrize("change", ["old_name_reused", "uploads_turned_off"])
def test_indexing_follows_existing_upload_after_rename(photos_env, change):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    p = write(root, "IMG.jpg", b"accepted", age=300)
    remote.upload = lambda path: UploadReceipt("A", False)
    s.run()
    renamed = p.rename(root / "holiday.jpg")
    if change == "old_name_reused":
        p.write_bytes(b"next camera import")  # still settling
    else:
        s.settings.photos_upload = False  # applies to future uploads
    remote.add("A", "IMG.jpg", b"accepted")
    s.run()
    copies = [q for q in root.rglob("*.jpg") if q.read_bytes() == b"accepted"]
    assert copies == [renamed]


def test_read_only_duplicate_association_follows_rename(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    remote.add("A", "phone.jpg", b"family")
    copy = write(root, "backup.jpg", b"family", age=300)
    s.run()
    assert s.state.get("upload_seen:A") and not s.state.files()
    renamed = copy.rename(root / "archive.jpg")
    s.run(force_full=True)
    assert list(root.rglob("*.jpg")) == [renamed]


def test_live_cloud_asset_with_unindexed_master_keeps_its_pc_file(photos_env):
    from pyicloud.common.cloudkit.models import CKLookupResponse
    from skyfold.remote import ICloudRemote

    s, remote, root, trashed = photos_env
    s.settings.photos_upload = True
    s.run()
    p = write(root, "camera.jpg", b"original", age=300)
    s.run()
    (aid,) = s.state.asset_ids()
    assert s.state.get("upload_unlisted:" + aid)

    def records(*, record_names, zone_id):
        if record_names == [aid]:
            rows = [{"recordType": "CPLAsset", "recordName": aid,
                     "fields": {"masterRef": {"type": "REFERENCE", "value": {"recordName": "master"}}}}]
        else:
            assert record_names == ["master"]
            rows = [{"recordName": "master", "serverErrorCode": "NOT_FOUND"}]
        return CKLookupResponse.model_validate({"records": rows})

    photos = SimpleNamespace(libraries={"root": SimpleNamespace(zone_id={"zoneName": "PrimarySync"})},
                             private_client=SimpleNamespace(lookup=records))
    remote.lookup = ICloudRemote(SimpleNamespace(photos=photos)).lookup
    remote.list_assets = lambda on_page: iter(())  # first complete library listing still lacks the upload
    s.run(force_full=True)
    assert aid in remote.assets
    assert p.exists() and not trashed


def test_same_size_timestamp_preserving_replacement_gets_settle_time(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    remote.add("A", "phone.jpg", b"aaaa")
    s.run()
    copy = write(root, "copy.jpg", b"aaaa", age=3600)
    s.run(force_full=True)
    st = copy.stat()
    incoming = write(root, "edited.tmp", b"bbbb")
    os.utime(incoming, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(incoming, copy)  # editor publishes a new file while preserving the image timestamp
    s.run()
    assert remote.uploads == ["copy.jpg"]


def test_restart_preserves_wait_for_an_observed_change(photos_env, tmp_path):
    from skyfold.state import State

    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    path = root / "camera.jpg"
    path.write_bytes(b"first")
    s.run()
    write(root, path.name, b"a different picture", age=86400)
    s.run()
    assert not getattr(remote, "uploads", [])
    restarted = engine.Syncer(State(tmp_path / "s.db"), remote, s.settings)
    restarted.run()
    assert not getattr(remote, "uploads", [])


def offline_worker(settings, state, monkeypatch):
    from skyfold import auth

    w = worker.SyncWorker(settings)
    w.state = state
    monkeypatch.setattr(worker, "ICloudRemote", lambda api: api)
    monkeypatch.setattr(auth, "_CURRENT", None)
    monkeypatch.setattr(w, "_ensure_watchers", lambda: None)
    monkeypatch.setattr(w, "_apply_timers", lambda: None)
    monkeypatch.setattr(w, "_refresh_storage", lambda **kw: None)
    return w


def test_account_switch_cannot_recycle_previous_accounts_download(photos_env, monkeypatch):
    s, first, root, trashed = photos_env
    s.settings.apple_id = "a@example.invalid"
    s.settings.photos_dir_confirmed = s.settings.photos_upload = True
    first.add("A", "family.jpg", b"family picture")
    w = offline_worker(s.settings, s.state, monkeypatch)
    w.set_api(first)
    first_copy = root / s.state.files()[0].rel_path
    w.sign_out()
    other = s.settings.copy()
    other.apple_id = "b@example.invalid"
    other.photos_upload = False  # the controller explicitly turns it off
    w.apply_settings(other, True)
    second = FakeRemote()
    second.add("B", "family.jpg", b"family picture")  # each account owns the same original photo
    w.set_api(second)
    assert not w.settings.photos_upload and not getattr(second, "uploads", [])
    second.remove("B")
    w.sync_now()
    # B's own download (family_B.jpg) goes to the Recycle Bin with B's photo; A's file is never B's
    assert first_copy.exists() and first_copy.name not in trashed


def test_portable_settings_for_another_account_cannot_upload_old_library(photos_env, monkeypatch):
    s, first, root, _ = photos_env
    s.settings.apple_id = "a@example.invalid"
    s.settings.photos_dir_confirmed = s.settings.photos_upload = True
    first.add("A", "private.jpg", b"account A only")
    w = offline_worker(s.settings, s.state, monkeypatch)
    w.set_api(first)
    assert (root / s.state.files()[0].rel_path).exists() and s.state.get("account")
    # Start a different portable copy. Settings travel with it; the database is per Windows user.
    saved = s.settings.copy()
    saved.apple_id = "b@example.invalid"
    w2 = offline_worker(saved, s.state, monkeypatch)
    second = FakeRemote()
    w2.set_api(second)
    assert not second.assets


def test_answering_photo_question_while_paused_does_not_recycle_notes(photos_env, notes_env, monkeypatch):
    s, remote, root, _ = photos_env
    ns, notes, notes_root, trashed = notes_env
    s.settings.photos_dir_confirmed = True
    s.settings.notes_enabled = True
    s.settings.notes_dir = str(notes_root)
    s.settings.delete_threshold = 1
    w = offline_worker(s.settings, s.state, monkeypatch)
    w.syncer, w.api, w.notes = s, remote, ns
    remote.add("A", "a.jpg", b"a")
    remote.add("B", "b.jpg", b"b")
    notes.put("N", "Budget")
    w.sync_now()
    page = notes_root / ns.state.all()["N"][1]
    for row in s.state.files():
        (root / row.rel_path).unlink()
    w.sync_now()
    question = w._pending
    assert set(question.to_cloud) == {"A", "B"}
    w.settings.paused = True
    w.set_paused(True)
    notes.put("N", "Renamed budget")  # the cloud rename normally retires Budget.html
    w.resolve_pending(f"reject:to_cloud:{question.gen_side['to_cloud']}", question.to_cloud)
    assert page.exists() and page.name not in trashed


def test_paused_tray_sync_includes_enabled_drive(photos_env, drive_env, monkeypatch):
    s, photos, _, _ = photos_env
    drive, remote, root, _ = drive_env
    remote.put("inbox.txt", b"new")
    s.settings.photos_dir_confirmed = s.settings.paused = s.settings.drive_enabled = True
    w = offline_worker(s.settings, s.state, monkeypatch)
    w.syncer, w.api, w.drive = s, photos, drive
    w.sync_now(True)
    if w._drive_thread is not None:
        w._drive_thread.join(10)
    assert (root / "inbox.txt").is_file()


def test_partial_first_drive_listing_waits_before_announcing_ready(drive_env, tmp_path):
    from skyfold.drive_remote import ICloudDrive, ROOT_ID
    from tests.test_drive_first_notice import _worker

    drive, _, _, _ = drive_env
    api = ICloudDrive(SimpleNamespace(drive=object()))
    api._folders = lambda ids: [{"drivewsid": ROOT_ID, "numberOfItems": 1, "items": []}]
    drive.remote = api
    w, titles = _worker(tmp_path)
    w.drive = drive
    assert w._run_drive([]) and api.incomplete == {""}
    assert "iCloud Drive is ready" not in titles


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only file attribute")
def test_old_read_only_restyle_journal_completes_and_preserves_page(notes_env):
    from tests.test_upload_restyle_safety import old_note

    s, page = old_note(notes_env)
    before = notes_engine._digest(page)
    old_body = page.read_bytes()
    backup = page.with_name(notes_engine.STAGE_PREFIX + "style-old-private.html")
    page.rename(backup)
    backup.chmod(stat.S_IREAD)
    try:
        updated = old_body.replace(b'<style id="skyfold">old</style>', notes_engine.NOTE_CSS.strip().encode())
        page.write_bytes(updated)
        after = notes_engine._digest(page)
        s.state.set("style_tx:A", json.dumps([page.relative_to(s.root).as_posix(), backup.name, before, after]))
        s.run()
        assert page.read_bytes() == updated
        assert not backup.exists() and not s.state.get("style_tx:A")
        assert s.state.get("page:A") == after
    finally:
        if backup.exists():
            backup.chmod(stat.S_IWRITE)


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only file attribute")
def test_read_only_page_with_cloud_edit_does_not_fail_after_publication(notes_env):
    from tests.test_upload_restyle_safety import old_note

    s, page = old_note(notes_env)
    page.chmod(stat.S_IREAD)
    try:
        s.remote.put("A", "Sample", body="new cloud content")
        result = s.run()
        if result.errors:
            assert b"new cloud content" in page.read_bytes()
            assert any(page.parent.glob(notes_engine.STAGE_PREFIX + "old-*.html"))
        assert result.errors == 0
    finally:
        for p in page.parent.glob("*.html"):
            p.chmod(stat.S_IWRITE)


@pytest.mark.parametrize(("status", "body", "consumed", "limited"), [
    (200, b"PK" + b"x" * 8192, 0, False),
    (403, b'{"message":"secondary rate limit"}' + b" " * 8192, 2000, True),
    (403, b"Forbidden", 9, False),
    (429, b"Too Many Requests", 0, True),
])
def test_streamed_rate_detection(status, body, consumed, limited):
    import requests
    from urllib3.response import HTTPResponse

    data = io.BytesIO(body)
    response = requests.Response()
    response.status_code = status
    response.reason = {200: "OK", 403: "Forbidden", 429: "Too Many Requests"}[status]
    response.raw = HTTPResponse(body=data, preload_content=False)
    try:
        assert isinstance(updater._rate_limited(response), updater.RateLimited) == limited
        assert response.raw.tell() == consumed
    finally:
        response.close()
