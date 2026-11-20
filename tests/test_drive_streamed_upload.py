"""1.0.19: Drive uploads are streamed from disk, byte for byte what requests' files= would send."""
import hashlib
import tracemalloc
from types import SimpleNamespace as NS

import pytest
import requests
from urllib3 import filepost

from skyfold import drive_remote
from skyfold.drive_remote import DriveItem, ICloudDrive


class FakeApi:
    """pyicloud's DriveService parts that upload() uses; the content post is recorded."""

    def __init__(self, keep_body=True):
        self.keep_body = keep_body
        self.body = b""
        self.headers = {}
        self.updated = None
        self.session = NS(post=self.post)

    def _get_upload_contentws_url(self, file_object, zone):
        pos = file_object.tell()
        file_object.seek(0, 2)
        self.announced = (file_object.name, file_object.tell())
        file_object.seek(pos)
        return "DOC", "https://upload.example/content"

    def post(self, url, data=None, headers=None, timeout=None, files=None):
        self.headers = headers or {}
        self.length = len(data)
        size = 0
        while chunk := data.read(1 << 16):
            size += len(chunk)
            if self.keep_body:
                self.body += chunk
        self.size = size
        return NS(json=lambda: {"singleFile": {"fileChecksum": "x"}})

    def _raise_if_error(self, resp):
        pass

    def _update_contentws(self, folder, info, doc, fh, zone, **kw):
        self.updated = (folder, doc, fh.name, zone)


def adapter(api):
    a = ICloudDrive(NS())
    a._drive = api
    return a


PARENT = DriveItem(path="", drivewsid="FOLDER::root", docwsid="root", zone="z", etag="e", is_dir=True)


def test_streamed_body_is_what_requests_would_send(tmp_path, monkeypatch):
    f = tmp_path / "Račun za mart; Šefov 'kopija' 2026.pdf"
    payload = bytes(range(256)) * 5000
    f.write_bytes(payload)
    monkeypatch.setattr(filepost, "choose_boundary", lambda: "BOUNDARY123")
    api = FakeApi()
    size, digest = adapter(api).upload(PARENT, f)
    with open(f, "rb") as fh:  # what pyicloud's send_file posts: files={name: file}
        ref = requests.Request("POST", "https://x", files={f.name: fh}).prepare()
    expected = ref.body.replace(ref.headers["Content-Type"].split("boundary=")[1].encode(), b"BOUNDARY123")
    assert api.body == expected
    assert api.length == len(expected) and api.headers["Content-Type"].endswith("boundary=BOUNDARY123")
    assert (size, digest) == (len(payload), hashlib.sha256(payload).hexdigest())
    assert api.announced == (f.name, len(payload)) and api.updated == ("root", "DOC", f.name, "z")


def test_big_upload_is_not_held_in_memory(tmp_path):
    f = tmp_path / "clip.mov"
    size = 24 << 20
    with open(f, "wb") as fh:
        fh.truncate(size)
    api = FakeApi(keep_body=False)
    tracemalloc.start()
    try:
        adapter(api).upload(PARENT, f)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert api.size == api.length and peak < 8 << 20, peak


def test_file_that_shrinks_while_sent_is_not_taken_as_sent(tmp_path):
    f = tmp_path / "log.txt"
    f.write_bytes(b"x" * 100_000)
    api = FakeApi()
    real_post = api.post

    def post(url, data=None, **kw):
        with open(f, "r+b") as fh:
            fh.truncate(10)
        return real_post(url, data=data, **kw)

    api.session = NS(post=post)
    with pytest.raises(Exception):
        adapter(api).upload(PARENT, f)
    assert api.updated is None


def test_real_requests_sends_the_same_body_over_http(tmp_path, monkeypatch):
    """Through requests and http.client to a local server: same body and length as files= would send."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    got = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers["Content-Length"])
            got.setdefault("bodies", []).append((self.headers.get("Transfer-Encoding"), self.rfile.read(n)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        f = tmp_path / "photo.heic"
        f.write_bytes(bytes(range(256)) * 4000)
        monkeypatch.setattr(filepost, "choose_boundary", lambda: "B0UNDARY")
        with open(f, "rb") as raw:
            form = drive_remote._StreamedForm(raw, f.name, f.stat().st_size)
            requests.post(url, data=form, headers={"Content-Type": form.content_type}, timeout=10)
            form.check_complete()
        with open(f, "rb") as fh:
            requests.post(url, files={f.name: fh}, timeout=10)
    finally:
        server.shutdown()
    (te1, streamed), (te2, reference) = got["bodies"]
    ref_boundary = reference.split(b"\r\n", 1)[0][2:]
    assert te1 is None and streamed == reference.replace(ref_boundary, b"B0UNDARY")
