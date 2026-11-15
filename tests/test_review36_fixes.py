"""Thirty-fourth follow-up review (Codex review 36): own regression tests next to the kept probes."""
from tests.test_engine import env as photos_env  # noqa: F401 - fixture


def test_a_renamed_photo_without_a_fingerprint_is_proven_by_icloud_copy(photos_env):
    s, cloud, root, _ = photos_env
    cloud.add("A1", "IMG_0001.jpg", b"ORIGINAL PHOTO")
    s.run(force_full=True)
    s.state._db.execute("UPDATE files SET hash=NULL")  # a row from before 1.0.6
    (root / "2025/11/IMG_0001.jpg").rename(root / "2025/11/Holiday.jpg")
    s.run()
    assert "A1" in cloud.assets and not cloud.deleted
    assert s.state.files()[0].rel_path == "2025/11/Holiday.jpg"


def test_another_file_of_the_same_size_is_not_taken_for_the_photo(photos_env):
    s, cloud, root, _ = photos_env
    cloud.add("A1", "IMG_0001.jpg", b"ORIGINAL PHOTO")
    s.run(force_full=True)
    (root / "2025/11/IMG_0001.jpg").unlink()
    (root / "2025/11/mine.jpg").write_bytes(b"SOMETHING ELSE")  # same length, other content
    s.run()
    assert s.state.files() == [] or s.state.files()[0].rel_path != "2025/11/mine.jpg"
    assert (root / "2025/11/mine.jpg").read_bytes() == b"SOMETHING ELSE"


def test_two_renamed_photos_of_the_same_size_each_find_their_own_file(photos_env):
    s, cloud, root, _ = photos_env
    cloud.add("A1", "IMG_0001.jpg", b"PHOTO NUMBER ONE")
    cloud.add("A2", "IMG_0002.jpg", b"PHOTO NUMBER TWO")
    s.run(force_full=True)
    (root / "2025/11/IMG_0001.jpg").rename(root / "2025/11/First.jpg")
    (root / "2025/11/IMG_0002.jpg").rename(root / "2025/11/Second.jpg")
    s.run()
    paths = {r.asset_id: r.rel_path for r in s.state.files()}
    assert paths == {"A1": "2025/11/First.jpg", "A2": "2025/11/Second.jpg"} and not cloud.deleted
