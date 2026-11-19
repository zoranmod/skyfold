"""Review 40: independent one-way uploads and per-account folder ownership."""
import hashlib
import json
import os
import stat
from types import SimpleNamespace

import pytest

from skyfold.engine import OWNER_FILE, SyncError, Syncer
from skyfold.remote import UploadReceipt
from skyfold.state import State
from tests.test_drive import write
from tests.test_engine import FakeRemote, env as photos_env
from tests.test_notes import env as notes_env
from tests.test_review38_codex_probes import offline_worker
from tests.test_review39_codex_probes import switch_accounts


def select_root(w, root, upload):
    settings = w.settings.copy()
    settings.photos_dir = str(root)
    settings.photos_upload = upload
    w.apply_settings(settings, True)
    w.sync_now()


def run_or_hold(s):
    """Stopping for an ambiguous root is also an acceptable safety outcome."""
    try:
        s.run(force_full=True)
    except SyncError:
        pass


@pytest.mark.parametrize("selected", ["ancestor", "subfolder"])
def test_selecting_around_another_accounts_root_cannot_export_it(photos_env, monkeypatch, selected):
    w, second, first_copy = switch_accounts(photos_env, monkeypatch)
    root = photos_env[2]
    chosen = root.parent if selected == "ancestor" else first_copy.parent
    select_root(w, chosen, True)
    assert "private.jpg" not in getattr(second, "uploads", [])


def test_returning_owner_cannot_upload_guest_accounts_downloads(photos_env, monkeypatch):
    w, second, _ = switch_accounts(photos_env, monkeypatch)
    second.add("GUEST", "guest-private.jpg", b"second account only")
    w.sync_now()
    guest = photos_env[2] / next(r.rel_path for r in w.state.files() if r.asset_id == "GUEST")
    assert guest.exists() and w.syncer._foreign
    w.sign_out()
    owner = w.settings.copy()
    owner.apple_id = "first@example.invalid"
    w.apply_settings(owner, True)
    first = photos_env[1]
    w.set_api(first)
    enabled = w.settings.copy()
    enabled.photos_upload = True
    w.apply_settings(enabled, False)
    w.sync_now()
    assert "guest-private.jpg" not in getattr(first, "uploads", [])


def test_upgrade_preserves_dc4ca0d_foreign_folder(photos_env, monkeypatch):
    s, second, root, _ = photos_env
    s.run()  # make the .skyfold marker without an account marker, as in dc4ca0d
    s.settings.apple_id = "second@example.invalid"
    s.settings.photos_dir_confirmed = True
    s.state.set("account", hashlib.sha256(s.settings.apple_id.encode()).hexdigest()[:16])
    s.state.set("foreign_files", "1")  # old DB after a switch to B, whose uploads were turned off
    write(root, "private.jpg", b"from first account", age=3600)
    assert not (root / OWNER_FILE).exists()
    w = offline_worker(s.settings, s.state, monkeypatch)
    w.set_api(second)
    setting = w.settings.copy()
    setting.photos_upload = True
    w.apply_settings(setting, False)
    w.sync_now()
    assert not second.assets


@pytest.mark.parametrize("change", ["in_place_edit", pytest.param("new_file", marks=pytest.mark.xfail(
    strict=True, reason="by design: a file added while B is signed in can't be told apart from B's own"))])
def test_foreign_snapshot_covers_later_files_and_sizes(photos_env, monkeypatch, change):
    w, second, first_copy = switch_accounts(photos_env, monkeypatch)
    second.add("EXPORT", "edited.jpg", b"edited family export at a new size")
    w.sync_now()
    own = photos_env[2] / next(r.rel_path for r in w.state.files() if r.asset_id == "EXPORT")
    foreign = first_copy if change == "in_place_edit" else first_copy.with_name("archived-edit.jpg")
    foreign.write_bytes(own.read_bytes())
    own.unlink()  # remove B's copy, leaving the owner's matching export
    w.sync_now()
    if "EXPORT" in second.assets:
        second.remove("EXPORT")
    w.sync_now()
    assert foreign.is_file()


@pytest.mark.parametrize("side", ["cloud", "pc"])
def test_another_valid_library_at_same_drive_path_is_not_a_deletion(photos_env, tmp_path, side):
    s, first, root, _ = photos_env
    s.settings.apple_id = "a@example.invalid"
    first.add("A", "a.jpg", b"same original" if side == "pc" else b"a only")
    s.run()
    other = s.settings.copy()
    other.apple_id = "b@example.invalid"
    other.photos_dir = str(tmp_path / "different-disk")
    second = FakeRemote()
    second.add("B", "a.jpg" if side == "pc" else "b.jpg", b"same original" if side == "pc" else b"b only")
    other_sync = Syncer(State(tmp_path / "b.db"), second, other)
    other_sync.run()
    other_rel = other_sync.state.files()[0].rel_path
    # The next removable disk has the same mount path and its own genuine Skyfold markers.
    root.rename(tmp_path / "disconnected")
    other_sync.root.rename(root)
    if side == "pc":
        first.remove("A")
    run_or_hold(s)
    if side == "cloud":
        assert "A" in first.assets
    else:
        assert (root / other_rel).is_file()


@pytest.mark.xfail(strict=True, reason="by design: a new photos folder is a full two-way copy of the library, "
                   "including photos once sent from the old folder")
def test_settings_folder_change_preserves_one_way_asset(photos_env, tmp_path, monkeypatch):
    s, remote, root, _ = photos_env
    s.settings.apple_id = "a@example.invalid"
    s.settings.photos_upload = s.settings.photos_dir_confirmed = True
    w = offline_worker(s.settings, s.state, monkeypatch)
    w.set_api(remote)
    write(root, "sent.jpg", b"one way", age=3600)
    w.sync_now()
    (aid,) = remote.assets
    assert w.state.get("upload_sent:" + aid) and not w.state.files()
    other = tmp_path / "New photos folder"
    select_root(w, other, False)
    for p in other.rglob("*.jpg"):
        p.unlink()  # user removes an unwanted download-back
    w.sync_now()
    assert aid in remote.assets and not w.state.files()


def test_lost_reply_does_not_take_an_unrelated_same_content_asset(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    source = write(root, "sent.jpg", b"same exported pixels", age=3600)
    remote.add("OTHER", "from-another-import.jpg", source.read_bytes())

    def upload(path):
        remote.add("SENT", path.name, path.read_bytes())
        return UploadReceipt(None, False)  # no usable registration ID

    remote.upload = upload
    s.run()
    # Remove only an unwanted download-back; the one-way upload itself remains on disk.
    for row in s.state.files():
        if row.asset_id == "SENT" and root / row.rel_path != source:
            (root / row.rel_path).unlink()
    s.run()
    assert "SENT" in remote.assets and "SENT" not in s.state.asset_ids()


@pytest.mark.xfail(strict=True, reason="accepted: overwriting a sent file with a tracked photo's exact bytes makes "
                   "it that photo for move detection; ignoring contents would let a reused file id hide a real move")
def test_edited_sent_file_cannot_be_claimed_as_a_tracked_move(photos_env):
    s, remote, root, _ = photos_env
    remote.add("PHONE", "phone.jpg", b"full quality")
    s.settings.photos_upload = True
    s.run()
    sent = write(root, "export.jpg", b"draft", age=3600)
    s.run()
    s.settings.photos_upload = False
    original = root / next(r.rel_path for r in s.state.files() if r.asset_id == "PHONE")
    sent.write_bytes(original.read_bytes())  # replace draft in-place with the original-quality export
    s.run()
    original.unlink()
    s.run()
    if "PHONE" in remote.assets:
        remote.remove("PHONE")
    s.run()
    assert sent.is_file()


def test_dc4ca0d_unchanged_kept_placeholder_is_not_uploaded(photos_env):
    s, remote, root, _ = photos_env
    s.run()
    kept = write(root, "local.jpg", b"lost from cloud but kept locally", age=3600)
    st = kept.stat()
    s.state.set(f"upload_file:{st.st_dev}:{st.st_ino}", json.dumps(["kept", "kept"]))
    s.settings.photos_upload = True
    s.run()
    assert not remote.assets


def test_renamed_legacy_keep_is_protected_before_upload_scan(photos_env):
    s, remote, root, _ = photos_env
    remote.add("OTHER", "other.jpg", b"family portrait")
    s.run()
    tracked = root / s.state.files()[0].rel_path
    saved = write(root, "kept.jpg", tracked.read_bytes(), age=3600)
    digest = hashlib.sha256(saved.read_bytes()).hexdigest()
    s.state.set("upload_done:kept.jpg", digest)
    s.state.set("upload_known:" + digest, "1")
    renamed = saved.rename(root / "archive.jpg")
    tracked.unlink()
    s.run()
    if "OTHER" in remote.assets:
        remote.remove("OTHER")
    s.run()
    assert renamed.is_file()


def test_tracking_at_a_path_cannot_recycle_a_known_foreign_file(photos_env, monkeypatch):
    w, second, first_copy = switch_accounts(photos_env, monkeypatch)
    own = photos_env[2] / next(r.rel_path for r in w.state.files() if r.asset_id == "B")
    assert own != first_copy and own.read_bytes() == first_copy.read_bytes()
    own.unlink()
    first_copy.rename(own)  # consolidate the two copies under the preferred filename
    assert w.syncer._foreign_file(own.stat())
    second.remove("B")
    w.sync_now()
    assert own.is_file()


def test_tracking_at_a_path_cannot_recycle_a_known_upload(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    sent = write(root, "sent.jpg", b"camera original", age=3600)
    s.run()
    remote.add("OTHER", "other.jpg", sent.read_bytes())
    s.run()
    row = next(r for r in s.state.files() if r.asset_id == "OTHER")
    path = root / row.rel_path
    path.unlink()
    sent.rename(path)
    assert s._is_marked_file(path, row.rel_path, path.stat())
    remote.remove("OTHER")
    s.run()
    assert path.is_file()


def test_zero_id_mark_protects_unchanged_bytes_at_the_same_path(photos_env):
    s, _, root, _ = photos_env
    s.run()
    p = write(root, "original.jpg", b"kept photo", age=3600)
    st = p.stat()
    # Explicit input from a provider that has no file index. No OS calls are patched.
    info = SimpleNamespace(st_dev=st.st_dev, st_ino=0, st_size=st.st_size, st_mtime_ns=st.st_mtime_ns)
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    s._mark(info, p.name, digest, "kept")
    assert s._is_marked_file(p, p.name, info)
    p.write_bytes(b"new contents")
    assert not s._is_marked_file(p, p.name, info)


def test_renamed_keep_on_an_actual_zero_id_volume_is_never_recycled(photos_env):
    s, remote, root, _ = photos_env
    remote.add("A", "phone.jpg", b"original")
    s.settings.delete_cloud_to_pc = False
    s.run()
    kept = root / s.state.files()[0].rel_path
    if kept.stat().st_ino:
        pytest.skip("requires a temporary directory on a volume that reports zero file IDs")
    remote.remove("A")
    s.run()
    saved = kept.rename(root / "archive.jpg")
    s.settings.delete_cloud_to_pc = True
    remote.add("B", "new.jpg", saved.read_bytes())
    s.run()
    (root / s.state.files()[0].rel_path).unlink()
    s.run()
    if "B" in remote.assets:
        remote.remove("B")
    s.run()
    assert saved.is_file()


def test_live_photo_lost_receipt_does_not_create_a_tracked_movie(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    still = write(root, "still.jpg", b"still", age=3600)
    remote.add("LIVE", still.name, still.read_bytes(), live=b"motion")
    remote.upload = lambda path: UploadReceipt(None, True)
    s.run()
    assert s.state.get("upload_sent:LIVE")
    assert not s.state.files() and not list(root.rglob("*.MOV"))


def test_replaced_drive_root_starts_own_first_download_count(photos_env, tmp_path, monkeypatch):
    from skyfold import worker
    from skyfold.drive_engine import DriveState
    from skyfold.i18n import t
    from tests.test_drive_first_notice import _Drive

    s, _, _, _ = photos_env
    w = offline_worker(s.settings, s.state, monkeypatch)
    w.settings.notifications = False
    w.drive_state = DriveState(tmp_path / "drive.db")
    monkeypatch.setattr(worker, "STATE_DB", tmp_path / "state.db")
    w.drive = _Drive()
    assert w._run_drive([], {"Docs/a.txt"}) and w.state.get("drive_first_count") == "1"
    w.drive = None
    setting = w.settings.copy()
    setting.drive_dir = str(tmp_path / "different folder")
    w.apply_settings(setting, False, True)
    w.drive = _Drive()
    seen = []
    w.notify.connect(lambda title, msg: seen.append(msg))
    assert w._run_drive([])
    assert seen == [t("The first sync is done: {n} files downloaded. From now on only changes are synced.", n=7)]


@pytest.mark.skipif(os.name != "nt", reason="Windows file sharing and NTFS hard links")
def test_open_archive_remains_read_only_across_cloud_update(notes_env, tmp_path):
    import _winapi

    s, remote, root, _ = notes_env
    remote.put("N", "Budget", body="original")
    s.run()
    page = root / s.state.all()["N"][1]
    archive = tmp_path / "budget-archive.html"
    os.link(page, archive)
    archive.chmod(stat.S_IREAD)
    try:
        handle = _winapi.CreateFile(str(archive), 0x80000000, 3, 0, 3, 0, 0)
        try:
            remote.put("N", "Budget", body="replacement")
            s.run()
            assert archive.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
        finally:
            _winapi.CloseHandle(handle)
        s.run()
        assert b"replacement" in page.read_bytes() and b"original" in archive.read_bytes()
        assert archive.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    finally:
        archive.chmod(stat.S_IWRITE)


def test_legacy_digest_does_not_block_a_new_independent_import(photos_env):
    s, remote, root, _ = photos_env
    s.run()
    kept = write(root, "old.jpg", b"family", age=3600)
    digest = hashlib.sha256(kept.read_bytes()).hexdigest()
    s.state.set("upload_done:old.jpg", digest)
    s.state.set("upload_known:" + digest, "1")
    s.settings.photos_upload = True
    s.run()
    assert not remote.assets
    fresh = write(root, "import.jpg", kept.read_bytes(), age=3600)
    assert not os.path.samefile(kept, fresh)
    s.run()
    assert getattr(remote, "uploads", []) == [fresh.name]


def test_portable_account_in_new_empty_folder_can_enable_upload(photos_env, tmp_path, monkeypatch):
    s, first, _, _ = photos_env
    s.settings.apple_id = "first@example.invalid"
    s.settings.photos_dir_confirmed = True
    w = offline_worker(s.settings, s.state, monkeypatch)
    w.set_api(first)
    settings = w.settings.copy()
    settings.apple_id = "second@example.invalid"
    root = tmp_path / "Brand new Photos"
    settings.photos_dir = str(root)
    assert not root.exists()
    reopened = offline_worker(settings, w.state, monkeypatch)
    remote = FakeRemote()
    reopened.set_api(remote)
    enabled = reopened.settings.copy()
    enabled.photos_upload = True
    reopened.apply_settings(enabled, False)
    write(root, "second-only.jpg", b"second only", age=3600)
    reopened.sync_now()
    assert getattr(remote, "uploads", []) == ["second-only.jpg"]


def test_returning_to_upload_folder_does_not_recreate_phone_deletion(photos_env, tmp_path, monkeypatch):
    s, remote, root, _ = photos_env
    s.settings.apple_id = "first@example.invalid"
    s.settings.photos_upload = s.settings.photos_dir_confirmed = True
    w = offline_worker(s.settings, s.state, monkeypatch)
    w.set_api(remote)
    source = write(root, "family.jpg", b"unchanged picture", age=3600)
    w.sync_now()
    (aid,) = remote.assets
    remote.remove(aid)
    w.sync_now()
    assert source.is_file() and not remote.assets
    select_root(w, tmp_path / "Other photos", True)
    # Return in a later app session, after the short-lived in-memory scan cache is gone.
    restarted = offline_worker(w.settings.copy(), State(tmp_path / "s.db"), monkeypatch)
    restarted.set_api(remote)
    select_root(restarted, root, True)
    assert not remote.assets and remote.uploads == ["family.jpg"]


def test_upgrade_follows_a_pending_upload_before_resending_it(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    source = write(root, "export.jpg", b"only one registration", age=3600)
    st = source.stat()
    receipt = remote.upload(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    # v1.0.15 and dc4ca0d both persisted the receipt and file ID while waiting for listing.
    s.state.set("upload_pending:" + digest, json.dumps(dict(
        rel=source.name, digest=digest, asset=receipt.asset_id, duplicate=False,
        identity=[st.st_dev, st.st_ino])))
    renamed = source.rename(root / "trip.jpg")
    s.run(force_full=True)
    assert renamed.exists() and remote.uploads == ["export.jpg"]
