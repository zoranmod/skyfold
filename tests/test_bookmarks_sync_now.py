"""Sync now refreshes the Safari bookmarks file (not only every 6 hours)."""
from __future__ import annotations

import time

from skyfold.config import Settings
from skyfold.worker import SyncWorker


def _worker():
    w = SyncWorker(Settings(photos_dir_confirmed=True, bookmarks_enabled=True, notifications=False))
    w.api = object()
    w.notes = None
    w._run_photos = lambda *a: True
    w._refresh_storage = lambda **kw: None
    w._emit_stats = lambda: None
    w._ensure_watchers = lambda: None
    runs = []
    w._run_bookmarks = lambda problems: runs.append(1) or True
    w._bookmarks_last = time.monotonic()  # refreshed a moment ago
    return w, runs


def test_automatic_pass_waits_for_the_interval():
    w, runs = _worker()
    w._cycle(False, False)
    assert runs == []


def test_sync_now_refreshes_bookmarks():
    w, runs = _worker()
    w._cycle(False, True)  # Sync now
    assert runs == [1]
