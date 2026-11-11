"""Tests for content verification in iCloud Drive (after the seventeenth Codex review): a new iCloud
version is taken as "what this PC has" only after its content was compared, never by size or time."""

import sqlite3

import pytest

from skyfold import drive_engine as de
from skyfold.engine import Cancelled
from tests.test_drive import env, write  # noqa: F401


def test_an_identical_download_leaves_the_pc_file_alone(env):  # noqa: F811
    s, d, root, trashed = env
    d.put("a.txt", b"same content")
    s.run()
    before = (root / "a.txt").stat()
    d.items["a.txt"]["etag"] = d._etag()  # only iCloud's version tag moves (opened on a phone, renamed…)
    res = s.run()
    after = (root / "a.txt").stat()
    row = s.state.all()["a.txt"]
    assert res.downloaded == 0 and res.conflicts == 0 and not trashed
    assert (after.st_mtime, after.st_ino) == (before.st_mtime, before.st_ino)
    assert row.etag == d.items["a.txt"]["etag"] and row.hash == de.hashlib.sha256(b"same content").hexdigest()
    assert s.run().downloaded == 0


def test_a_deletion_on_the_pc_is_checked_against_the_content_in_icloud(env):  # noqa: F811
    s, d, root, _ = env
    d.put("same.txt", b"unchanged")
    d.put("edit.txt", b"version 1")
    s.run()
    d.items["same.txt"]["etag"] = d._etag()  # tag moved, content as synced
    d.items["edit.txt"].update(data=b"version 2", etag=d._etag())  # same size, same time, other content
    (root / "same.txt").unlink()
    (root / "edit.txt").unlink()
    for _ in range(3):
        s.run()
    assert "same.txt" not in d.items  # the deletion went to iCloud
    assert d.items["edit.txt"]["data"] == b"version 2" and (root / "edit.txt").read_bytes() == b"version 2"


def test_without_a_fingerprint_nothing_is_assumed(env):  # noqa: F811
    s, d, root, _ = env
    d.put("old.txt", b"from an older database")
    s.run()
    s.state._db.execute("UPDATE drive_files SET hash=NULL")  # a row written before fingerprints existed
    d.items["old.txt"]["etag"] = d._etag()
    (root / "old.txt").unlink()
    s.run()
    assert (root / "old.txt").read_bytes() == b"from an older database"  # comes back rather than being trashed
    assert "old.txt" in d.items


def test_the_fingerprint_follows_the_same_pc_version_only(tmp_path):
    st = de.DriveState(tmp_path / "d.db")
    st.put(de.Base("a.txt", False, "e1", 4, 100.0, 4, False, "h1"))
    st.put(de.Base("a.txt", False, "e2", 4, 100.0, 4, False))  # same PC version, new tag
    assert st.all()["a.txt"].hash == "h1"
    st.put(de.Base("a.txt", False, None, 5, 200.0, 5, False))  # another PC version
    assert st.all()["a.txt"].hash is None


def test_a_database_from_before_gets_the_new_column(tmp_path):
    db = sqlite3.connect(tmp_path / "old.db")
    db.executescript("""CREATE TABLE drive_files (path TEXT PRIMARY KEY, is_dir INTEGER NOT NULL, etag TEXT,
        size INTEGER, lmtime REAL, lsize INTEGER, pending INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE drive_meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO drive_files VALUES('a.txt', 0, 'e1', 4, 100.0, 4, 0);""")
    db.commit()
    db.close()
    st = de.DriveState(tmp_path / "old.db")
    row = st.all()["a.txt"]
    assert row.etag == "e1" and row.hash is None
    st.put(de.Base("a.txt", False, "e1", 4, 100.0, 4, False, "h"))
    assert st.all()["a.txt"].hash == "h"


def test_first_sync_compares_files_that_are_on_both_sides(env):  # noqa: F811
    s, d, root, _ = env
    write(root, "same.txt", b"identical")
    write(root, "differs.txt", b"PC text..")
    d.put("same.txt", b"identical")
    d.put("differs.txt", b"iCloud tx")  # same size, other content
    res = s.run()
    assert res.conflicts == 1 and (root / "same.txt").read_bytes() == b"identical"
    texts = {p.read_bytes() for p in root.iterdir() if p.is_file() and not p.name.startswith(".")}
    assert {b"identical", b"PC text..", b"iCloud tx"} <= texts


def test_quit_cannot_be_undone_by_a_pass_that_is_just_starting(env):  # noqa: F811
    s, d, root, _ = env
    d.put("a.txt", b"a")
    s.closing.set()
    with pytest.raises(Cancelled):
        s.run()
    assert not (root / "a.txt").exists() and s.closing.is_set()
