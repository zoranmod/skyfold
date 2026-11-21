"""1.0.20: photos and videos go up two at a time; this thread keeps every decision."""
import os
import threading
import time

import pytest

from skyfold import engine
from tests.test_drive import write
from tests.test_engine import env  # noqa: F401  (fixture)


def streaming(remote, body):
    real = remote.upload
    remote.streams_progress = True

    def upload(path, on_bytes=None):
        body(path, on_bytes)
        return real(path)

    remote.upload = upload


def test_two_photos_are_sent_at_the_same_time(env):  # noqa: F811
    s, remote, root, _ = env
    s.settings.photos_upload = True
    s.run()
    for name in ("a.mov", "b.mov"):
        write(root, name, name.encode() * 500, age=300)
    together = threading.Barrier(engine.Syncer.PHOTO_UPLOAD_WORKERS, timeout=5)
    writers = []
    put = s.state.set

    def record(key, value):
        writers.append(threading.get_ident())
        put(key, value)

    s.state.set = record

    def body(path, on_bytes):
        on_bytes(100)
        together.wait()  # passes only when both are being sent at once
        on_bytes(100)

    streaming(remote, body)
    s.run()
    assert sorted(remote.uploads) == ["a.mov", "b.mov"]
    assert set(writers) == {threading.get_ident()}  # the database is written by the sync thread only


def test_files_stay_locked_while_they_are_sent(env):  # noqa: F811
    if os.name != "nt":
        pytest.skip("Windows share modes")
    s, remote, root, _ = env
    s.settings.photos_upload = True
    s.run()
    p = write(root, "clip.mov", b"v" * 1000, age=300)
    refused = []

    def body(path, on_bytes):
        try:
            with open(p, "r+b"):
                pass
        except PermissionError:
            refused.append(True)
        on_bytes(1000)

    streaming(remote, body)
    s.run()
    assert refused and remote.uploads == ["clip.mov"]
    with open(p, "r+b"):  # unlocked again afterwards
        pass


def test_stop_stops_both_uploads_unregistered(env):  # noqa: F811
    s, remote, root, _ = env
    s.settings.photos_upload = True
    s.run()
    for name in ("a.mov", "b.mov"):
        write(root, name, name.encode() * 500, age=300)
    started = threading.Barrier(2, timeout=5)

    def body(path, on_bytes):
        started.wait()
        s.cancel_flag.set()  # the Stop button
        for _ in range(200):
            on_bytes(1)
            time.sleep(0.01)
        raise AssertionError("not stopped")

    streaming(remote, body)
    with pytest.raises(engine.Cancelled):
        s.run()
    assert getattr(remote, "uploads", []) == []
