"""1.0.19: new Drive files go up several at a time; replacements go alone, after them."""
import threading
import time

import pytest

from skyfold import drive_engine as de
from skyfold.drive_remote import UPLOAD_WORKERS
from skyfold.remote import RemoteUnavailable
from tests.test_drive import env, write  # noqa: F401


def streaming(remote, body):
    """FakeDrive with the real adapter's on_bytes protocol around its own upload."""
    real = remote.upload
    remote.streams_progress = True

    def upload(parent, local, data=None, on_bytes=None):
        body(local, on_bytes)
        return real(parent, local)

    remote.upload = upload


def test_new_files_are_sent_at_the_same_time(env):  # noqa: F811
    sync, remote, root, _ = env
    sync.run()
    for i in range(UPLOAD_WORKERS):
        write(root, f"v{i}.mov", b"x" * 1000, age=300)
    together = threading.Barrier(UPLOAD_WORKERS, timeout=5)

    def body(local, on_bytes):
        on_bytes(500)
        together.wait()  # only passes when all of them are being sent at once
        on_bytes(500)

    streaming(remote, body)
    res = sync.run()
    assert res.uploaded == UPLOAD_WORKERS and res.errors == 0
    assert sorted(remote.uploads) == [f"v{i}.mov" for i in range(UPLOAD_WORKERS)]
    rows = sync.state.all()
    assert all(rows[f"v{i}.mov"].hash and not rows[f"v{i}.mov"].pending for i in range(UPLOAD_WORKERS))


def test_a_replacement_goes_alone_after_the_new_files(env):  # noqa: F811
    sync, remote, root, _ = env
    remote.put("old.txt", b"v1")
    sync.run()
    write(root, "old.txt", b"v2 from the PC", age=300)
    write(root, "a.mov", b"a" * 100, age=300)
    write(root, "b.mov", b"b" * 100, age=300)
    active, peak, order = [0], [0], []
    lock = threading.Lock()

    def body(local, on_bytes):
        with lock:
            active[0] += 1
            order.append((local.name, active[0]))
        time.sleep(0.05)
        on_bytes(1)
        with lock:
            active[0] -= 1

    streaming(remote, body)
    sync.run()
    assert remote.items["old.txt"]["data"] == b"v2 from the PC"
    assert order[-1] == ("old.txt", 1)  # last, and nothing else was being sent with it


def test_one_lost_connection_stops_the_others_unrecorded(env):  # noqa: F811
    sync, remote, root, _ = env
    sync.run()
    for name in ("a.mov", "b.mov", "c.mov"):
        write(root, name, b"z" * 100, age=300)
    started = threading.Barrier(3, timeout=5)
    stopped = []

    def body(local, on_bytes):
        started.wait()
        if local.name == "a.mov":
            raise RemoteUnavailable("connection lost")
        for _ in range(200):  # the others keep sending until told to stop
            try:
                on_bytes(1)
            except de.Cancelled:
                stopped.append(local.name)
                raise
            time.sleep(0.01)

    streaming(remote, body)
    with pytest.raises(RemoteUnavailable):
        sync.run()
    assert sorted(stopped) == ["b.mov", "c.mov"]
    assert not remote.uploads and not any(p.endswith(".mov") for p in sync.state.all())


def test_stop_stops_every_upload_in_flight(env):  # noqa: F811
    sync, remote, root, _ = env
    sync.run()
    for name in ("a.mov", "b.mov"):
        write(root, name, b"z" * 100, age=300)
    started = threading.Barrier(2, timeout=5)

    def body(local, on_bytes):
        started.wait()
        sync.cancel_flag.set()  # the Stop button
        for _ in range(200):
            on_bytes(1)
            time.sleep(0.01)
        raise AssertionError("not stopped")

    streaming(remote, body)
    with pytest.raises(de.Cancelled):
        sync.run()
    assert not remote.uploads
