"""1.0.20: the upload send buffer is a setting: 8 MB (default) or automatic (Windows' own tuning)."""
import socket

import requests

from skyfold import drive_remote
from skyfold.config import Settings
from skyfold.worker import _apply_upload_buffer

SND = (socket.SOL_SOCKET, socket.SO_SNDBUF)


def opts(session, url):
    return session.get_adapter(url).poolmanager.connection_pool_kw["socket_options"]


def test_automatic_leaves_the_buffer_to_windows_and_a_change_remounts(monkeypatch):
    monkeypatch.setattr(drive_remote, "SEND_BUFFER", drive_remote.SEND_BUFFER)
    url = "https://p31-docws.icloud.com/ws/upload"
    session = requests.Session()
    _apply_upload_buffer(Settings(upload_buffer_mb=8))
    drive_remote._big_send_buffer(session, url)
    assert (*SND, 8 << 20) in opts(session, url)
    _apply_upload_buffer(Settings(upload_buffer_mb=0))
    drive_remote._big_send_buffer(session, url)
    assert not any(o[:2] == SND for o in opts(session, url))  # automatic: no fixed size
    assert (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1) in opts(session, url)


def test_hand_edited_sizes_are_kept_in_range(tmp_path, monkeypatch):
    import json

    from skyfold import config

    f = tmp_path / "settings.json"
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "SETTINGS_FILE", f)
    for raw, kept in ((500, 64), (-3, 0), (16, 16)):
        f.write_text(json.dumps({"upload_buffer_mb": raw}), encoding="utf-8")
        assert Settings.load().upload_buffer_mb == kept
    assert Settings().upload_buffer_mb == 0  # automatic by default (measured faster, 1.0.20)


def test_speed_is_logged_per_upload(caplog, monkeypatch):
    import logging

    monkeypatch.setattr(drive_remote, "SEND_BUFFER", 0)
    with caplog.at_level(logging.INFO, logger="skyfold.drive_remote"):
        drive_remote._log_speed("Drive", 50_000_000, 10.0)
    assert "5.00 MB/s" in caplog.text and "automatic" in caplog.text
