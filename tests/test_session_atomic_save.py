"""The saved sign-in is replaced in one step: a save cut off midway leaves the previous one whole."""

import json
import os

import pytest

from skyfold import auth


def _service(folder):
    from pyicloud import PyiCloudService

    auth._serialize_session_saves()
    return PyiCloudService("atomic@example.invalid", None, cookie_directory=str(folder),
                           authenticate=False, refresh_interval=None)


@pytest.fixture(autouse=True)
def _any_session(monkeypatch):
    monkeypatch.setattr(auth, "_CURRENT", None)


def test_save_writes_session_and_cookies(tmp_path):
    api = _service(tmp_path)
    api.session.data["session_token"] = "token-1"
    api.session.cookies.set("X-APPLE-WEBAUTH-TOKEN", "c1", domain=".icloud.com", path="/")
    api.session._save_session_data()
    assert json.loads(open(api.session.session_path, encoding="utf-8").read())["session_token"] == "token-1"
    assert "X-APPLE-WEBAUTH-TOKEN" in open(api.session.cookiejar_path, encoding="utf-8").read()
    assert not [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]


def test_failed_write_keeps_the_previous_session(tmp_path, monkeypatch):
    api = _service(tmp_path)
    api.session.data["session_token"] = "token-1"
    api.session._save_session_data()

    real_dump = json.dump

    def dies_midway(obj, f, *a, **k):
        f.write('{"session_tok')  # what a power cut would leave behind
        raise OSError("disk went away")

    monkeypatch.setattr(json, "dump", dies_midway)
    api.session.data["session_token"] = "token-2"
    api.session._save_session_data()  # logged, not raised
    monkeypatch.setattr(json, "dump", real_dump)

    assert json.loads(open(api.session.session_path, encoding="utf-8").read())["session_token"] == "token-1"
    assert not [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]


def test_restored_service_reads_the_saved_files(tmp_path):
    api = _service(tmp_path)
    api.session.data["session_token"] = "token-3"
    api.session._save_session_data()
    again = _service(tmp_path)
    assert again.session.data.get("session_token") == "token-3"
