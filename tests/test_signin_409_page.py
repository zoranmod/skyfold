"""Apple may answer the two-factor page request with 409 plus the page: the sign-in goes on."""

import pytest
import requests
from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudAPIResponseException

from skyfold import auth


def _api(tmp_path, monkeypatch, status, ctype):
    def fake(_session, method, url, **kwargs):
        r = requests.Response()
        r.status_code = status
        r.url = url
        r.reason = "Conflict"
        r.headers["Content-Type"] = ctype
        r._content = b'<html><script type="application/json" class="boot_args">{}</script></html>' \
            if "html" in ctype else b'{"error":"x"}'
        return r

    monkeypatch.setattr(requests.Session, "request", fake)
    auth._serialize_session_saves()  # installs Skyfold's request wrapper
    (tmp_path / "s").mkdir()
    return PyiCloudService("review@example.invalid", None, cookie_directory=str(tmp_path / "s"),
                           authenticate=False, refresh_interval=None)


def test_the_two_factor_page_with_409_is_handed_to_pyicloud(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, 409, "text/html;charset=UTF-8")
    r = api.session.get("https://idmsa.apple.com/appleauth/auth", headers={"Accept": "text/html"})
    assert r.status_code == 409 and "boot_args" in r.text


@pytest.mark.parametrize("url,ctype", [("https://idmsa.apple.com/appleauth/auth/signin", "text/html"),
                                       ("https://idmsa.apple.com/appleauth/auth", "application/json")])
def test_other_409_answers_still_raise(tmp_path, monkeypatch, url, ctype):
    api = _api(tmp_path, monkeypatch, 409, ctype)
    with pytest.raises(PyiCloudAPIResponseException):
        api.session.get(url)
