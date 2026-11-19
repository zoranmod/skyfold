"""Offline safety contracts for dc4ca0d, using synthetic libraries and temporary files."""
import hashlib
import json
import os
import shutil
import stat
from types import SimpleNamespace

import pytest

from skyfold.remote import ICloudRemote, LookupIncomplete, UploadReceipt
from tests.test_drive import env as drive_env, write
from tests.test_engine import FakeRemote, _minutes_pass, env as photos_env
from tests.test_notes import env as notes_env
from tests.test_review38_codex_probes import offline_worker


def switch_accounts(photos_env, monkeypatch):
    s, first, root, _ = photos_env
    s.settings.apple_id = "first@example.invalid"
    s.settings.photos_dir_confirmed = s.settings.photos_upload = True
    first.add("A", "family.jpg", b"shared picture")
    first.add("PRIVATE", "private.jpg", b"first account only")
    w = offline_worker(s.settings, s.state, monkeypatch)
    w.set_api(first)
    first_copy = root / next(r.rel_path for r in s.state.files() if r.asset_id == "A")
    w.sign_out()
    other = w.settings.copy()
    other.apple_id = "second@example.invalid"
    w.apply_settings(other, True)
    second = FakeRemote()
    second.add("B", "family.jpg", b"shared picture")
    w.set_api(second)
    assert w.syncer._foreign and not w.settings.photos_upload
    return w, second, first_copy


def test_foreign_photo_is_not_claimed_by_move_detection(photos_env, monkeypatch):
    w, second, first_copy = switch_accounts(photos_env, monkeypatch)
    second_copy = photos_env[2] / w.state.files()[0].rel_path
    assert second_copy != first_copy and first_copy.read_bytes() == second_copy.read_bytes()
    second_copy.unlink()  # clean up B's downloaded duplicate
    w.sync_now()
    # A safe implementation may delete B here or hold the ambiguous move for review.
    if "B" in second.assets:
        second.remove("B")
    w.sync_now()
    assert first_copy.exists()


def test_reselecting_protected_folder_does_not_upload_previous_accounts_photos(
        photos_env, tmp_path, monkeypatch):
    w, second, _ = switch_accounts(photos_env, monkeypatch)
    clean = w.settings.copy()
    clean.photos_dir = str(tmp_path / "Second library")
    clean.photos_upload = True
    w.apply_settings(clean, True)
    w.sync_now()
    old = w.settings.copy()
    old.photos_dir = str(photos_env[2])  # select the previous library again in Settings
    w.apply_settings(old, True)
    w.sync_now()
    assert "private.jpg" not in getattr(second, "uploads", [])


def test_edited_duplicate_that_became_owned_can_be_renamed(photos_env):
    s, remote, root, _ = photos_env
    remote.add("PHONE", "phone.jpg", b"original")
    s.settings.photos_upload = True
    s.run()
    copy = write(root, "copy.jpg", b"original", age=3600)
    s.run(force_full=True)
    assert s._marked(copy.stat(), copy.name)[1] == "copy"
    write(root, copy.name, b"edited picture", age=3600)  # same NTFS file, new contents
    s.run()
    _minutes_pass(s)
    s.run(force_full=True)
    assert len(remote.assets) == 2 and copy.name not in {r.rel_path for r in s.state.files()}  # sent, one-way
    copy.rename(root / "finished.jpg")
    s.run()
    assert len(remote.assets) == 2 and not remote.deleted


def test_975_kept_file_renamed_while_app_closed_survives_migration(photos_env):
    s, remote, root, _ = photos_env
    s.run()
    p = write(root, "local.jpg", b"only on PC", age=3600)
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    # Both records persisted by 975c07c when the user kept this copy.
    s.state.set("upload_done:local.jpg", digest)
    s.state.set("upload_known:" + digest, "1")
    p.rename(root / "archive.jpg")
    s.settings.photos_upload = True
    s.run()
    assert not remote.assets


def test_old_read_only_association_acquires_identity_on_upgrade(photos_env):
    s, remote, root, _ = photos_env
    s.run()
    remote.add("A", "phone.jpg", b"same")
    copy = write(root, "backup.jpg", b"same", age=3600)
    digest = hashlib.sha256(copy.read_bytes()).hexdigest()
    s.state.set("upload_seen:A", json.dumps([copy.name, digest]))
    s.state.set("upload_done:backup.jpg", digest)
    s.settings.photos_upload = True
    s.run(force_full=True)
    assert list(root.rglob("*.jpg")) == [copy]
    renamed = copy.rename(root / "archive.jpg")
    s.run(force_full=True)
    assert list(root.rglob("*.jpg")) == [renamed]


def test_pending_upload_cannot_claim_distinct_identical_replacement(photos_env):
    s, remote, root, _ = photos_env
    s.run()
    s.settings.photos_upload = True
    p = write(root, "input.jpg", b"camera", age=3600)
    remote.upload = lambda path: UploadReceipt("NEW", False)
    s.run()
    archived = p.rename(root.parent / "archive.jpg")
    shutil.copy2(archived, p)
    assert not os.path.samefile(archived, p)
    remote.add("NEW", p.name, p.read_bytes())
    s.run()
    p.unlink()
    s.run()
    assert "NEW" in remote.assets


@pytest.mark.parametrize("reply", ["TRY_AGAIN_LATER", "NOT_FOUND"])
def test_unlisted_upload_survives_inconclusive_asset_lookup(photos_env, reply):
    from pyicloud.common.cloudkit.models import CKLookupResponse

    s, remote, root, trash = photos_env
    s.settings.photos_upload = True
    s.run()
    p = write(root, "new.jpg", b"new upload", age=3600)
    s.run()
    (aid,) = remote.assets

    def lookup(*, record_names, zone_id):
        assert record_names == [aid]
        return CKLookupResponse.model_validate({"records": [{"recordName": aid, "serverErrorCode": reply}]})

    photos = SimpleNamespace(libraries={"root": SimpleNamespace(zone_id={"zoneName": "PrimarySync"})},
                             private_client=SimpleNamespace(lookup=lookup))
    remote.lookup = ICloudRemote(SimpleNamespace(photos=photos)).lookup
    remote.list_assets = lambda on_page: iter(())  # the accepted asset hasn't reached the full listing yet
    s.run(force_full=True)
    assert aid in remote.assets and p.exists() and not trash


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only attribute and NTFS hard links")
def test_cloud_note_edit_preserves_read_only_hardlinked_archive(notes_env, tmp_path):
    s, remote, root, _ = notes_env
    remote.put("N", "Budget", body="original")
    s.run()
    page = root / s.state.all()["N"][1]
    archive = tmp_path / "archived-budget.html"
    os.link(page, archive)
    archive.chmod(stat.S_IREAD)
    try:
        remote.put("N", "Budget", body="new budget")
        assert s.run().errors == 0 and b"new budget" in page.read_bytes()
        assert b"original" in archive.read_bytes()
        assert archive.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    finally:
        archive.chmod(stat.S_IWRITE)


def test_migration_checks_bytes_before_marking_an_old_path(photos_env):
    s, remote, root, _ = photos_env
    remote.add("TRIP", "travel.jpg", b"my trip")
    s.run()
    old_copy = write(root, "backup.jpg", b"other picture", age=3600)
    digest = hashlib.sha256(old_copy.read_bytes()).hexdigest()
    s.state.set("upload_done:backup.jpg", digest)
    s.state.set("upload_known:" + digest, "1")
    old_copy.unlink()
    tracked = root / s.state.files()[0].rel_path
    tracked.rename(old_copy)
    s.run()
    assert "TRIP" in remote.assets and s.state.files()[0].rel_path == "backup.jpg"


def test_renaming_still_settling_photo_preserves_wait(photos_env):
    s, remote, root, _ = photos_env
    s.run()
    s.settings.photos_upload = True
    write(root, "editing.jpg", b"part", age=0)
    s.run()
    # A copy program restores the source mtime, then gives the file its final name.
    p = write(root, "editing.jpg", b"next chunk", age=3600)
    s.run()
    assert not remote.assets
    p.rename(root / "final.jpg")
    s.run()
    assert not remote.assets


def test_ready_notice_counts_both_parts_of_initial_download(drive_env, tmp_path):
    from tests.test_drive_first_notice import _worker
    from skyfold.i18n import t

    drive, remote, root, _ = drive_env
    remote.put("one.txt", b"one")
    remote.put("Folder/two.txt", b"two")
    walk = remote.walk

    def initial_listing(*args, **kwargs):
        listing = walk(*args, **kwargs)
        remote.incomplete = {"Folder"}
        del listing["Folder/two.txt"]
        return listing

    w, titles = _worker(tmp_path)
    w.drive = drive
    messages = []
    w.notify.connect(lambda title, text: messages.append(text))
    remote.walk = initial_listing
    assert w._run_drive([]) and not titles
    assert (root / "one.txt").is_file() and not (root / "Folder/two.txt").exists()
    remote.walk = walk
    assert w._run_drive([]) and (root / "Folder/two.txt").is_file()
    assert titles == [t("iCloud Drive is ready")]
    assert messages == [t("The first sync is done: {n} files downloaded. From now on only changes are synced.", n=2)]


def test_manual_drive_pass_while_paused_stops_after_one(photos_env, drive_env, monkeypatch):
    s, photos, _, _ = photos_env
    drive, remote, root, _ = drive_env
    s.settings.photos_dir_confirmed = s.settings.paused = s.settings.drive_enabled = True
    w = offline_worker(s.settings, s.state, monkeypatch)
    w.syncer, w.api, w.drive = s, photos, drive
    remote.put("one.txt", b"one")
    w.sync_now(False)
    assert remote.full_walks == 0
    w.sync_now(True)
    if w._drive_thread is not None:
        w._drive_thread.join(10)
    assert not w.drive_running() and not w._drive_once and w.settings.paused
    assert remote.full_walks == 1 and (root / "one.txt").is_file()
    remote.put("two.txt", b"two")
    w._drive_changed()
    w.sync_now(False)
    assert remote.full_walks == 1 and not (root / "two.txt").exists()


def test_pc_deletion_uses_full_list_after_incomplete_lookup(photos_env):
    s, remote, root, _ = photos_env
    for aid in ("A", "B"):
        remote.add(aid, aid + ".jpg", aid.encode())
    s.settings.delete_threshold = 1
    s.run()
    for row in s.state.files():
        (root / row.rel_path).unlink()

    def missing_master(ids):
        assert set(ids) == {"A", "B"}
        raise LookupIncomplete("original metadata pending")

    remote.lookup = missing_master
    before = remote.full_reads
    result = s.run()
    assert remote.full_reads == before + 1 and set(remote.assets) == {"A", "B"}
    assert set(result.pending.to_cloud) == {"A", "B"}


def test_keep_on_volume_without_ids_survives_next_sync(photos_env):
    s, remote, root, _ = photos_env
    remote.add("A", "phone.jpg", b"original")
    s.run()
    p = root / s.state.files()[0].rel_path
    if p.stat().st_ino:
        pytest.skip("Requires a test volume that reports file index zero")
    s.settings.photos_upload = True
    s.settings.delete_threshold = 1
    remote.add("B", "other.jpg", b"other")
    s.run()
    for aid in ("A", "B"):
        remote.remove(aid)
    result = s.run()
    s.reject_to_pc(result.pending.to_pc)
    s.run()
    assert not remote.assets and p.exists()


def test_legacy_size_time_mark_does_not_suppress_replacement(photos_env):
    s, remote, root, _ = photos_env
    s.run()
    p = write(root, "scan.jpg", b"old", age=3600)
    old = p.stat()
    # v1.0.15 accepts this older record only when both size and time still match.
    s.state.set("upload_done:scan.jpg", f"{old.st_size}:{int(old.st_mtime)}")
    p.unlink()
    write(root, "scan.jpg", b"new independent picture", age=3600)
    s.settings.photos_upload = True
    s.run()
    assert getattr(remote, "uploads", []) == ["scan.jpg"]


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing modes")
def test_migration_retries_a_kept_file_open_exclusively(photos_env):
    import _winapi

    s, remote, root, _ = photos_env
    s.run()
    p = write(root, "saved.jpg", b"kept on this PC", age=3600)
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    s.state.set("upload_done:saved.jpg", digest)
    s.state.set("upload_known:" + digest, "1")
    handle = _winapi.CreateFile(str(p), 0x80000000, 0, 0, 3, 0, 0)
    try:
        s.run()
    finally:
        _winapi.CloseHandle(handle)
    s.settings.photos_upload = True
    s.run()
    assert not remote.assets


def test_new_cloud_asset_cannot_take_over_a_kept_pc_copy(photos_env):
    s, remote, root, trash = photos_env
    remote.add("ORIGINAL", "trip.jpg", b"picture")
    remote.add("OTHER", "other.jpg", b"other")
    s.settings.delete_threshold = 1
    s.run()
    saved = root / next(r.rel_path for r in s.state.files() if r.asset_id == "ORIGINAL")
    for aid in ("ORIGINAL", "OTHER"):
        remote.remove(aid)
    question = s.run().pending
    s.reject_to_pc(question.to_pc)
    s.run()
    assert s._marked(saved.stat(), saved.relative_to(root).as_posix())[1] == "kept"
    # A fresh import of the same camera original retains its original filename and capture date.
    remote.add("REIMPORT", "trip.jpg", b"picture")
    s.run()
    remote.remove("REIMPORT")
    s.run()
    assert saved.is_file() and saved.name not in trash


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing modes")
def test_keep_while_photo_is_open_does_not_suppress_later_edit(photos_env):
    import _winapi
    import msvcrt

    s, remote, root, _ = photos_env
    remote.add("ORIGINAL", "trip.jpg", b"camera")
    s.settings.photos_upload = True
    s.settings.delete_cloud_to_pc = False
    s.run()
    p = root / s.state.files()[0].rel_path
    handle = _winapi.CreateFile(str(p), 0x40000000, 0, 0, 3, 0, 0)
    with os.fdopen(msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY), "wb") as editor:
        remote.remove("ORIGINAL")
        s.run()
        editor.write(b"edited camera photograph")
    os.utime(p, (1_600_000_000, 1_600_000_000))
    s.run()
    _minutes_pass(s)
    s.run()
    assert getattr(remote, "uploads", []) == ["trip.jpg"]


def test_reassigned_volume_id_cannot_hide_a_tracked_rename(photos_env):
    s, remote, root, _ = photos_env
    remote.add("B", "current.jpg", b"current picture")
    s.run()
    current = root / s.state.files()[0].rel_path
    st = current.stat()
    # Durable provider state after a previous file was removed and its ID was reissued.
    # Seed the old database row to avoid relying on the allocator to reuse an ID on demand.
    old_digest = hashlib.sha256(b"different, formerly kept picture").hexdigest()
    s.state.set(f"upload_file:{st.st_dev}:{st.st_ino}", json.dumps([old_digest, "kept"]))
    current.rename(root / "my-picture.jpg")
    s.run()
    assert "B" in remote.assets and s.state.files()[0].rel_path == "my-picture.jpg"
