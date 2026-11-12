"""Review 26 (B1-B6): records follow the file, an unknown identity proves nothing."""
import os

from skyfold import engine as pe
from tests.test_drive import env as drive_env  # noqa: F401
from tests.test_engine import env as photos_env  # noqa: F401


def test_drive_a_users_file_named_like_a_recorded_previous_version_is_kept(drive_env):  # noqa: F811
    s, d, root, recycled = drive_env
    d.put("a.txt", b"x")
    s.run()
    mine = root / "a (previous version).txt"
    mine.write_bytes(b"MY OWN FILE")
    old = mine.stat().st_mtime - 60
    os.utime(mine, (old, old))  # not "still being written"
    s.state.set_meta("prev:a (previous version).txt", "1:1")  # a stale record of some other file
    s.run()
    s.run()
    assert mine.read_bytes() == b"MY OWN FILE" and mine.name not in recycled
    assert not s.state.meta_items("prev:")
    assert mine.name in d.items  # an ordinary file: synced like any other


def test_drive_records_of_files_that_are_gone_are_dropped(drive_env):  # noqa: F811
    s, d, root, _ = drive_env
    d.put("a.txt", b"x")
    s.state.set_meta("tmp:.skyfold-tmp-gone", "1")
    s.state.set_meta("aside:.skyfold-tmp-old-1-a.txt", "1")
    s.run()
    assert not s.state.meta_items("tmp:") and not s.state.meta_items("aside:")


def test_legacy_marker_is_still_taken_over_when_the_database_tracks_files(photos_env):  # noqa: F811
    s, r, root, _ = photos_env
    r.add("A1", "photo.jpg", b"one")
    s.run()
    os.rename(root / pe.MARKER, root / pe.LEGACY_MARKERS[0])  # as left by the earlier app
    s.run()
    assert (root / pe.MARKER).is_file() and not (root / pe.LEGACY_MARKERS[0]).exists()


def test_album_folder_recorded_without_identity_is_kept(photos_env):  # noqa: F811
    s, r, root, _ = photos_env
    s.settings.albums_as_folders = True
    r.add("A1", "photo.jpg", b"one")
    r.album_map = {"Holiday": ["A1"]}
    s.run(force_full=True)
    s.state.set("albumdir:Albums/Holiday", "1")  # written by a build that kept no identity
    s.settings.albums_as_folders = False
    s.run(force_full=True)
    assert (root / "Albums/Holiday").is_dir() and not (root / "Albums/Holiday/photo.jpg").exists()
