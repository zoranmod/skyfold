"""1.1: the account picture in Settings comes from the user's own card in Contacts, stays on this PC
and leaves with the account. Made-up pictures and a fake iCloud only."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage

from skyfold import avatar, extras
from tests.test_review27_codex_ui_probes import qapp  # noqa: F401


def _png(w: int, h: int, colour: str = "#3366cc") -> bytes:
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor(colour))
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


PHOTO_URL = "https://p01-contacts.example.invalid/a.jpg"


class _Api:
    """A fake iCloud: the Contacts service answers the card (or refuses, like the real one did with
    pyicloud's own request), and the photo address answers the picture."""

    def __init__(self, card, status=200, content=b"", refuse=()):
        self.params = {"dsid": "1"}
        self.card, self.refuse = card, set(refuse)
        self.asked: list[str] = []
        self._status, self._content = status, content
        self.session = SimpleNamespace(get=self._get)

    def get_webservice_url(self, name):
        assert name == "contacts"
        return "https://p01-contactsws.example.invalid"

    def _get(self, url, params=None, timeout=None, stream=False):
        self.asked.append(url.rsplit("/co", 1)[-1] if "/co" in url else url)
        if url == PHOTO_URL:
            body = self._content
            return SimpleNamespace(status_code=self._status, headers={"Content-Type": "image/png"},
                                   iter_content=lambda n: [body[i:i + n] for i in range(0, len(body), n)])
        key = url.rsplit("/co", 1)[-1]
        if key in self.refuse or (key.startswith("/mecard") and "/startup" in self.refuse_until_startup()
                                  and "prefToken" not in (params or {})):
            return SimpleNamespace(status_code=400, json=lambda: {})
        if key == "/startup":
            return SimpleNamespace(status_code=200, json=lambda: {"prefToken": "p", "syncToken": "s"})
        assert params.get("clientVersion") and params.get("locale")  # the Contacts app's parameters
        return SimpleNamespace(status_code=200, json=lambda: self.card)

    def refuse_until_startup(self):
        return self.refuse


def test_the_photo_on_my_card_is_read(qapp):  # noqa: F811
    png = _png(30, 20)
    api = _Api({"contacts": [{"firstName": "River", "photo": {"url": PHOTO_URL}}]}, content=png)
    assert extras.account_photo(api) == png
    assert api.asked == ["/mecard/", PHOTO_URL]


def test_the_card_after_the_start_up_request_when_apple_wants_it(qapp):  # noqa: F811
    png = _png(30, 20)
    api = _Api({"contacts": [{"photo": {"url": PHOTO_URL}}]}, content=png, refuse={"/startup-needed"})
    api.refuse = {"/startup"}  # (marker: the card is refused until the start-up tokens come along)
    api.refuse_until_startup = lambda: {"/startup"}
    real = api._get

    def get(url, params=None, timeout=None, stream=False):
        key = url.rsplit("/co", 1)[-1]
        if key == "/startup":
            api.asked.append(key)
            return SimpleNamespace(status_code=200, json=lambda: {"prefToken": "p", "syncToken": "s"})
        if key.startswith("/mecard") and "prefToken" not in (params or {}):
            api.asked.append(key)
            return SimpleNamespace(status_code=400, json=lambda: {})
        return real(url, params, timeout, stream)

    api.session.get = get
    assert extras.account_photo(api) == png
    assert api.asked == ["/mecard/", "/mecard", "/startup", "/mecard/", PHOTO_URL]


@pytest.mark.parametrize("card", [{"contacts": []}, {"contacts": [{"firstName": "River"}]}, {},
                                  {"contacts": [{"photo": {"url": "http://plain.example.invalid/a.jpg"}}]}])
def test_no_photo_means_no_picture_and_nothing_fetched(qapp, card):  # noqa: F811
    api = _Api(card, content=b"x")
    assert extras.account_photo(api) is None
    assert PHOTO_URL not in api.asked


def test_a_failed_or_huge_download_gives_no_picture(qapp):  # noqa: F811
    card = {"contacts": [{"photo": {"url": PHOTO_URL}}]}
    assert extras.account_photo(_Api(card, status=404, content=b"x")) is None
    assert extras.account_photo(_Api(card, content=b"x" * (extras.PHOTO_MAX + 1))) is None


def test_a_refused_card_gives_no_picture(qapp):  # noqa: F811
    api = _Api({"contacts": [{"photo": {"url": PHOTO_URL}}]}, content=b"x",
               refuse={"/mecard/", "/mecard", "/startup"})
    api.refuse_until_startup = lambda: set()
    assert extras.account_photo(api) is None
    assert PHOTO_URL not in api.asked


def test_saved_as_a_small_square_per_apple_id_and_forgotten(qapp, tmp_path):  # noqa: F811
    assert avatar.save("river@example.test", _png(300, 200), tmp_path)
    saved = QImage(str(avatar.path_for("River@Example.test", tmp_path)))  # (the case doesn't matter)
    assert (saved.width(), saved.height()) == (avatar.SIDE, avatar.SIDE)
    assert avatar.path_for("other@example.test", tmp_path) != avatar.path_for("river@example.test", tmp_path)
    assert "river" not in avatar.path_for("river@example.test", tmp_path).name  # (no address in a file name)
    assert not avatar.save("river@example.test", b"not a picture", tmp_path)
    avatar.save("other@example.test", _png(40, 40), tmp_path)
    avatar.forget_all(tmp_path)
    assert not list(tmp_path.glob("account-*.png"))


def test_the_worker_reads_it_once_a_day(qapp, tmp_path, monkeypatch):  # noqa: F811
    from skyfold import worker as worker_mod

    calls = []
    monkeypatch.setattr(extras, "account_photo", lambda api: calls.append(api) or _png(50, 50))
    monkeypatch.setattr(avatar, "PRIVATE_DIR", tmp_path)
    w = SimpleNamespace(api=object(), settings=SimpleNamespace(apple_id="river@example.test"), _avatar_at=0.0)
    worker_mod.SyncWorker._refresh_avatar(w)
    worker_mod.SyncWorker._refresh_avatar(w)
    assert len(calls) == 1 and avatar.path_for("river@example.test", tmp_path).exists()
    monkeypatch.setattr(extras, "account_photo", lambda api: None)  # taken off the card
    w._avatar_at = 0.0
    worker_mod.SyncWorker._refresh_avatar(w)
    assert not avatar.path_for("river@example.test", tmp_path).exists()


def test_settings_shows_the_picture(qapp, tmp_path, monkeypatch):  # noqa: F811
    from skyfold.config import Settings
    from skyfold.ui import dialogs, theme
    from skyfold.ui.settings_parts import Avatar

    monkeypatch.setattr(avatar, "PRIVATE_DIR", tmp_path)
    avatar.save("river@example.test", _png(64, 64, "#00ff00"), tmp_path)
    d = dialogs.SettingsDialog(Settings(apple_id="river@example.test"), theme.DARK)
    try:
        pic = d.findChild(Avatar)
        assert not pic.picture.isNull()
        shot = pic.grab().toImage()
        mid = shot.pixelColor(shot.width() // 2, shot.height() // 2)
        assert mid.green() > 200 and mid.red() < 60
    finally:
        d.close()
