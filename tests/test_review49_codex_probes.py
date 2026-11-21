"""Release 1.0.20 probes. Exercise real pyicloud requests with local responses only."""

import hashlib
import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
from pyicloud.services.photos_cloudkit.client import PhotosCloudKitClient
from pyicloud.services.photos_cloudkit.service import PhotosService

from skyfold import config, drive_remote
from skyfold.engine import Cancelled
from skyfold.remote import AuthRequired, ICloudRemote, RemoteUnavailable
from skyfold.worker import _apply_upload_buffer
from tests.test_drive import write
from tests.test_engine import env
from tests.test_review48_codex_probes import photos_with, success


@pytest.mark.parametrize("fault", ["connect-timeout", "http-403", "http-408", "http-503", "per-file-422"])
def test_registration_that_could_not_save_is_retried(env, tmp_path, monkeypatch, fault):
    sync, _, root, _ = env
    sync.run()
    path = write(root, "trip.mov", b"holiday", age=300)
    healthy = False
    registered = []

    def reply(request):
        if urlsplit(request.url).path.endswith("/putAsset"):
            if healthy:
                registered.append(path.name)
            elif fault == "connect-timeout":
                raise requests.ConnectTimeout("TCP connection was never established")
            elif fault == "per-file-422":
                return 200, [{"response": {"status": 422}}]
            else:
                return int(fault.split("-")[1]), {}
        return success(request)

    sync.remote, _, _ = photos_with(tmp_path, monkeypatch, reply)
    try:
        sync.upload_new()
    except (AuthRequired, RemoteUnavailable):
        pass
    assert not registered
    healthy = True
    later = time.time() + 7200
    monkeypatch.setattr(time, "time", lambda: later)
    sync.upload_new()
    assert registered == [path.name], sync.state.items("upload_lost:")


@pytest.mark.parametrize("outcome", ["lost-read", "duplicate"])
def test_existing_registration_is_not_replayed(env, tmp_path, monkeypatch, outcome):
    sync, _, root, _ = env
    sync.run()
    path = write(root, "registered.mov", b"video", age=300)
    attempts = []

    def reply(request):
        if urlsplit(request.url).path.endswith("/putAsset"):
            attempts.append(path.name)
            if outcome == "lost-read":
                raise requests.ReadTimeout("server saved the asset, then the reply timed out")
            return 200, [{"cplAsset": "existing", "response": {"status": 409}}]
        return success(request)

    sync.remote, _, _ = photos_with(tmp_path, monkeypatch, reply)
    sync.upload_new()
    sync.upload_new()
    assert attempts == [path.name]
    if outcome == "lost-read":
        assert sync.state.get("upload_lost:" + hashlib.sha256(b"video").hexdigest())
    else:
        assert sync.state.get("upload_sent:existing")


def test_account_without_upload_endpoint_does_not_mark_files_sent(env, tmp_path, monkeypatch):
    sync, _, root, _ = env
    sync.run()
    write(root, "waiting.jpg", b"camera", age=300)
    requests_made = []

    def reply(request):
        requests_made.append(request.url)
        return success(request)

    working, _, session = photos_with(tmp_path, monkeypatch, reply)
    # pyicloud constructs this client even when the account only advertises ckdatabasews.
    client = PhotosCloudKitClient(base_url="https://photos.example", session=session, base_params={})
    library = SimpleNamespace(_client=client, zone_id={"zoneName": "root"})
    sync.remote = ICloudRemote(SimpleNamespace(photos=SimpleNamespace(libraries={"root": library})))
    try:
        sync.upload_new()
    except RemoteUnavailable:
        pass
    assert requests_made == []
    sync.remote = working  # refreshed account service list now includes photosupload
    later = time.time() + 7200
    monkeypatch.setattr(time, "time", lambda: later)
    assert sync.upload_new() == 1, sync.state.items("upload_lost:")


def test_outage_discovering_library_does_not_delay_files(env, tmp_path, monkeypatch):
    sync, _, root, _ = env
    sync.run()
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        write(root, name, name.encode(), age=300)
    zones = []

    def offline(request):
        if urlsplit(request.url).path.endswith("/records/query"):
            return 200, {"records": [{"recordName": "index", "recordType": "CheckIndexingState",
                                      "fields": {"state": {"type": "STRING", "value": "FINISHED"}}}]}
        assert "/zones/list" in request.url
        zones.append(request.url)
        raise requests.ConnectTimeout("offline while discovering the private library")

    _, _, session = photos_with(tmp_path, monkeypatch, offline)
    photos = PhotosService("https://photos.example", session, {}, None, None,
                           photos_upload_url="https://photos.example")
    sync.remote = ICloudRemote(SimpleNamespace(photos=photos))
    try:
        sync.upload_new()
    except RemoteUnavailable:
        pass
    assert zones
    assert sync.state.items("upload_retry:") == []


def test_upload_only_rate_limit_keeps_incoming_photos_syncing(env, tmp_path, monkeypatch):
    sync, cloud, root, _ = env
    sync.settings.photos_upload = True
    sync.run()
    cloud.add("iphone-id", "from-phone.jpg", b"from the phone")
    write(root, "from-pc.mov", b"from the pc", age=300)
    refused = []

    def busy(request):
        if urlsplit(request.url).path.endswith("/putAsset"):
            refused.append(request.url)
            return 429, {}
        return success(request)

    upload_remote, _, _ = photos_with(tmp_path, monkeypatch, busy)
    monkeypatch.setattr(cloud, "upload", upload_remote.upload)
    monkeypatch.setattr(cloud, "streams_progress", True, raising=False)
    for _ in range(2):
        try:
            sync.run()
        except RemoteUnavailable:
            pass
    assert refused
    assert [p.read_bytes() for p in root.rglob("from-phone.jpg")] == [b"from the phone"]


@pytest.mark.skipif(os.name != "nt", reason="Windows file identity")
@pytest.mark.parametrize("with_other_file", [False, True], ids=["next-pass", "same-pass"])
def test_rejected_hard_link_respects_the_files_retry_delay(env, tmp_path, monkeypatch, with_other_file):
    sync, _, root, _ = env
    sync.run()
    path = write(root, "a.mov", b"shared inode", age=300)
    os.link(path, root / "c.mov")
    refusal_observed = threading.Event()
    if with_other_file:
        write(root, "b.mov", b"other", age=300)
        sync.cb.activity = lambda kind, *args: refusal_observed.set() if kind == "error" else None
    refused = []

    def reply(request):
        if urlsplit(request.url).path == "/5":
            # The independent transfer is still active when the coordinator handles a's refusal.
            assert refusal_observed.wait(5)
        if urlsplit(request.url).path.endswith("/putAsset"):
            if json.loads(request.body)["files"][0]["fileName"] == "b.mov":
                return success(request)
            refused.append(request.url)
            return 200, [{"response": {"status": 422}}]
        return success(request)

    sync.remote, _, _ = photos_with(tmp_path, monkeypatch, reply)
    sync.upload_new()
    assert len(refused) == 1
    sync.upload_new()
    assert len(refused) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows deny-write sharing")
def test_stop_releases_unsubmitted_file_before_waiting_for_slow_peer(env, monkeypatch):
    sync, cloud, root, _ = env
    sync.run()
    write(root, "a.mov", b"first", age=300)
    second = write(root, "b.mov", b"second", age=300)
    waiting = threading.Event()
    reply = threading.Event()
    writable = []
    digest = hashlib.file_digest
    shutdown = ThreadPoolExecutor.shutdown

    def stop_during_second_hash(stream, algorithm):
        if os.fstat(stream.fileno()).st_ino == second.stat().st_ino:
            assert waiting.wait(5)
            sync.cancel_flag.set()
        return digest(stream, algorithm)

    def upload(file, on_bytes):
        on_bytes(file.stat().st_size)
        waiting.set()
        assert reply.wait(5)
        on_bytes(0)

    def drain(pool, *args, **kwargs):
        if not reply.is_set():
            try:
                with second.open("r+b"):
                    writable.append(True)
            except PermissionError:
                writable.append(False)
            finally:
                reply.set()
        return shutdown(pool, *args, **kwargs)

    monkeypatch.setattr(hashlib, "file_digest", stop_during_second_hash)
    monkeypatch.setattr(ThreadPoolExecutor, "shutdown", drain)
    monkeypatch.setattr(cloud, "upload", upload)
    monkeypatch.setattr(cloud, "streams_progress", True, raising=False)
    with pytest.raises(Cancelled):
        sync.upload_new()
    assert writable == [True]


def test_legacy_settings_use_automatic_on_direct_and_proxy_connections(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(drive_remote, "SEND_BUFFER", 8 << 20)
    _apply_upload_buffer(config.Settings.load())
    assert drive_remote.SEND_BUFFER == 0
    url = "https://upload.example/content"
    with requests.Session() as session:
        drive_remote._big_send_buffer(session, url)
        adapter = session.get_adapter(url)
        proxy = adapter.proxy_manager_for("http://proxy.example:8080")
        for pool in (adapter.poolmanager, proxy):
            options = pool.connection_pool_kw["socket_options"]
            assert (socket.SOL_SOCKET, socket.SO_SNDBUF) not in [o[:2] for o in options]
        assert drive_remote._adapter_buffer(session, url) == 0
