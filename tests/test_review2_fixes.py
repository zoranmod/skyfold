"""Regression tests for the second independent (Codex) review of v1.0.5."""

import os
import shutil

import pytest

from skyfold import drive_engine as de
from skyfold.engine import SyncError
from tests.test_drive import env, write  # noqa: F401
from tests.test_notes import env as notes_env  # noqa: F401


# --- Drive ---------------------------------------------------------------------------------------
def test_parent_of_a_chosen_folder_is_never_trashed_with_unsynced_contents(env):  # noqa: F811
    s, d, root, _ = env
    s.folders = ["Docs/Work"]
    d.put("Docs/Work/w.txt", b"w")
    for i in range(30):
        d.put(f"Docs/Home/h{i}.txt", b"h")  # not chosen: never on this PC
    s.run()
    for p in [p for p in d.items if p.startswith("Docs/Work")]:
        del d.items[p]  # Work deleted in iCloud...
    shutil.rmtree(root / "Docs")  # ...and Docs removed on the PC
    s.run(changed={"Docs"})
    s.run()
    assert all(f"Docs/Home/h{i}.txt" in d.items for i in range(30)), d.trashed


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
def test_a_junction_never_leads_drive_outside_its_folder(env, tmp_path):  # noqa: F811
    import _winapi

    s, d, root, _ = env
    s.run()
    outside = tmp_path / "outside"
    outside.mkdir()
    _winapi.CreateJunction(str(outside), str(root / "Docs"))
    d.put("Docs/new.txt", b"cloud bytes")
    s.run()
    assert not (outside / "new.txt").exists()


def test_a_drive_folder_of_another_apple_id_is_refused(env, tmp_path):  # noqa: F811
    s, d, root, _ = env
    s.account = "account-a"
    d.put("private-A.txt", b"A")
    s.run()
    other = de.DriveSyncer(de.DriveState(tmp_path / "b.db"), d, root, delete_threshold=3)
    other.account = "account-b"
    with pytest.raises(SyncError):
        other.run()


def test_approved_deletion_keeps_a_file_edited_after_the_question(env):  # noqa: F811
    s, d, root, trashed = env
    s.delete_threshold = 1
    for n in ("a.txt", "b.txt"):
        d.put(n, b"v1")
    s.run()
    del d.items["a.txt"]
    del d.items["b.txt"]
    r = s.run()
    assert sorted(r.pending_to_pc) == ["a.txt", "b.txt"]
    write(root, "a.txt", b"edited after the question", age=30)
    s.approved |= {"a.txt", "b.txt"}
    s.run()
    assert (root / "a.txt").read_bytes() == b"edited after the question"
    assert "b.txt" in trashed


def test_a_look_alike_folder_is_not_taken_for_a_rename(env):  # noqa: F811
    s, d, root, _ = env
    d.put("old/a.txt", b"old")
    s.run()
    for p in [p for p in d.items if p.startswith("old")]:
        del d.items[p]
    d.put("new/a.txt", b"new")  # a different folder, same file names and sizes
    s.run()
    s.run()
    assert (root / "new/a.txt").read_bytes() == b"new"


def test_partly_listed_folder_never_means_deleted(env):  # noqa: F811
    s, d, root, trashed = env
    d.put("a.txt", b"a")
    d.put("b.txt", b"b")
    s.run()
    real_walk = d.walk

    def short_walk(*a, **k):
        out = real_walk(*a, **k)
        out.pop("b.txt", None)  # iCloud says 2 items but lists only one
        d.incomplete = {""}
        return out

    d.walk = short_walk
    s.run()
    assert (root / "b.txt").exists() and trashed == []


def test_names_differing_only_in_case_never_overwrite_each_other(env):  # noqa: F811
    s, d, root, _ = env
    d.put("Report.txt", b"upper")
    d.put("report.txt", b"lower")
    s.run()
    files = [p for p in root.iterdir() if p.is_file() and p.name.lower() == "report.txt"]
    assert len(files) == 1 and files[0].read_bytes() in (b"upper", b"lower")
    assert "Report.txt" in d.items and "report.txt" in d.items  # nothing deleted in iCloud
    s.run()
    assert "Report.txt" in d.items and "report.txt" in d.items


# --- Notes ---------------------------------------------------------------------------------------
def test_an_unrelated_notes_html_is_never_replaced(notes_env):  # noqa: F811
    s, r, root, _ = notes_env
    root.mkdir(parents=True, exist_ok=True)
    (root / "Notes.html").write_text("<html>my own index</html>", encoding="utf-8")
    r.put("n1", "A")
    with pytest.raises(SyncError):
        s.run()
    assert (root / "Notes.html").read_text(encoding="utf-8") == "<html>my own index</html>"


def test_empty_answer_keeps_even_a_small_notes_export(notes_env):  # noqa: F811
    s, r, root, trashed = notes_env
    r.put("n1", "One")
    r.put("n2", "Two")
    s.run()
    r.__dict__.update({k: type(v)() for k, v in r.__dict__.items() if isinstance(v, dict)})
    s.run(full=True)
    assert trashed == [] and (root / "Notes/One.html").exists() and (root / "Notes/Two.html").exists()


def test_deletion_approved_during_the_same_pass_keeps_a_fresh_edit(env):  # noqa: F811
    """The user approves while the pass is still downloading; a file edited after the question stays."""
    s, d, root, trashed = env
    s.delete_threshold = 1
    for n in ("a.txt", "b.txt", "c.txt"):
        d.put(n, b"v1")
    s.run()
    del d.items["a.txt"]
    del d.items["b.txt"]
    d.put("c.txt", b"changed in iCloud")  # keeps the pass busy downloading after the question

    def on_pending(to_cloud, to_pc):
        write(root, "a.txt", b"edited after the question", age=30)
        s.approved.update(to_pc)

    s.on_pending = on_pending
    s.run()
    assert (root / "a.txt").read_bytes() == b"edited after the question"
    assert "b.txt" in trashed
