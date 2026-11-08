"""Sign-in always checks the password with Apple, even when a saved session is still valid."""

import pytest
import pyicloud
from pyicloud import exceptions as ex

from skyfold import auth


class FakeService:
    calls: list = []

    def __init__(self, apple_id, password, cookie_directory=None, authenticate=True, refresh_interval=None):
        self.password = password
        self.requires_2fa = False
        self.requires_2sa = False
        self.is_trusted_session = True
        self.session = type("S", (), {"data": {"session_token": "saved", "trust_token": "trusted"}})()
        FakeService.calls.append(("init", authenticate))

    def authenticate(self, force_refresh=False, service=None):
        FakeService.calls.append(("authenticate", force_refresh, dict(self.session.data)))
        if self.password != "right":
            raise ex.PyiCloudFailedLoginException("Invalid email/password combination.")


@pytest.fixture()
def fake(monkeypatch, tmp_path):
    FakeService.calls = []
    monkeypatch.setattr(pyicloud, "PyiCloudService", FakeService)
    monkeypatch.setattr(auth, "SESSION_DIR", tmp_path / "session")
    return FakeService


def test_sign_in_checks_the_password_even_with_a_saved_session(fake):
    step = auth.start_login("you@example.com", "right")
    assert not step.needs_code
    assert ("init", False) in fake.calls
    _, force, data = next(c for c in fake.calls if c[0] == "authenticate")
    # the saved web token is dropped (so Apple checks the password); the trust token stays (no new 2FA)
    assert force and "session_token" not in data and data["trust_token"] == "trusted"


def test_wrong_password_is_reported(fake):
    with pytest.raises(auth.LoginError):
        auth.start_login("you@example.com", "wrong")


def test_log_never_contains_the_apple_id():
    import logging

    from skyfold.app import _RedactEmails

    rec = logging.LogRecord("x", logging.INFO, "", 0, "Sign-in rejected for %s", ("someone@example.com",), None)
    _RedactEmails().filter(rec)
    assert "someone@example.com" not in rec.getMessage() and "<apple-id>" in rec.getMessage()
