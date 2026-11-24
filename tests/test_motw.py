"""Files downloaded from iCloud carry Windows' "came from the internet" mark (Zone.Identifier)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from skyfold import motw

from test_drive import FakeDrive, env, write  # noqa: F401  (the Drive fixture)
from test_engine import FakeRemote
from skyfold import engine as eng
from skyfold.config import Settings
from skyfold.state import State

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the mark is a Windows (NTFS) file stream")


def unblock(p: Path) -> None:
    os.remove(str(p) + motw.STREAM)  # what Explorer's "Unblock" does


def test_mark_and_read_back(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"hello")
    assert motw.zone(p) is None
    assert motw.mark(p) is True
    assert motw.zone(p) == 3 and p.read_bytes() == b"hello" and p.stat().st_size == 5
    text = Path(str(p) + motw.STREAM).read_bytes()
    assert text == b"[ZoneTransfer]\r\nZoneId=3\r\n"  # the zone only: no address of where it came from


def test_a_disk_without_streams_is_no_error(tmp_path, monkeypatch):
    p = tmp_path / "a.txt"
    p.write_bytes(b"x")

    def refuse(*a, **k):
        raise OSError(1, "not supported")

    monkeypatch.setattr(motw, "open", refuse, raising=False)
    assert motw.mark(p) is False and p.read_bytes() == b"x"


def test_removing_the_mark_keeps_the_files_time(tmp_path):
    """Unblocking a file must not look like an edit (Drive would upload it again)."""
    p = tmp_path / "a.txt"
    p.write_bytes(b"x")
    motw.mark(p)
    os.utime(p, (1_600_000_000, 1_600_000_000))
    unblock(p)
    assert p.stat().st_mtime == 1_600_000_000 and motw.zone(p) is None


def test_a_copy_gets_the_originals_mark_and_keeps_its_time(tmp_path):
    src, dst, plain, plain_copy = (tmp_path / n for n in ("a", "b", "c", "d"))
    for p in (src, dst, plain, plain_copy):
        p.write_bytes(b"x")
    motw.mark(src)
    os.utime(dst, (1_500_000_000, 1_500_000_000))
    motw.copy_mark(src, dst)
    assert motw.zone(dst) == 3 and dst.stat().st_mtime == 1_500_000_000
    motw.copy_mark(plain, plain_copy)
    assert motw.zone(plain_copy) is None


# --- iCloud Drive ---

def test_drive_download_is_marked_and_stays_synced(env):  # noqa: F811
    s, d, root, _ = env
    d.put("Docs/a.pdf", b"%PDF-1")
    d.put("empty.txt", b"")
    r = s.run()
    assert r.downloaded == 2
    f = root / "Docs/a.pdf"
    assert motw.zone(f) == 3 and motw.zone(root / "empty.txt") == 3
    assert f.stat().st_mtime == 1_600_000_000  # iCloud's time, set after the mark
    for run in (lambda: s.run(), lambda: s.run(changed={"Docs/a.pdf"})):
        r = run()
        assert (r.downloaded, r.uploaded, r.conflicts) == (0, 0, 0) and not d.trashed
    unblock(f)  # the user unblocks it: not a change of the file
    r = s.run(changed={"Docs/a.pdf"})
    assert (r.downloaded, r.uploaded, r.conflicts) == (0, 0, 0) and not d.trashed


def test_drive_new_version_is_marked_again(env):  # noqa: F811
    s, d, root, _ = env
    d.put("a.txt", b"v1")
    s.run()
    unblock(root / "a.txt")
    d.put("a.txt", b"version 2")
    s.run()
    assert (root / "a.txt").read_bytes() == b"version 2" and motw.zone(root / "a.txt") == 3
    assert (s.run().uploaded, d.trashed) == (0, [])


def test_drive_restricted_file_stays_restricted(env):  # noqa: F811
    s, d, root, _ = env
    d.put("a.txt", b"v1")
    s.run()
    motw.mark(root / "a.txt", 4)
    os.utime(root / "a.txt", (1_600_000_000, 1_600_000_000))
    d.put("a.txt", b"version 2")
    s.run()
    assert motw.zone(root / "a.txt") == 4


def test_drive_same_file_already_on_the_pc_is_left_alone(env):  # noqa: F811
    s, d, root, _ = env
    write(root, "a.txt", b"same")
    os.utime(root / "a.txt", (1_600_000_000, 1_600_000_000))
    d.put("a.txt", b"same")
    r = s.run()
    assert (r.downloaded, r.uploaded, r.conflicts) == (0, 0, 0)
    assert motw.zone(root / "a.txt") is None  # the user's own file: never marked


def test_drive_pc_files_are_never_marked(env):  # noqa: F811
    s, d, root, _ = env
    s.run()
    write(root, "mine.txt", b"from this pc")
    assert s.run().uploaded == 1
    assert motw.zone(root / "mine.txt") is None


def test_drive_download_works_where_the_mark_cant_be_written(env, monkeypatch):  # noqa: F811
    s, d, root, _ = env
    monkeypatch.setattr(motw, "mark", lambda *a, **k: False)
    d.put("a.txt", b"v1")
    assert s.run().downloaded == 1 and (root / "a.txt").read_bytes() == b"v1"
    r = s.run()
    assert (r.downloaded, r.uploaded) == (0, 0)


# --- iCloud Photos ---

@pytest.fixture()
def photos(tmp_path, monkeypatch):
    monkeypatch.setattr(eng.Syncer, "_trash", lambda self, p: p.unlink())
    settings = Settings(photos_dir=str(tmp_path / "Slike"), delete_threshold=3, folder_structure="year_month")
    remote = FakeRemote()
    return eng.Syncer(State(tmp_path / "s.db"), remote, settings), remote, tmp_path / "Slike"


def test_photos_and_live_photo_videos_are_marked(photos):
    s, r, root = photos
    r.add("A1", "IMG_0001.HEIC", b"x" * 10, live=b"v" * 5)
    assert s.run().downloaded == 2
    for name in ("IMG_0001.HEIC", "IMG_0001.MOV"):
        f = root / "2025/11" / name
        assert motw.zone(f) == 3
    res = s.run()
    assert res.downloaded == 0 and res.deleted_local == 0


def test_album_copy_is_marked_like_its_photo(photos, monkeypatch):
    s, r, root = photos
    r.add("A1", "IMG_0001.JPG", b"x" * 10)
    r.album_map["Trip"] = ["A1"]

    def no_links(*a, **k):
        raise OSError(1, "no hard links on this disk")

    monkeypatch.setattr(eng.os, "link", no_links)
    s.run()
    copies = [p for p in root.rglob("IMG_0001.JPG") if "Albums" in p.parts]
    assert copies and all(motw.zone(p) == 3 for p in copies)


# --- Notes attachments ---

def test_notes_marks_only_the_downloaded_attachment(tmp_path):
    from skyfold.notes_engine import _mark_download

    assets = tmp_path / "_assets" / "rec1"
    assets.mkdir(parents=True)
    got = assets / "3f2a.bin"
    got.write_bytes(b"%PDF")
    elsewhere = tmp_path / "other.txt"
    elsewhere.write_bytes(b"x")
    _mark_download(str(got), str(assets))
    _mark_download(str(elsewhere), str(assets))  # not in the folder it was downloaded into
    _mark_download(str(assets / "missing.bin"), str(assets))
    assert motw.zone(got) == 3 and motw.zone(elsewhere) is None
