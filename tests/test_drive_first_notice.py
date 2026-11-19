"""'iCloud Drive is ready' comes after the first full pass, also when a quick pass ran before it."""
from __future__ import annotations

from types import SimpleNamespace

from skyfold.config import Settings
from skyfold.state import State
from skyfold.worker import SyncWorker


class _Drive:
    def __init__(self):
        self.remote = None

    def run(self, *a, **k):
        full = k.get("changed") is None and not any(isinstance(x, set) for x in a)
        return SimpleNamespace(full=full, followup=False, pending_to_cloud=[], pending_to_pc=[],
                               downloaded=7 if full else 1, uploaded=0, deleted_local=0, deleted_cloud=0,
                               errors=0)


def _worker(tmp_path):
    w = SyncWorker(Settings(drive_enabled=True, drive_dir=str(tmp_path / "d"), notifications=False))
    w.state = State(tmp_path / "w.db")
    w.drive = _Drive()
    seen = []
    w.notify.connect(lambda title, text: seen.append(title))
    return w, seen


def test_quick_pass_first_then_full_pass_still_says_ready(tmp_path):
    w, seen = _worker(tmp_path)
    assert w._run_drive([], {"Docs/a.txt"})  # a change on the PC before the first full read
    assert seen == []
    assert w._run_drive([])
    assert seen == ["iCloud Drive is ready"]
    assert w._run_drive([])
    assert seen == ["iCloud Drive is ready"]  # once


def test_existing_setup_gets_no_ready_notice(tmp_path):
    w, seen = _worker(tmp_path)
    w.state.set("drive_last_sync", "2026-11-01T00:00:00")  # synced before this version
    assert w._run_drive([])
    assert seen == []
