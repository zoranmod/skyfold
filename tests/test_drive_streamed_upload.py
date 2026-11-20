"""1.0.19: Drive uploads are streamed from disk, byte for byte what requests' files= would send."""
import hashlib
import tracemalloc
from types import SimpleNamespace as NS

import pytest
import requests
from urllib3 import filepost

from skyfold import drive_remote
from skyfold.drive_remote import DriveItem, ICloudDrive
from tests.test_drive import env as drive_env  # noqa: F401  (fixture)


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

    def post(self, url, data=None, headers=None, timeout=None, files=None, **kw):
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


def test_streamed_form_reports_every_byte_sent(tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"z" * 300_000)
    seen = []
    with open(f, "rb") as raw:
        form = drive_remote._StreamedForm(raw, f.name, 300_000, seen.append)
        while form.read(8192):
            pass
    assert sum(seen) == 300_000


def test_drive_upload_shows_megabytes_and_stops(drive_env, monkeypatch):
    from skyfold import drive_engine as de
    sync, remote, root, _ = drive_env
    sync.run()
    (root / "film.mov").write_bytes(b"m" * 4_000_000)
    import os, time as _t
    old = _t.time() - 600
    os.utime(root / "film.mov", (old, old))
    texts = []
    sync.cb.progress = lambda done, total, text: texts.append((done, total, text))
    remote.streams_progress = True
    real = remote.upload

    def upload(parent, local, data=None, on_bytes=None):
        for _ in range(8):
            on_bytes(500_000)
        return real(parent, local)

    monkeypatch.setattr(remote, "upload", upload)
    monkeypatch.setattr(de.time, "monotonic", iter(range(0, 10_000, 1)).__next__)
    sync.run()
    assert any(d == 4_000_000 and "MB" in text for d, total, text in texts), texts[-3:]
    # Stop in the middle: the upload stops at its next piece, nothing is recorded as sent
    (root / "film2.mov").write_bytes(b"n" * 4_000_000)
    os.utime(root / "film2.mov", (old, old))

    def stopped_midway(parent, local, data=None, on_bytes=None):
        on_bytes(500_000)
        sync.cancel_flag.set()  # the Stop button
        on_bytes(500_000)
        raise AssertionError("not stopped")

    monkeypatch.setattr(remote, "upload", stopped_midway)
    with pytest.raises(de.Cancelled):
        sync.run()
    assert "film2.mov" not in remote.items


def test_upload_connections_get_a_big_send_buffer():
    """Windows' 64 KB default caps one connection at ~0.6 MB/s to Apple; uploads get 8 MB."""
    import socket

    session = requests.Session()
    drive_remote._big_send_buffer(session, "https://p31-docws.icloud.com/ws/upload?x=1")
    adapter = session.get_adapter("https://p31-docws.icloud.com/ws/upload")
    assert isinstance(adapter, drive_remote._BigSendAdapter)
    opts = adapter.poolmanager.connection_pool_kw["socket_options"]
    assert (socket.SOL_SOCKET, socket.SO_SNDBUF, drive_remote.SEND_BUFFER) in opts
    assert (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1) in opts  # urllib3's own default kept
    # other hosts keep the ordinary adapter
    assert not isinstance(session.get_adapter("https://www.icloud.com/"), drive_remote._BigSendAdapter)
    drive_remote._big_send_buffer(session, "https://p31-docws.icloud.com/other")
    assert session.get_adapter("https://p31-docws.icloud.com/x") is adapter  # mounted once


def test_big_send_buffer_really_reaches_the_socket():
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from urllib3.connection import HTTPConnection

    sizes = []
    real_create = socket.create_connection

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        session = requests.Session()
        url = f"http://127.0.0.1:{srv.server_address[1]}/"
        adapter = drive_remote._BigSendAdapter()
        session.mount(url, adapter)
        orig = HTTPConnection._new_conn

        def spy(self):
            sock = orig(self)
            sizes.append(sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF))
            return sock

        HTTPConnection._new_conn = spy
        try:
            session.get(url, timeout=5)
        finally:
            HTTPConnection._new_conn = orig
    finally:
        srv.shutdown()
    assert sizes and sizes[0] >= drive_remote.SEND_BUFFER
