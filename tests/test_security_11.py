"""1.1 security review: text from iCloud is never HTML, photos never get a runnable name, nothing of
the account is kept after signing out, and certificates are always checked. Made-up data only."""
from types import SimpleNamespace

from PySide6.QtCore import Qt

from tests.test_review27_codex_ui_probes import qapp  # noqa: F401

UNC = '<img src="\\\\203.0.113.5\\s\\a.png">'


def test_names_from_icloud_are_plain_text_in_the_panel_and_dialogs(qapp):  # noqa: F811
    from skyfold.ui import dialogs, theme
    from skyfold.ui.flyout import ActivityRow
    from skyfold.ui.safe_text import plain_tip

    row = ActivityRow("2026-11-27T10:00:00+00:00", "info", f"Notes: renamed {UNC} to x", theme.DARK, "")
    assert row.text.textFormat() == Qt.PlainText and row.detail.textFormat() == Qt.PlainText
    assert "<img" not in row.toolTip() and "&lt;img" in row.toolTip()
    assert dialogs._label(UNC).textFormat() == Qt.PlainText
    assert plain_tip(UNC).startswith("<qt>") and "<img" not in plain_tip(UNC)


def test_message_boxes_never_read_html(qapp, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QLabel

    from skyfold.ui import dialogs

    seen = []
    monkeypatch.setattr(dialogs.MessageDialog, "exec", lambda self: seen.append(
        [lb.textFormat() for lb in self.findChildren(QLabel) if lb.text()]) or 0)
    dialogs.inform(None, "x", UNC)  # (a title other than the app's name heads the message: shown too)
    dialogs.ask(None, UNC, UNC, heading=UNC, detail=UNC)
    assert seen == [[Qt.PlainText] * 2, [Qt.PlainText] * 3]


def test_photo_names_never_run_and_never_take_a_device_name():
    from skyfold.engine import _safe_name

    assert _safe_name("Beach.hta") == "Beach.hta.bin"
    assert _safe_name("x.lnk").endswith(".bin") and _safe_name("IMG_1.HEIC") == "IMG_1.HEIC"
    assert _safe_name("NUL.jpg") == "_NUL.jpg" and _safe_name("con").startswith("_con")
    assert _safe_name("Trips", media=False) == "Trips"  # (album folders keep their names)


def test_open_in_the_photos_window_only_opens_photos_and_videos(qapp, monkeypatch, tmp_path):  # noqa: F811
    from skyfold.ui import photos_window as pw

    opened, shown = [], []
    monkeypatch.setattr(pw.QDesktopServices, "openUrl", lambda u: opened.append(u.toLocalFile()))
    fake = SimpleNamespace(_select_in_explorer=lambda p: shown.append(p), message=lambda *_: None)
    pw.PhotosWindow.open_file(fake, tmp_path / "Beach.hta.bin")
    pw.PhotosWindow.open_file(fake, tmp_path / "IMG_1.HEIC")
    assert [p.name for p in shown] == ["Beach.hta.bin"] and opened and opened[0].endswith("IMG_1.HEIC")


def test_no_account_picture_and_no_bookmarks_after_signing_out(qapp, monkeypatch, tmp_path):  # noqa: F811
    from skyfold import auth, avatar, native_host

    monkeypatch.setattr(auth, "signed_out", lambda: True)
    assert not avatar.save("river@example.test", b"whatever", tmp_path)
    monkeypatch.setattr(native_host, "cache_file", lambda: tmp_path / "bm.json")
    (tmp_path / "bm.json").write_text('{"items": []}', encoding="utf-8")
    assert native_host.answer({"cmd": "bookmarks"}) == {"ok": False, "error": "no-bookmarks"}


def test_a_picture_read_while_signing_out_is_never_kept(qapp, monkeypatch, tmp_path):  # noqa: F811
    from skyfold import avatar, extras
    from skyfold import worker as worker_mod

    monkeypatch.setattr(avatar, "PRIVATE_DIR", tmp_path)
    w = SimpleNamespace(api=object(), settings=SimpleNamespace(apple_id="river@example.test"), _avatar_at=0.0)

    def fetch(api):
        w.api = None  # signed out while the picture was on its way
        return b"picture"

    monkeypatch.setattr(extras, "account_photo", fetch)
    worker_mod.SyncWorker._refresh_avatar(w)
    assert not list(tmp_path.glob("account-*.png"))


def test_certificates_are_always_checked(monkeypatch):
    import requests

    from skyfold import netpolicy

    seen = {}
    monkeypatch.setattr(netpolicy, "_INSTALLED", False)
    monkeypatch.setattr(requests.Session, "send", lambda self, req, **kw: seen.update(kw) or "sent")
    monkeypatch.setattr(netpolicy, "check", lambda *a, **k: None)
    from skyfold import netdiag

    monkeypatch.setattr(netdiag, "timed", lambda fn, s, r, kw: fn(s, r, **kw))
    netpolicy.install()
    req = requests.Request("GET", "https://www.icloud.com/").prepare()
    requests.Session().send(req, verify=False)
    assert seen.get("verify") is True
