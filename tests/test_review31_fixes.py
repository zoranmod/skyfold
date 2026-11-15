"""Twenty-ninth follow-up review (Codex review 31): own regression tests next to the kept probes."""
import os
import shutil
import threading

from skyfold import config, notes_engine as ne
from tests.test_drive import env as drive_env, write  # noqa: F401 - fixtures
from tests.test_engine import env as photos_env  # noqa: F401


# A31-1 -----------------------------------------------------------------------------------------
def test_unchanged_files_waiting_for_approval_keep_their_records_when_others_changed(drive_env):
    s, cloud, root, _ = drive_env
    s.delete_threshold = 1
    for name in ("a", "b", "c"):
        cloud.put(f"Trip/{name}.txt", b"SYNCED")
    s.run()
    cloud.put("Trip/c.txt", b"EDITED IN ICLOUD")
    shutil.rmtree(root / "Trip")
    first = s.run()
    assert set(first.pending_to_cloud) == {"Trip/a.txt", "Trip/b.txt"}
    s.run()
    s.run()
    assert (root / "Trip/c.txt").read_bytes() == b"EDITED IN ICLOUD"  # the edit came back
    assert not (root / "Trip/a.txt").exists() and not (root / "Trip/b.txt").exists()  # still only waiting
    assert s.state.all().get("Trip/a.txt") is not None


def test_a_kept_folder_is_announced_once_while_its_files_wait(drive_env):
    s, cloud, root, _ = drive_env
    s.delete_threshold = 1
    told = []
    real = s.cb.activity
    s.cb.activity = lambda kind, text, target="": (told.append(text), real(kind, text, target))
    cloud.put("Trip/a.txt", b"A")
    cloud.put("Trip/b.txt", b"B")
    s.run()
    write(root, "Trip/desktop.ini", b"LOCAL ONLY")
    cloud.trash(cloud._item("Trip"))
    for _ in range(3):
        s.run()
    assert len([x for x in told if "Trip" in x and "isn't created in iCloud again" in x]) == 1
    s.approved.update({"Trip/a.txt", "Trip/b.txt"})
    s.run()
    s.run()
    assert not (root / "Trip/a.txt").exists() and (root / "Trip/desktop.ini").exists()
    assert "Trip" not in cloud.items and s.state.get_meta("shell:pc:Trip") is not None
    assert not [p for p in s.state.all() if p == "Trip" or p.startswith("Trip/")]


# A31-3 -----------------------------------------------------------------------------------------
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


def test_an_orphan_journal_pair_is_set_aside_together(tmp_path, monkeypatch):
    _, private = _private(monkeypatch, tmp_path)
    (private / "state.sqlite3-wal").write_bytes(b"WAL")
    (private / "state.sqlite3-shm").write_bytes(b"SHM")
    config.migrate_private_files()
    wal = list(private.glob("state.sqlite3-wal.orphan-*"))
    shm = list(private.glob("state.sqlite3-shm.orphan-*"))
    assert len(wal) == len(shm) == 1 and wal[0].read_bytes() == b"WAL" and shm[0].read_bytes() == b"SHM"
    assert wal[0].name.rsplit("-", 1)[1] == shm[0].name.rsplit("-", 1)[1]


def test_an_orphan_journal_that_cant_be_renamed_is_copied_and_reported(tmp_path, monkeypatch):
    _, private = _private(monkeypatch, tmp_path)
    (private / "state.sqlite3-wal").write_bytes(b"WAL")
    real = os.rename

    def refuse(src, dst):
        if str(src).endswith("-wal"):
            raise PermissionError(32, "in use")
        return real(src, dst)

    monkeypatch.setattr(config.os, "rename", refuse)
    config.migrate_private_files()
    assert [p.read_bytes() for p in private.glob("state.sqlite3-wal.orphan-*")] == [b"WAL"]
    assert not config.JOURNAL_PROBLEMS  # (review 32: its bytes are safe in the copy, so the start goes on)


def test_a_journal_next_to_its_database_is_left_alone(tmp_path, monkeypatch):
    import sqlite3
    from contextlib import closing

    _, private = _private(monkeypatch, tmp_path)
    with closing(sqlite3.connect(private / "state.sqlite3")) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (v)")
        conn.commit()
    (private / "state.sqlite3-wal").write_bytes(b"belongs to it")
    config.migrate_private_files()
    assert (private / "state.sqlite3-wal").read_bytes() == b"belongs to it"
    assert not list(private.glob("*.orphan-*"))


# B31-6 -----------------------------------------------------------------------------------------
def test_sketch_data_of_one_thread_is_not_seen_by_another():
    ne._SKETCH_FALLBACKS.clear()
    ne._SKETCH_FALLBACKS["D1"] = "mine"
    seen = []
    t = threading.Thread(target=lambda: seen.append(dict(ne._SKETCH_FALLBACKS)))
    t.start()
    t.join()
    assert seen == [{}] and dict(ne._SKETCH_FALLBACKS) == {"D1": "mine"}
    ne._SKETCH_FALLBACKS.clear()


# B30-4 follow-up -------------------------------------------------------------------------------
def test_a_hold_is_told_again_after_a_pass_without_missing_files(photos_env, monkeypatch):
    s, cloud, root, _ = photos_env
    s._told_unsure = (1, ("x",), ())
    s.run(force_full=True)  # nothing missing
    assert s._told_unsure is None
