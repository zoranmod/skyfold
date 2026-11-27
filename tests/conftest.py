"""Shared test setup: shut Qt down in order once every test has run.

Some tests open real windows, worker threads and thread-pool tasks. Left to the interpreter's
own teardown, Qt sometimes crashes after all tests have passed, which fails CI at random.
"""


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


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _bookmarks_cache_in_tmp(tmp_path, monkeypatch):
    """No test ever writes the real Safari bookmarks cache (it lives in the user's private folder)."""
    from skyfold import native_host

    path = tmp_path / "safari-bookmarks-cache.json"
    monkeypatch.setattr(native_host, "cache_file", lambda: path)
    return path


@pytest.fixture(autouse=True)
def _signed_in_memory_reset(monkeypatch):
    """A Sign out in one test (kept in memory for the rest of that run) doesn't carry into the next."""
    from skyfold import auth

    monkeypatch.setattr(auth, "_SIGNED_OUT_NOW", False)


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
