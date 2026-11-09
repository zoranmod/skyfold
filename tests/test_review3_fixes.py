"""Regression tests for the third independent (Codex) review, of 1.0.6 (commit 9cb7be4)."""

import os
import shutil
import time
from datetime import datetime, timezone

import pytest

from skyfold import auth
from skyfold import drive_engine as de
from skyfold.engine import SyncError
from tests.test_drive import env, write  # noqa: F401
from tests.test_engine import env as photos_env  # noqa: F401
from tests.test_notes import env as notes_env  # noqa: F401

windows_only = pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")


def _junction(target, link):
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


# --- Drive ---------------------------------------------------------------------------------------
@windows_only
def test_quick_pass_never_uploads_through_a_junction(env, tmp_path):  # noqa: F811
    s, d, root, _ = env
    d.put("Docs/a.txt", b"a")
    s.run()
    shutil.rmtree(root / "Docs")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_bytes(b"not for iCloud")
    _junction(outside, root / "Docs")
    s.run(changed={"Docs/private.txt"})
    s.run()
    assert "Docs/private.txt" not in d.items


def test_unmarked_old_folder_is_not_claimed_by_a_new_database(env, tmp_path):  # noqa: F811
    s, d, root, _ = env
    s.run()  # an old-style marker without an account line
    marker = root / de.MARKER
    marker.unlink()  # hidden files can't be rewritten in place on Windows
    marker.write_text("This folder is synced by Skyfold.\n", encoding="utf-8")
    write(root, "private-A.txt", b"A's file")
    other = de.DriveSyncer(de.DriveState(tmp_path / "b.db"), d, root, delete_threshold=3)
    other.account = "account-b"
    with pytest.raises(SyncError):
        other.run()
    assert "private-A.txt" not in d.items


def test_unreadable_marker_stops_the_sync(env):  # noqa: F811
    s, d, root, _ = env
    s.account = "account-a"
    s.run()
    marker = root / de.MARKER
    os.chmod(marker, 0o666)
    marker.unlink()
    marker.mkdir()  # can't be read as a file
    with pytest.raises(SyncError):
        s.run()


def test_approved_deletion_keeps_a_file_that_became_a_folder(env):  # noqa: F811
    s, d, root, trashed = env
    s.delete_threshold = 1
    for n in ("a.txt", "b.txt"):
        d.put(n, b"v1")
    s.run()
    del d.items["a.txt"]
    del d.items["b.txt"]
    s.run()
    (root / "a.txt").unlink()
    write(root, "a.txt/draft.txt", b"only copy")
    s.approved |= {"a.txt", "b.txt"}
    s.run()
    assert (root / "a.txt/draft.txt").read_bytes() == b"only copy"


def test_approved_cloud_deletion_skips_a_file_restored_on_the_pc(env):  # noqa: F811
    s, d, root, _ = env
    s.delete_threshold = 1
    for n in ("a.txt", "b.txt"):
        d.put(n, b"v1")
    s.run()
    (root / "a.txt").unlink()
    (root / "b.txt").unlink()
    r = s.run()
    assert sorted(r.pending_to_cloud) == ["a.txt", "b.txt"]
    write(root, "a.txt", b"restored and edited", age=30)
    s.approved |= {"a.txt", "b.txt"}
    s.run()
    s.run()
    assert "a.txt" in d.items and d.items["a.txt"]["data"] == b"restored and edited"


def test_icloud_rename_with_a_changed_file_inside_brings_the_change(env):  # noqa: F811
    s, d, root, _ = env
    d.put("old/a.txt", b"old")
    s.run()
    d._relocate("old", "new")
    d.items["new/a.txt"]["data"] = b"new"  # same size, edited on the phone
    d.items["new/a.txt"]["etag"] = d._etag()
    d.items["new/a.txt"]["mtime"] = 1_700_000_000.0
    real_item = d._item

    def item(p):
        it = real_item(p)
        m = d.items[p].get("mtime")
        return it if m is None else de.DriveItem(it.path, it.drivewsid, it.docwsid, it.zone, it.etag, it.is_dir,
                                                 it.size, m)

    d._item = item
    s.run()
    s.run()
    assert (root / "new/a.txt").read_bytes() == b"new"


def test_partly_listed_folder_is_never_deleted_in_icloud_as_a_whole(env):  # noqa: F811
    s, d, root, _ = env
    d.put("Docs/a.txt", b"a")
    s.run()
    d.put("Docs/new-in-icloud.txt", b"only in iCloud")
    real_walk = d.walk

    def short_walk(*a, **k):
        out = real_walk(*a, **k)
        out.pop("Docs/new-in-icloud.txt", None)
        d.incomplete = {"Docs"}
        return out

    d.walk = short_walk
    shutil.rmtree(root / "Docs")
    s.run()
    assert "Docs/new-in-icloud.txt" in d.items and "Docs" not in d.trashed


def test_case_twin_folders_stay_apart(env):  # noqa: F811
    s, d, root, _ = env
    d.put("Docs/one.txt", b"1")
    d.put("docs/two.txt", b"2")
    s.run()
    s.run()
    assert "docs/two.txt" in d.items and "Docs/two.txt" not in d.items and "Docs/one.txt" in d.items


def test_protection_scan_is_fast_for_many_folders(env):  # noqa: F811
    s, d, root, _ = env
    remote = {f"F{i}/f.txt": de.DriveItem(f"F{i}/f.txt", f"x{i}", f"x{i}", "z", "e", False, 1) for i in range(3000)}
    remote.update({f"F{i}": de.DriveItem(f"F{i}", f"d{i}", f"d{i}", "z", "e", True, 0) for i in range(3000)})
    base = {f"F{i}": de.Base(f"F{i}", True, "e", None, None, None, False) for i in range(3000)}
    t0 = time.perf_counter()
    s._keep_unsynced_remote([f"F{i}" for i in range(3000)], remote, base)
    assert time.perf_counter() - t0 < 2


# --- Photos ----------------------------------------------------------------------------------------
def test_same_name_size_and_date_but_other_content_is_not_adopted(photos_env):  # noqa: F811
    s, r, root, trashed = photos_env
    (root / "2025/11").mkdir(parents=True)
    mine = root / "2025/11/IMG_0001.JPG"
    mine.write_bytes(b"PC!")
    ts = datetime(2025, 11, 3, 10, tzinfo=timezone.utc).timestamp()
    os.utime(mine, (ts, ts))
    r.add("A1", "IMG_0001.JPG", b"WEB")
    s.run()
    assert mine.read_bytes() == b"PC!"
    assert any(p.read_bytes() == b"WEB" for p in (root / "2025/11").iterdir() if p != mine)
    r.remove("A1")
    s.run(force_full=True)
    assert mine.exists() and mine.read_bytes() == b"PC!"


def test_identical_file_already_there_is_adopted(photos_env):  # noqa: F811
    s, r, root, _ = photos_env
    (root / "2025/11").mkdir(parents=True)
    mine = root / "2025/11/IMG_0001.JPG"
    mine.write_bytes(b"same")
    ts = datetime(2025, 11, 3, 10, tzinfo=timezone.utc).timestamp()
    os.utime(mine, (ts, ts))
    r.add("A1", "IMG_0001.JPG", b"same")
    s.run()
    assert len(list((root / "2025/11").iterdir())) == 1


@windows_only
def test_photos_never_write_through_a_junction(photos_env, tmp_path):  # noqa: F811
    s, r, root, _ = photos_env
    s.run()
    outside = tmp_path / "outside"
    outside.mkdir()
    _junction(outside, root / "2025")
    r.add("A1", "IMG.JPG", b"photo")
    try:
        s.run()
    except SyncError:
        pass
    assert not any(outside.rglob("*"))


# --- Notes -----------------------------------------------------------------------------------------
def test_a_note_missing_once_is_kept_until_confirmed(notes_env):  # noqa: F811
    s, r, root, trashed = notes_env
    r.put("n1", "One")
    r.put("n2", "Two")
    s.run()
    hidden = r.notes.pop("n2")
    s.run(full=True)
    assert (root / "Notes/Two.html").exists() and trashed == []
    s.run(full=True)  # missing again: now it really is gone
    assert not (root / "Notes/Two.html").exists()
    r.notes["n2"] = hidden


def test_index_is_not_fooled_by_a_foreign_page_and_keeps_tmp_files(notes_env):  # noqa: F811
    s, r, root, _ = notes_env
    root.mkdir(parents=True, exist_ok=True)
    (root / "Notes.html").write_text("<html>mentions skyfold-collapsed by chance</html>", encoding="utf-8")
    (root / "Notes.html.tmp").write_text("mine", encoding="utf-8")
    r.put("n1", "A")
    with pytest.raises(SyncError):
        s.run()
    assert "by chance" in (root / "Notes.html").read_text(encoding="utf-8")
    assert (root / "Notes.html.tmp").read_text(encoding="utf-8") == "mine"


def test_a_big_index_is_recognised_as_ours(notes_env):  # noqa: F811
    s, r, root, _ = notes_env
    for i in range(2000):
        r.put(f"n{i}", f"A rather long note title number {i} " + "x" * 120)
    s.write_index() if (root / ".skyfold-notes").exists() else None
    root.mkdir(parents=True, exist_ok=True)
    s._check_root() if hasattr(s, "_check_root") else None
    for nid, n in r.notes.items():
        s.state.put(nid, f"Notes/{nid}.html", n.title, "Notes", "m", False)
    s.write_index()
    s.write_index()  # its own big page must not be refused


# --- sign-out ------------------------------------------------------------------------------------
def test_a_session_left_behind_after_sign_out_is_never_used(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "SESSION_DIR", tmp_path / "session")
    monkeypatch.setattr(auth, "SIGNED_OUT", tmp_path / "signed-out")
    (tmp_path / "session").mkdir()
    monkeypatch.setattr(auth.shutil, "rmtree", lambda *a, **k: None)  # a file stays locked
    monkeypatch.setattr("time.sleep", lambda s: None)
    auth.logout()
    assert auth.restore("you@example.com") is None
