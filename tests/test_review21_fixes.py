"""Tests for the fixes after the twenty-first (Codex) review."""

import logging
import sqlite3

from skyfold.app import _RedactEmails
from skyfold.state import State
from tests.test_engine import env as photos_env  # noqa: F401


def _filtered(name, msg, args=(), exc_info=None):
    rec = logging.LogRecord(name, logging.WARNING, "lib.py", 12, msg, args, exc_info)
    _RedactEmails().filter(rec)
    return rec.getMessage()


def test_other_libraries_keep_their_format_string_and_numbers_only():
    text = _filtered("pyicloud.session", "Request to %s failed with %d: %s", ("https://x/y", 503, "<b>Private</b>"))
    assert text.startswith("Request to … failed with 503: …") and "Private" not in text and "lib.py:12" in text
    assert "Private" not in _filtered("pyicloud", "Got <b>Private</b> from the server")  # one finished string
    assert "Private" not in _filtered("urllib3", ValueError("<b>Private</b>"))  # an error used as the message
    assert "ValueError" in _filtered("urllib3", ValueError("<b>Private</b>"))


def test_skyfolds_own_messages_stay_readable():
    assert _filtered("skyfold.worker", "Drive: downloaded %s", ("Docs/a.txt",)) == "Drive: downloaded Docs/a.txt"
    assert "Private" not in _filtered("skyfold.worker", ValueError("<b>Private</b>"))


def test_a_photo_database_from_before_gets_the_fingerprint_column(tmp_path):
    db = sqlite3.connect(tmp_path / "old.db")
    db.executescript("""CREATE TABLE files (asset_id TEXT NOT NULL, resource_key TEXT NOT NULL, rel_path TEXT NOT NULL,
        size INTEGER, is_video INTEGER NOT NULL DEFAULT 0, asset_date TEXT, synced_at TEXT,
        PRIMARY KEY (asset_id, resource_key));
        INSERT INTO files VALUES('A1', 'original', '2025/11/p.jpg', 8, 0, NULL, NULL);""")
    db.commit()
    db.close()
    rows = State(tmp_path / "old.db").files()
    assert rows[0].rel_path == "2025/11/p.jpg" and rows[0].hash is None


def test_downloads_remember_their_fingerprint_and_unchanged_photos_are_still_recycled(photos_env):  # noqa: F811
    import hashlib

    s, remote, root, _ = photos_env
    remote.add("A1", "photo.jpg", b"original")
    s.run()
    assert s.state.files()[0].hash == hashlib.sha256(b"original").hexdigest()
    remote.remove("A1")
    s.run(force_full=True)
    assert not (root / "2025/11/photo.jpg").exists() and not s.state.files()
