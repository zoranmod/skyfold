"""Tests for the fixes after the eighteenth (Codex) review: the PC side is checked by content too."""

import os

from skyfold import drive_engine as de
from tests.test_drive import env, write  # noqa: F401
from tests.test_engine import env as photos_env  # noqa: F401


def _silent_edit(p, data):
    st = p.stat()
    p.write_bytes(data)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))


def test_the_scrub_takes_turns_within_its_budget(env, monkeypatch):  # noqa: F811
    s, d, root, _ = env
    for i in range(6):
        d.put(f"f{i}.txt", b"0123456789")
    s.run()
    monkeypatch.setattr(de, "SCRUB_BYTES", 25)  # two 10-byte files per full pass
    read = []
    real = de.file_hash
    monkeypatch.setattr(de, "file_hash", lambda p: (read.append(p.name), real(p))[1])
    for _ in range(3):
        s.run()
    assert read == ["f0.txt", "f1.txt", "f2.txt", "f3.txt", "f4.txt", "f5.txt"]  # all came round, in turn
    _silent_edit(root / "f4.txt", b"abcdefghij")
    for _ in range(4):
        s.run()
    assert d.items["f4.txt"]["data"] == b"abcdefghij"


def test_a_silently_edited_file_in_a_folder_deleted_in_icloud_is_kept(env):  # noqa: F811
    s, d, root, trashed = env
    s.delete_threshold = 1000
    d.put("D/a.txt", b"aaaa")
    d.put("D/b.txt", b"bbbb")
    s.run()
    _silent_edit(root / "D/a.txt", b"MINE")
    d.trash(d._item("D"))
    for _ in range(4):
        res = s.run()
        s.approved |= set(res.pending_to_pc)
    assert (root / "D/a.txt").read_bytes() == b"MINE" and "D/a.txt" not in trashed
    assert d.items["D/a.txt"]["data"] == b"MINE"


def test_a_partial_answer_is_never_taken_for_the_file(env, monkeypatch):  # noqa: F811
    s, d, root, _ = env
    d.put("a.txt", b"base-0")
    s.run()
    monkeypatch.setattr(d, "download", lambda item, dest, on_bytes: dest.write_bytes(b"bas"))
    assert s._cloud_hash(d._item("a.txt")) is None


def test_a_photo_moved_inside_the_folder_is_followed_by_its_content(photos_env):  # noqa: F811
    s, remote, root, trashed = photos_env
    remote.add("A1", "photo.jpg", b"original")
    s.run()
    (root / "Sorted").mkdir()
    os.rename(root / "2025/11/photo.jpg", root / "Sorted/photo.jpg")
    s.run()
    assert [r.rel_path for r in s.state.files()] == ["Sorted/photo.jpg"]
    assert "A1" in remote.assets and not trashed and not remote.deleted
