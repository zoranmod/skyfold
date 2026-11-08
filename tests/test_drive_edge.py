"""Drive edge cases: edits racing deletions/renames, unreadable folders, case-only renames, clock skew."""

from __future__ import annotations

import os
import shutil
import sys
import time
import unicodedata
from pathlib import Path

import pytest

from tests.test_drive import FakeDrive, write, env  # noqa: E402,F401
from skyfold import drive_engine as de  # noqa: E402
from skyfold.drive_remote import DriveItem  # noqa: E402


@pytest.fixture()
def env25(tmp_path, monkeypatch):
    """Like env but with the app's default threshold (25)."""
    trashed: list[str] = []

    def fake_trash(self, p: Path):
        trashed.append(p.relative_to(self.root).as_posix())
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()

    monkeypatch.setattr(de.DriveSyncer, "_trash_local", fake_trash)
    drive = FakeDrive()
    root = tmp_path / "Drive"
    s = de.DriveSyncer(de.DriveState(tmp_path / "d.db"), drive, root, delete_threshold=25)
    return s, drive, root, trashed


# 1. Deleted in iCloud + edited on PC: the PC edit is trashed instead of kept/uploaded
def test_icloud_delete_vs_pc_edit_keeps_pc_edit(env25):
    s, d, root, trashed = env25
    d.put("a.txt", b"v1")
    s.run()
    d.trash(DriveItem("a.txt", "", "", "", None, False))
    write(root, "a.txt", b"important PC edit", age=30)
    s.run()
    assert trashed == [], "edited PC file was sent to the Recycle Bin"


# 2. Deleted on PC + edited in iCloud: the newer iCloud version is trashed
def test_pc_delete_vs_icloud_edit_keeps_icloud_edit(env25):
    s, d, root, trashed = env25
    d.put("a.txt", b"v1")
    s.run()
    (root / "a.txt").unlink()
    d.put("a.txt", b"important iPhone edit")
    s.run()
    assert d.trashed == [], "newer iCloud version was trashed"


# 3. iCloud edit made shortly after a PC upload is never downloaded (etag None = "learn it")
def test_icloud_edit_after_pc_upload_is_downloaded(env25):
    s, d, root, _ = env25
    s.run()
    write(root, "a.txt", b"from pc", age=30)
    s.run()
    assert d.items["a.txt"]["data"] == b"from pc"
    d.put("a.txt", b"edited on iPhone a bit later")  # before Skyfold's next pass
    s.run()
    s.run()
    assert (root / "a.txt").read_bytes() == b"edited on iPhone a bit later"


# 4. Renamed on PC while edited in iCloud: iCloud edit silently diverges
def test_pc_rename_vs_icloud_edit(env25):
    s, d, root, _ = env25
    d.put("a.txt", b"original")
    s.run()
    (root / "a.txt").rename(root / "b.txt")
    d.put("a.txt", b"iCloud edit!!")  # same name, new content and version in iCloud
    s.run()
    s.run()
    s.run()
    pc = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file() and p.name != de.MARKER}
    cloud = {k: v["data"] for k, v in d.items.items()}
    # the iCloud edit must be on the PC somewhere, and both sides must agree
    assert b"iCloud edit!!" in pc.values(), (pc, cloud)
    assert pc == cloud, (pc, cloud)


# 5. Renamed in iCloud while edited on PC: the PC edit goes to the Recycle Bin
def test_icloud_rename_vs_pc_edit(env25):
    s, d, root, trashed = env25
    d.put("a.txt", b"original")
    s.run()
    d._relocate("a.txt", "b.txt")
    write(root, "a.txt", b"PC edit, longer", age=30)
    s.run()
    assert trashed == [], "the PC edit was trashed"


# 6. Case-only folder rename on a Mac (Docs -> docs) with few files
def test_icloud_case_only_folder_rename(env25):
    s, d, root, trashed = env25
    for i in range(3):
        d.put(f"Docs/f{i}.txt", f"content {i}".encode())
    s.run()
    d._relocate("Docs", "docs")
    for _ in range(3):
        s.run()
    assert d.trashed == [], f"iCloud trashed {d.trashed}"
    assert trashed == [], f"PC trashed {trashed}"
    assert sorted(p for p in d.items if not d.items[p]["dir"]) == ["docs/f0.txt", "docs/f1.txt", "docs/f2.txt"]


# 7. Case-only file rename on a Mac (a.txt -> A.txt)
def test_icloud_case_only_file_rename(env25):
    s, d, root, trashed = env25
    d.put("a.txt", b"hello")
    s.run()
    d._relocate("a.txt", "A.txt")
    s.run()
    s.run()
    assert trashed == [] and d.trashed == []
    assert [p.name for p in root.iterdir() if p.name != de.MARKER] == ["A.txt"]


# 8. A subfolder that can't be listed (permissions, I/O hiccup) must not look deleted
def test_unreadable_subfolder_is_not_deleted_in_icloud(env25, monkeypatch):
    s, d, root, _ = env25
    d.put("Private/a.txt", b"a")
    d.put("Private/b.txt", b"b")
    d.put("ok.txt", b"o")
    s.run()
    real = os.scandir

    def scandir(p="."):
        if Path(p) == root / "Private":
            raise PermissionError(13, "Access is denied", str(p))
        return real(p)

    monkeypatch.setattr(os, "scandir", scandir)
    s.run()
    assert d.trashed == [], f"iCloud trashed {d.trashed}"


def test_unreadable_folder_in_quick_pass_is_not_deleted_in_icloud(env25, monkeypatch):
    s, d, root, _ = env25
    d.put("Private/a.txt", b"a")
    s.run()
    real = os.scandir

    def scandir(p="."):
        if Path(p) == root / "Private":
            raise PermissionError(13, "Access is denied", str(p))
        return real(p)

    monkeypatch.setattr(os, "scandir", scandir)
    s.run(changed={"Private/a.txt"})
    assert d.trashed == [], f"iCloud trashed {d.trashed}"


# 9. Two conflicts on the same file within one minute: first PC copy overwritten
def test_two_conflicts_same_minute_keep_both_pc_copies(env25):
    s, d, root, _ = env25
    d.put("a.txt", b"base")
    s.run()
    d.put("a.txt", b"icloud 1")
    write(root, "a.txt", b"PC version ONE", age=30)
    s.run()  # conflict copy #1 (not uploaded yet in this pass)
    d.put("a.txt", b"icloud 2 longer")
    write(root, "a.txt", b"PC version TWO!!", age=30)
    s.run()  # same minute -> same conflict name
    s.run()
    local = [p.read_bytes() for p in root.iterdir() if "PC conflict" in p.name]
    cloud = [v["data"] for k, v in d.items.items() if "PC conflict" in k]
    everything = local + cloud
    assert b"PC version ONE" in everything, "first PC version is gone"


# 10. Crash between putting a download in place and writing the DB -> spurious conflict copy
def test_crash_after_download_before_db_write_no_spurious_conflict(env25, monkeypatch):
    s, d, root, _ = env25
    d.put("a.txt", b"v1")
    s.run()
    d.put("a.txt", b"version 2 bigger")
    real_put = s.state.put

    def crash(b):
        if b.path == "a.txt" and b.etag == d.items["a.txt"]["etag"]:
            raise KeyboardInterrupt("power cut")
        return real_put(b)

    monkeypatch.setattr(s.state, "put", crash)
    with pytest.raises(KeyboardInterrupt):
        s.run()
    monkeypatch.setattr(s.state, "put", real_put)
    r = s.run()
    s.run()
    assert r.conflicts == 0 and not any("PC conflict" in p for p in d.items), list(d.items)


# 11. Same size, different content on first sync: silently "adopted" and never reconciled
def test_first_sync_same_size_different_content_is_not_adopted(env25):
    s, d, root, _ = env25
    write(root, "config.ini", b"AAAA")
    d.put("config.ini", b"BBBB")
    s.run()
    s.run()
    local = (root / "config.ini").read_bytes()
    copies = [p for p in root.iterdir() if "conflict" in p.name] + [p for p in d.items if "conflict" in p]
    assert local == d.items["config.ini"]["data"] or copies, "sides differ but Skyfold thinks they're synced"


# 12. Chosen folder renamed on the PC -> iCloud folder trashed
def test_chosen_folder_renamed_on_pc_not_trashed_in_icloud(env25):
    s, d, root, _ = env25
    d.put("Work/a.txt", b"a")
    s.folders = ["Work"]
    s.run()
    (root / "Work").rename(root / "Work2")
    s.run()
    assert d.trashed == [], f"iCloud trashed {d.trashed}"


# 13. Folder renamed on PC while a new file appears in it in iCloud
def test_pc_folder_rename_with_new_icloud_file(env25):
    s, d, root, trashed = env25
    for i in range(3):
        d.put(f"F/f{i}.txt", f"c{i}".encode())
    s.run()
    (root / "F").rename(root / "G")
    d.put("F/new.txt", b"new from phone")
    for _ in range(4):
        s.run()
    assert not (root / "F").exists() and "F" not in d.items, (sorted(d.items), trashed)
    assert (root / "G/new.txt").read_bytes() == b"new from phone"


# 14. Folder deleted in iCloud while the PC added a new (unsynced) file inside it
def test_icloud_folder_delete_vs_new_pc_file(env25):
    s, d, root, trashed = env25
    d.put("F/a.txt", b"a")
    s.run()
    d.trash(DriveItem("F", "", "", "", None, True))
    write(root, "F/brand new.txt", b"only on this PC", age=30)
    s.run()
    assert (root / "F/brand new.txt").exists() and "F/brand new.txt" not in trashed
    s.run()
    s.run()
    assert "F/brand new.txt" in d.items  # goes up again


# 15. Locked PC file: iCloud copy trashed before the upload even reads the file
def test_locked_file_does_not_remove_icloud_copy(env25, monkeypatch):
    s, d, root, _ = env25
    d.put("db.sqlite", b"v1")
    s.run()
    write(root, "db.sqlite", b"edited, now locked", age=30)

    real = Path.read_bytes

    def locked(self):
        if self.name == "db.sqlite":
            raise PermissionError(13, "The process cannot access the file because it is being used", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", locked)
    s.run()
    assert "db.sqlite" in d.items, "iCloud copy trashed although the PC file couldn't be read"


# 16. New PC file with an mtime in the future is never uploaded
def test_future_mtime_file_is_uploaded(env25):
    s, d, root, _ = env25
    s.run()
    write(root, "from camera.jpg", b"x", age=-3600 * 24)  # clock skew: a day ahead
    s.run()
    assert "from camera.jpg" in d.items


# "in sync", never uploaded, and the next iCloud edit silently overwrites it (no conflict copy).


def test_local_file_blocking_new_icloud_folder_does_not_stop_all_sync(env25):
    s, d, root, trashed = env25
    s.run()
    write(root, "Notes", b"a local file called Notes", age=30)
    s.run()  # uploaded
    d.put("other.txt", b"other")
    del d.items["Notes"]; d.put("notes/x.txt", b"x")  # iCloud: folder 'notes' replaces file 'Notes'
    r = s.run()   # mkdir 'notes' fails: a file 'Notes' exists (case-insensitive) -> unhandled?
    assert (root / "other.txt").exists()
