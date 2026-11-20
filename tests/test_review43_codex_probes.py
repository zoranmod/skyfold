"""Review 43. Ordinary Explorer, editor and upgrade operations, with the offline Photos fake."""
import hashlib
import json
import os

import pytest

from skyfold import engine
from skyfold.state import FileRow
from tests.test_review42_codex_probes import second_library

from tests.test_drive import write
from tests.test_engine import env  # noqa: F401 -- pytest fixture


def test_replace_one_photo_with_another_keeps_surviving_cloud_photo(env):
    sync, remote, root, _ = env
    remote.add("kept", "summer.jpg", b"summer original")
    remote.add("removed", "cover.jpg", b"old cover")
    sync.run()
    by_id = {row.asset_id: root / row.rel_path for row in sync.state.files()}
    os.replace(by_id["kept"], by_id["removed"])  # Explorer: move and replace
    sync.run()
    assert by_id["removed"].read_bytes() == b"summer original"
    assert "kept" in remote.assets, remote.deleted


@pytest.mark.xfail(strict=True, reason="known limitation (every version): an editor that replaces the file plus a rename before the next check leaves nothing in common with the photo (name, file id, contents); documented in README and CHANGELOG, protected by the deletion limit and iCloud's Recently Deleted")
def test_editor_save_then_rename_before_next_scan_keeps_cloud_photo(env):
    sync, remote, root, _ = env
    remote.add("photo", "camera.jpg", b"original")
    sync.run()
    path = root / sync.state.files()[0].rel_path
    new = write(path.parent, "editor.tmp", b"edited and captioned")
    os.replace(new, path)
    saved = path.rename(path.with_name("holiday.jpg"))
    sync.run()
    assert saved.read_bytes() == b"edited and captioned"
    assert "photo" in remote.assets, remote.deleted


def test_turning_off_album_mirroring_after_editor_save_preserves_cloud_photo(env):
    sync, remote, root, _ = env
    remote.add("photo", "camera.jpg", b"original")
    remote.album_map = {"Holiday": ["photo"]}
    sync.run()
    path = root / sync.state.files()[0].rel_path
    assert os.path.samefile(path, root / "Albums/Holiday/camera.jpg")
    os.replace(write(path.parent, "editor.tmp", b"retouched"), path)
    sync.run()
    sync.settings.albums_as_folders = False
    sync.run(force_full=True)
    sync.run()
    assert path.read_bytes() == b"retouched"
    assert "photo" in remote.assets, remote.deleted


def test_1015_matching_between_full_scans_stays_synced_after_upgrade(env):
    # v1.0.15 _match_upload(full=False) commits this combination on a byte match.
    # A user upgrading before the next full library listing has this database.
    sync, remote, root, _ = env
    sync.settings.apple_id = "first@example.invalid"
    remote.add("matched", "original.jpg", b"accepted photo")
    source = write(root, "original.jpg", b"accepted photo", age=3600)
    (root / engine.MARKER).write_text("Skyfold\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    date = remote.assets["matched"].date.isoformat()
    sync.state.add_file(FileRow("matched", "original", source.name, source.stat().st_size, False, date, digest), date)
    sync.state.set("upload_done:" + source.name, digest)
    sync.state.set("upload_unlisted:matched", "1")
    sync.run(force_full=True)
    source.unlink()
    sync.run()
    assert "matched" in remote.deleted


def test_refusing_existing_foreign_subfolder_writes_no_marker(env, tmp_path):
    first, _, root, _ = env
    first.settings.apple_id = "first@example.invalid"
    first.run()
    child = root / "Archive"
    child.mkdir()
    second, remote = second_library(first, child, tmp_path / "second.db")
    with pytest.raises(engine.SyncError):
        second.run()
    assert list(child.iterdir()) == []
    assert not getattr(remote, "uploads", [])


def test_another_accounts_nested_library_is_outside_recycling_scope(env, tmp_path):
    first, remote, root, _ = env
    first.settings.apple_id = "first@example.invalid"
    remote.add("A", "camera.jpg", b"family photo")
    first.run()
    original = root / first.state.files()[0].rel_path
    other = tmp_path / "Second library"
    second, _ = second_library(first, other, tmp_path / "second.db")
    second.run()
    nested = other.rename(root / "Second library")
    archive = original.rename(nested / original.name)
    write(original.parent, original.name, b"another image")
    first.run()
    remote.remove("A")
    first.run()
    assert archive.is_file()


def test_claimed_legacy_folder_keeps_existing_photo_private_after_editor_save(env):
    sync, remote, root, _ = env
    root.mkdir()
    (root / engine.MARKER).write_text("Skyfold\n", encoding="utf-8")
    path = write(root, "family.jpg", b"old family photo", age=3600)
    sync.settings.apple_id = "second@example.invalid"
    sync.run()  # choosing the folder inventories its existing files even when upload is off
    assert not getattr(remote, "uploads", [])
    os.replace(write(root, "editor.tmp", b"old family photo with caption", age=3600), path)
    sync.settings.photos_upload = True
    sync.run()
    assert not getattr(remote, "uploads", [])


def test_upgrade_of_shared_1015_folder_never_uploads_other_accounts_download(env):
    # The 1.0.15 worker reset A's rows on sign-in as B but kept the same Photos
    # directory. Verified separately with two runs of the tagged release.
    sync, b, root, _ = env
    sync.settings.apple_id = "b@example.invalid"
    b.add("B", "b.jpg", b"Bs photo")
    date = b.assets["B"].date.isoformat()
    foreign = write(root, "2025/11/private.jpg", b"As private photo", age=3600)
    own = write(root, "2025/11/b.jpg", b"Bs photo", age=3600)
    (root / engine.MARKER).write_text("Skyfold\n", encoding="utf-8")
    sync.state.set("account", hashlib.sha256(b"b@example.invalid").hexdigest()[:16])
    sync.state.add_file(FileRow("B", "original", own.relative_to(root).as_posix(), own.stat().st_size,
                                False, date, hashlib.sha256(own.read_bytes()).hexdigest()), date)
    sync.run(force_full=True)
    sync.settings.photos_upload = True
    sync.run()
    assert foreign.name not in getattr(b, "uploads", [])


def test_returning_1015_source_does_not_recreate_a_deleted_cloud_photo(env):
    sync, remote, root, _ = env
    sync.settings.apple_id = "a@example.invalid"
    sync.settings.photos_upload = True
    source = write(root, "one.jpg", b"original upload", age=3600)
    (root / engine.MARKER).write_text("Skyfold\n", encoding="utf-8")
    receipt = remote.upload(source)
    st = source.stat()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    sync.state.set("upload_pending:" + digest, json.dumps(dict(
        rel=source.name, digest=digest, asset=receipt.asset_id, duplicate=False,
        identity=[st.st_dev, st.st_ino])))
    saved = source.rename(root.parent / "one.jpg")
    sync.run(force_full=True)
    remote.remove(receipt.asset_id)  # the user deletes the sent item from their phone
    sync.run()
    saved.rename(source)
    sync.run()
    assert not remote.assets, remote.uploads
