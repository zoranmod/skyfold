"""Review 25: Skyfold tidies up only what it wrote down as its own (private files, index, album entries)."""
from skyfold import drive_engine as de, engine as pe, notes_engine as ne
from tests.test_drive import env as drive_env  # noqa: F401
from tests.test_engine import env as photos_env  # noqa: F401
from tests.test_notes import env as notes_env  # noqa: F401


def test_drive_keeps_no_record_of_private_files_after_a_normal_sync(drive_env):  # noqa: F811
    s, d, root, _ = drive_env
    d.put("a.txt", b"one")
    s.run()
    d.put("a.txt", b"two")  # replaces the PC copy: a download and a moved-aside file
    s.run()
    assert (root / "a.txt").read_bytes() == b"two"
    assert not s.state.meta_items("tmp:") and not s.state.meta_items("aside:")
    assert not [p for p in root.iterdir() if p.name.startswith(de.TMP_PREFIX)]


def test_drive_removes_its_own_unfinished_download_and_forgets_it(drive_env):  # noqa: F811
    s, d, root, _ = drive_env
    d.put("a.txt", b"x")
    s.run()
    mine = root / f"{de.TMP_PREFIX}mine"
    theirs = root / f"{de.TMP_PREFIX}theirs"
    mine.write_bytes(b"half")
    theirs.write_bytes(b"personal")
    s._own(mine, "tmp")
    s.run()
    assert not mine.exists() and theirs.read_bytes() == b"personal"
    assert not s.state.meta_items("tmp:")


def test_notes_keeps_no_record_of_private_files_after_a_normal_sync(notes_env):  # noqa: F811
    s, r, root, _ = notes_env
    r.put("n1", "A")
    s.run()
    r.put("n2", "B")
    s.run(full=True)
    assert not s.state.items("tmp:")
    assert s.state.get("index_owned") == "1" and (root / ne.INDEX).exists()


def test_notes_index_it_wrote_is_still_updated(notes_env):  # noqa: F811
    s, r, root, _ = notes_env
    r.put("n1", "First")
    s.run()
    r.put("n2", "Second")
    s.run()
    assert "Second" in (root / ne.INDEX).read_text(encoding="utf-8")


def test_album_folder_skyfold_made_is_removed_but_a_users_folder_stays(photos_env):  # noqa: F811
    s, r, root, _ = photos_env
    s.settings.albums_as_folders = True
    (root / pe.ALBUMS_DIR / "Mine").mkdir(parents=True)
    r.add("A1", "photo.jpg", b"one")
    r.album_map = {"Holiday": ["A1"]}
    s.run(force_full=True)
    assert (root / pe.ALBUMS_DIR / "Holiday/photo.jpg").exists()
    s.settings.albums_as_folders = False
    s.run(force_full=True)
    assert not (root / pe.ALBUMS_DIR / "Holiday").exists()
    assert (root / pe.ALBUMS_DIR / "Mine").is_dir()  # and so Albums itself stays too
    assert not s.state.items("albumid:")


def test_album_link_from_an_earlier_version_is_recognised(photos_env):  # noqa: F811
    s, r, root, trashed = photos_env
    s.settings.albums_as_folders = True
    r.add("A1", "photo.jpg", b"one")
    r.album_map = {"Holiday": ["A1"]}
    s.run(force_full=True)
    rel = f"{pe.ALBUMS_DIR}/Holiday/photo.jpg"
    s.state.set("albumid:" + rel, None)  # as written by a version that kept no file identity
    s.state.set("albums_refreshed", None)
    s.run(force_full=True)
    assert s.state.get("albumid:" + rel)
