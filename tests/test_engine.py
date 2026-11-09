"""Engine tests against a fake iCloud. Run: .venv\\Scripts\\python -m pytest tests"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from skyfold import engine as eng
from skyfold.config import Settings
from skyfold.remote import Changes, RemoteAsset, RemoteResource
from skyfold.state import State


class FakeRemote:
    def __init__(self) -> None:
        self.assets: dict[str, RemoteAsset] = {}
        self.blobs: dict[str, bytes] = {}
        self.version = 0
        self.deleted: list[str] = []
        self.log: list[tuple[int, str, str]] = []
        self.full_reads = 0
        self.lookups = 0
        self.oldest_token = 0
        self.album_map: dict[str, list[str]] = {}

    def add(self, aid: str, name: str, data: bytes, live: bytes | None = None, video=False,
            date=datetime(2025, 11, 3, 10, tzinfo=timezone.utc)) -> None:
        res = [RemoteResource("original", name, len(data), f"u/{aid}", video)]
        self.blobs[f"u/{aid}"] = data
        if live is not None:
            res.append(RemoteResource("original_video", Path(name).stem + ".MOV", len(live), f"u/{aid}v", True))
            self.blobs[f"u/{aid}v"] = live
        self.assets[aid] = RemoteAsset(aid, name, date, video, res)
        self.version += 1
        self.log.append((self.version, "add", aid))

    def remove(self, aid: str) -> None:
        del self.assets[aid]
        self.version += 1
        self.log.append((self.version, "del", aid))

    def cursor(self) -> str:
        return f"c{self.version}"

    def list_assets(self, on_page=None):
        self.full_reads += 1
        yield from list(self.assets.values())

    def changes(self, since: str):
        start = int(since[1:])
        if start < self.oldest_token:  # simulates CloudKit's "change token expired"
            return None
        alive, gone = {}, set()
        for v, kind, aid in self.log:
            if v <= start:
                continue
            if kind == "add" and aid in self.assets:
                alive[aid] = self.assets[aid]
                gone.discard(aid)
            elif kind == "del":
                alive.pop(aid, None)
                gone.add(aid)
        return Changes(list(alive.values()), gone, self.cursor())

    def albums(self):
        return [(k, [i for i in v if i in self.assets]) for k, v in self.album_map.items()]

    def lookup(self, ids):
        self.lookups += 1
        return {a: self.assets[a] for a in ids if a in self.assets}

    def delete(self, aid: str) -> None:
        self.deleted.append(aid)
        self.remove(aid)

    def download(self, res, dest: Path, on_bytes) -> None:
        data = self.blobs[res.url]
        dest.write_bytes(data)
        on_bytes(len(data))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    trashed: list[str] = []

    def fake_trash(self, p: Path) -> None:
        trashed.append(p.name)
        p.unlink()

    monkeypatch.setattr(eng.Syncer, "_trash", fake_trash)
    settings = Settings(photos_dir=str(tmp_path / "Slike"), delete_threshold=3, folder_structure="year_month")
    remote = FakeRemote()
    syncer = eng.Syncer(State(tmp_path / "s.db"), remote, settings)
    return syncer, remote, tmp_path / "Slike", trashed


def test_download_and_layout(env):
    s, r, root, _ = env
    r.add("A1", "IMG_0001.HEIC", b"x" * 10, live=b"v" * 5)
    r.add("A2", "IMG_0002.JPG", b"y" * 7)
    res = s.run()
    assert res.downloaded == 3
    assert (root / "2025/11/IMG_0001.HEIC").read_bytes() == b"x" * 10
    assert (root / "2025/11/IMG_0001.MOV").exists()
    assert (root / eng.MARKER).exists()
    # nothing changed → no full scan
    assert s.run().full_scan is False


def test_name_collision_gets_suffix(env):
    s, r, root, _ = env
    r.add("AAAAAA1", "IMG_0001.JPG", b"1")
    r.add("BBBBBB2", "IMG_0001.JPG", b"22")
    s.run()
    files = sorted(p.name for p in (root / "2025/11").iterdir())
    assert files == ["IMG_0001.JPG", "IMG_0001_BBBBBB.JPG"] or files == ["IMG_0001.JPG", "IMG_0001_AAAAAA.JPG"]


def test_delete_on_phone_trashes_on_pc(env):
    s, r, root, trashed = env
    r.add("A1", "IMG_0001.HEIC", b"x", live=b"v")
    s.run()
    r.remove("A1")
    res = s.run()
    assert res.deleted_local == 1
    assert sorted(trashed) == ["IMG_0001.HEIC", "IMG_0001.MOV"]


def test_delete_on_pc_deletes_in_icloud(env):
    s, r, root, trashed = env
    r.add("A1", "IMG_0001.HEIC", b"x", live=b"v")
    r.add("A2", "IMG_0002.HEIC", b"y")
    s.run()
    (root / "2025/11/IMG_0001.HEIC").unlink()
    res = s.run()
    assert r.deleted == ["A1"]
    assert res.deleted_cloud == 1
    assert trashed == ["IMG_0001.MOV"]  # leftover live video goes too
    s.run()  # our own delete moved the cursor: one confirming scan
    assert s.run().full_scan is False


def test_deleting_only_live_video_does_not_come_back(env):
    s, r, root, _ = env
    r.add("A1", "IMG_0001.HEIC", b"x", live=b"v")
    s.run()
    (root / "2025/11/IMG_0001.MOV").unlink()
    s.run(force_full=True)
    assert not (root / "2025/11/IMG_0001.MOV").exists()
    assert r.deleted == []


def test_moved_file_is_followed_not_deleted(env):
    s, r, root, _ = env
    r.add("A1", "IMG_0001.HEIC", b"abc")
    s.run()
    (root / "Odmor").mkdir()
    os.replace(root / "2025/11/IMG_0001.HEIC", root / "Odmor/IMG_0001.HEIC")
    s.run(force_full=True)
    assert r.deleted == []
    assert not (root / "2025/11/IMG_0001.HEIC").exists()


def test_threshold_holds_mass_delete(env):
    s, r, root, trashed = env
    for i in range(5):
        r.add(f"A{i}", f"IMG_{i}.JPG", b"z")
    s.run()
    for i in range(5):
        r.remove(f"A{i}")
    res = s.run()
    assert res.pending and len(res.pending.to_pc) == 5
    assert trashed == []
    s.approve(res.pending.to_pc)
    res = s.run()
    assert res.deleted_local == 5


def test_threshold_reject_restores_pc_deletes(env):
    s, r, root, _ = env
    for i in range(5):
        r.add(f"A{i}", f"IMG_{i}.JPG", b"z")
    s.run()
    for p in (root / "2025/11").iterdir():
        p.unlink()
    res = s.run()
    assert res.pending and len(res.pending.to_cloud) == 5 and r.deleted == []
    # pending deletions must not trigger a full scan on every poll
    assert s.run().full_scan is False
    s.reject_to_cloud(res.pending.to_cloud)
    s.run(force_full=True)
    assert len(list((root / "2025/11").iterdir())) == 5


def test_missing_folder_stops_sync(env, tmp_path):
    s, r, root, trashed = env
    r.add("A1", "IMG_0001.JPG", b"x")
    s.run()
    os.rename(root, tmp_path / "drugo")
    with pytest.raises(eng.SyncError):
        s.run()
    assert r.deleted == []


def test_adopts_existing_identical_file(env):
    s, r, root, _ = env
    (root / "2025/11").mkdir(parents=True)
    (root / "2025/11/IMG_0001.JPG").write_bytes(b"same")
    ts = datetime(2025, 11, 3, 10, tzinfo=timezone.utc).timestamp()  # Skyfold dates each photo like this
    os.utime(root / "2025/11/IMG_0001.JPG", (ts, ts))
    r.add("A1", "IMG_0001.JPG", b"same")
    res = s.run()
    assert res.downloaded == 0
    assert len(list((root / "2025/11").iterdir())) == 1


def test_same_name_and_size_but_other_date_is_not_adopted(env):
    """Someone's own file that happens to share name and size is never taken for the photo."""
    s, r, root, trashed = env
    (root / "2025/11").mkdir(parents=True)
    (root / "2025/11/IMG_0001.JPG").write_bytes(b"PC!")
    r.add("A1", "IMG_0001.JPG", b"WEB")
    s.run()
    assert (root / "2025/11/IMG_0001.JPG").read_bytes() == b"PC!"
    r.assets.pop("A1", None)
    s.run(force_full=True)
    assert (root / "2025/11/IMG_0001.JPG").read_bytes() == b"PC!" and "IMG_0001.JPG" not in trashed


def test_pc_delete_disabled_keeps_icloud(env):
    s, r, root, _ = env
    s.settings.delete_pc_to_cloud = False
    r.add("A1", "IMG_0001.JPG", b"x")
    s.run()
    (root / "2025/11/IMG_0001.JPG").unlink()
    s.run()
    s.run(force_full=True)
    assert r.deleted == [] and not (root / "2025/11/IMG_0001.JPG").exists()


# incremental (change feed) -------------------------------------------------------------
def test_everyday_sync_reads_only_changes(env):
    s, r, root, trashed = env
    for i in range(50):
        r.add(f"A{i}", f"IMG_{i}.JPG", b"x")
    assert s.run().full_scan is True  # first run reads everything
    assert r.full_reads == 1
    r.add("NEW", "IMG_NEW.JPG", b"n")
    r.remove("A3")
    res = s.run()
    assert res.full_scan is False and r.full_reads == 1
    assert res.downloaded == 1 and res.deleted_local == 1
    assert (root / "2025/11/IMG_NEW.JPG").exists() and trashed == ["IMG_3.JPG"]


def test_pc_delete_uses_lookup_not_full_read(env):
    s, r, root, _ = env
    r.add("A1", "IMG_1.JPG", b"x")
    r.add("A2", "IMG_2.JPG", b"y")
    s.run()
    (root / "2025/11/IMG_1.JPG").unlink()
    res = s.run()
    assert r.deleted == ["A1"] and res.full_scan is False and r.lookups == 1 and r.full_reads == 1


def test_expired_change_token_falls_back_to_full_read(env):
    s, r, root, _ = env
    r.add("A1", "IMG_1.JPG", b"x")
    s.run()
    r.add("A2", "IMG_2.JPG", b"y")
    r.oldest_token = 99
    res = s.run()
    assert res.full_scan is True and res.downloaded == 1


def test_daily_full_check_and_manual_full_check(env):
    s, r, root, _ = env
    r.add("A1", "IMG_1.JPG", b"x")
    s.run()
    assert s.run(force_full=True).full_scan is True
    s.state.set("last_full_scan", "2000-01-01T00:00:00+00:00")
    assert s.run().full_scan is True
    assert s.run().full_scan is False


def test_photo_added_then_deleted_between_syncs_is_ignored(env):
    s, r, root, trashed = env
    r.add("A1", "IMG_1.JPG", b"x")
    s.run()
    r.add("TMP", "IMG_TMP.JPG", b"t")
    r.remove("TMP")
    res = s.run()
    assert res.downloaded == 0 and res.deleted_local == 0 and trashed == []


def test_flat_layout(env):
    s, r, root, _ = env
    s.settings.folder_structure = "flat"
    r.add("A1", "IMG_1.JPG", b"x")
    s.run()
    assert (root / "IMG_1.JPG").exists()


# album folders --------------------------------------------------------------------------
def test_albums_are_hard_links(env):
    s, r, root, _ = env
    r.add("A1", "IMG_1.JPG", b"one")
    r.add("A2", "IMG_2.JPG", b"two")
    r.album_map = {"Odmor/Rim": ["A1"], "Porodica": ["A1", "A2"]}
    s.run()
    link = root / "Albums/Odmor/Rim/IMG_1.JPG"
    assert link.read_bytes() == b"one"
    assert os.stat(link).st_ino == os.stat(root / "2025/11/IMG_1.JPG").st_ino  # same file, no extra space
    assert (root / "Albums/Porodica/IMG_2.JPG").exists()


def test_deleting_original_still_deletes_in_icloud_despite_album_copy(env):
    s, r, root, _ = env
    r.add("A1", "IMG_1.JPG", b"one")
    r.add("A2", "IMG_2.JPG", b"two")
    r.album_map = {"Best": ["A1"]}
    s.run()
    (root / "2025/11/IMG_1.JPG").unlink()
    s.run()
    assert r.deleted == ["A1"]
    assert not (root / "Albums/Best/IMG_1.JPG").exists()


def test_photo_deleted_in_icloud_leaves_album_folder(env):
    s, r, root, trashed = env
    r.add("A1", "IMG_1.JPG", b"one")
    r.album_map = {"Best": ["A1"]}
    s.run()
    r.remove("A1")
    s.run()
    assert not (root / "Albums").exists()


def test_albums_off_removes_links(env):
    s, r, root, _ = env
    r.add("A1", "IMG_1.JPG", b"one")
    r.album_map = {"Best": ["A1"]}
    s.run()
    s.settings.albums_as_folders = False
    s.run(force_full=True)
    assert not (root / "Albums").exists() and (root / "2025/11/IMG_1.JPG").exists()


def test_photos_stop_when_disk_would_be_full(env, monkeypatch):
    import shutil
    from collections import namedtuple

    s, r, root, _ = env
    r.add("A1", "IMG_1.JPG", b"x" * 1000)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: usage(10, 10, eng.DISK_RESERVE + 500))
    with pytest.raises(eng.SyncError, match="disk space"):
        s.run()
    assert not (root / "2025/11/IMG_1.JPG").exists()


def test_rebuilt_database_recognises_same_name_photos(env, tmp_path):
    """Regression: after the sync database was rebuilt, a second iCloud photo with the same file
    name was downloaded again as name_<id>_2 instead of being recognised as name_<id>."""
    s, r, root, _ = env
    r.add("AAAAAA1", "IMG_1537.HEIC", b"same bytes")
    r.add("BBBBBB2", "IMG_1537.HEIC", b"same bytes")
    s.run()
    files = sorted(p.name for p in (root / "2025/11").iterdir())
    s2 = eng.Syncer(State(tmp_path / "fresh.db"), r, s.settings)  # empty database, same folder
    res = s2.run()
    assert res.downloaded == 0
    assert sorted(p.name for p in (root / "2025/11").iterdir()) == files


def test_transfer_text_shows_files_size_speed_and_time_left():
    from skyfold.engine import duration_text, transfer_text
    from skyfold.ui_text import Rate

    now = [0.0]
    rate = Rate(clock=lambda: now[0])
    rate.add(0, 0)
    now[0] = 10.0
    rate.add(20_000_000, 100)  # 2 MB/s, 10 files/s
    text = transfer_text("Drive: ", 100, 1100, 20_000_000, 60_000_000, rate)
    assert "100 of 1,100 files" in text and "20.0 MB of 60.0 MB" in text and "2.0 MB/s" in text
    # bytes say 20 s, files say 100 s: the longer estimate wins
    assert "about 2 min left" in text
    assert duration_text(30) == "less than a minute"
    assert duration_text(80 * 60) == "about 1 h 20 min"
    assert duration_text(12 * 3600) == "about 12 h"


def test_check_time_is_recorded_even_when_nothing_changed(env):
    s, r, root, _ = env
    r.add("A1", "IMG_0001.JPG", b"x")
    s.run()
    s.state.set("last_sync", "2000-01-01T00:00:00+00:00")
    assert s.run().full_scan is False
    assert s.state.get("last_sync") != "2000-01-01T00:00:00+00:00"


def test_throttle_spaces_out_downloads(monkeypatch):
    from skyfold import engine as e

    clock = [100.0]
    slept = []
    monkeypatch.setattr(e.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(e.time, "sleep", lambda s: slept.append(s))
    th = e.Throttle()
    th.take(5_000_000)
    assert slept == []  # no limit set
    th.rate = 1_000_000  # 1 MB/s
    th.take(1_000_000)
    th.take(1_000_000)
    th.take(1_000_000)
    assert sum(slept) == 3.0 and max(slept) <= 1.0  # 2nd MB waits 1 s, 3rd 2 s, in short steps
    th.rate = 2_000_000  # a new limit starts fresh
    slept.clear()
    th.take(1_000_000)
    assert slept == []
