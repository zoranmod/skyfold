"""Regression tests for the fifth independent (Codex) review, of commit 2397b67."""

import os
from datetime import datetime, timezone

import pytest

from skyfold import drive_engine as de
from skyfold.engine import SyncError
from tests.test_drive import env, write  # noqa: F401
from tests.test_engine import env as photos_env  # noqa: F401
from tests.test_notes import env as notes_env  # noqa: F401


def _junction(target, link):
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


# --- Photos ----------------------------------------------------------------------------------------
def test_a_file_saved_while_the_photo_downloads_is_never_overwritten(photos_env):  # noqa: F811
    s, r, root, _ = photos_env
    r.add("A1", "IMG.JPG", b"WEB")
    real = r.download

    def download(res, dest, on_bytes):
        real(res, dest, on_bytes)
        (root / "2025/11/IMG.JPG").write_bytes(b"saved by another program")  # appears at the last moment

    r.download = download
    s.run()
    assert (root / "2025/11/IMG.JPG").read_bytes() == b"saved by another program"
    assert any(p.read_bytes() == b"WEB" for p in (root / "2025/11").iterdir())


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
def test_album_cleanup_never_removes_a_junction(photos_env, tmp_path):  # noqa: F811
    s, r, root, _ = photos_env
    r.add("A1", "IMG.JPG", b"photo")
    r.album_map = {"Trip": ["A1"]}
    s.run()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "mine.jpg").write_bytes(b"mine")
    _junction(outside, root / "Albums/Linked")
    r.album_map = {}
    s.run(force_full=True)
    assert (root / "Albums/Linked").exists() and (outside / "mine.jpg").exists()


# --- Drive ---------------------------------------------------------------------------------------
def test_an_old_database_is_not_applied_to_another_folder(env, tmp_path):  # noqa: F811
    s, d, root, _ = env
    d.put("cloud-only.txt", b"keep me")
    s.run()
    s.state.set_meta("root", None)  # as written by a version before 1.0.6
    other = tmp_path / "Other"
    other.mkdir()
    (other / de.MARKER).write_text("This folder is synced by Skyfold.\n", encoding="utf-8")
    write(other, "local-only.txt", b"unrelated")
    s2 = de.DriveSyncer(s.state, d, other, delete_threshold=3)
    s2.account = "account-a"
    with pytest.raises(SyncError):
        s2.run()
    assert "cloud-only.txt" in d.items and "local-only.txt" not in d.items


def test_approval_waits_when_the_folder_cant_be_read(env, monkeypatch):  # noqa: F811
    s, d, root, trashed = env
    s.delete_threshold = 1
    d.put("Project/a.txt", b"a")
    d.put("Project/b.txt", b"b")
    s.run()
    for p in [p for p in d.items if p.startswith("Project")]:
        del d.items[p]
    s.run()
    real_walk = os.walk

    def unreadable_walk(top, *a, onerror=None, **k):
        if str(top).endswith("Project") and onerror:
            onerror(PermissionError(13, "denied", str(top)))
            return iter(())
        return real_walk(top, *a, onerror=onerror, **k)

    monkeypatch.setattr(de.os, "walk", unreadable_walk)
    s.approved |= {"Project"}
    s.run()
    assert "Project" not in trashed and (root / "Project").exists()


@pytest.mark.parametrize("cloud_mtime", [None, "close"])
def test_both_sides_changed_keep_both_whatever_the_times(env, cloud_mtime):  # noqa: F811
    s, d, root, _ = env
    d.put("a.txt", b"BASE")
    s.run()
    p = write(root, "a.txt", b"DESK", age=30)
    d.items["a.txt"]["data"] = b"IPAD"
    d.items["a.txt"]["etag"] = d._etag()
    real_item = d._item
    m = None if cloud_mtime is None else os.stat(p).st_mtime + 0.2

    def item(path):
        it = real_item(path)
        if path != "a.txt":
            return it
        return de.DriveItem(it.path, it.drivewsid, it.docwsid, it.zone, it.etag, it.is_dir, it.size, m)

    d._item = item
    s.run()
    s.run()
    texts = [q.read_bytes() for q in root.iterdir() if q.is_file() and not q.name.startswith(".")]
    assert b"DESK" in texts and b"IPAD" in texts


def test_nothing_moves_in_a_partly_listed_folder(env):  # noqa: F811
    s, d, root, _ = env
    d.put("Docs/pc.txt", b"pc v1")
    d.put("Docs/cloud.txt", b"cloud v1")
    d.put("Docs/hidden.txt", b"h")
    s.run()
    write(root, "Docs/pc.txt", b"pc v2 edited", age=30)
    d.put("Docs/cloud.txt", b"cloud v2 edited")
    uploads_before = len(d.uploads)
    real_walk = d.walk

    def short_walk(*a, **k):
        out = real_walk(*a, **k)
        out.pop("Docs/hidden.txt", None)
        d.incomplete = {"Docs"}
        return out

    d.walk = short_walk
    r = s.run()
    assert len(d.uploads) == uploads_before and r.downloaded == 0


def test_quick_pass_keeps_the_synced_spelling(env):  # noqa: F811
    s, d, root, _ = env
    d.put("docs/a.txt", b"a")
    s.run()
    d.put("Docs/b.txt", b"twin")
    s.run()
    write(root, "docs/c.txt", b"new", age=30)
    s.run(changed={"Docs/c.txt"})
    assert "Docs/c.txt" not in d.items and "Docs/a.txt" not in d.items


def test_a_few_downloads_get_one_activity_entry_each(env):  # noqa: F811
    s, d, root, _ = env
    seen = []

    class CB(de.Callbacks):
        def activity(self, kind, text, target=""):
            seen.append(text)

    s.cb = CB()
    for n in ("a.txt", "b.txt", "c.txt"):
        d.put(f"Docs/{n}", b"x")
    s.run()
    assert sum("downloaded" in x for x in seen) == 3


# --- Notes -----------------------------------------------------------------------------------------
def test_a_locked_notes_old_page_is_removed_once_possible(notes_env, monkeypatch):  # noqa: F811
    s, r, root, _ = notes_env
    from skyfold import notes_engine as ne

    r.put("n1", "Secret")
    s.run()
    real = ne.NotesSyncer._trash
    monkeypatch.setattr(ne.NotesSyncer, "_trash", lambda self, p: (_ for _ in ()).throw(PermissionError(13, "busy")))
    r.put("n1", "Secret", locked=True)
    s.run()
    assert (root / "Notes/Secret.html").exists()
    monkeypatch.setattr(ne.NotesSyncer, "_trash", real)
    r.put("n1", "Secret", locked=True)  # any later read of it
    s.run(full=True)
    assert not (root / "Notes/Secret.html").exists()
