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
