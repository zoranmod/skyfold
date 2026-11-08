"""More Drive edge cases (renames racing edits, empty listings, Mac names)."""

from __future__ import annotations

import os
import shutil
import sys
import time
import unicodedata
from pathlib import Path

import pytest

from tests.test_drive import FakeDrive, write  # noqa: E402
from skyfold import drive_engine as de  # noqa: E402
from skyfold.drive_remote import DriveItem  # noqa: E402


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


# Folder renamed in iCloud while a file inside was edited on the PC: the PC edit is recorded as
# "in sync", never uploaded, and the next iCloud edit silently overwrites it (no conflict copy).
def test_icloud_folder_rename_vs_pc_edit_inside(env25):
    s, d, root, trashed = env25
    for i in range(3):
        d.put(f"F/f{i}.txt", f"c{i}".encode())
    s.run()
    d._relocate("F", "G")
    write(root, "F/f0.txt", b"PC edit of f0", age=30)
    s.run()
    s.run()
    assert cloud(d).get("G/f0.txt") == b"PC edit of f0" or any(
        v == b"PC edit of f0" for v in cloud(d).values()), (files(root), cloud(d))


def test_icloud_folder_rename_then_icloud_edit_overwrites_pc_edit(env25):
    s, d, root, trashed = env25
    for i in range(3):
        d.put(f"F/f{i}.txt", f"c{i}".encode())
    s.run()
    d._relocate("F", "G")
    write(root, "F/f0.txt", b"PC edit of f0", age=30)
    s.run()
    d.put("G/f0.txt", b"phone edit")
    s.run()
    everything = list(files(root).values()) + list(cloud(d).values())
    assert b"PC edit of f0" in everything, "PC edit permanently overwritten, no conflict copy, no Recycle Bin"


# Folder renamed on PC while a file inside was edited in iCloud: etags dropped -> edit never downloaded.
def test_pc_folder_rename_vs_icloud_edit_inside(env25):
    s, d, root, _ = env25
    for i in range(3):
        d.put(f"F/f{i}.txt", f"c{i}".encode())
    s.run()
    (root / "F").rename(root / "G")
    d.put("F/f0.txt", b"phone edit, longer")
    s.run()
    s.run()
    s.run()
    assert files(root).get("G/f0.txt") == cloud(d).get("G/f0.txt"), (files(root), cloud(d))


# iCloud listing comes back empty (Apple glitch) for a small Drive: everything on the PC is trashed.
def test_empty_icloud_listing_does_not_wipe_small_drive(env25):
    s, d, root, trashed = env25
    for i in range(10):
        d.put(f"Docs/f{i}.txt", b"x")
    s.run()
    saved = dict(d.items)
    d.items.clear()  # a listing that says "root is empty"
    s.run()
    d.items.update(saved)
    assert trashed == [], f"everything went to the Recycle Bin: {trashed}"


# A waiting big deletion vanishes from the result of any quick pass (worker then clears the question).
# NFD names from a Mac must round-trip without duplicates.
def test_nfd_names_round_trip(env25):
    s, d, root, trashed = env25
    name = unicodedata.normalize("NFD", "Café/Résumé.txt")
    d.put(name, b"cv")
    s.run()
    r = s.run()
    r2 = s.run()
    assert (r.uploaded, r.downloaded, r2.uploaded, r2.downloaded) == (0, 0, 0, 0)
    assert d.trashed == [] and trashed == []


# Thousands of single-file deletions: planning must not be quadratic.