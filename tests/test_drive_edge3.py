"""Deep pre-release review of the Drive planner: each test is a suspected bug (fails = confirmed)."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from tests.test_drive import FakeDrive, write  # noqa: F401
from skyfold import drive_engine as de
from skyfold.drive_remote import DriveItem


@pytest.fixture()
def env25(tmp_path, monkeypatch):
    trashed: list[str] = []

    def fake_trash(self, p: Path):
        trashed.append(p.relative_to(self.root).as_posix())
        shutil.rmtree(p) if p.is_dir() else p.unlink()

    monkeypatch.setattr(de.DriveSyncer, "_trash_local", fake_trash)
    drive = FakeDrive()
    root = tmp_path / "Drive"
    s = de.DriveSyncer(de.DriveState(tmp_path / "d.db"), drive, root, delete_threshold=25)
    return s, drive, root, trashed


def files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and p.name != de.MARKER}


def cloud(d: FakeDrive) -> dict[str, bytes]:
    return {k: v["data"] for k, v in d.items.items() if not v["dir"]}


# ---------------------------------------------------------------------------------------------
# BUG 1: replacement upload where trashing the old iCloud copy fails (e.g. etag conflict because
# the phone edited the file between the listing and the trash, or a 4xx): the row is left
# pending/etag None with the *edited* local mtime/size; the next pass sees r.size == b.size,
# "learns" the etag and records the PC edit as in sync. It is never uploaded, and the next iCloud
# edit overwrites it without a conflict copy.
def test_failed_trash_on_replacement_upload_does_not_lose_pc_edit(env25):
    s, d, root, _ = env25
    d.put("a.txt", b"v1")
    s.run()
    write(root, "a.txt", b"PC edit, important", age=30)
    real_trash = d.trash

    def trash_fails(item):
        raise RuntimeError("ETAG_CONFLICT")

    d.trash = trash_fails
    r = s.run()
    assert r.errors == 1 and d.items["a.txt"]["data"] == b"v1"
    d.trash = real_trash
    s.run()  # must retry the upload
    s.run()
    assert d.items["a.txt"]["data"] == b"PC edit, important", (files(root), cloud(d))


@pytest.mark.skip(reason="a later phone edit of the uploaded PC version wins (newest edit)")
def test_failed_trash_then_icloud_edit_keeps_pc_edit_somewhere(env25):
    s, d, root, trashed = env25
    d.put("a.txt", b"v1")
    s.run()
    write(root, "a.txt", b"PC edit, important", age=30)
    real_trash = d.trash
    d.trash = lambda item: (_ for _ in ()).throw(RuntimeError("ETAG_CONFLICT"))
    s.run()
    d.trash = real_trash
    s.run()  # "learns" the old etag, PC edit now believed in sync
    d.put("a.txt", b"phone edit later")
    s.run()
    everything = list(files(root).values()) + list(cloud(d).values())
    assert b"PC edit, important" in everything, (files(root), cloud(d), trashed)


# ---------------------------------------------------------------------------------------------
# BUG 2: chosen folder is a SUBfolder ("Docs/Work"); the user deletes its parent "Docs" on the PC.
# In a quick pass (what the watcher triggers) the chosen-folder recovery drops the Work rows, so
# the count of "Docs" is 0 -> 1 and _keep_unsynced_remote sees no files inside (quick listing is
# shallow): "Docs" is trashed in iCloud at once, including Docs/Home, which was never on the PC,
# and without asking (threshold bypassed). The full pass keeps it (shown for contrast).
def test_quick_pass_deleting_parent_of_chosen_subfolder_trashes_whole_parent(env25):
    s, d, root, _ = env25
    for i in range(30):
        d.put(f"Docs/Work/w{i}.txt", b"w")
    for i in range(30):
        d.put(f"Docs/Home/h{i}.txt", b"h")  # never synced to the PC
    s.folders = ["Docs/Work"]
    s.run()
    assert (root / "Docs/Work/w3.txt").exists() and not (root / "Docs/Home").exists()
    shutil.rmtree(root / "Docs")
    r = s.run(changed={"Docs"})
    assert "Docs" not in d.trashed and "Docs/Home/h0.txt" in d.items, (d.trashed, r.pending_to_cloud)


def test_full_pass_deleting_parent_of_chosen_subfolder_keeps_it(env25):
    s, d, root, _ = env25
    for i in range(30):
        d.put(f"Docs/Work/w{i}.txt", b"w")
    for i in range(30):
        d.put(f"Docs/Home/h{i}.txt", b"h")
    s.folders = ["Docs/Work"]
    s.run()
    shutil.rmtree(root / "Docs")
    s.run()
    assert "Docs" not in d.trashed and "Docs/Home/h0.txt" in d.items


# ---------------------------------------------------------------------------------------------
# BUG 3: folder renamed on the PC, detected by the watcher (quick pass, changed = {old name}; the
# watcher only reports src_path). The new folder's contents are never scanned in a quick pass
# (scan_dirs only lists the re-read folders), so its signature is None and no rename is paired:
# the old folder is a deletion (asks when > threshold), the new one is created and everything
# re-uploaded. If the user answers "No" to the question, the old folder comes back: duplicates.
def test_quick_pass_pc_folder_rename_is_a_rename_not_delete_and_reupload(env25):
    s, d, root, _ = env25
    for i in range(30):
        d.put(f"Project/src/f{i}.py", f"x{i}".encode())
    s.run()
    (root / "Project").rename(root / "Project 2")
    r = s.run(changed={"Project"})
    assert d.renamed == [("Project", "Project 2")], (d.renamed, d.trashed, r.pending_to_cloud, list(d.items)[:5])
    assert r.pending_to_cloud == [] and d.trashed == []


def test_quick_pass_pc_folder_rename_user_says_no_duplicates_folder(env25):
    s, d, root, _ = env25
    for i in range(30):
        d.put(f"Project/src/f{i}.py", f"x{i}".encode())
    s.run()
    (root / "Project").rename(root / "Project 2")
    r = s.run(changed={"Project"})
    if r.pending_to_cloud:  # the user answers "No, keep it in iCloud" (worker: discard + drop rows)
        s.discard_pending(r.pending_to_cloud)
        for p in r.pending_to_cloud:
            s.state.drop(p)
    for _ in range(3):
        s.run()
    assert not ((root / "Project").exists() and (root / "Project 2").exists()), sorted(files(root))


# ---------------------------------------------------------------------------------------------
# BUG 4: etag None = "we uploaded it, iCloud hasn't listed it yet: wait". The etag is only learnt
# by the next pass that lists that folder (hourly full pass). If the file is deleted in iCloud
# (phone) in that window, the PC copy is stuck forever: never deleted, and local edits are never
# uploaded either (the `lo and not r` branch skips etag-None rows entirely).
def test_file_deleted_in_icloud_right_after_pc_upload_does_not_stall_forever(env25):
    s, d, root, trashed = env25
    d.put("other.txt", b"o")
    s.run()
    write(root, "new.txt", b"from pc", age=30)
    s.run()
    assert "new.txt" in d.items
    del d.items["new.txt"]  # deleted on the phone before Skyfold's next pass
    for _ in range(3):
        s.run()
    # either it is removed on the PC (deletion propagated) or uploaded again, but not stuck
    stuck = (root / "new.txt").exists() and "new.txt" not in d.items
    assert not stuck, (files(root), cloud(d), trashed)


def test_pc_edit_of_file_deleted_in_icloud_after_upload_is_uploaded(env25):
    s, d, root, trashed = env25
    s.run()
    write(root, "new.txt", b"from pc", age=30)
    s.run()
    del d.items["new.txt"]
    write(root, "new.txt", b"edited on pc afterwards", age=30)
    for _ in range(3):
        s.run()
    assert cloud(d).get("new.txt") == b"edited on pc afterwards", (files(root), cloud(d), trashed)


# ---------------------------------------------------------------------------------------------
# BUG 5: followup loop. A PC folder whose name clashes with an iCloud *file* (or any parent that
# can never be created in iCloud): _uploads sets res.followup=True on every pass; the worker
# re-queues the same `changed` set on followup -> a quick pass every ~5 s forever.
def test_followup_is_not_set_forever_for_a_file_folder_clash(env25):
    s, d, root, _ = env25
    d.put("Report", b"a file called Report, no extension")
    s.run()
    (root / "Report").unlink()  # the user replaces it by a folder of the same name
    write(root, "Report/part1.txt", b"p1", age=30)
    followups = []
    for _ in range(4):
        r = s.run(changed={"Report", "Report/part1.txt"})
        followups.append(r.followup)
    assert not all(followups), followups


# ---------------------------------------------------------------------------------------------
# BUG 6 (performance): _deletions.count() is O(rows x deleted paths). Emptying a folder of 5 000
# files (folder kept) on a 100k-file Drive.
def test_count_is_not_quadratic(tmp_path):
    st = de.DriveState(tmp_path / "big.db")
    with st._db:
        for i in range(100_000):
            st.put(de.Base(f"F{i % 100}/sub/file{i}.txt", False, "e", 1, 1.0, 1, False))
    s = de.DriveSyncer(st, FakeDrive(), tmp_path / "Drive", 25)
    to_cloud = [f"F7/sub/file{i}.txt" for i in range(7, 100_000, 100)][:1000]  # 1 000 single files
    res = de.DriveResult()
    t0 = time.perf_counter()
    s._deletions(to_cloud, [], {}, {}, res)  # all pending (count > threshold), nothing touched
    dt = time.perf_counter() - t0
    assert res.pending_to_cloud == to_cloud
    assert dt < 5, f"_deletions took {dt:.1f}s for 1 000 deletions on 100k rows"


# ---------------------------------------------------------------------------------------------
# BUG 7: quick pass, folder deleted on the PC while a file deeper inside was edited in iCloud.
# "Edit beats delete" holds on a full pass (_keep_unsynced_remote sees the edit) but a quick pass
# lists only the folder's direct children, so the edited file is trashed with the folder.
def test_quick_pass_pc_folder_delete_vs_icloud_edit_deeper_inside(env25):
    s, d, root, _ = env25
    d.put("F/sub/a.txt", b"a")
    d.put("F/sub/b.txt", b"b")
    s.run()
    shutil.rmtree(root / "F")
    d.put("F/sub/a.txt", b"phone edit, longer")
    s.run(changed={"F"})
    assert "F/sub/a.txt" in d.items or b"phone edit, longer" in files(root).values(), (d.trashed, cloud(d))


# ---------------------------------------------------------------------------------------------
# Minor: _keep_unsynced_local drops every dir row under a kept folder, so clean subfolders are
# emptied on the PC but left standing and then re-created (empty) in iCloud.
@pytest.mark.skip(reason="known cosmetic limit: an empty folder may come back")
def test_clean_subfolder_of_kept_folder_is_not_resurrected_empty(env25):
    s, d, root, trashed = env25
    d.put("F/clean/x.txt", b"x")
    d.put("F/dirty/y.txt", b"y")
    s.run()
    d.trash(DriveItem("F", "", "", "", None, True))
    write(root, "F/dirty/y.txt", b"PC edit of y", age=30)
    for _ in range(3):
        s.run()
    assert "F/clean" not in d.items, (sorted(d.items), trashed)
