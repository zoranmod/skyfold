"""Regression tests for the tenth independent (Codex) review, of commit 8349205."""

import os

import pytest
import requests

from skyfold import drive_engine as de
from skyfold.drive_remote import DriveItem
from tests.test_drive import env, write  # noqa: F401


def _copies(d, data):
    return sorted(p for p, it in d.items.items() if it["data"] == data)


def _lose_answer(d, monkeypatch, which, times=1):
    """iCloud does the step, but the answer never arrives (a timeout)."""
    real = getattr(d, which)
    left = [times]

    def step(item, arg):
        got = real(item, arg)
        if left[0]:
            left[0] -= 1
            raise requests.exceptions.Timeout("read timed out")
        return got

    monkeypatch.setattr(d, which, step)


def _with_mtimes(d, monkeypatch):
    real = d._item

    def item(p):
        r = real(p)
        return DriveItem(r.path, r.drivewsid, r.docwsid, r.zone, r.etag, r.is_dir, r.size,
                         d.items[p].get("mtime", r.mtime))

    monkeypatch.setattr(d, "_item", item)


def test_a_folder_recovered_while_listed_partly_is_never_duplicated(env, monkeypatch):  # noqa: F811
    s, d, root, _ = env
    s.delete_threshold = 1000
    for i in range(40):
        d.put(f"G/A/f{i}.txt", f"file {i}".encode())
    d.put("F/keep.txt", b"keep")
    s.run()
    os.renames(root / "G/A", root / "F/D")
    _lose_answer(d, monkeypatch, "move")
    s.run()  # moved to F/A in iCloud, the answer was lost
    real_walk = d.walk
    short = [True]

    def walk(*a, **k):
        out = real_walk(*a, **k)
        if short and short.pop():  # F/A listed only partly this once
            out = {p: r for p, r in out.items() if not p.startswith("F/A/")}
            d.incomplete = {"F/A"}
        else:
            d.incomplete = set()
        return out

    monkeypatch.setattr(d, "walk", walk)
    for _ in range(5):
        s.run()
    for i in range(40):
        assert len(_copies(d, f"file {i}".encode())) == 1
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*.txt") if "f1." in p.name) == ["F/D/f1.txt"]


def test_an_icloud_edit_of_the_same_size_during_recovery_reaches_the_pc(env, monkeypatch):  # noqa: F811
    s, d, root, _ = env
    _with_mtimes(d, monkeypatch)
    d.put("Docs/a.txt", b"base")
    s.run()
    os.rename(root / "Docs/a.txt", root / "Docs/b.txt")
    _lose_answer(d, monkeypatch, "rename")
    s.run()
    d.items["Docs/b.txt"].update(data=b"EDIT", etag=d._etag(), mtime=1_700_000_000.0)  # same size
    for _ in range(4):
        s.run()
    assert (root / "Docs/b.txt").read_bytes() == b"EDIT"
    assert _copies(d, b"EDIT") == ["Docs/b.txt"] and not _copies(d, b"base")


def test_a_second_move_never_erases_the_first_ones_record(env, monkeypatch):  # noqa: F811
    s, d, root, _ = env
    d.put("G/a.txt", b"first")
    d.put("F/keep.txt", b"keep")
    d.put("H/x.txt", b"second")
    s.run()
    os.rename(root / "G/a.txt", root / "F/d.txt")
    os.rename(root / "H/x.txt", root / "H/y.txt")
    _lose_answer(d, monkeypatch, "move")  # the first move's answer is lost; the other works
    for _ in range(4):
        s.run()
    assert _copies(d, b"first") == ["F/d.txt"] and _copies(d, b"second") == ["H/y.txt"]


def test_a_crash_while_the_database_follows_icloud_loses_nothing(env, monkeypatch, tmp_path):  # noqa: F811
    s, d, root, _ = env
    d.put("G/a.txt", b"moved")
    d.put("F/keep.txt", b"keep")
    s.run()
    os.rename(root / "G/a.txt", root / "F/d.txt")
    real_rename = d.rename
    monkeypatch.setattr(d, "rename", lambda item, name: (_ for _ in ()).throw(
        RuntimeError("iCloud refused the rename: CONFLICT")))

    class Crash(BaseException):
        pass

    real_put = s.state.put
    crashed = []

    def put(b):
        if b.path == "F/a.txt" and not crashed:
            crashed.append(True)
            raise Crash  # dies after the old row was removed, before the new one is written
        return real_put(b)

    monkeypatch.setattr(s.state, "put", put)
    with pytest.raises(Crash):
        s.run()
    monkeypatch.setattr(d, "rename", real_rename)
    fresh = de.DriveSyncer(de.DriveState(tmp_path / "d.db"), d, root, delete_threshold=3)
    for _ in range(4):
        fresh.run()
    assert _copies(d, b"moved") == ["F/d.txt"]


def test_an_icloud_deletion_during_recovery_is_not_undone(env, monkeypatch):  # noqa: F811
    s, d, root, trashed = env
    d.put("Docs/a.txt", b"deleted in iCloud")
    d.put("Docs/other.txt", b"other")
    s.run()
    os.rename(root / "Docs/a.txt", root / "Docs/b.txt")
    _lose_answer(d, monkeypatch, "rename")
    s.run()
    del d.items["Docs/b.txt"]  # deleted in iCloud before the move was settled
    for _ in range(4):
        s.run()
    assert not _copies(d, b"deleted in iCloud")
    assert not (root / "Docs/b.txt").exists() and "Docs/b.txt" in trashed
