"""Release-path probes for the Drive pool, upload recovery and Notes migration."""

import cProfile
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS

import pytest
import requests
from pyicloud.exceptions import PyiCloudConnectionException

from skyfold import drive_engine as de
from skyfold.drive_remote import UPLOAD_WORKERS
from skyfold.engine import Cancelled
from skyfold.remote import ICloudRemote, UploadRejected
from skyfold.worker import SyncWorker
from tests.test_drive import env as drive_env, write
from tests.test_drive_streamed_upload import FakeApi, PARENT, adapter
from tests.test_notes import env as notes_env


def use_stream(remote, upload):
    remote.streams_progress = True
    remote.upload = upload


@pytest.mark.parametrize("reply", [
    {"status": {"status_code": 2, "error_message": "registration refused"}},
    {"results": [{"status": {"status_code": 2, "error_message": "registration refused"}}]},
], ids=["request-refused", "item-refused"])
def test_missing_document_confirmation_keeps_the_only_pc_copy(drive_env, reply):
    from pyicloud.services.drive import DriveService

    sync, remote, root, recycled = drive_env
    sync.run()
    path = write(root, "report.pdf", b"only copy")
    api = FakeApi()
    api._document_root, api.params = "https://docs.example", {}
    api._update_contentws = DriveService._update_contentws.__get__(api)

    def post(url, **kwargs):
        if url.endswith("/update/documents"):
            return NS(json=lambda: reply)
        api.post(url, **kwargs)
        return NS(json=lambda: {"singleFile": {"size": path.stat().st_size, "fileChecksum": "sig",
                                              "wrappingKey": "key", "referenceChecksum": "ref"}})

    api.session.post = post
    use_stream(remote, adapter(api).upload)
    first = sync.run()
    sync.run()
    sync.run()
    assert path.is_file() and first.uploaded == 0, (first.uploaded, recycled)


@pytest.mark.skipif(os.name != "nt", reason="Windows share modes")
def test_readable_file_open_for_writing_keeps_cloud_original(drive_env):
    sync, remote, root, _ = drive_env
    remote.put("budget.txt", b"cloud copy")
    sync.run()
    path = write(root, "budget.txt", b"edited locally")
    use_stream(remote, adapter(FakeApi()).upload)
    with path.open("r+b"):  # A Windows editor can allow reads and retain its write handle.
        result = sync.run()
    assert result.uploaded == 0
    assert not remote.trashed and remote.items["budget.txt"]["data"] == b"cloud copy"


def test_desktop_session_transmits_three_new_bodies_concurrently(drive_env):
    sync, remote, root, _ = drive_env
    sync.run()
    for i in range(UPLOAD_WORKERS):
        write(root, f"{i}.mov", b"video")
    lock = threading.Lock()
    together = threading.Event()
    api = FakeApi(keep_body=False)
    active = peak = 0

    class Session(requests.Session):
        def request(self, method, url, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == UPLOAD_WORKERS:
                    together.set()
            try:
                together.wait(1)
                while kwargs["data"].read(16384):
                    pass
                return NS(json=lambda: {"singleFile": {"size": 5}})
            finally:
                with lock:
                    active -= 1

    with Session() as session:
        SyncWorker._serialize_requests(NS(session=session))  # installed by set_api in the desktop app
        api.session = session
        api._update_contentws = lambda *a, **kw: {
            "results": [{"document": {"document_id": "DOC", "name": a[3].name}}]
        }
        use_stream(remote, adapter(api).upload)
        assert sync.run().uploaded == UPLOAD_WORKERS
    assert peak == UPLOAD_WORKERS


def test_connection_failure_stops_peers_and_does_not_touch_replacement(drive_env):
    sync, remote, root, _ = drive_env
    remote.put("old.txt", b"cloud")
    sync.run()
    for name in ("a.mov", "b.mov", "c.mov", "old.txt"):
        write(root, name, b"PC edit")
    register = remote.upload
    failure_seen = threading.Event()
    sync.cb.activity = lambda kind, *args: failure_seen.set() if kind == "error" else None

    def send(parent, path, on_bytes):
        if path.name == "a.mov":
            raise PyiCloudConnectionException("content server stopped responding")
        # Siblings are still sending when the sync thread sees the network failure.
        for _ in range(150):
            if failure_seen.wait(0.01):
                break
            on_bytes(0)
        on_bytes(path.stat().st_size)
        on_bytes(0)
        return register(parent, path)

    use_stream(remote, send)
    try:
        sync.run()
    except (PyiCloudConnectionException, Cancelled):
        pass
    assert (remote.uploads, remote.trashed) == ([], [])


def test_lost_replacement_registration_reply_does_not_duplicate_identical_bytes(drive_env):
    sync, remote, root, _ = drive_env
    remote.put("draft.txt", b"v1")
    sync.run()
    write(root, "draft.txt", b"edited draft")
    register = remote.upload

    def lose_reply(parent, path):
        register(parent, path)  # iCloud saved the file before the response was lost
        raise PyiCloudConnectionException("lost registration response")

    remote.upload = lose_reply
    try:
        sync.run()
    except PyiCloudConnectionException:
        pass
    remote.upload = register
    sync.run()
    sync.run()
    assert list(remote.items) == ["draft.txt"], sorted(remote.items)
    assert remote.items["draft.txt"]["data"] == b"edited draft"


def test_confirmed_drive_upload_is_not_downloaded_again_to_learn_its_etag(drive_env):
    sync, remote, root, _ = drive_env
    sync.run()
    path = write(root, "holiday.mov", b"original content")
    api = FakeApi()
    downloads = []
    download = remote.download

    def register(folder, info, document_id, file_object, zone, **kwargs):
        remote.put(file_object.name, path.read_bytes())
        remote.items[file_object.name]["id"] = document_id
        item = remote._item(file_object.name)
        doc = {"document_id": document_id, "etag": item.etag, "name": item.path,
               "parent_id": folder, "size": item.size, "zone": zone}
        return {"results": [{"document": doc}]}

    def fetch(item, dest, on_bytes):
        downloads.append(item.size)
        return download(item, dest, on_bytes)

    api._update_contentws = register
    use_stream(remote, adapter(api).upload)
    remote.download = fetch
    assert sync.run().uploaded == 1
    sync.run()
    assert not downloads, f"{sum(downloads)} bytes re-downloaded for an unchanged confirmed version"


def test_sync_thread_records_a_confirmed_new_file_even_when_stop_was_pressed(drive_env):
    sync, remote, root, _ = drive_env
    sync.run()
    path = write(root, "holiday.mov", b"movie")
    register = remote.upload
    committing = threading.Event()
    release_reply = threading.Event()
    stopped = sync._stopped

    def check_stop():
        flag = stopped()
        if flag:
            release_reply.set()
        return flag

    sync._stopped = check_stop

    def send(parent, path, on_bytes):
        on_bytes(path.stat().st_size)
        on_bytes(0)
        register(parent, path)
        committing.set()
        release_reply.wait(2)  # bounded reply latency even if a future coordinator doesn't poll
        return 5, hashlib.sha256(b"movie").hexdigest()

    use_stream(remote, send)
    with ThreadPoolExecutor(1) as runner:
        task = runner.submit(sync.run)
        assert committing.wait(5)
        sync.cancel_flag.set()
        with pytest.raises(Cancelled):
            task.result(timeout=10)
    tracked = path.name in sync.state.all()
    path.unlink()  # while paused, the user removes the file that iCloud already confirmed
    sync.run()
    assert tracked and not path.exists(), (tracked, "restored to PC" if path.exists() else "gone")


@pytest.mark.parametrize("existing", [False, True], ids=["new-file", "replacement"])
@pytest.mark.parametrize("failure", [Cancelled, PyiCloudConnectionException], ids=["stop", "network"])
def test_unregistered_upload_retries_from_start_and_state_is_written_by_sync_thread(
        drive_env, existing, failure):
    sync, remote, root, _ = drive_env
    if existing:
        remote.put("film.mov", b"old")
    sync.run()
    payload = b"new version"
    write(root, "film.mov", payload)
    register = remote.upload
    calls, writers = [], []
    put = sync.state.put

    def record(row):
        writers.append(threading.get_ident())
        put(row)

    def send(parent, path, on_bytes):
        with path.open("rb") as stream:
            calls.append(stream.read(2))
            on_bytes(2)
            if len(calls) == 1:
                raise failure("during content transfer")
            on_bytes(len(stream.read()))
        on_bytes(0)
        return register(parent, path)

    sync.state.put = record
    use_stream(remote, send)
    try:
        sync.run()
    except (Cancelled, PyiCloudConnectionException):
        pass
    assert not remote.uploads
    row = sync.state.all().get("film.mov")
    assert row.pending if existing else row is None
    assert sync.run().uploaded == 1
    assert calls == [b"ne", b"ne"] and remote.items["film.mov"]["data"] == payload
    assert not sync.state.all()["film.mov"].pending
    assert writers and set(writers) == {threading.get_ident()}


def test_replacement_emits_intermediate_byte_progress(drive_env, monkeypatch):
    sync, remote, root, _ = drive_env
    remote.put("clip.mov", b"old")
    sync.run()
    write(root, "clip.mov", b"new video")
    register = remote.upload
    clock = [100.0]
    monkeypatch.setattr(de, "time", NS(time=de.time.time, monotonic=lambda: clock[0]))
    seen = []
    sync.cb.progress = lambda done, total, text: seen.append(done) if "uploading" in text.lower() else None

    def send(parent, path, on_bytes):
        for n in (3, 3, 3):
            clock[0] += 5
            on_bytes(n)
        on_bytes(0)
        return register(parent, path)

    use_stream(remote, send)
    assert sync.run().uploaded == 1
    assert any(0 < done < 9 for done in seen), seen


def test_no_upload_phase_is_shown_for_a_quiet_drive(drive_env):
    sync, _, _, _ = drive_env
    sync.run()
    seen = []
    sync.cb.progress = lambda done, total, text: seen.append(text)
    sync.run()
    assert not any("uploading" in text.lower() for text in seen), seen


def test_notes_first_folder_rename_after_upgrade_moves_pages(notes_env):
    sync, remote, root, _ = notes_env
    remote.put("n1", "Shopping")
    sync.run()
    for _ in range(4):  # finish the normal one-time quality/index refreshes first
        sync.run()
    sync.state.set("folder_paths", None)  # 1.0.18 already had the cursor and exported page
    remote.dirs["F1"] = ("Home", None)
    remote.v += 1
    sync.run()
    sync.run()
    assert (root / "Home/Shopping.html").is_file()
    assert not (root / "Notes/Shopping.html").exists()


def test_stop_while_registration_is_queued_behind_another_upload(tmp_path):
    """One slow content POST already holds the desktop session when registration queues."""
    path = tmp_path / "small.mov"
    path.write_bytes(b"movie")
    api = FakeApi()
    occupied, release, queued, stopped = (threading.Event() for _ in range(4))
    registered = []
    busy = "https://upload.example/large"
    commit = "https://docs.example/register"

    class Session(requests.Session):
        def post(self, url, **kwargs):
            if url == commit:
                queued.set()
            return super().post(url, **kwargs)

        def request(self, method, url, **kwargs):
            if url == busy:
                occupied.set()
                assert release.wait(5)
            elif url == commit and stopped.is_set():
                registered.append(path.name)
            else:
                return api.post(url, **kwargs)
            return NS(json=lambda: {"results": [{"document": {"document_id": "DOC"}}]})

    def progress(n):
        if stopped.is_set():
            raise Cancelled()

    with Session() as session, ThreadPoolExecutor(2) as pool:
        SyncWorker._serialize_requests(NS(session=session))
        api.session = session
        api._update_contentws = lambda *a, **kw: session.post(commit).json()
        def next_request_occupies_connection(response):
            pool.submit(session.post, busy)
            assert occupied.wait(5)
        api._raise_if_error = next_request_occupies_connection
        small = pool.submit(adapter(api).upload, PARENT, path, on_bytes=progress)
        try:
            assert queued.wait(5)
            stopped.set()  # registration has not yet left the PC
        finally:
            release.set()
        try:
            small.result(5)
        except Cancelled:
            pass
    assert not registered


def test_photos_short_content_receipt_never_reaches_put_asset(tmp_path, monkeypatch):
    from pyicloud.services.photos_cloudkit.upload import PhotosUploader

    path = tmp_path / "clip.mov"
    path.write_bytes(b"movie")
    registered = []

    def content_post(url, data, **kwargs):
        assert b"".join(iter(lambda: data.read(16384), b"")) == b"movie"
        return NS(status_code=200, json=lambda: {"singleFile": {"size": 2}})

    session = NS(post=content_post)
    uploader = PhotosUploader(session=session, base_url="https://photos.example", base_params={}, timeout=30)
    monkeypatch.setattr(uploader, "create_upload_url",
                        lambda *, zone_name, assets: {next(iter(assets)): "https://upload.example/content"})
    def put_asset(**kwargs):
        registered.extend(kwargs["files"])
        return [NS(cplAsset="asset", response=None, is_duplicate=False)]
    monkeypatch.setattr(uploader, "put_asset", put_asset)
    client = NS(_uploader=uploader, upload_file=uploader.upload)
    cloud = ICloudRemote(None)
    cloud._photos = NS(libraries={"root": NS(_client=client, zone_id={"zoneName": "z"})})
    try:
        cloud.upload(path, on_bytes=lambda n: None)
    except UploadRejected:
        pass
    assert uploader._session is session
    assert not registered, "a two-byte content receipt was used to register a five-byte video"


def test_deleted_sibling_folders_do_not_each_rescan_the_whole_drive(drive_env):
    sync, remote, root, _ = drive_env
    count = 80
    for i in range(count):
        remote.put(f"Folder{i}/page.txt", b"x")
    sync.run()
    for path in root.glob("Folder*/page.txt"):
        path.unlink()
        path.parent.rmdir()
    with cProfile.Profile() as profile:
        result = sync.run()
    assert len(result.pending_to_cloud) == count
    prefix_tests = sum(sub.callcount for entry in profile.getstats()
                       if getattr(entry.code, "co_name", "") in ("_cloud_shape", "<listcomp>")
                       for sub in (entry.calls or []) if "startswith" in str(sub.code))
    assert prefix_tests < 20 * count, f"{prefix_tests} prefix tests before the confirmation was ready"
