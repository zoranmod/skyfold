"""Windows sync recovery and pinned-session integration, using local data only."""

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from pyicloud.exceptions import PyiCloudAPIResponseException, PyiCloudConnectionException
from pyicloud.session import PyiCloudSession

from skyfold import auth
from skyfold.drive_remote import _unlocked_post
from skyfold.engine import Cancelled
from skyfold.remote import _CountingSession
from skyfold.worker import SyncWorker
from tests.test_drive import env as drive_env, write
from tests.test_drive_streamed_upload import FakeApi, adapter
from tests.test_notes import env as notes_env


def test_same_length_cloud_edit_after_failed_replacement_keeps_both_versions(drive_env):
    sync, drive, root, recycled = drive_env
    drive.put("draft.txt", b"first draft")
    sync.run()
    path = write(root, "draft.txt", b"PC edit")
    original_upload = drive.upload

    def disconnected(*args, **kwargs):
        raise PyiCloudConnectionException("connection lost before registration")

    drive.upload = disconnected
    with pytest.raises(PyiCloudConnectionException):
        sync.run()
    assert sync.state.all()[path.name].pending
    drive.upload = original_upload
    drive.put(path.name, b"MacEdit")  # Same length, different bytes, newer cloud document.
    sync.run()
    sync.run()
    assert path.read_bytes() == b"MacEdit"
    assert b"PC edit" in [p.read_bytes() for p in root.glob("*.txt")], recycled


def register_with_etag(drive, parent, path):
    payload = path.read_bytes()
    drive.uploads.append(path.name)
    drive.put(path.name, payload)
    return len(payload), hashlib.sha256(payload).hexdigest(), drive.items[path.name]["etag"]


def test_first_listing_that_lags_a_confirmed_upload_leaves_pc_copy_in_place(drive_env):
    sync, drive, root, recycled = drive_env
    sync.run()
    drive.upload = lambda parent, path: register_with_etag(drive, parent, path)
    path = write(root, "clip.mov", b"video")
    assert sync.run().uploaded == 1
    walk = drive.walk

    def previous_listing(*args, **kwargs):
        listing = walk(*args, **kwargs)
        listing.pop(path.name, None)
        return listing

    drive.walk = previous_listing
    old = sync.run()
    drive.walk = walk
    sync.run()
    assert not recycled and old.deleted_local == 0
    assert path.read_bytes() == drive.items[path.name]["data"]


def test_edit_while_a_replacement_waits_does_not_upload_the_same_version_twice(drive_env):
    sync, drive, root, _ = drive_env
    drive.put("draft.txt", b"old")
    sync.run()
    write(root, "draft.txt", b"first PC edit")
    write(root, "film.mov", b"new file")

    def send(parent, path):
        if path.name == "film.mov":
            # The sync has scanned the draft, but replacements wait until all fresh files finish.
            write(root, "draft.txt", b"final PC edit, saved during the movie upload")
        return register_with_etag(drive, parent, path)

    drive.upload = send
    assert sync.run().uploaded == 2
    assert drive.items["draft.txt"]["data"] == (root / "draft.txt").read_bytes()
    sync.run()
    assert drive.uploads.count("draft.txt") == 1, (drive.uploads, drive.trashed)


@pytest.mark.parametrize("reply", ["conflict-name", "json-null"])
def test_registration_needs_evidence_for_the_path_it_marks_as_saved(drive_env, reply):
    sync, drive, root, _ = drive_env
    sync.run()
    path = write(root, "meeting.txt", b"my version")
    api = FakeApi()
    drive.streams_progress = True
    drive.upload = adapter(api).upload

    def register(folder, info, document_id, file_object, zone, **kwargs):
        if reply == "json-null":
            return json.loads("null")
        # Another device took the requested name during the upload. add_file allows conflicts.
        drive.put("meeting.txt", b"their version")
        drive.put("meeting (2).txt", path.read_bytes())
        drive.items["meeting (2).txt"]["id"] = document_id
        item = drive._item("meeting (2).txt")
        return {"results": [{"status": {"status_code": 0},
                             "document": {"document_id": document_id, "etag": item.etag,
                                          "parent_id": folder, "name": "meeting (2)",
                                          "extension": "txt", "size": item.size, "zone": zone}}]}

    api._update_contentws = register
    result = sync.run()
    row = sync.state.all().get(path.name)
    if reply == "json-null":
        assert result.uploaded == 0
    else:
        assert row is None or row.etag != drive._item("meeting (2).txt").etag


def test_file_deferred_until_folder_is_listed_is_not_shown_as_done(drive_env):
    sync, drive, root, _ = drive_env
    sync.run()
    write(root, "New/plan.txt", b"plan")
    walk = drive.walk

    def slow_folder_listing(*args, **kwargs):
        result = walk(*args, **kwargs)
        result.pop("New", None)
        return result

    drive.walk = slow_folder_listing
    messages = []
    sync.cb.progress = lambda done, total, text: messages.append(text)
    assert sync.run().uploaded == 0 and not drive.uploads
    assert not any("1 of 1 files done" in text for text in messages), messages


def test_notes_upgrade_repairs_a_folder_rename_already_polled_by_1018(notes_env):
    sync, remote, root, _ = notes_env
    remote.put("n", "Shopping")
    for _ in range(5):
        sync.run()
    remote.dirs["F1"] = ("Home", None)
    remote.v += 1
    # 1.0.18 polled the folder-only change: cursor advanced, page stayed at its old path.
    sync.state.set("cursor", remote.cursor())
    sync.state.set("folder_paths", None)
    result = sync.run()
    assert result.full and (root / "Home/Shopping.html").is_file()
    assert not (root / "Notes/Shopping.html").exists()


@contextmanager
def local_server(handler):
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


def session_in(folder):
    session = PyiCloudSession(SimpleNamespace(account_name="offline@example.invalid"),
                              "test-client", str(folder))
    session.trust_env = False
    return session


def test_truncated_content_reply_aborts_before_trashing_replacements(drive_env, tmp_path):
    sync, drive, root, _ = drive_env
    drive.put("old.txt", b"cloud original")
    sync.run()
    write(root, "new.mov", b"new movie")
    write(root, "old.txt", b"changed locally")
    register = drive.upload

    class TruncatedReply(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b'{"singleFile":')
            self.close_connection = True

        def log_message(self, *args):
            pass

    with session_in(tmp_path) as session, local_server(TruncatedReply) as url:
        SyncWorker._serialize_requests(SimpleNamespace(session=session))

        def send(parent, path):
            if path.name == "new.mov":
                _unlocked_post(session, url, data=path.read_bytes(), timeout=3, allow_redirects=False)
            return register(parent, path)

        drive.upload = send
        try:
            sync.run()
        except (PyiCloudAPIResponseException, PyiCloudConnectionException, requests.RequestException):
            pass
    assert not drive.trashed, "the replacement started after the content connection broke"


def test_overlapping_body_posts_and_metadata_persist_a_restorable_current_session(tmp_path, monkeypatch):
    # Real requests, cookie extraction, pyicloud response processing and atomic saves.
    from skyfold import netpolicy

    monkeypatch.setattr(netpolicy, "check", lambda url, **kw: None)  # a local server stands in for Apple
    auth._serialize_session_saves()
    gate = threading.Barrier(4, timeout=8)

    class Reply(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            gate.wait()
            self.send_response(200)
            self.send_header("Set-Cookie", f"{self.path[1:]}=saved; Path=/; Max-Age=600")
            self.send_header("X-Apple-ID-Account-Country", "BA")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    with session_in(tmp_path) as session, local_server(Reply) as url, ThreadPoolExecutor(4) as pool:
        monkeypatch.setattr(auth, "_CURRENT", session)
        SyncWorker._serialize_requests(SimpleNamespace(session=session))
        sends = [pool.submit(_unlocked_post, session, f"{url}/body{i}", data=b"hi", timeout=10)
                 for i in range(3)]
        sends.append(pool.submit(session.post, url + "/metadata", timeout=10))
        for sent in sends:
            assert sent.result(12).status_code == 200
        assert json.loads(Path(session.session_path).read_text())["account_country"] == "BA"
        with session_in(tmp_path) as restored:
            assert restored.cookies.get_dict() == {key: "saved" for key in ("body0", "body1", "body2", "metadata")}
    assert not list(tmp_path.glob("*.tmp"))


def test_photos_registration_hook_is_thread_local_and_clears_after_stop(tmp_path):
    from pyicloud.services.photos_cloudkit.upload import PhotosUploader

    armed, proceed = threading.Event(), threading.Event()
    seen = []

    class Session(requests.Session):
        def post(self, url, **kwargs):
            if "putAsset" in url:
                armed.set()  # The wrapper has installed its check on the registration thread.
                assert proceed.wait(5)
            return super().post(url, **kwargs)

        def request(self, method, url, **kwargs):
            seen.append(url)
            if "createUploadUrl" in url:
                key = next(iter(kwargs["json"]["assets"]))
                payload = {"uploadUrls": {key: "https://body.example/file"}}
            elif url.endswith("/file"):
                assert kwargs["data"].read() == b"photo"
                payload = {"singleFile": {"size": 5}}
            else:
                payload = {}
            return SimpleNamespace(status_code=200, json=lambda: payload)

    file = tmp_path / "clip.jpg"
    file.write_bytes(b"photo")

    def progress(n):
        if armed.is_set():
            raise Cancelled()

    with Session() as session, ThreadPoolExecutor(1) as pool:
        SyncWorker._serialize_requests(SimpleNamespace(session=session))
        uploader = PhotosUploader(session=_CountingSession(session, progress), base_url="https://photos.example",
                                  base_params={}, timeout=(1, 1))
        send = pool.submit(uploader.upload, str(file), zone_name="z")
        try:
            assert armed.wait(5)
            session.get("https://notes.example/while-armed")
        finally:
            proceed.set()
        with pytest.raises(Cancelled):
            send.result(5)
        pool.submit(session.get, "https://notes.example/after-stop").result(5)
    assert not any("putAsset" in url for url in seen)
    assert seen[-2:] == ["https://notes.example/while-armed", "https://notes.example/after-stop"]
