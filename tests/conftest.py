"""Shared test setup: shut Qt down in order once every test has run.

Some tests open real windows, worker threads and thread-pool tasks. Left to the interpreter's
own teardown, Qt sometimes crashes after all tests have passed, which fails CI at random.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


def _isolate_profile(root, monkeypatch):
    home = root / "profile"
    local = home / "AppData" / "Local"
    app = root / "app"
    for key, path in (("HOME", home), ("USERPROFILE", home), ("LOCALAPPDATA", local),
                      ("APPDATA", home / "AppData" / "Roaming"), ("_SKYFOLD_TEST_DATA_DIR", app / "data")):
        monkeypatch.setenv(key, str(path))
    return app, local


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    # config probes its portable folder and loads the language while modules are being imported.
    # Set the roots before collection, and let subprocesses inherit them.
    session_folder = TemporaryDirectory(prefix="skyfold-tests-", ignore_cleanup_errors=True)
    config.add_cleanup(session_folder.cleanup)
    env = pytest.MonkeyPatch()
    config.add_cleanup(env.undo)
    _isolate_profile(Path(session_folder.name), env)


def pytest_sessionfinish(session, exitstatus):
    try:
        from PySide6.QtCore import QThreadPool
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    QThreadPool.globalInstance().waitForDone(10_000)
    for w in app.topLevelWidgets():
        w.close()
        w.deleteLater()
    app.processEvents()
    if hasattr(app, "shutdown"):  # PySide6: deletes the application object and its children now
        app.shutdown()


@pytest.fixture(autouse=True)
def _bookmarks_cache_in_tmp(tmp_path, monkeypatch):
    """No test ever writes the real Safari bookmarks cache (it lives in the user's private folder)."""
    from skyfold import native_host

    path = tmp_path / "safari-bookmarks-cache.json"
    monkeypatch.setattr(native_host, "cache_file", lambda: path)
    return path


@pytest.fixture(autouse=True)
def _account_files_in_tmp(tmp_path_factory, monkeypatch):
    """Account-switch and sign-out tests run the real cleanup code, including its default paths."""
    from skyfold import auth, avatar, config, worker
    from skyfold.ui import dialogs

    # Keep application data separate: many tests expect their own tmp_path to start empty.
    root = tmp_path_factory.mktemp("account")
    app, local = _isolate_profile(root, monkeypatch)
    private = local / config.APP_ID
    private.mkdir(parents=True)
    portable = app / "data"
    portable.mkdir(parents=True)
    monkeypatch.setattr(config, "APP_DIR", app)
    monkeypatch.setattr(config, "DATA_DIR", portable)
    monkeypatch.setattr(config, "SETTINGS_FILE", portable / "settings.json")
    monkeypatch.setattr(config, "PRIVATE_DIR", private)
    monkeypatch.setattr(config, "SESSION_DIR", private / "session")
    monkeypatch.setattr(config, "STATE_DB", private / config.DATABASE)
    monkeypatch.setattr(config, "LOG_FILE", private / "skyfold.log")
    monkeypatch.setattr(auth, "SESSION_DIR", config.SESSION_DIR)
    monkeypatch.setattr(auth, "SIGNED_OUT", private / "signed-out")
    monkeypatch.setattr(avatar, "PRIVATE_DIR", private)
    monkeypatch.setattr(dialogs, "PRIVATE_DIR", private)
    monkeypatch.setattr(worker, "STATE_DB", config.STATE_DB)
    monkeypatch.setattr(auth, "_CURRENT", None)
    monkeypatch.setattr(auth, "_SIGNED_OUT_NOW", False)
    return root


@pytest.fixture(autouse=True)
def _no_modal_account_notice(monkeypatch):
    """The notice after signing in with another Apple ID is a modal box shown a moment later; in tests
    nobody would close it (a test that checks it patches it itself)."""
    from skyfold import app

    monkeypatch.setattr(app, "inform", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _network_policy_per_test():
    """The network rules patch Requests for the whole process: each test starts and ends without
    them (tests talk to local servers), and tests that want them install them."""
    import requests

    from skyfold import netdiag, netpolicy

    send = requests.Session.send
    installed = netpolicy._INSTALLED
    yield
    requests.Session.send = send
    netpolicy._INSTALLED = installed
    netdiag._until = 0.0


@pytest.fixture(autouse=True)
def _crash_log_in_tmp(tmp_path, monkeypatch):
    """No test writes the user's real crash.log: a test that starts Skyfold (main) gets one in its own
    temporary folder, and afterwards a crash in a later test is reported by pytest as usual."""
    import faulthandler
    import sys

    from skyfold import app

    monkeypatch.setattr(app, "LOG_FILE", tmp_path / "skyfold.log")
    monkeypatch.setattr(app, "_CRASH_FILE", None)
    yield
    if app._CRASH_FILE is not None:
        faulthandler.enable(file=sys.__stderr__, all_threads=True)
        app._CRASH_FILE.close()
