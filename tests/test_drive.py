"""iCloud Drive sync against a fake Drive (no network)."""

from __future__ import annotations

import os
import time
from pathlib import Path, PurePosixPath

import pytest

from skyfold import drive_engine as de
from skyfold.drive_remote import DriveItem


class FakeDrive:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}  # path -> {"data": bytes|None, "etag": int, "dir": bool}
        self.n = 0
        self.trashed: list[str] = []
        self.renamed: list[tuple[str, str]] = []
        self.uploads: list[str] = []
        self.full_walks = 0
        self.quick_walks = 0

    def _etag(self) -> str:
        self.n += 1
        return f"e{self.n}"

    def put(self, path: str, data: bytes | None) -> None:
        parts = PurePosixPath(path).parts
        for i in range(1, len(parts)):
            d = "/".join(parts[:i])
            self.items.setdefault(d, {"data": None, "etag": self._etag(), "dir": True})
        self.items[path] = {"data": data, "etag": self._etag(), "dir": data is None}

    def _item(self, p):
        it = self.items[p]
        return DriveItem(p, p, p, "z", it["etag"], it["dir"], len(it["data"] or b""), 1_600_000_000.0)

    def walk(self, on_progress=None, folders=None, want=None):
        if folders is None:
            self.full_walks += 1
            out = {"": DriveItem("", "root", "root", "z", None, True)}
            out.update({p: self._item(p) for p in self.items})
            return out
        self.quick_walks += 1
        out = {f.path: f for f in folders}
        dirs = {f.path for f in folders}
        for p in self.items:
            parent = str(PurePosixPath(p).parent) if "/" in p else ""
            if parent in dirs:
                out[p] = self._item(p)
        return out

    def download(self, item, dest: Path, on_bytes):
        data = self.items[item.path]["data"]
        dest.write_bytes(data)
        on_bytes(len(data))

    def upload(self, parent, local: Path, data=None):
        path = f"{parent.path}/{local.name}" if parent.path else local.name
        self.uploads.append(path)
        self.put(path, local.read_bytes())

    def mkdir(self, parent, name):
        self.put(f"{parent.path}/{name}" if parent.path else name, None)

    def trash(self, item):
        self.trashed.append(item.path)
        for p in [p for p in self.items if p == item.path or p.startswith(item.path + "/")]:
            del self.items[p]

    def _relocate(self, old: str, new: str) -> None:
        for p in [p for p in self.items if p == old or p.startswith(old + "/")]:
            it = self.items.pop(p)
            it["etag"] = self._etag()
            self.items[new + p[len(old):]] = it
        self.renamed.append((old, new))

    def rename(self, item, new_name):
        parent = item.path.rsplit("/", 1)[0] if "/" in item.path else ""
        self._relocate(item.path, f"{parent}/{new_name}" if parent else new_name)
        return None

    def move(self, item, folder):
        name = item.path.rsplit("/", 1)[-1]
        new = f"{folder.path}/{name}" if folder.path else name
        self._relocate(item.path, new)
        return None


def write(root: Path, rel: str, data: bytes, age: float = 60) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    ts = time.time() - age
    os.utime(p, (ts, ts))
    return p


@pytest.fixture()
def env(tmp_path, monkeypatch):
    trashed: list[str] = []

    def fake_trash(self, p: Path):
        trashed.append(p.relative_to(self.root).as_posix())
        if p.is_dir():
            import shutil
            shutil.rmtree(p)
        else:
            p.unlink()

    monkeypatch.setattr(de.DriveSyncer, "_trash_local", fake_trash)
    drive = FakeDrive()
    root = tmp_path / "Drive"
    s = de.DriveSyncer(de.DriveState(tmp_path / "d.db"), drive, root, delete_threshold=3)
    return s, drive, root, trashed


def test_first_sync_downloads_tree(env):
    s, d, root, _ = env
    d.put("Docs/a.txt", b"hello")
    d.put("Docs/Sub/b.txt", b"x")
    d.put("Empty", None)
    r = s.run()
    assert r.downloaded == 2 and r.errors == 0
    assert (root / "Docs/a.txt").read_bytes() == b"hello"
    assert (root / "Docs/Sub/b.txt").exists() and (root / "Empty").is_dir()
    r = s.run()
    assert (r.downloaded, r.uploaded) == (0, 0)


def test_new_pc_file_and_folder_are_uploaded(env):
    s, d, root, _ = env
    d.put("Docs/a.txt", b"a")
    s.run()
    write(root, "Docs/new.txt", b"new")
    (root / "Fresh").mkdir()
    write(root, "Fresh/inside.txt", b"in")
    r = s.run()
    assert "Docs/new.txt" in d.uploads and "Fresh" in d.items
    s.run()  # file inside a brand-new folder follows on the next run
    assert d.items["Fresh/inside.txt"]["data"] == b"in"
    r = s.run()
    assert (r.downloaded, r.uploaded) == (0, 0)


def test_changed_in_icloud_is_downloaded(env):
    s, d, root, _ = env
    d.put("a.txt", b"v1")
    s.run()
    d.put("a.txt", b"version2")
    r = s.run()
    assert r.downloaded == 1 and (root / "a.txt").read_bytes() == b"version2"


def test_changed_on_pc_replaces_icloud_copy(env):
    s, d, root, _ = env
    d.put("a.txt", b"v1")
    s.run()
    write(root, "a.txt", b"edited on pc", age=30)
    r = s.run()
    assert r.uploaded == 1 and d.trashed == ["a.txt"] and d.items["a.txt"]["data"] == b"edited on pc"
    r = s.run()
    assert (r.downloaded, r.uploaded) == (0, 0)


def test_file_still_being_written_waits(env):
    s, d, root, _ = env
    s.run()
    write(root, "busy.docx", b"...", age=0)
    assert s.run().uploaded == 0
    write(root, "busy.docx", b"...", age=60)
    assert s.run().uploaded == 1


def test_conflict_keeps_both(env):
    s, d, root, _ = env
    d.put("a.txt", b"base")
    s.run()
    d.put("a.txt", b"icloud edit")
    write(root, "a.txt", b"pc edit!", age=30)
    r = s.run()
    assert r.conflicts == 1 and (root / "a.txt").read_bytes() == b"icloud edit"
    copies = [p.name for p in root.iterdir() if "PC conflict" in p.name]
    assert len(copies) == 1
    s.run()
    assert any("PC conflict" in p for p in d.items)  # the PC version reaches iCloud too


def test_deleted_on_pc_goes_to_icloud_trash(env):
    s, d, root, _ = env
    d.put("a.txt", b"a")
    d.put("b.txt", b"b")
    s.run()
    (root / "a.txt").unlink()
    r = s.run()
    assert r.deleted_cloud == 1 and d.trashed == ["a.txt"] and "b.txt" in d.items


def test_deleted_in_icloud_goes_to_recycle_bin(env):
    s, d, root, trashed = env
    d.put("Docs/a.txt", b"a")
    s.run()
    d.trash(DriveItem("Docs/a.txt", "", "", "", None, False))
    r = s.run()
    assert r.deleted_local == 1 and trashed == ["Docs/a.txt"]


def test_folder_deleted_on_pc_trashes_folder_once(env):
    s, d, root, _ = env
    d.put("F/1.txt", b"1")
    d.put("F/2.txt", b"2")
    s.run()
    import shutil
    shutil.rmtree(root / "F")
    r = s.run()
    assert d.trashed == ["F"] and r.errors == 0


def test_mass_delete_waits_for_ok(env):
    s, d, root, trashed = env
    for i in range(6):
        d.put(f"F/{i}.txt", b"x")
    s.run()
    for i in range(6):
        d.trash(DriveItem(f"F/{i}.txt", "", "", "", None, False))
    r = s.run()
    assert len(r.pending_to_pc) == 6 and trashed == []
    s.approved.update(r.pending_to_pc)
    r = s.run()
    assert r.deleted_local == 6


def test_ignored_files_are_not_uploaded(env):
    s, d, root, _ = env
    s.run()
    write(root, "desktop.ini", b"x")
    write(root, "~$report.docx", b"x")
    write(root, "Thumbs.db", b"x")
    assert s.run().uploaded == 0


def test_same_file_on_both_sides_is_adopted(env):
    s, d, root, _ = env
    write(root, "a.txt", b"same")
    os.utime(root / "a.txt", (1_600_000_000, 1_600_000_000))  # the fake's iCloud time
    d.put("a.txt", b"same")
    r = s.run()
    assert (r.downloaded, r.uploaded, r.conflicts) == (0, 0, 0)


def test_same_size_but_other_time_on_first_sync_keeps_both(env):
    s, d, root, _ = env
    write(root, "a.txt", b"mine")
    d.put("a.txt", b"them")
    r = s.run()
    assert r.conflicts == 1 and (root / "a.txt").read_bytes() == b"them"


def test_missing_drive_folder_stops(env, tmp_path):
    s, d, root, _ = env
    d.put("a.txt", b"a")
    s.run()
    os.rename(root, tmp_path / "elsewhere")
    with pytest.raises(de.SyncError):
        s.run()
    assert d.trashed == []


def test_failed_replacement_upload_is_retried_not_deleted(env):
    s, d, root, trashed = env
    d.put("a.txt", b"v1")
    s.run()
    write(root, "a.txt", b"edited", age=30)
    real_upload = d.upload

    def boom(parent, local):
        raise OSError("network dropped")

    d.upload = boom
    r = s.run()
    assert r.errors == 1 and d.trashed == ["a.txt"] and "a.txt" not in d.items
    d.upload = real_upload
    r = s.run()  # iCloud copy is gone, but the PC file must be uploaded, not deleted
    assert trashed == [] and r.uploaded == 1 and d.items["a.txt"]["data"] == b"edited"


def test_quick_sync_after_pc_changes_reads_only_those_folders(env):
    s, d, root, _ = env
    d.put("Docs/a.txt", b"a")
    d.put("Docs/b.txt", b"b")
    d.put("Other/c.txt", b"c")
    s.run()
    assert d.full_walks == 1
    write(root, "Docs/new.txt", b"new")
    (root / "Docs/b.txt").unlink()
    write(root, "Docs/a.txt", b"edited a", age=30)
    r = s.run(changed={"Docs/new.txt", "Docs/b.txt", "Docs/a.txt"})
    assert d.full_walks == 1 and d.quick_walks == 1 and not r.full
    assert d.items["Docs/new.txt"]["data"] == b"new" and "Docs/b.txt" not in d.items
    assert d.items["Docs/a.txt"]["data"] == b"edited a"
    assert "Other/c.txt" in d.items and (root / "Other/c.txt").exists()  # untouched folder stays as is


def test_quick_sync_new_folder_needs_followup(env):
    s, d, root, _ = env
    d.put("Docs/a.txt", b"a")
    s.run()
    write(root, "Docs/New/x.txt", b"x")
    r = s.run(changed={"Docs/New", "Docs/New/x.txt"})
    assert "Docs/New" in d.items and r.followup
    s.run(changed={"Docs/New", "Docs/New/x.txt"})
    assert d.items["Docs/New/x.txt"]["data"] == b"x"
    assert d.full_walks == 1


def test_quick_sync_without_a_full_read_falls_back_to_full(env):
    s, d, root, _ = env
    d.put("a.txt", b"a")
    r = s.run(changed={"a.txt"})
    assert r.full and d.full_walks == 1


def test_cache_survives_restart_so_quick_sync_works(env, tmp_path):
    s, d, root, _ = env
    cache = tmp_path / "cache.json"
    s.cache_file = cache
    d.put("Docs/a.txt", b"a")
    s.run()
    assert cache.exists()
    s2 = de.DriveSyncer(s.state, d, root, 3, cache_file=cache)  # e.g. after the app restarted
    write(root, "Docs/b.txt", b"b")
    r = s2.run(changed={"Docs/b.txt"})
    assert not r.full and d.full_walks == 1 and d.items["Docs/b.txt"]["data"] == b"b"


def test_drive_stops_when_disk_would_be_full(env, monkeypatch):
    import shutil
    from collections import namedtuple

    from skyfold import engine as eng

    s, d, root, _ = env
    d.put("big.bin", b"x" * 5000)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: usage(10, 10, eng.DISK_RESERVE + 100))
    with pytest.raises(de.SyncError, match="disk space"):
        s.run()
    assert not (root / "big.bin").exists()


def test_many_files_download_in_parallel(env):
    s, d, root, _ = env
    for i in range(40):
        d.put(f"Many/f{i}.txt", f"file {i}".encode())
    r = s.run()
    assert r.downloaded == 40 and r.errors == 0
    assert all((root / f"Many/f{i}.txt").read_bytes() == f"file {i}".encode() for i in range(40))
    assert (s.run().downloaded, s.run().uploaded) == (0, 0)


def test_mac_package_stays_in_icloud_without_errors(env):
    s, d, root, _ = env
    d.put("App/Proj.xcodeproj", b"zip")
    d.put("App/main.swift", b"code")
    real = d.download

    def download(item, dest, on_bytes):
        if item.path.endswith(".xcodeproj"):
            raise de.MacPackage(item.path)
        real(item, dest, on_bytes)

    d.download = download
    r = s.run()
    assert (r.downloaded, r.errors) == (1, 0)
    assert not (root / "App/Proj.xcodeproj").exists()
    r = s.run()  # not retried, never deleted in iCloud, never uploaded
    assert (r.downloaded, r.errors, d.trashed) == (0, 0, [])
    assert "App/Proj.xcodeproj" in d.items


def test_names_windows_cannot_store_stay_in_icloud(env):
    s, d, root, _ = env
    d.put("Docs/what?.txt", b"q")
    d.put("Docs/AUX.txt", b"r")
    d.put("Bad: folder/in.txt", b"x")
    d.put("Docs/ok.txt", b"fine")
    r = s.run()
    assert (r.downloaded, r.errors) == (1, 0)
    r = s.run()
    assert (r.downloaded, r.uploaded, r.errors, d.trashed) == (0, 0, 0, [])
    assert de.windows_name_problem("a/" + "x" * 300, root)
    assert de.windows_name_problem("Docs/ok.txt", root) is None


def test_package_that_icloud_sends_as_zip_is_recognised(env):
    s, d, root, _ = env
    d.put("App/Proj.xcodeproj", b"x" * 500)  # iCloud lists the package's own size...
    real = d.download

    def download(item, dest, on_bytes):
        if item.path.endswith(".xcodeproj"):
            dest.write_bytes(b"PKzipped")  # ...but delivers a zip of it
            return
        real(item, dest, on_bytes)

    d.download = download
    r = s.run()
    assert (r.downloaded, r.errors) == (0, 0)
    assert not (root / "App/Proj.xcodeproj").exists() and "App/Proj.xcodeproj" in s.mac_packages


def test_transient_icloud_errors_are_retried(monkeypatch):
    import requests
    from skyfold import drive_remote as dr

    monkeypatch.setattr(dr.time, "sleep", lambda _s: None)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise requests.exceptions.ReadTimeout("read timed out")
        return "ok"

    assert dr.with_retries(flaky) == "ok" and len(calls) == 3

    def broken():
        raise ValueError("not a network problem")

    with pytest.raises(ValueError):
        dr.with_retries(broken)


def test_big_deletion_is_asked_right_away_and_done_once_approved(env):
    s, d, root, trashed = env
    for i in range(5):
        d.put(f"Old/f{i}.txt", b"x")
    s.run()
    for p in [p for p in list(d.items) if p.startswith("Old")]:
        del d.items[p]
    for i in range(3):
        d.put(f"New/n{i}.txt", b"new")
    asked = []

    def on_pending(to_cloud, to_pc):
        asked.append((to_cloud, to_pc, (root / "New/n0.txt").exists()))
        s.approved.update(to_pc)  # the user says yes while downloads are still to come

    s.on_pending = on_pending
    r = s.run()
    assert asked and asked[0][1] == ["Old"] and asked[0][2] is False  # asked before downloading
    assert trashed == ["Old"] and r.pending_to_pc == [] and r.downloaded == 3


def test_rejected_deletion_does_not_come_back_in_the_same_pass(env):
    s, d, root, trashed = env
    for i in range(5):
        d.put(f"Old/f{i}.txt", b"x")
    s.run()
    for p in [p for p in list(d.items) if p.startswith("Old")]:
        del d.items[p]
    s.on_pending = lambda to_cloud, to_pc: s.discard_pending(to_pc)
    r = s.run()
    assert r.pending_to_pc == [] and trashed == []


def test_only_chosen_folders_are_synced(env):
    s, d, root, trashed = env
    d.put("Photos/a.jpg", b"a")
    d.put("Photos/Trip/b.jpg", b"b")
    d.put("Coding/x.py", b"x")
    d.put("Docs/Work/w.txt", b"w")
    d.put("Docs/Home/h.txt", b"h")
    d.put("readme.txt", b"r")
    s.folders = ["Photos", "Docs/Work"]
    r = s.run()
    assert r.errors == 0
    assert (root / "Photos/Trip/b.jpg").exists() and (root / "Docs/Work/w.txt").exists()
    assert (root / "readme.txt").exists()  # files in the Drive root always sync
    assert not (root / "Coding").exists() and not (root / "Docs/Home").exists()
    # a PC folder outside the choice is never uploaded, and nothing in iCloud is touched
    write(root, "Private/p.txt", b"p")
    r = s.run()
    assert (r.uploaded, r.downloaded, d.trashed, trashed) == (0, 0, [], [])
    assert "Private" not in d.items and "Coding/x.py" in d.items


def test_unchosen_folders_are_listed_for_keep_or_recycle(tmp_path):
    root = tmp_path / "D"
    for p in ("Photos/Trip", "Docs/Work", "Docs/Home", "Coding"):
        (root / p).mkdir(parents=True)
    assert sorted(de.unsynced_local_dirs(root, None, ["Photos", "Docs/Work"])) == ["Coding", "Docs/Home"]
    assert de.unsynced_local_dirs(root, ["Photos"], None) == []
    assert de.unsynced_local_dirs(root, ["Photos", "Coding"], ["Photos"]) == ["Coding"]


def test_folder_sizes_from_cache(tmp_path):
    import json
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"items": [
        {"path": "Photos", "is_dir": True, "size": 0},
        {"path": "Photos/a.jpg", "is_dir": False, "size": 10},
        {"path": "Photos/Trip/b.jpg", "is_dir": False, "size": 5}]}), encoding="utf-8")
    sizes = de.folder_sizes(f)
    assert sizes["Photos"] == (2, 15) and sizes["Photos/Trip"] == (1, 5)



def test_file_renamed_on_pc_is_renamed_in_icloud(env):
    s, d, root, trashed = env
    d.put("Docs/old name.txt", b"content")
    s.run()
    (root / "Docs/old name.txt").rename(root / "Docs/new name.txt")
    r = s.run()
    assert d.renamed == [("Docs/old name.txt", "Docs/new name.txt")]
    assert (r.uploaded, d.trashed, r.moved) == (0, [], 1)
    r = s.run()  # settles: nothing left to do
    assert (r.uploaded, r.downloaded, d.trashed) == (0, 0, [])


def test_file_moved_on_pc_is_moved_in_icloud(env):
    s, d, root, _ = env
    d.put("A/f.txt", b"abc")
    d.put("B/keep.txt", b"k")
    s.run()
    (root / "A/f.txt").rename(root / "B/f.txt")
    s.run()
    assert "B/f.txt" in d.items and "A/f.txt" not in d.items and d.trashed == []


def test_folder_renamed_on_pc_is_renamed_in_icloud(env):
    s, d, root, _ = env
    for i in range(5):
        d.put(f"Project/src/f{i}.py", f"x{i}".encode())
    s.run()
    (root / "Project").rename(root / "Project 2")
    r = s.run()
    assert d.renamed == [("Project", "Project 2")]
    assert (r.uploaded, d.trashed, r.pending_to_cloud) == (0, [], [])
    r = s.run()
    assert (r.uploaded, r.downloaded, d.trashed) == (0, 0, [])
    assert (root / "Project 2/src/f3.py").read_bytes() == b"x3"


def test_rename_in_icloud_is_renamed_on_pc(env):
    s, d, root, trashed = env
    d.put("Docs/a.txt", b"hello")
    for i in range(4):
        d.put(f"Folder/x{i}.txt", b"y" * i)
    s.run()
    d._relocate("Docs/a.txt", "Docs/b.txt")
    d._relocate("Folder", "Folder renamed")
    r = s.run()
    assert (r.downloaded, trashed) == (0, [])
    assert (root / "Docs/b.txt").read_bytes() == b"hello" and not (root / "Docs/a.txt").exists()
    assert (root / "Folder renamed/x3.txt").exists() and not (root / "Folder").exists()
    r = s.run()
    assert (r.uploaded, r.downloaded, trashed, d.trashed) == (0, 0, [], [])


def test_ambiguous_renames_fall_back_to_add_and_delete(env):
    s, d, root, _ = env
    d.put("a.txt", b"same")
    d.put("b.txt", b"same")
    s.run()
    for name in ("a.txt", "b.txt"):
        os.utime(root / name, (1_600_000_000, 1_600_000_000))
    s.run()
    (root / "a.txt").rename(root / "c.txt")
    (root / "b.txt").rename(root / "d.txt")
    s.run()
    assert d.renamed == []  # same size and time twice: no guessing


def test_ignored_names_in_icloud_are_never_deleted(env):
    s, d, root, _ = env
    d.put("Work/report.tmp", b"t")
    d.put("Work/cache.partial/inner.txt", b"i")
    d.put("Work/real.txt", b"r")
    s.run()
    s.run()
    assert d.trashed == [] and "Work/report.tmp" in d.items


def test_drop_is_exact_about_folder_names(tmp_path):
    st = de.DriveState(tmp_path / "x.db")
    for p in ("My_Docs/a.txt", "MyXDocs/keep.txt", "docs/keep2.txt", "Docs/b.txt"):
        st.put(de.Base(p, False, "e", 1, 1.0, 1, False))
    st.drop("My_Docs")
    st.drop("Docs")
    assert sorted(st.all()) == ["MyXDocs/keep.txt", "docs/keep2.txt"]


def test_quick_pass_still_asks_before_a_big_deletion(env):
    s, d, root, trashed = env
    for i in range(20):
        d.put(f"Big/sub/f{i}.txt", b"x")
    d.put("top.txt", b"t")
    s.run()
    for p in [p for p in list(d.items) if p.startswith("Big")]:
        del d.items[p]
    write(root, "top.txt", b"changed")
    r = s.run(changed={"top.txt"})
    assert trashed == [] and (root / "Big").exists()


def test_conflict_with_failed_download_never_trashes_icloud(env):
    s, d, root, _ = env
    d.put("a.txt", b"v1")
    s.run()
    d.put("a.txt", b"v2 from phone")
    write(root, "a.txt", b"pc edit", age=60)
    real = d.download
    d.download = lambda item, dest, cb: (_ for _ in ()).throw(RuntimeError("network"))
    s.run()
    d.download = real
    s.run()
    assert d.trashed == [] and (root / "a.txt").read_bytes() == b"v2 from phone"


@pytest.mark.skipif(os.name != "nt", reason="Windows file attributes")
def test_dot_files_are_hidden_like_on_a_mac(env):
    s, d, root, _ = env
    d.put("Shortcuts/.WorkflowHiddenFile", b"")
    d.put("Shortcuts/visible.txt", b"v")
    s.run()
    hidden = lambda p: bool(os.stat(p).st_file_attributes & 0x2)  # noqa: E731
    assert hidden(root / "Shortcuts/.WorkflowHiddenFile") and not hidden(root / "Shortcuts/visible.txt")
    r = s.run()  # an attribute isn't a change: nothing to sync
    assert (r.uploaded, r.downloaded) == (0, 0) and d.trashed == []
