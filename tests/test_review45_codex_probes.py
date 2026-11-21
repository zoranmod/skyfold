"""Offline regressions for Drive and Photos streaming, using synthetic files and replies."""
import hashlib
import socket
from types import SimpleNamespace

import pytest
import requests

from skyfold import drive_remote
from skyfold.engine import Cancelled
from skyfold.remote import _CountingReader, _CountingSession
from tests.test_drive_streamed_upload import FakeApi, PARENT, adapter
from tests.test_photos_upload_progress import RecordingSession, remote_with


def test_same_size_edit_does_not_publish_a_mixed_version(tmp_path):
    path = tmp_path / "project.bin"
    first = b"a" * (1 << 20)
    last = b"b" * len(first)
    path.write_bytes(first)
    api = FakeApi()
    edited = False

    def edit_after_first_chunk(n):
        nonlocal edited
        if not edited:
            edited = True
            try:
                with path.open("r+b") as out:
                    out.write(last)
            except PermissionError:
                pass  # holding a Windows deny-write handle is also a valid fix

    try:
        _, digest = adapter(api).upload(PARENT, path, on_bytes=edit_after_first_chunk)[:2]
    except Exception:
        assert api.updated is None
        return
    assert edited and api.updated is not None
    assert digest in {hashlib.sha256(version).hexdigest() for version in (first, last)}


def test_drive_does_not_register_a_short_server_receipt(tmp_path):
    path = tmp_path / "movie.mov"
    path.write_bytes(b"movie")
    api = FakeApi()

    def short_receipt(*args, **kwargs):
        api.post(*args, **kwargs)
        return SimpleNamespace(json=lambda: {"singleFile": {"size": 2, "receipt": "stored"}})

    api.session.post = short_receipt
    try:
        adapter(api).upload(PARENT, path)
    except Exception:
        pass
    assert api.updated is None


def test_stop_while_waiting_for_drive_receipt_prevents_registration(tmp_path):
    path = tmp_path / "movie.mov"
    path.write_bytes(b"movie")
    api = FakeApi()
    stopped = False

    def content_post(*args, **kwargs):
        nonlocal stopped
        response = api.post(*args, **kwargs)
        stopped = True  # Stop while the server prepares its reply
        return response

    def check_progress(n):
        if stopped:
            raise Cancelled()

    api.session.post = content_post
    with pytest.raises(Cancelled):
        adapter(api).upload(PARENT, path, on_bytes=check_progress)
    assert api.updated is None


def test_drive_does_not_claim_success_without_a_document_result(tmp_path):
    path = tmp_path / "plan.txt"
    path.write_bytes(b"plan")
    api = FakeApi()
    api._update_contentws = lambda *args, **kwargs: {"results": []}
    with pytest.raises(Exception):
        adapter(api).upload(PARENT, path)


def test_stop_while_waiting_for_photos_receipt_prevents_put_asset(tmp_path, monkeypatch):
    from pyicloud.services.photos_cloudkit.upload import PhotosUploader
    from skyfold.remote import ICloudRemote

    path = tmp_path / "clip.mov"
    path.write_bytes(b"movie")
    stopped = False
    registered = []

    def content_post(url, data, **kwargs):
        nonlocal stopped
        while data.read(16384):
            pass
        stopped = True
        return SimpleNamespace(status_code=200, json=lambda: {"singleFile": {"size": 5}})

    def progress(n):
        if stopped:
            raise Cancelled()

    def put_asset(**kwargs):
        registered.extend(kwargs["files"])
        return []

    session = SimpleNamespace(post=content_post)
    uploader = PhotosUploader(session=session, base_url="https://photos.example", base_params={}, timeout=30)
    monkeypatch.setattr(uploader, "create_upload_url",
                        lambda *, zone_name, assets: {next(iter(assets)): "https://upload.example/content"})
    monkeypatch.setattr(uploader, "put_asset", put_asset)
    client = SimpleNamespace(_uploader=uploader, upload_file=uploader.upload)
    remote = ICloudRemote(None)
    remote._photos = SimpleNamespace(libraries={"root": SimpleNamespace(_client=client, zone_id={"zoneName": "z"})})
    try:
        remote.upload(path, on_bytes=progress)
    except Exception:
        pass
    assert stopped and uploader._session is session
    assert not registered


@pytest.mark.parametrize("wrapper", ["drive", "photos"])
def test_preserved_method_redirect_does_not_send_an_exhausted_body(tmp_path, wrapper):
    class Redirect(drive_remote._BigSendAdapter):
        def __init__(self):
            super().__init__()
            self.writes = []

        def send(self, request, **kwargs):
            body = b"".join(iter(lambda: request.body.read(16384), b""))
            self.writes.append((int(request.headers["Content-Length"]), body))
            response = requests.Response()
            response.request = request
            response.url = request.url
            response._content = b'{"singleFile": {"size": 6}}'
            response.status_code = 200
            if len(self.writes) == 1:
                response.status_code = 307
                response.headers["Location"] = "/second"
                response._content = b""
            return response

    path = tmp_path / "photo.jpg"
    path.write_bytes(b"sample")
    transport = Redirect()
    with requests.Session() as session, path.open("rb") as raw:
        session.trust_env = False
        session.mount("https://upload.example/", transport)
        try:
            if wrapper == "drive":
                api = FakeApi()
                api.session = session
                adapter(api).upload(PARENT, path)
            else:
                _CountingSession(session, None).post("https://upload.example/first", data=raw)
        except Exception:
            if len(transport.writes) != 1:
                raise
        # Refusing a redirect is safe. If it was followed, both writes must agree.
    assert transport.writes[0][0] == len(transport.writes[0][1])
    if len(transport.writes) > 1:
        assert transport.writes == [transport.writes[0], transport.writes[0]]


@pytest.mark.parametrize("wrapper", ["drive", "photos"])
def test_zero_length_read_never_consumes_payload(tmp_path, wrapper):
    path = tmp_path / "sample.bin"
    path.write_bytes(b"payload")
    counted = []
    with path.open("rb") as raw:
        if wrapper == "drive":
            reader = drive_remote._StreamedForm(raw, path.name, 7, counted.append)
            assert reader.read(65536).startswith(b"--")  # get past the multipart header
        else:
            reader = _CountingReader(raw, counted.append)
        assert reader.read(0) == b""
        assert raw.tell() == 0 and not counted


def test_send_buffer_applies_to_the_https_proxy_tunnel():
    url = "https://upload.example/content"
    with requests.Session() as session:
        drive_remote._big_send_buffer(session, url)
        manager = session.get_adapter(url).proxy_manager_for("http://proxy.example:8080")
        options = manager.connection_pool_kw.get("socket_options", [])
        assert (socket.SOL_SOCKET, socket.SO_SNDBUF, drive_remote.SEND_BUFFER) in options


@pytest.mark.parametrize("exception", [None, Cancelled, RuntimeError])
def test_photos_restores_exact_session_when_upload_exits(tmp_path, exception):
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"photo")
    session = RecordingSession()
    remote, uploader = remote_with(session)

    def progress(n):
        # 1.0.20: each upload works on its own copy of the uploader; the shared one is never changed
        assert uploader._session is session
        if exception:
            raise exception()

    if exception:
        from skyfold.remote import UploadRejected

        # (1.0.20: an error before registration means "not saved": reported as a refusal, retried later)
        with pytest.raises(UploadRejected if exception is RuntimeError else exception):
            remote.upload(path, on_bytes=progress)
    else:
        assert remote.upload(path, on_bytes=progress).asset_id == "ASSET1"
    assert uploader._session is session
