"""Skyfold's own requests go only to Apple's iCloud and GitHub's update servers; the opt-in
connection diagnostics never write a name, an address, an id or a secret."""

from __future__ import annotations

import time

import pytest
import requests
from requests.adapters import BaseAdapter

from skyfold import netdiag, netpolicy
from skyfold.netpolicy import NetworkPolicyBlocked, destination


@pytest.mark.parametrize("url,kind", [
    ("https://idmsa.apple.com/appleauth/auth/signin/init", "apple"),
    ("https://setup.icloud.com/setup/ws/1/accountLogin", "apple"),
    ("https://p31-ckdatabasews.icloud.com/database/1/com.apple.photos.cloud/production/private/records/query", "apple"),
    ("https://cvws.icloud-content.com/B/abc/photo.jpg?token=x", "apple"),
    ("https://setup.icloud.com.cn/setup/ws/1/accountLogin", "apple"),
    ("https://gateway.icloud.com/x", "apple"),
    ("https://IDMSA.Apple.COM./x", "apple"),
    ("https://api.github.com/repos/zoranmod/skyfold/releases/latest", "github"),
    ("https://tmaproduction.blob.core.windows.net/bundle", "github"),
    ("https://release-assets.githubusercontent.com/x", "github"),
    # refused
    ("http://idmsa.apple.com/x", ""),
    ("https://idmsa.apple.com:8443/x", ""),
    ("https://idmsa.apple.com.example.org/x", ""),
    ("https://notapple.com/x", ""),
    ("https://evilicloud.com/x", ""),
    ("https://user:pw@idmsa.apple.com/x", ""),
    ("https://17.253.144.10/x", ""),
    ("https://[2620:149::1]/x", ""),
    ("https://other.blob.core.windows.net/x", ""),
    ("https://gist.githubusercontent.com/x", ""),
    ("https://example.com/?u=https://idmsa.apple.com", ""),
    ("ftp://idmsa.apple.com/x", ""),
    ("https://[bad/x", ""),
    ("https://xn--pple-43d.com/x", ""),
    ("", ""),
])
def test_destination(url, kind):
    assert destination(url) == kind


class FakeAdapter(BaseAdapter):
    """Stands in for the network: records what reached it and answers 200, or a redirect."""

    def __init__(self, redirects=None):
        super().__init__()
        self.sent: list[str] = []
        self.redirects = redirects or {}

    def send(self, request, **kwargs):
        self.sent.append(request.url)
        r = requests.Response()
        r.request, r.url, r._content = request, request.url, b"{}"
        target = self.redirects.get(request.url)
        r.status_code = 302 if target else 200
        if target:
            r.headers["Location"] = target
        return r

    def close(self):
        pass


def session_with(adapter, cls=requests.Session):
    s = cls() if cls is requests.Session else cls
    s.trust_env = False
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def test_allowed_requests_go_and_others_never_reach_the_network():
    netpolicy.install()
    a = FakeAdapter()
    s = session_with(a)
    assert s.get("https://setup.icloud.com/x").status_code == 200
    for url in ("https://example.com/x", "http://setup.icloud.com/x", "https://10.0.0.1/x"):
        with pytest.raises(NetworkPolicyBlocked):
            s.get(url)
    assert a.sent == ["https://setup.icloud.com/x"]


def test_a_redirect_elsewhere_is_refused_before_it_is_followed():
    netpolicy.install()
    a = FakeAdapter({"https://setup.icloud.com/start": "https://collector.example.net/steal?c=1"})
    s = session_with(a)
    with pytest.raises(NetworkPolicyBlocked):
        s.get("https://setup.icloud.com/start")
    assert a.sent == ["https://setup.icloud.com/start"]


def test_an_icloud_session_never_goes_to_github(tmp_path):
    from types import SimpleNamespace

    from pyicloud.session import PyiCloudSession

    netpolicy.install()
    a = FakeAdapter()
    s = PyiCloudSession(SimpleNamespace(account_name="offline@example.invalid"), "test-client", str(tmp_path))
    session_with(a, s)
    with pytest.raises(NetworkPolicyBlocked):
        requests.Session.request(s, "GET", "https://api.github.com/repos/zoranmod/skyfold")
    assert a.sent == []


def test_a_refusal_is_not_taken_for_being_offline():
    """pyicloud and Skyfold retry Requests errors as "offline": a refusal is something else."""
    assert not issubclass(NetworkPolicyBlocked, requests.RequestException)
    assert "address" in str(NetworkPolicyBlocked()) and "http" not in str(NetworkPolicyBlocked())


def test_install_is_done_once():
    netpolicy.install()
    first = requests.Session.send
    netpolicy.install()
    assert requests.Session.send is first and getattr(first, "_skyfold_policy", False)


def test_the_update_dialog_loads_no_pictures_from_outside():
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QTextDocument
    from PySide6.QtWidgets import QApplication

    from skyfold.ui.dialogs import _ReleaseNotes

    QApplication.instance() or QApplication([])
    view = _ReleaseNotes()
    for url in ("file://server/share/a.png", "https://example.com/a.png", "C:/Windows/a.png"):
        got = view.loadResource(QTextDocument.ImageResource, QUrl(url))
        assert got.width() == 1 and got.height() == 1  # a blank pixel: Qt doesn't go and load it itself


def test_release_notes_never_show_a_picture_from_this_pc(tmp_path):
    """Rendered, not only asked: a picture next to the notes stays out of the document."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QColor, QImage, QTextDocument
    from PySide6.QtWidgets import QApplication

    from skyfold.ui.dialogs import _ReleaseNotes

    QApplication.instance() or QApplication([])
    pic = QImage(400, 400, QImage.Format_ARGB32)
    pic.fill(QColor("#ff0000"))
    path = tmp_path / "a.png"
    pic.save(str(path))
    view = _ReleaseNotes()
    view.resize(400, 300)
    url = QUrl.fromLocalFile(str(path))
    view.setMarkdown("Notes" + chr(10) * 2 + f"![x]({url.toString()})")
    view.document().setTextWidth(380)
    assert view.document().size().height() < 150  # (a 400 px picture would make it far taller)
    loaded = view.document().resource(QTextDocument.ImageResource, url)
    assert loaded is None or loaded.width() <= 1


# --- connection diagnostics ---

CANARY = "ALICE-PRIVATE"


def test_diagnostics_keep_only_fixed_labels():
    cases = {
        f"https://p31-docws.icloud.com/ws/{CANARY}/download/by_id?document_id={CANARY}":
            ("GET", "icloud", "docws", "/ws/…/download/by_id"),
        f"https://cvws.icloud-content.com/B/{CANARY}/Patient%20list.pdf?token={CANARY}":
            ("GET", "apple-content", "cvws", "content"),
        f"https://idmsa.apple.com/appleauth/auth/signin/init?dsid={CANARY}":
            ("GET", "apple-auth", "idmsa", "/appleauth/auth/signin/init"),
        f"https://p12-ckdatabasews.icloud.com/database/1/com.apple.notes/production/private/records/{CANARY}":
            ("GET", "icloud", "ckdatabasews", "/database/1/com.apple.notes/production/private/records/…"),
        f"https://{CANARY}.icloud.com/x": ("GET", "icloud", "other", "/…"),
        f"https://idmsa.apple.com.{CANARY}.invalid/a": ("GET", "unknown", "-", "-"),
        f"https://alice:{CANARY}@idmsa.apple.com/a": ("GET", "unknown", "-", "-"),
        "https://[bad/x": ("GET", "unknown", "-", "-"),
    }
    for url, want in cases.items():
        assert netdiag.describe("GET", url) == want
    assert netdiag.describe(CANARY, "https://idmsa.apple.com/")[0] == "OTHER"


def test_diagnostics_file_has_no_private_data(tmp_path, monkeypatch):
    monkeypatch.setattr(netdiag, "_file", tmp_path / "connections.log")
    netpolicy.install()

    class Failing(FakeAdapter):
        def send(self, request, **kwargs):
            if "fail" in request.url:
                raise requests.ConnectionError(f"{CANARY}@example.test Cookie: token={CANARY}")
            return super().send(request, **kwargs)

    s = session_with(Failing())
    s.headers["Cookie"] = f"X-APPLE-WEBAUTH-TOKEN={CANARY}"
    s.get(f"https://p31-docws.icloud.com/ws/{CANARY}/download?x={CANARY}")  # off: nothing written
    assert not netdiag.files()
    netdiag.start()
    s.get(f"https://p31-docws.icloud.com/ws/{CANARY}/download?x={CANARY}")
    s.get(f"https://cvws.icloud-content.com/{CANARY}.jpg", stream=True)
    with pytest.raises(requests.ConnectionError):
        s.get(f"https://p31-docws.icloud.com/fail/{CANARY}")
    with pytest.raises(NetworkPolicyBlocked):
        s.get(f"https://{CANARY}.example.org/")
    netdiag.stop()
    text = "".join(p.read_text(encoding="utf-8") for p in netdiag.files())
    assert CANARY.lower() not in text.lower() and "example" not in text and "icloud.com" not in text
    assert "GET icloud docws /ws/…/download status=200 ms=" in text
    assert "ms-to-headers=" in text and "failure=connection" in text and "failure=policy-blocked" in text
    lines = len(text.splitlines())
    s.get("https://setup.icloud.com/x")  # stopped: nothing more
    assert len("".join(p.read_text(encoding="utf-8") for p in netdiag.files()).splitlines()) == lines


def test_diagnostics_stop_by_themselves_even_after_sleep(tmp_path, monkeypatch):
    monkeypatch.setattr(netdiag, "_file", tmp_path / "connections.log")
    netdiag.start(minutes=30)
    assert netdiag.on()
    later = time.time() + 31 * 60  # the PC slept through the end
    monkeypatch.setattr(netdiag.time, "time", lambda: later)
    assert not netdiag.on() and netdiag.until() == 0.0


def test_diagnostics_stay_small_and_can_be_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(netdiag, "_file", tmp_path / "connections.log")
    monkeypatch.setattr(netdiag, "MAX_BYTES", 2000)
    netdiag.start()
    for _ in range(200):
        netdiag._write("GET icloud docws /ws status=200 ms=12 failure=none")
    sizes = [p.stat().st_size for p in netdiag.files()]
    assert len(sizes) == 2 and all(n <= 2000 for n in sizes)
    netdiag.delete()
    assert not netdiag.files() and not netdiag.on()


def test_a_full_disk_stops_recording_not_syncing(tmp_path, monkeypatch):
    monkeypatch.setattr(netdiag, "_file", tmp_path / "missing" / "deeper" / "connections.log")
    netdiag.start()

    def full(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(netdiag, "open", full, raising=False)
    netdiag._write("x")
    assert not netdiag.on()


def test_diagnostics_are_off_at_start():
    import importlib

    fresh = importlib.reload(netdiag)
    assert not fresh.on()
