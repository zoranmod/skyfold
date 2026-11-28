"""A Stop pressed just after the Drive thread decided to start a pass still stops that pass: the worker
clears Drive's stop flag under the lock a Stop takes, and the pass itself no longer clears it."""
import threading
import time

import pytest

from skyfold import drive_engine as de
from skyfold import worker
from skyfold.config import Settings
from skyfold.engine import Cancelled


class _NoRemote:
    def __getattr__(self, name):
        raise AssertionError("the pass must not reach iCloud")


def test_a_stop_before_the_pass_starts_is_kept(tmp_path):
    s = de.DriveSyncer(de.DriveState(tmp_path / "d.db"), _NoRemote(), tmp_path / "Drive", 25)
    s.cancel_cleared_by_caller = True  # (as the worker sets it)
    s.cancel_flag.set()  # Stop, between the worker's decision and run()
    with pytest.raises(Cancelled):
        s.run()


def test_a_stop_waits_for_the_drive_threads_decision(tmp_path):
    w = worker.SyncWorker(Settings())
    w.drive = de.DriveSyncer(de.DriveState(tmp_path / "d.db"), _NoRemote(), tmp_path / "Drive", 25)
    done = threading.Event()
    with w._drive_lock:  # the Drive thread is deciding
        threading.Thread(target=lambda: (w.cancel(), done.set()), daemon=True).start()
        time.sleep(0.2)
        assert not done.is_set()  # the Stop waits for the decision, then reaches the pass
    assert done.wait(2) and w.drive.cancel_flag.is_set() and w._drive_stopped
