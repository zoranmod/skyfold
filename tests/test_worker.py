"""End-to-end test of the Qt worker thread against the fake iCloud (no network)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from skyfold import engine as eng
from skyfold import worker as wk
from skyfold.config import Settings
from tests.test_engine import FakeRemote


class Driver(QObject):
    syncNow = Signal(bool)
    resolve = Signal(str, list)
    apply = Signal(object, bool, bool)


def pump(cond, timeout=10.0):
    app = QCoreApplication.instance()
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture()
def qt(tmp_path, monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    remote = FakeRemote()
    monkeypatch.setattr(wk, "STATE_DB", tmp_path / "state.db")
    monkeypatch.setattr(wk.auth, "restore", lambda _id: object())
    monkeypatch.setattr(wk, "ICloudRemote", lambda _api: remote)
    monkeypatch.setattr(eng.Syncer, "_trash", lambda self, p: p.unlink())
    settings = Settings(apple_id="test@example.com", photos_dir=str(tmp_path / "Slike"),
                        photos_dir_confirmed=True, delete_threshold=3, notifications=True,
                        folder_structure="year_month")
    w = wk.SyncWorker(settings)
    th = QThread()
    w.moveToThread(th)
    th.started.connect(w.start)
    d = Driver()
    d.syncNow.connect(w.sync_now)
    d.resolve.connect(w.resolve_pending)
    d.apply.connect(w.apply_settings)
    states: list[tuple[str, str]] = []
    pend: list = []
    notes: list = []
    w.stateChanged.connect(lambda s, t: states.append((s, t)))
    w.pendingChanged.connect(pend.append)
    w.notify.connect(lambda a, b: notes.append(a))
    remote.add("A1", "IMG_0001.HEIC", b"abc", live=b"mov")
    yield app, w, th, d, remote, Path(settings.photos_dir), states, pend, notes
    w.shutdown()
    th.quit()
    th.wait(3000)


def test_worker_full_flow(qt):
    app, w, th, d, remote, root, states, pend, notes = qt
    th.start()
    assert pump(lambda: states and states[-1][0] == "idle"), states
    assert (root / "2025/11/IMG_0001.HEIC").exists()
    assert "Photos synced" in notes

    # phone deletes 5 photos at once -> held for confirmation, nothing removed
    for i in range(5):
        remote.add(f"B{i}", f"IMG_1{i}.JPG", b"z")
    states.clear()
    d.syncNow.emit(True)
    assert pump(lambda: states and states[-1][0] == "idle")
    for i in range(5):
        remote.remove(f"B{i}")
    d.syncNow.emit(True)
    assert pump(lambda: pend and pend[-1] is not None)
    assert len(pend[-1].to_pc) == 5
    assert len(list((root / "2025/11").glob("IMG_1*.JPG"))) == 5

    # user approves -> files go
    d.resolve.emit("approve", pend[-1].to_pc)
    assert pump(lambda: not list((root / "2025/11").glob("IMG_1*.JPG")))


def test_worker_waits_for_folder_choice(qt):
    app, w, th, d, remote, root, states, pend, notes = qt
    w.settings.photos_dir_confirmed = False
    th.start()
    assert pump(lambda: states and states[-1][0] == "setup")
    assert not root.exists()


def test_worker_offline_then_recovers(qt, monkeypatch):
    app, w, th, d, remote, root, states, pend, notes = qt

    def offline(_id):
        raise wk.auth.Offline("no network")

    monkeypatch.setattr(wk.auth, "restore", offline)
    th.start()
    assert pump(lambda: states and states[-1][0] == "offline")
    monkeypatch.setattr(wk.auth, "restore", lambda _id: object())
    d.syncNow.emit(False)
    assert pump(lambda: states and states[-1][0] == "idle"), states


def test_pc_delete_goes_to_icloud_without_pressing_sync(qt):
    """Regression: the folder is created by the first sync (first run), so the folder watcher
    must start afterwards; deleting a file then reaches iCloud on its own within seconds."""
    app, w, th, d, remote, root, states, pend, notes = qt
    assert not root.exists()
    th.start()
    target = root / "2025/11/IMG_0001.HEIC"
    assert pump(target.exists, 30), states  # the first sync downloads it (slow CI machines need time)
    assert pump(lambda: states and states[-1][0] == "idle", 30), states
    assert pump(lambda: "photos" in w._watchers and w._watchers["photos"].alive(), 5)
    target.unlink()
    assert pump(lambda: remote.deleted == ["A1"], 15), (remote.deleted, states[-3:])


def test_slow_drive_runs_in_background_while_photos_sync(qt, monkeypatch, tmp_path):
    """Regression: a long Drive read (~10 min on big accounts) must not hold up Photos."""
    import threading

    from tests.test_drive import FakeDrive

    app, w, th, d, remote, root, states, pend, notes = qt
    gate = threading.Event()
    drive = FakeDrive()
    drive.put("Docs/a.txt", b"a")
    slow_walk = drive.walk

    def walk(*a, **k):
        gate.wait(10)  # Drive is "slow" until the test lets it finish
        return slow_walk(*a, **k)

    drive.walk = walk
    monkeypatch.setattr(wk, "ICloudDrive", lambda _api: drive)
    w.settings.drive_enabled = True
    w.settings.drive_dir = str(tmp_path / "Drive")
    th.start()
    # Photos finishes while Drive is still busy, and the panel shows Drive working.
    assert pump(lambda: (root / "2025/11/IMG_0001.HEIC").exists(), 10)
    assert pump(lambda: w.drive_running() and states and states[-1][0] == "syncing", 5), states[-3:]
    remote.add("A2", "IMG_0002.JPG", b"new")
    d.syncNow.emit(False)  # a later Photos check is not blocked either
    assert pump(lambda: (root / "2025/11/IMG_0002.JPG").exists(), 10)
    assert w.drive_running()
    gate.set()
    assert pump(lambda: not w.drive_running() and states and states[-1][0] == "idle", 10), states[-3:]
    assert (tmp_path / "Drive/Docs/a.txt").read_bytes() == b"a"


def test_requests_from_two_threads_are_serialized():
    import threading

    class Session:
        def __init__(self):
            self.active = 0
            self.max_active = 0

        def request(self, method, url, **kw):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            time.sleep(0.02)
            self.active -= 1
            return url

    class Api:
        session = Session()

    api = Api()
    wk.SyncWorker._serialize_requests(api)
    threads = [threading.Thread(target=lambda: [api.session.request("GET", "u") for _ in range(5)]) for _ in range(4)]
    for x in threads:
        x.start()
    for x in threads:
        x.join()
    assert api.session.max_active == 1


def test_rejected_request_renews_session_silently(qt, monkeypatch):
    """Apple's 421/450 'renew your web token' must not end in a sign-in prompt."""
    from skyfold.remote import AuthRequired

    app, w, th, d, remote, root, states, pend, notes = qt
    calls = {"n": 0}
    real_cursor = remote.cursor

    def flaky_cursor():
        calls["n"] += 1
        if calls["n"] == 2:  # second sync: Apple rejects once
            raise AuthRequired("421")
        return real_cursor()

    remote.cursor = flaky_cursor
    restores = []
    monkeypatch.setattr(wk.auth, "restore", lambda _id: restores.append(1) or object())
    th.start()
    assert pump(lambda: states and states[-1][0] == "idle")
    remote.add("A2", "IMG_0002.JPG", b"x")
    d.syncNow.emit(True)
    assert pump(lambda: (root / "2025/11/IMG_0002.JPG").exists(), 10), states[-4:]
    assert all(s[0] != "auth" for s in states) and len(restores) >= 2


def test_really_expired_session_asks_to_sign_in(qt, monkeypatch):
    from skyfold.remote import AuthRequired

    app, w, th, d, remote, root, states, pend, notes = qt
    th.start()
    assert pump(lambda: states and states[-1][0] == "idle")

    def dead():
        raise AuthRequired("expired")

    remote.cursor = dead
    monkeypatch.setattr(wk.auth, "restore", lambda _id: None)  # saved session no longer valid
    d.syncNow.emit(True)
    assert pump(lambda: states and states[-1][0] == "auth", 10), states[-4:]


def test_activity_history_can_be_cleared(tmp_path):
    from skyfold.state import State

    s = State(tmp_path / "s.db")
    s.log_activity("2026-11-08T17:00:00", "error", "Download failed (x)")
    s.log_activity("2026-11-08T17:01:00", "download", "Downloaded 2 new files", "C:/a.jpg")
    assert len(s.recent_activity()) == 2
    s.clear_activity()
    assert s.recent_activity() == []


def test_settings_survive_a_damaged_file(tmp_path, monkeypatch):
    import json
    from skyfold import config

    f = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", f)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    f.write_text(json.dumps({"apple_id": "a@b.c", "drive_folders": "Documents", "speed_limit_mb": "fast",
                             "folder_structure": "weird", "paused": "yes", "poll_minutes": 5}), encoding="utf-8")
    s = config.Settings.load()
    assert (s.apple_id, s.drive_folders, s.speed_limit_mb, s.folder_structure, s.paused, s.poll_minutes) == \
        ("a@b.c", None, 0, "flat", False, 5)
    f.write_text("[]", encoding="utf-8")
    assert config.Settings.load() == config.Settings()
