"""Review 42. Ordinary Explorer and Settings sequences against offline Photos libraries."""
import hashlib
import json
import os
import shutil

import pytest

from skyfold import engine
from skyfold.state import State
from tests.test_drive import write
from tests.test_engine import FakeRemote, env as repo_photos
from tests.test_review38_codex_probes import offline_worker
from tests.test_review40_codex_probes import run_or_hold


@pytest.fixture
def photos_env(repo_photos):
    repo_photos[0].settings.apple_id = "a@example.invalid"
    return repo_photos


def second_library(parent, folder, db):
    settings = parent.settings.copy()
    settings.apple_id = "b@example.invalid"
    settings.photos_dir = str(folder)
    settings.photos_upload = settings.photos_dir_confirmed = True
    remote = FakeRemote()
    return engine.Syncer(State(db), remote, settings), remote


def test_save_replace_then_rename_keeps_cloud_original(photos_env):
    s, remote, root, _ = photos_env
    remote.add("A", "original.jpg", b"original")
    s.run()
    p = root / s.state.files()[0].rel_path
    replacement = write(p.parent, "editor.tmp", b"new picture with caption")
    assert replacement.stat().st_ino != p.stat().st_ino
    os.replace(replacement, p)  # a normal editor save via a temporary file
    s.run()  # a complete pass sees the new file at the old pathname
    edited = p.rename(p.with_name("holiday.jpg"))
    s.run()
    assert edited.exists() and "A" in remote.assets


def test_move_and_reuse_original_name_does_not_send_download_back(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    remote.add("A", "camera.jpg", b"first photo")
    s.run()
    p = root / s.state.files()[0].rel_path
    moved = p.rename(p.with_name("earlier.jpg"))
    write(p.parent, p.name, b"next camera export", age=3600)
    s.run()
    assert moved.exists() and moved.name not in getattr(remote, "uploads", [])


def test_swapped_names_then_remove_one_deletes_only_the_correct_asset(photos_env):
    s, remote, root, _ = photos_env
    remote.add("A", "first.jpg", b"one")
    remote.add("B", "second.jpg", b"two different")
    s.run()
    paths = {r.asset_id: root / r.rel_path for r in s.state.files()}
    a, b = paths["A"], paths["B"]
    tmp = a.rename(a.with_name("temporary.jpg"))
    b.rename(a)
    tmp.rename(b)
    s.run()  # let the scan finish with both names occupied
    assert a.read_bytes() == b"two different" and b.read_bytes() == b"one"
    a.unlink()  # the user removed B's photo
    s.run()
    assert "A" in remote.assets and "A" not in remote.deleted


def test_refused_foreign_subfolder_selection_does_not_hide_owners_moves(photos_env, tmp_path):
    a, first, root, _ = photos_env
    a.settings.apple_id = "a@example.invalid"
    first.add("A", "family.jpg", b"A's photo")
    a.run()
    p = root / a.state.files()[0].rel_path
    b, _ = second_library(a, p.parent, tmp_path / "b.db")
    with pytest.raises(engine.SyncError):
        b.run()
    renamed = p.rename(p.with_name("holiday.jpg"))
    a.run()
    assert renamed.exists() and "A" in first.assets


@pytest.mark.xfail(strict=True, reason="known limitation: a restored backup of the same library is indistinguishable from it (same account, same library id); missing photos count as deleted, as in every version (deletion limit, iCloud's Recently Deleted)")
@pytest.mark.parametrize("layout", ["fingerprint_only", "copied_library_id"])
def test_restored_or_legacy_library_does_not_delete_newer_cloud_photo(photos_env, tmp_path, layout):
    s, remote, root, _ = photos_env
    s.settings.apple_id = "a@example.invalid"
    earlier = tmp_path / "Earlier Photos"
    if layout == "fingerprint_only":
        # 113fafb wrote only the fingerprint to each independently configured folder.
        for folder in (root, earlier):
            folder.mkdir()
            (folder / engine.OWNER_FILE).write_text(s._owner_fp() + "\n", encoding="utf-8")
            (folder / engine.MARKER).write_text("Skyfold\n", encoding="utf-8")
    s.run()
    if layout == "copied_library_id":
        shutil.copytree(root, earlier)  # normal folder backup, including hidden markers
    remote.add("NEW", "new.jpg", b"after the backup")
    s.run()
    current = root.rename(tmp_path / "Current Photos saved")
    earlier.rename(root)
    run_or_hold(s)
    assert list(current.rglob("new.jpg")) and "NEW" in remote.assets


def test_claimed_legacy_folder_does_not_recycle_a_held_file(photos_env, tmp_path):
    old, first, root, _ = photos_env
    old.settings.apple_id = ""
    first.add("A", "family.jpg", b"family")
    old.run()  # no Apple ID in fixture: produces old-style folder with generic marker only
    held = root / old.state.files()[0].rel_path
    b, second = second_library(old, root, tmp_path / "b.db")
    second.add("B", "family.jpg", held.read_bytes())
    run_or_hold(b)
    second.remove("B")
    run_or_hold(b)
    assert held.is_file()


@pytest.mark.parametrize("marker", engine.LEGACY_MARKERS)
def test_known_old_marker_does_not_make_preexisting_photos_new_uploads(photos_env, marker):
    s, remote, root, _ = photos_env
    s.settings.apple_id = "b@example.invalid"
    s.settings.photos_upload = True
    write(root, marker, b"legacy folder")
    write(root, "a-private.jpg", b"from the previous owner's library", age=3600)
    run_or_hold(s)
    assert "a-private.jpg" not in getattr(remote, "uploads", [])


@pytest.mark.xfail(strict=True, reason='not applicable: dc4ca0d was never released, so no installation has this layout')
def test_legacy_mixed_root_with_b_rows_does_not_send_as_private_photos(photos_env):
    s, second, root, _ = photos_env
    s.settings.apple_id = ""  # create the earlier package's marker-only root
    second.add("B", "b.jpg", b"B's own download")
    s.run()
    s.state.set("foreign_files", "1")  # dc4ca0d after B downloaded into A's folder
    s.state.set("account", hashlib.sha256(b"b@example.invalid").hexdigest()[:16])
    write(root, "a-private.jpg", b"A's pre-existing photograph", age=3600)
    assert not (root / engine.OWNER_FILE).exists()
    s.settings.apple_id = "b@example.invalid"
    s.settings.photos_upload = True
    run_or_hold(s)
    assert "a-private.jpg" not in getattr(second, "uploads", [])


@pytest.mark.xfail(strict=True, reason='not applicable: 278feb8 was never released, so no installation has guest downloads')
def test_upgrade_preserves_a_guest_download_that_is_already_on_disk(photos_env, monkeypatch):
    s, first, root, _ = photos_env
    s.settings.apple_id = "b@example.invalid"
    s.settings.photos_dir_confirmed = True
    b_owner = hashlib.sha256(b"b@example.invalid").hexdigest()[:16]
    a_owner = hashlib.sha256(b"a@example.invalid").hexdigest()[:16]
    # 278feb8's persisted layout: A owns the root, B has a tracked guest download without a mark.
    second = FakeRemote()
    second.add("B", "b-private.jpg", b"B's private photograph")
    guest_settings = s.settings.copy()
    guest_settings.apple_id = ""
    engine.Syncer(s.state, second, guest_settings).run()
    row = s.state.files()[0]
    guest = root / row.rel_path
    s.state.set("fid:B#original", None)
    s.state.set("account", b_owner)
    (root / engine.OWNER_FILE).write_text(a_owner + "\n", encoding="utf-8")
    w = offline_worker(s.settings, s.state, monkeypatch)
    w.set_api(second)
    w.sign_out()
    owner = w.settings.copy()
    owner.apple_id = "a@example.invalid"
    w.apply_settings(owner, True)
    w.set_api(first)
    owner = w.settings.copy()
    owner.photos_dir_confirmed = owner.photos_upload = True
    w.apply_settings(owner, False)
    w.sync_now()
    assert guest.exists() and guest.name not in getattr(first, "uploads", [])


def test_album_mirror_cannot_feed_another_users_library(photos_env, tmp_path):
    parent, first, root, _ = photos_env
    parent.settings.apple_id = "a@example.invalid"
    parent.settings.albums_as_folders = True
    album_root = root / engine.ALBUMS_DIR
    child, second = second_library(parent, album_root, tmp_path / "child.db")
    child.run()  # this genuine B library exists before A chooses its parent
    first.add("PRIVATE", "private.jpg", b"only A owns this")
    first.album_map["Trip"] = ["PRIVATE"]
    run_or_hold(parent)
    # Move B's library out of the overlapping configuration, as the UI instructs.
    new_root = album_root.rename(tmp_path / "Bs separate Photos")
    child.settings.photos_dir = str(new_root)
    run_or_hold(child)
    assert "private.jpg" not in getattr(second, "uploads", [])


@pytest.mark.xfail(strict=True, reason="accepted: 1.0.15 recorded no file ids; a file put at a photo's name while Skyfold was being upgraded is taken for it once")
def test_first_upgrade_pass_does_not_claim_a_replacement_at_tracked_name(photos_env):
    s, remote, root, _ = photos_env
    s.settings.apple_id = ""  # generic 1.0.15 marker
    remote.add("A", "camera.jpg", b"original")
    s.run()
    row = s.state.files()[0]
    s.state.set("fid:A#original", None)  # v1.0.15 FileRows have a hash but no file ID
    p = root / row.rel_path
    original = p.rename(p.with_name("my-original.jpg"))
    shutil.copy2(original, p)
    assert not os.path.samefile(original, p)
    s.settings.apple_id = "a@example.invalid"
    s.run()  # first scan after upgrade
    remote.remove("A")
    s.run()
    assert p.is_file()


def test_legacy_keep_survives_a_move_before_first_upgrade_scan(photos_env):
    s, remote, root, _ = photos_env
    s.settings.apple_id = ""  # generic 1.0.15 marker
    remote.add("B", "photo.jpg", b"family")
    s.run()
    row = s.state.files()[0]
    s.state.set("fid:B#original", None)  # faithful pre-file-ID database
    p = root / row.rel_path
    separate = write(root, "kept/photo.jpg", p.read_bytes(), age=3600)
    s.state.set("upload_done:kept/photo.jpg", row.hash)
    archive = root / "Archive"
    archive.mkdir()
    kept = separate.rename(archive / separate.name)
    p.unlink()
    s.settings.apple_id = "a@example.invalid"
    s.run()
    s.run()  # lazy file-ID collection on the next ordinary scan
    if "B" in remote.assets:
        remote.remove("B")
    s.run()
    assert kept.is_file()


@pytest.mark.xfail(strict=True, reason='accepted: the file is sent once more and iCloud keeps one copy; nothing is deleted')
def test_accepted_legacy_upload_is_not_resent_after_source_returns(photos_env):
    s, remote, root, _ = photos_env
    s.settings.apple_id = ""
    s.run()
    p = write(root, "one.jpg", b"accepted only once", age=3600)
    original_id = p.stat()
    receipt = remote.upload(p)
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    s.state.set("upload_pending:" + digest, json.dumps(dict(
        rel=p.name, digest=digest, asset=receipt.asset_id, duplicate=False,
        identity=[original_id.st_dev, original_id.st_ino])))
    archive = p.rename(root.parent / "one.jpg")
    s.settings.apple_id = "a@example.invalid"
    s.settings.photos_upload = True
    s.run(force_full=True)  # upgrade while the source is stored outside the Photos folder
    archive.rename(p)
    s.run()
    assert remote.uploads == ["one.jpg"] and p.stat().st_ino == original_id.st_ino


@pytest.mark.xfail(strict=True, reason='accepted: the file is sent once more and iCloud keeps one copy; nothing is deleted')
def test_counted_legacy_digest_does_not_replay_the_original_source(photos_env):
    s, remote, root, _ = photos_env
    s.settings.apple_id = ""
    remote.add("UNRELATED", "unrelated.jpg", b"different")
    s.run()  # the same account's previously synced root
    p = write(root, "phone-copy.jpg", b"same original", age=3600)
    remote.add("PHONE", "camera.jpg", p.read_bytes())
    prior = remote.upload(p)
    assert prior.is_duplicate
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    s.state.set("upload_done:" + p.name, digest)
    s.state.set("upload_seen:PHONE", json.dumps([p.name, digest]))
    actual_source = p.rename(root / "z-archive.jpg")
    shutil.copy2(actual_source, root / "a-new-copy.jpg")
    s.settings.apple_id = "a@example.invalid"
    s.settings.photos_upload = True
    s.run(force_full=True)
    assert actual_source.name not in remote.uploads


def test_content_followed_copy_keeps_its_cloud_asset_after_an_edit_and_rename(photos_env):
    s, remote, root, _ = photos_env
    remote.add("PHONE", "phone.jpg", b"original")
    s.run()
    p = root / s.state.files()[0].rel_path
    restored = root / "archive.jpg"
    shutil.copy2(p, restored)
    p.unlink()
    s.run()
    assert s.state.files()[0].rel_path == restored.name  # the app has accepted the move by contents
    restored.write_bytes(b"updated caption and metadata")
    restored.rename(root / "finished.jpg")
    s.run()
    assert "PHONE" in remote.assets
