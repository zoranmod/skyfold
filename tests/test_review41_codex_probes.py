"""Review 41: ordinary operations on temporary photo folders and offline fake libraries."""
import hashlib
import json
from datetime import timedelta

import pytest

from skyfold.engine import MARKER, OWNER_FILE, Syncer
from skyfold.remote import UploadReceipt
from skyfold.state import State
from tests.test_drive import write
from tests.test_engine import FakeRemote, env as photos_env
from tests.test_review38_codex_probes import offline_worker
from tests.test_review39_codex_probes import assert_refused, switch_accounts
from tests.test_review40_codex_probes import run_or_hold, select_root


def return_to_first(worker, first):
    worker.sign_out()
    settings = worker.settings.copy()
    settings.apple_id = "first@example.invalid"
    worker.apply_settings(settings, True)
    worker.set_api(first)
    settings = worker.settings.copy()
    settings.photos_upload = True
    worker.apply_settings(settings, False)
    worker.sync_now()


def test_guest_renaming_own_download_does_not_delete_it_from_icloud(photos_env, monkeypatch):
    w, second, first_copy = switch_accounts(photos_env, monkeypatch)
    assert_refused(w, second, first_copy)  # B has no download in A's folder to rename
    assert "B" in second.assets


@pytest.mark.parametrize("history", ["edited_by_guest", "downloaded_with_278feb8"])
def test_returning_owner_cannot_send_guests_private_picture(photos_env, monkeypatch, history):
    w, second, first_copy = switch_accounts(photos_env, monkeypatch)
    second.add("PRIVATE-B", "b-private.jpg", b"B's private photograph")
    w.sync_now()
    assert_refused(w, second, first_copy)
    first = photos_env[1]
    return_to_first(w, first)
    assert not any("b-private" in n for n in getattr(first, "uploads", []))


def test_parent_library_cannot_recycle_nested_library_photo(photos_env, tmp_path):
    parent, first, root, _ = photos_env
    parent.settings.apple_id = "a@example.invalid"
    parent.settings.folder_structure = "year"
    settings = parent.settings.copy()
    settings.apple_id = "b@example.invalid"
    settings.photos_dir = str(root / "2025")
    settings.folder_structure = "flat"
    second = FakeRemote()
    second.add("B", "family.jpg", b"same camera original")
    child = Syncer(State(tmp_path / "child.db"), second, settings)
    child.run()
    b_file = child.root / child.state.files()[0].rel_path
    assert b_file.parent == root / "2025"
    first.add("A", "family.jpg", b_file.read_bytes())
    run_or_hold(parent)
    first.remove("A")
    run_or_hold(parent)
    assert b_file.is_file()


def test_parent_download_is_not_exported_by_nested_library_owner(photos_env, tmp_path):
    parent, first, root, _ = photos_env
    parent.settings.apple_id = "a@example.invalid"
    parent.settings.folder_structure = "year"
    settings = parent.settings.copy()
    settings.apple_id = "b@example.invalid"
    settings.photos_dir = str(root / "2025")
    settings.photos_upload = True
    second = FakeRemote()
    child = Syncer(State(tmp_path / "child.db"), second, settings)
    run_or_hold(child)  # (1.0.16: a folder inside another account's photos folder isn't synced)
    first.add("PRIVATE-A", "private.jpg", b"only in A")
    run_or_hold(parent)
    run_or_hold(child)
    assert "private.jpg" not in getattr(second, "uploads", [])


def test_other_same_account_library_at_old_path_cannot_delete_cloud_photo(photos_env, tmp_path, monkeypatch):
    s, remote, root, _ = photos_env
    s.settings.apple_id = "a@example.invalid"
    s.settings.photos_dir_confirmed = True
    w = offline_worker(s.settings, s.state, monkeypatch)
    w.set_api(remote)
    remote.add("OLD", "old.jpg", b"early")
    w.sync_now()
    current = tmp_path / "Current"
    select_root(w, current, False)
    remote.add("NEW", "new.jpg", b"recent")
    w.sync_now()
    # Restore the earlier Photos directory at the configured name while the app is closed.
    current.rename(tmp_path / "Saved current")
    root.rename(current)
    reopened = Syncer(State(tmp_path / "s.db"), remote, w.settings)
    run_or_hold(reopened)
    assert "NEW" in remote.assets and "NEW" not in remote.deleted


def test_edit_and_rename_of_download_does_not_delete_cloud_original(photos_env):
    s, remote, root, _ = photos_env
    remote.add("TRIP", "camera.jpg", b"original photograph")
    s.run()
    p = root / s.state.files()[0].rel_path
    p.write_bytes(b"edited photograph with a new caption")
    saved = p.rename(p.with_name("holiday.jpg"))
    s.run()
    assert saved.is_file() and "TRIP" in remote.assets


def test_legacy_pending_rename_with_reused_old_name_is_not_sent_again(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    original = write(root, "export.jpg", b"already accepted", age=3600)
    st = original.stat()
    receipt = remote.upload(original)
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    s.state.set("upload_pending:" + digest, json.dumps(dict(
        rel=original.name, digest=digest, asset=receipt.asset_id, duplicate=False,
        identity=[st.st_dev, st.st_ino])))
    original.rename(root / "first-export.jpg")
    write(root, "export.jpg", b"next export", age=3600)
    s.run(force_full=True)
    assert "first-export.jpg" not in remote.uploads


@pytest.mark.parametrize("operation", ["move_kept_with_same_name", "rename_actual_download"])
def test_legacy_digest_does_not_select_a_destructive_move(photos_env, operation):
    s, remote, root, _ = photos_env
    remote.add("CLOUD", "photo.jpg", b"family")
    s.run()
    tracked = root / s.state.files()[0].rel_path
    saved = write(root, "kept/photo.jpg", tracked.read_bytes(), age=3600)
    digest = hashlib.sha256(saved.read_bytes()).hexdigest()
    s.state.set("upload_done:kept/photo.jpg", digest)
    if operation == "move_kept_with_same_name":
        archive = root / "Archive"
        archive.mkdir()
        kept = saved.rename(archive / "photo.jpg")
        tracked.unlink()
        s.run()
        if "CLOUD" in remote.assets:
            remote.remove("CLOUD")
        s.run()
        assert kept.is_file()
    else:
        saved.unlink()
        tracked.rename(root / "holiday.jpg")
        s.run()
        assert "CLOUD" in remote.assets


def test_two_legacy_kept_files_do_not_share_one_migration_allowance(photos_env):
    s, remote, root, _ = photos_env
    s.run()
    for name in ("edited", "archive"):
        p = write(root, name + ".jpg", b"photo kept locally", age=3600)
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        s.state.set("upload_done:" + p.name, digest)
        p.rename(root / (name + "-renamed.jpg"))
    s.settings.photos_upload = True
    s.run()
    assert not remote.assets


@pytest.mark.parametrize("case", ["same_camera_filename", "legacy_different_filename"])
def test_ambiguous_receipt_cannot_grant_deletion_rights_on_sent_asset(photos_env, case):
    s, remote, root, _ = photos_env
    s.run()
    s.settings.photos_upload = True
    p = write(root, "IMG_0001.jpg", b"same exported original", age=3600)
    name = p.name if case == "same_camera_filename" else "archive.jpg"
    send = remote.upload
    if case == "legacy_different_filename":
        sent_id = send(p).asset_id
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        st = p.stat()
        s.state.set("upload_pending:" + digest, json.dumps(dict(
            rel=p.name, digest=digest, asset=None, duplicate=False, identity=[st.st_dev, st.st_ino])))
    else:
        def no_receipt(path):
            send(path)
            return UploadReceipt(None, False)
        remote.upload = no_receipt
        s.upload_new()
        (sent_id,) = remote.assets
    # A separate phone import/duplicate is visible at the next full read. Newest sorts first.
    remote.add("OTHER", name, p.read_bytes(), date=remote.assets[sent_id].date + timedelta(days=1))
    s.run(force_full=True)
    for row in s.state.files():
        if row.asset_id == sent_id and root / row.rel_path != p:
            (root / row.rel_path).unlink()  # only remove the unwanted second download
    s.run()
    assert p.exists() and sent_id in remote.assets


def test_differently_named_duplicate_with_lost_reply_is_not_downloaded_and_deleted(photos_env):
    s, remote, root, _ = photos_env
    s.run()
    s.settings.photos_upload = True
    remote.add("PHONE", "IMG_0456.jpg", b"camera original")
    p = write(root, "holiday.jpg", b"camera original", age=3600)
    send = remote.upload

    def lost(path):
        assert send(path).is_duplicate
        return UploadReceipt(None, False)  # the PC did not receive the duplicate result

    remote.upload = lost
    s.run(force_full=True)
    for row in s.state.files():
        if row.asset_id == "PHONE":
            (root / row.rel_path).unlink()  # remove the second download, keeping holiday.jpg
    s.run()
    assert p.exists() and "PHONE" in remote.assets


def test_matched_1015_upload_survives_rename_after_upgrade(photos_env):
    s, remote, root, _ = photos_env
    s.settings.folder_structure = "flat"
    remote.add("MATCHED", "upload.jpg", b"already in the full listing")
    s.run()
    row = s.state.files()[0]
    # v1.0.15's successful match persisted both the FileRow and upload_done:<rel>.
    # The later full listing cleared upload_unlisted. There is no separate kept copy.
    s.state.set("upload_done:" + row.rel_path.lower(), row.hash)
    s.settings.photos_upload = True
    s.run(force_full=True)
    p = (root / row.rel_path).rename(root / "renamed.jpg")
    s.run()
    assert p.is_file() and "MATCHED" in remote.assets and not remote.deleted


def test_selecting_parent_again_does_not_treat_rename_inside_month_as_deletion(photos_env, monkeypatch):
    s, remote, root, _ = photos_env
    s.settings.apple_id = "a@example.invalid"
    s.settings.photos_dir_confirmed = True
    w = offline_worker(s.settings, s.state, monkeypatch)
    remote.add("A", "camera.jpg", b"my photograph")
    w.set_api(remote)
    p = root / w.state.files()[0].rel_path
    select_root(w, p.parent, False)
    select_root(w, root, False)
    tracked = root / next(r.rel_path for r in w.state.files() if r.asset_id == "A")
    assert tracked == p
    renamed = tracked.rename(tracked.with_name("holiday.jpg"))
    w.sync_now()
    assert renamed.is_file() and "A" in remote.assets


def test_selecting_an_existing_pre_owner_library_does_not_upload_it_to_b(photos_env, tmp_path, monkeypatch):
    old, first, old_root, _ = photos_env
    # Before owner files, an actual Skyfold library had the regular marker and downloaded photos.
    first.add("PRIVATE-A", "a-private.jpg", b"A only")
    old.run()
    assert (old_root / MARKER).is_file() and not (old_root / OWNER_FILE).exists()
    settings = old.settings.copy()
    settings.apple_id = "current-b@example.invalid"
    settings.photos_dir_confirmed = settings.photos_upload = True
    settings.photos_dir = str(tmp_path / "Bs own library")
    second = FakeRemote()
    w = offline_worker(settings, State(tmp_path / "b.db"), monkeypatch)
    w.set_api(second)
    select_root(w, old_root, True)
    assert "a-private.jpg" not in getattr(second, "uploads", [])
