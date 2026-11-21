"""Parallel Photos uploads against the pinned pyicloud uploader and offline transports."""

import hashlib
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
from pyicloud.services.photos_cloudkit.upload import PhotosUploader
from pyicloud.session import PyiCloudSession

from skyfold import drive_remote
from skyfold.config import Settings
from skyfold.engine import Cancelled
from skyfold.remote import AuthRequired, ICloudRemote, RemoteUnavailable, UploadReceipt
from skyfold.worker import SyncWorker, _apply_upload_buffer
from tests.test_drive import write
from tests.test_engine import env


def success(request):
    route = urlsplit(request.url).path
    if route.endswith("/createUploadUrl"):
        key, size = next(iter(json.loads(request.body)["assets"].items()))
        return 200, {"uploadUrls": {key: f"https://body.example/{size}"}}
    if route.endswith("/putAsset"):
        name = json.loads(request.body)["files"][0]["fileName"]
        return 200, [{"cplAsset": name, "response": {"status": 200}}]
    assert urlsplit(request.url).hostname == "body.example"
    size = sum(map(len, iter(lambda: request.body.read(16384), b"")))
    return 200, {"singleFile": {"size": size}}


def photos_with(tmp_path, monkeypatch, reply):
    service = SimpleNamespace(account_name="review@example.invalid", requires_2sa=False)
    session = PyiCloudSession(service, "review", str(tmp_path))
    session.trust_env = False

    def send(request, **kwargs):
        status, data = reply(request)
        response = requests.Response()
        response.status_code, response.request, response.url = status, request, request.url
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(data).encode()
        return response

    monkeypatch.setattr(session, "send", send)
    SyncWorker._serialize_requests(SimpleNamespace(session=session))
    uploader = PhotosUploader(session=session, base_url="https://photos.example", base_params={}, timeout=(2, 3))
    library = SimpleNamespace(_client=SimpleNamespace(_uploader=uploader), zone_id={"zoneName": "z"})
    cloud = ICloudRemote(SimpleNamespace(photos=SimpleNamespace(libraries={"root": library})))
    return cloud, uploader, session


@pytest.mark.parametrize("fault", ["http-429", "retryable-503", "reservation-without-url"])
def test_known_unsaved_file_remains_retryable(env, tmp_path, monkeypatch, fault):
    sync, _, root, _ = env
    sync.run()
    path = write(root, "clip.mov", b"video", age=300)
    failing = True
    registered = []

    def reply(request):
        route = urlsplit(request.url).path
        if failing and route.endswith("/createUploadUrl") and fault == "reservation-without-url":
            return 200, {"uploadUrls": {}}
        if route.endswith("/putAsset"):
            if failing and fault == "http-429":
                return 429, {"reason": "Too many requests"}
            if failing and fault == "retryable-503":
                return 200, [{"response": {"status": 503, "isRetryable": True}}]
            registered.append(path.name)
        return success(request)

    sync.remote, _, _ = photos_with(tmp_path, monkeypatch, reply)
    try:
        sync.upload_new()
    except (AuthRequired, RemoteUnavailable, requests.ConnectionError):
        pass  # terminating the pass and leaving the file retryable is also valid
    assert not registered
    failing = False
    later = time.time() + 7200
    monkeypatch.setattr(time, "time", lambda: later)
    sync.upload_new()
    assert registered == [path.name], sync.state.items("upload_lost:")


def test_lost_content_connection_stops_other_unregistered_uploads_and_new_work(env, tmp_path, monkeypatch):
    sync, _, root, _ = env
    sync.run()
    for i, name in enumerate(("a.mov", "b.mov", "c.mov"), 1):
        write(root, name, b"x" * i, age=300)
    together = threading.Barrier(2, timeout=5)
    reported = threading.Event()
    registered = []
    reserved_sizes = []
    sync.cb.activity = lambda kind, *args: reported.set() if kind == "error" else None

    def reply(request):
        route = urlsplit(request.url).path
        if route.endswith("/createUploadUrl"):
            reserved_sizes.extend(json.loads(request.body)["assets"].values())
        if route in ("/1", "/2"):
            together.wait()
            if route == "/1":
                raise requests.ConnectionError("lost content connection")
            # A slow second file is still transferring when the first failure is observed.
            reported.wait(1)
            return success(request)
        if route.endswith("/putAsset"):
            registered.append(json.loads(request.body)["files"][0]["fileName"])
        return success(request)

    sync.remote, _, _ = photos_with(tmp_path, monkeypatch, reply)
    try:
        sync.upload_new()
    except (AuthRequired, RemoteUnavailable, requests.ConnectionError, Cancelled):
        pass
    assert sorted(reserved_sizes[:2]) == [1, 2]
    assert not registered and 3 not in reserved_sizes, (registered, reserved_sizes)


@pytest.mark.parametrize("outcome", ["receipt", "lost-reply"])
def test_stop_drain_preserves_outcome_of_in_flight_registration(env, monkeypatch, outcome):
    sync, cloud, root, _ = env
    sync.run()
    path = write(root, "stop.mov", b"video", age=300)
    release_reply = threading.Event()
    shutdown = ThreadPoolExecutor.shutdown
    registrations = []

    def release_on_shutdown(pool, *args, **kwargs):
        release_reply.set()  # registration is on the server, and the coordinator is draining workers
        return shutdown(pool, *args, **kwargs)

    def upload(file, on_bytes):
        on_bytes(file.stat().st_size)
        on_bytes(0)
        registrations.append(file.name)
        if len(registrations) == 1:
            sync.cancel_flag.set()
            if not release_reply.wait(5):
                raise AssertionError("coordinator did not drain")
            if outcome == "lost-reply":
                raise TimeoutError("putAsset reply lost after registration")
        return UploadReceipt("asset", False)

    monkeypatch.setattr(ThreadPoolExecutor, "shutdown", release_on_shutdown)
    cloud.streams_progress, cloud.upload = True, upload
    with pytest.raises(Cancelled):
        sync.upload_new()
    sync.cancel_flag.clear()
    sync.upload_new()
    assert registrations == [path.name], "an already registered video was sent again"
    key = "upload_lost:" + hashlib.sha256(b"video").hexdigest() if outcome == "lost-reply" else "upload_sent:asset"
    assert sync.state.get(key)


def test_pinned_uploader_copies_use_independent_counters_and_receipts(tmp_path, monkeypatch):
    together = threading.Barrier(2, timeout=5)

    def reply(request):
        if urlsplit(request.url).hostname == "body.example":
            together.wait()
            assert uploader._session is session
        return success(request)

    remote, uploader, session = photos_with(tmp_path, monkeypatch, reply)
    files = [write(tmp_path, f"{i}.jpg", b"x" * n) for i, n in enumerate((220000, 530000))]
    counts = [[], []]
    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(remote.upload, file, on_bytes=counts[i].append) for i, file in enumerate(files)]
        assert [job.result(8).asset_id for job in jobs] == [file.name for file in files]
    assert [sum(events) for events in counts] == [file.stat().st_size for file in files]
    assert uploader._session is session


def test_real_default_attempt_cap_and_continuation(env):
    sync, cloud, root, _ = env
    sync.run()
    for i in range(103):
        write(root, f"shot-{i:03}.jpg", str(i).encode(), age=300)
    calls = []

    def upload(path, on_bytes):
        on_bytes(path.stat().st_size)
        calls.append(path.name)
        return UploadReceipt(path.name, False)

    cloud.streams_progress, cloud.upload = True, upload
    assert sync.upload_new() == 100
    assert sync.upload_new() == 3
    assert len(set(calls)) == len(calls) == 103


@pytest.mark.skipif(os.name != "nt", reason="Windows NTFS file identity")
def test_two_names_for_one_local_photo_only_send_its_bytes_once(env):
    sync, cloud, root, _ = env
    sync.run()
    path = write(root, "first.mov", b"video", age=300)
    os.link(path, root / "second.mov")
    calls = []

    def upload(path, on_bytes):
        calls.append(path.name)
        on_bytes(path.stat().st_size)
        return UploadReceipt("asset", path.name == "second.mov")

    cloud.streams_progress, cloud.upload = True, upload
    sync.upload_new()
    assert len(calls) == 1, calls


def test_drive_speed_log_uses_buffer_of_request_that_just_finished(tmp_path, monkeypatch, caplog):
    from tests.test_drive_streamed_upload import FakeApi, PARENT, adapter

    path = write(tmp_path, "clip.mov", b"x" * (2 << 20))
    api = FakeApi(keep_body=False)
    monkeypatch.setattr(drive_remote, "SEND_BUFFER", 8 << 20)
    stamps = iter((10.0, 12.0))
    monkeypatch.setattr(drive_remote, "time", SimpleNamespace(monotonic=lambda: next(stamps)))

    class Session(requests.Session):
        def post(self, url, **kwargs):
            assert self.get_adapter(url).buffer == 8 << 20
            response = api.post(url, **kwargs)
            _apply_upload_buffer(Settings(upload_buffer_mb=0))  # changed during a background Drive upload
            return response

    with Session() as api.session, caplog.at_level(logging.INFO, logger="skyfold.drive_remote"):
        adapter(api).upload(PARENT, path)
    assert "send buffer 8 MB" in caplog.text, caplog.text


def test_stop_during_preflight_hash_skips_reservation(env, tmp_path, monkeypatch):
    sync, _, root, _ = env
    sync.run()
    write(root, "video.mov", b"video", age=300)
    calls = []
    file_digest = hashlib.file_digest

    def interrupted_digest(*args, **kwargs):
        sync.cancel_flag.set()  # Stop while the sync thread reads a local video
        return file_digest(*args, **kwargs)

    def reply(request):
        calls.append(urlsplit(request.url).path)
        return success(request)

    monkeypatch.setattr(hashlib, "file_digest", interrupted_digest)
    sync.remote, _, _ = photos_with(tmp_path, monkeypatch, reply)
    with pytest.raises(Cancelled):
        sync.upload_new()
    assert not calls, calls
