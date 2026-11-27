"""A watcher starting on the worker must be retired if Quit drains the registry first."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from skyfold import worker
from skyfold.config import Settings


def test_shutdown_during_watcher_construction(tmp_path, monkeypatch):
    starting = Event()
    resume = Event()
    stopped = Event()

    class StartingWatcher:
        def __init__(self, root, hit):
            starting.set()
            assert resume.wait(5)

        def stop(self):
            stopped.set()

    monkeypatch.setattr(worker, "_Watcher", StartingWatcher)
    sync = worker.SyncWorker(Settings(photos_dir=str(tmp_path)))
    with ThreadPoolExecutor(max_workers=1) as pool:
        started = pool.submit(sync._ensure_watchers)
        try:
            assert starting.wait(5)
            sync.shutdown()
        finally:
            resume.set()
        started.result(timeout=5)

    assert stopped.is_set()
    assert not sync._watchers
