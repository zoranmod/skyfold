"""1.0.19: Photos uploads show MB, speed and time left; Stop stops a big video; big send buffer."""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import requests

from skyfold import drive_remote, engine
from skyfold.remote import ICloudRemote, UploadReceipt

UPLOAD_URL = "https://p42-contentws.icloud.com/upload/abc?token=x"


class RecordingSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.sent = b""
        self.headers_seen = None

    def post(self, url=None, data=None, timeout=None, **kw):
        n = 0
        while chunk := data.read(8192):
            self.sent += chunk
            n += len(chunk)
        self.length = len(data)
        return NS(ok=True)


class FakeUploader:
    """Does what pyicloud's PhotosUploader.send_bytes does with the file: an open handle as data."""

    def __init__(self, session):
        self._session = session

    def upload(self, path, *, zone_name, import_group=None):
        with Path(path).open("rb") as handle:
            self._session.post(url=UPLOAD_URL, data=handle, timeout=30)
        return NS(cplAsset="ASSET1", is_duplicate=False)


def remote_with(session):
    uploader = FakeUploader(session)
    client = NS(_uploader=uploader, upload_file=uploader.upload)
    library = NS(_client=client, zone_id={"zoneName": "PrimarySync"})
    r = ICloudRemote(None)
    r._photos = NS(libraries={"root": library})
    return r, uploader


def test_photo_upload_counts_every_byte_and_restores_the_session(tmp_path):
    f = tmp_path / "IMG_0412.MOV"
    payload = bytes(range(256)) * 9000
    f.write_bytes(payload)
    session = RecordingSession()
    r, uploader = remote_with(session)
    seen = []
    receipt = r.upload(f, on_bytes=seen.append)
    assert receipt == UploadReceipt("ASSET1", False)
    assert session.sent == payload and session.length == len(payload) and sum(seen) == len(payload)
    assert uploader._session is session  # put back after the upload
    adapter = session.get_adapter("https://p42-contentws.icloud.com/x")
    assert isinstance(adapter, drive_remote._BigSendAdapter)


def test_stop_during_a_photo_upload_is_a_stop_not_a_rejection(tmp_path):
    f = tmp_path / "long.mov"
    f.write_bytes(b"v" * 2_000_000)
    session = RecordingSession()
    r, uploader = remote_with(session)

    def stop(_k):
        raise engine.Cancelled()

    with pytest.raises(engine.Cancelled):
        r.upload(f, on_bytes=stop)
    assert uploader._session is session


def test_upload_text_reads_like_downloads():
    from skyfold import i18n
    from skyfold.ui_text import Rate

    rate = Rate(clock=iter([0, 10]).__next__)
    rate.add(0, 0)
    rate.add(20_000_000, 0)
    text = engine.upload_text("IMG_0412.MOV", 120_000_000, 600_000_000, rate)
    assert text.startswith("Uploading IMG_0412.MOV · 120") and "600" in text and "/s" in text and "left" in text
    i18n.set_language("bs")  # (an older settings file may still ask for it: the app stays English)
    assert engine.upload_text("a.mov", 1, 2, rate).startswith("Uploading a.mov")


from tests.test_engine import env  # noqa: E402,F401  (fixture)
from tests.test_drive import write  # noqa: E402


def test_engine_shows_photo_upload_progress_and_stops(env):  # noqa: F811
    s, remote, root, _ = env
    s.settings.photos_upload = True
    s.run()
    texts = []
    s.cb.progress = lambda done, total, text: texts.append((done, total, text))
    real = remote.upload
    remote.streams_progress = True

    def upload(path, on_bytes=None):
        for _ in range(4):
            on_bytes(1_000)
        return real(path)

    remote.upload = upload
    write(root, "clip.mov", b"c" * 4_000, age=300)
    s.run()
    assert remote.uploads == ["clip.mov"]
    assert any(d == 4_000 and t == 4_000 and "clip.mov" in text for d, t, text in texts), texts

    def stopped(path, on_bytes=None):
        on_bytes(1_000)
        s.cancel_flag.set()  # the Stop button
        on_bytes(1_000)
        raise AssertionError("not stopped")

    remote.upload = stopped
    write(root, "second.mov", b"d" * 4_000, age=300)
    with pytest.raises(engine.Cancelled):
        s.run()
    assert remote.uploads == ["clip.mov"] and not s.state.get("upload_retry:second.mov")
