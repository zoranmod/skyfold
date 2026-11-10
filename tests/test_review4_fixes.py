"""Regression tests for the fourth independent (Codex) review, of commit 0ee5e33."""

import os
import shutil
from datetime import datetime, timezone

import pyicloud
import pytest

from skyfold import auth
from skyfold import drive_engine as de
from skyfold.engine import SyncError
from tests.test_auth import FakeService
from tests.test_drive import env, write  # noqa: F401
from tests.test_engine import env as photos_env  # noqa: F401
from tests.test_notes import env as notes_env  # noqa: F401

PHOTO_TS = datetime(2025, 11, 3, 10, tzinfo=timezone.utc).timestamp()


def _junction(target, link):
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


# --- Photos ----------------------------------------------------------------------------------------
def test_every_occupied_name_is_compared_never_overwritten(photos_env):  # noqa: F811
    s, r, root, _ = photos_env
    folder = root / "2025/11"
    folder.mkdir(parents=True)
    first, second = folder / "IMG.JPG", folder / "IMG_A1.JPG"
    for p, data in ((first, b"PC1"), (second, b"PC2")):
        p.write_bytes(data)
        os.utime(p, (PHOTO_TS, PHOTO_TS))
    r.add("A1", "IMG.JPG", b"WEB")
    s.run()
    assert first.read_bytes() == b"PC1" and second.read_bytes() == b"PC2"
    assert any(p.read_bytes() == b"WEB" for p in folder.iterdir() if p not in (first, second))


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
def test_album_folders_never_write_through_a_junction(photos_env, tmp_path):  # noqa: F811
    s, r, root, _ = photos_env
    r.add("A1", "IMG.JPG", b"photo")
    s.run()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "Albums").mkdir(exist_ok=True)
    _junction(outside, root / "Albums/Trip")
    r.album_map = {"Trip": ["A1"]}
    s.run(force_full=True)
    assert not any(outside.iterdir())


# --- Drive ---------------------------------------------------------------------------------------
def test_another_drive_folder_starts_afresh(env, tmp_path):  # noqa: F811
    s, d, root, _ = env
    d.put("cloud-only.txt", b"keep me")
    s.run()
    other_root = tmp_path / "Other"
    other_root.mkdir()
    (other_root / de.MARKER).write_text("This folder is synced by Skyfold.\n", encoding="utf-8")  # old style
    s2 = de.DriveSyncer(s.state, d, other_root, delete_threshold=3)  # same database, another folder
    s2.account = "account-a"
    with pytest.raises(SyncError):
        s2.run()
    assert "cloud-only.txt" in d.items


def test_folder_replaced_by_a_file_during_the_question_is_kept(env):  # noqa: F811
    s, d, root, trashed = env
    s.delete_threshold = 1
    d.put("Project/a.txt", b"a")
    d.put("Project/b.txt", b"b")
    s.run()
    for p in [p for p in d.items if p.startswith("Project")]:
        del d.items[p]
    s.run()
    shutil.rmtree(root / "Project")
    write(root, "Project", b"a new draft", age=30)
    s.approved |= {"Project"}
    s.run()
    assert (root / "Project").read_bytes() == b"a new draft" and "Project" not in trashed


def test_edits_on_both_sides_within_seconds_keep_both(env):  # noqa: F811
    s, d, root, _ = env
    d.put("a.txt", b"BASE")
    s.run()
    write(root, "a.txt", b"DESK", age=30)
    d.items["a.txt"]["data"] = b"IPAD"
    d.items["a.txt"]["etag"] = d._etag()
    s.run()
    s.run()
    texts = [p.read_bytes() for p in root.iterdir() if p.is_file() and not p.name.startswith(".")]
    assert b"DESK" in texts and b"IPAD" in texts


def test_nothing_is_deleted_in_a_partly_listed_folder(env):  # noqa: F811
    s, d, root, _ = env
    d.put("Docs/a.txt", b"a")
    s.run()
    d.put("Docs/other.txt", b"o")
    (root / "Docs/a.txt").unlink()
    real_walk = d.walk

    def short_walk(*a, **k):
        out = real_walk(*a, **k)
        out.pop("Docs/other.txt", None)
        d.incomplete = {"Docs"}
        return out

    d.walk = short_walk
    s.run()
    assert "Docs/a.txt" in d.items


def test_the_synced_spelling_keeps_winning(env):  # noqa: F811
    s, d, root, _ = env
    d.put("report.txt", b"old")
    s.run()
    d.put("Report.txt", b"new twin")  # a second spelling appears later
    s.run()
    s.run(changed={"report.txt"})
    assert d.items["report.txt"]["data"] == b"old" and d.items["Report.txt"]["data"] == b"new twin"
    assert (root / "report.txt").read_bytes() == b"old"


# --- Notes -----------------------------------------------------------------------------------------
def test_a_note_seen_alive_in_between_is_not_removed(notes_env):  # noqa: F811
    s, r, root, trashed = notes_env
    r.put("n1", "One")
    r.put("n2", "Two")
    s.run()
    hidden = r.notes.pop("n2")
    s.run(full=True)  # missing once
    r.notes["n2"] = hidden
    r.put("n2", "Two")  # changed: seen alive by an ordinary read
    s.run()
    r.notes.pop("n2")
    s.run(full=True)  # missing again - but it was alive in between
    assert trashed == []


def test_a_page_that_couldnt_be_removed_is_tried_again(notes_env, monkeypatch):  # noqa: F811
    s, r, root, trashed = notes_env
    r.put("n1", "One")
    s.run()
    from skyfold import notes_engine as ne

    real = ne.NotesSyncer._trash

    def busy(self, p):
        raise PermissionError(13, "in use", str(p))

    monkeypatch.setattr(ne.NotesSyncer, "_trash", busy)
    r.delete("n1")
    s.run()
    assert (root / "Notes/One.html").exists()
    monkeypatch.setattr(ne.NotesSyncer, "_trash", real)
    s.run()  # the deletion is read again (the read wasn't marked done) and removal is retried
    assert not (root / "Notes/One.html").exists()


# --- sign-in -------------------------------------------------------------------------------------
def test_a_cancelled_sign_in_keeps_the_signed_out_mark(tmp_path, monkeypatch):
    FakeService.calls = []
    monkeypatch.setattr(pyicloud, "PyiCloudService", FakeService)
    monkeypatch.setattr(auth, "SESSION_DIR", tmp_path / "session")
    monkeypatch.setattr(auth, "SIGNED_OUT", tmp_path / "signed-out")
    (tmp_path / "signed-out").write_text("", encoding="utf-8")
    step = auth.start_login("you@example.com", "right")  # the dialog is then cancelled
    assert (tmp_path / "signed-out").exists()
    auth.commit_login(step.api)  # only an accepted sign-in clears it
    assert not (tmp_path / "signed-out").exists()
