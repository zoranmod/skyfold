"""Thirtieth follow-up review (Codex review 32): own regression tests next to the kept probes."""
import shutil
import sqlite3
from contextlib import closing

from skyfold import config, drive_engine as de
from tests.test_drive import env as drive_env, write  # noqa: F401 - fixtures


# A32-4 -----------------------------------------------------------------------------------------
def test_an_approval_given_for_this_pc_never_deletes_in_icloud(drive_env):
    s, cloud, root, recycled = drive_env
    s.delete_threshold = 1
    cloud.put("a.txt", b"A")
    cloud.put("b.txt", b"B")
    s.run()
    for name in ("a.txt", "b.txt"):  # asked about this PC
        cloud.trash(cloud._item(name))
    first = s.run()
    assert set(first.pending_to_pc) == {"a.txt", "b.txt"}
    cloud.put("a.txt", b"A")  # meanwhile back in iCloud, and gone on this PC
    cloud.put("b.txt", b"B")
    for name in ("a.txt", "b.txt"):
        (root / name).unlink()
    s.approved.update({"a.txt", "b.txt"})
    second = s.run()
    assert set(second.pending_to_cloud) == {"a.txt", "b.txt"}  # a new question for iCloud
    assert "a.txt" in cloud.items and "b.txt" in cloud.items and not s.approved


def test_an_approval_still_works_for_the_side_it_was_given_for(drive_env):
    s, cloud, root, recycled = drive_env
    s.delete_threshold = 1
    cloud.put("a.txt", b"A")
    cloud.put("b.txt", b"B")
    s.run()
    for name in ("a.txt", "b.txt"):
        (root / name).unlink()
    first = s.run()
    s.approved.update(first.pending_to_cloud)
    s.run()
    assert "a.txt" not in cloud.items and "b.txt" not in cloud.items


# A32-5 -----------------------------------------------------------------------------------------
def test_a_real_folder_move_on_this_pc_is_still_a_move(drive_env):
    s, cloud, root, _ = drive_env
    cloud.put("Trip/a.txt", b"A")
    cloud.put("Trip/b.txt", b"BB")
    s.run()
    (root / "Trip").rename(root / "Holiday")
    res = s.run()
    assert res.moved == 1 and "Holiday/a.txt" in cloud.items and not cloud.uploads


def test_a_folder_without_fingerprints_is_never_taken_for_a_move(drive_env):
    s, cloud, root, _ = drive_env
    s.delete_threshold = 1
    cloud.put("Trip/a.txt", b"A")
    cloud.put("Trip/b.txt", b"BB")
    s.run()
    for p in ("Trip/a.txt", "Trip/b.txt"):  # rows from before 1.0.6: no content fingerprint
        b = s.state.all()[p]
        s.state.put(de.Base(p, False, b.etag, b.size, b.lmtime, b.lsize, False, None))
        s.state._db.execute("UPDATE drive_files SET hash=NULL WHERE path=?", (p,))
    (root / "Trip").rename(root / "Holiday")
    res = s.run()
    assert res.moved == 0 and res.pending_to_cloud == ["Trip"]  # added + a deletion that is asked about


def test_an_unknown_size_never_matches():
    assert de._signature({"a": None}) is None and de._signature({"a": 0}) == (("a", 0),)


# A32-1 / A32-2 ---------------------------------------------------------------------------------
def test_a_case_change_of_a_synced_file_that_icloud_deleted_stays_that_file(drive_env):
    s, cloud, root, recycled = drive_env
    cloud.put("Report.txt", b"R")
    s.run()
    cloud.trash(cloud._item("Report.txt"))
    (root / "Report.txt").rename(root / "tmp.txt")
    (root / "tmp.txt").rename(root / "REPORT.txt")
    s.run()
    assert not cloud.uploads  # not "new on this PC": the deletion from iCloud follows it


# B32-3 -----------------------------------------------------------------------------------------
def _private(monkeypatch, tmp_path):
    portable, private = tmp_path / "portable", tmp_path / "private"
    portable.mkdir()
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(config, "DATA_DIR", portable)
    monkeypatch.setattr(config, "SETTINGS_FILE", portable / "settings.json")
    monkeypatch.setattr(config, "PRIVATE_DIR", private)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    return portable, private


def test_the_earlier_wal_database_gets_no_new_files_next_to_it(tmp_path, monkeypatch):
    portable, private = _private(monkeypatch, tmp_path)
    live = tmp_path / "live.sqlite3"
    with closing(sqlite3.connect(live)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE t (v)")
        conn.execute("INSERT INTO t VALUES ('in wal')")
        conn.commit()
        shutil.copy2(live, portable / "state.sqlite3")
        shutil.copy2(str(live) + "-wal", portable / "state.sqlite3-wal")
    before = sorted(p.name for p in portable.iterdir())
    config.migrate_private_files()
    assert sorted(p.name for p in portable.iterdir()) == before  # (no -shm made there)
    with closing(sqlite3.connect(private / "state.sqlite3")) as conn:
        assert conn.execute("SELECT v FROM t").fetchone() == ("in wal",)
    assert not [p for p in private.iterdir() if p.name.endswith((".snap", ".new"))]


def test_a_damaged_copy_is_not_taken_over(tmp_path, monkeypatch):
    portable, private = _private(monkeypatch, tmp_path)
    with closing(sqlite3.connect(portable / "state.sqlite3")) as conn:
        conn.execute("CREATE TABLE t (v)")
        conn.executemany("INSERT INTO t VALUES (?)", [("x" * 500,)] * 200)
        conn.commit()
    data = bytearray((portable / "state.sqlite3").read_bytes())
    data[4096:4096 + 64] = b"\xff" * 64  # a torn page header (the table's root page)
    (portable / "state.sqlite3").write_bytes(bytes(data))
    config.migrate_private_files()
    assert config.MIGRATION_PROBLEMS and not (private / "state.sqlite3").exists()


# A32-3 -----------------------------------------------------------------------------------------
def test_an_orphan_journal_that_cant_be_kept_stops_the_start(tmp_path, monkeypatch):
    _, private = _private(monkeypatch, tmp_path)
    private.mkdir()
    (private / "state.sqlite3-wal").write_bytes(b"WAL")
    monkeypatch.setattr(config.os, "rename", lambda *_a: (_ for _ in ()).throw(PermissionError(32, "in use")))
    monkeypatch.setattr(config, "_copy_new", lambda *_a: (_ for _ in ()).throw(OSError(28, "disk full")))
    config.migrate_private_files()
    assert config.JOURNAL_PROBLEMS and (private / "state.sqlite3-wal").read_bytes() == b"WAL"
