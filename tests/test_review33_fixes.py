"""Thirty-first follow-up review (Codex review 33): own regression tests next to the kept probes."""
import os
import sqlite3
from contextlib import closing
from types import SimpleNamespace

from skyfold import config
from tests.test_drive import env as drive_env, write  # noqa: F401 - fixtures
from tests.test_review28_codex_probes import deny_lookup


def _two_pending_in_icloud(s, cloud, root):
    s.delete_threshold = 1
    for p in ("Alpha/a.txt", "Beta/b.txt"):
        cloud.put(p, b"ORIGINAL")
    s.run()
    for p in ("Alpha/a.txt", "Beta/b.txt"):
        (root / p).unlink()
    res = s.run()
    assert set(res.pending_to_cloud) == {"Alpha/a.txt", "Beta/b.txt"}
    return res


# A33-1 / A33-2 ---------------------------------------------------------------------------------
def test_an_ok_for_the_other_side_is_not_taken(drive_env):
    s, cloud, root, _ = drive_env
    res = _two_pending_in_icloud(s, cloud, root)
    assert s.approve(res.pending_to_cloud, "pc") == []  # that question was about iCloud
    s.run()
    assert "Alpha/a.txt" in cloud.items and "Beta/b.txt" in cloud.items
    assert s.approve(res.pending_to_cloud, "cloud") == res.pending_to_cloud
    s.run()
    assert "Alpha/a.txt" not in cloud.items and "Beta/b.txt" not in cloud.items


def test_the_worker_ignores_an_answer_to_a_question_that_changed(drive_env):
    from skyfold.worker import SyncWorker

    s, cloud, root, _ = drive_env
    res = _two_pending_in_icloud(s, cloud, root)
    shown = SimpleNamespace(to_pc=[], to_cloud=res.pending_to_cloud)
    now = SimpleNamespace(to_pc=list(res.pending_to_cloud), to_cloud=[])  # the question turned around
    emitted = []
    worker = SimpleNamespace(_pending=None, _drive_pending=now, drive=s, drive_state=s.state,
                             pendingChanged=SimpleNamespace(emit=emitted.append), _kick_drive=lambda **_k: None)
    SyncWorker.resolve_pending(worker, "approve:to_cloud", shown.to_cloud)
    assert not s.approved and worker._drive_pending is now and emitted == [now]


def test_a_quick_pass_never_forgets_what_was_asked(drive_env):
    s, cloud, root, _ = drive_env
    _two_pending_in_icloud(s, cloud, root)
    write(root, "Alpha/new.txt", b"N")
    s.run(changed={"Alpha/new.txt"})
    assert set(s._asked_cloud) >= {"Alpha/a.txt", "Beta/b.txt"}
    s.run()
    assert "Beta/b.txt" in cloud.items and "Alpha/a.txt" in cloud.items  # still waiting for the answer


# A33-5 -----------------------------------------------------------------------------------------
def test_a_folder_changed_in_icloud_is_not_moved_with_a_pc_rename(drive_env):
    s, cloud, root, _ = drive_env
    s.delete_threshold = 1
    cloud.put("Trip/a.txt", b"A")
    cloud.put("Trip/b.txt", b"B")
    s.run()
    cloud.put("Trip/b.txt", b"B EDITED IN ICLOUD")
    (root / "Trip").rename(root / "Holiday")
    res = s.run()
    assert res.moved == 0 and cloud.items["Trip/b.txt"]["data"] == b"B EDITED IN ICLOUD"


# A33-6 -----------------------------------------------------------------------------------------
def _private(monkeypatch, tmp_path):
    portable, private = tmp_path / "portable", tmp_path / "private"
    portable.mkdir()
    private.mkdir()
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(config, "DATA_DIR", portable)
    monkeypatch.setattr(config, "SETTINGS_FILE", portable / "settings.json")
    monkeypatch.setattr(config, "PRIVATE_DIR", private)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    return portable, private


def test_an_orphan_rollback_journal_is_set_aside_with_the_others(tmp_path, monkeypatch):
    _, private = _private(monkeypatch, tmp_path)
    (private / "state.sqlite3-journal").write_bytes(b"JOURNAL")
    (private / "state.sqlite3-wal").write_bytes(b"WAL")
    config.migrate_private_files()
    tags = {p.name.rsplit("-", 1)[1] for p in private.glob("state.sqlite3-*.orphan-*")}
    assert len(tags) == 1 and [p.read_bytes() for p in private.glob("state.sqlite3-journal.orphan-*")] == [b"JOURNAL"]


def test_a_database_that_cant_be_looked_at_next_to_a_journal_stops_the_start(tmp_path, monkeypatch):
    _, private = _private(monkeypatch, tmp_path)
    (private / "state.sqlite3-wal").write_bytes(b"WAL")
    with monkeypatch.context() as m:
        deny_lookup(m, private / "state.sqlite3", winerror=5)
        config.migrate_private_files()
    assert config.JOURNAL_PROBLEMS and (private / "state.sqlite3-wal").read_bytes() == b"WAL"


def test_a_database_that_cant_be_looked_at_without_journals_starts(tmp_path, monkeypatch):
    _, private = _private(monkeypatch, tmp_path)
    with monkeypatch.context() as m:
        deny_lookup(m, private / "state.sqlite3", winerror=5)
        config.migrate_private_files()
    assert not config.JOURNAL_PROBLEMS


# B33-2 -----------------------------------------------------------------------------------------
def test_a_rollback_mode_earlier_database_gets_no_files_next_to_it(tmp_path, monkeypatch):
    portable, private = _private(monkeypatch, tmp_path)
    with closing(sqlite3.connect(portable / "state.sqlite3")) as conn:
        conn.execute("CREATE TABLE t (v)")
        conn.execute("INSERT INTO t VALUES ('x')")
        conn.commit()
    before = sorted(os.listdir(portable))
    config.migrate_private_files()
    assert sorted(os.listdir(portable)) == before and not config.MIGRATION_PROBLEMS
    with closing(sqlite3.connect(private / "state.sqlite3")) as conn:
        assert conn.execute("SELECT v FROM t").fetchone() == ("x",)
