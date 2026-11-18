"""The update window and the panel row (with a pretend release; nothing is downloaded)."""
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

from skyfold import updater
from skyfold.ui import dialogs, theme
from skyfold.ui.flyout import Flyout

REL = updater.Release("9.9.9", "**Faster sync**\n\n- one\n- two", updater.PAGE, "z", "s")


def _app():
    return QApplication.instance() or QApplication([])


def _wait(app, cond, secs=3.0):
    end = time.time() + secs
    while time.time() < end and not cond():
        app.processEvents()
        time.sleep(0.02)


def test_the_window_shows_whats_new_and_installs_on_update_now(monkeypatch, tmp_path):
    app = _app()
    monkeypatch.setattr(updater, "download", lambda rel, progress=None, cancelled=None: (progress(5, 10), tmp_path)[1])
    d = dialogs.UpdateDialog(REL, can_install=True)
    d.show()
    assert "Faster sync" in d.notes.toPlainText()
    got = []
    d.ready.connect(got.append)
    d.go.click()
    _wait(app, lambda: bool(got))
    assert got == [tmp_path] and not d.go.isEnabled()
    d.close()


def test_a_failed_download_is_shown_and_can_be_tried_again(monkeypatch):
    app = _app()

    def broken(rel, progress=None, cancelled=None):
        raise updater.UpdateError(updater.t("the download doesn't match its published checksum"))

    monkeypatch.setattr(updater, "download", broken)
    d = dialogs.UpdateDialog(REL, can_install=True)
    d.go.click()
    _wait(app, lambda: d.go.isEnabled())
    assert d.go.isEnabled() and "checksum" in d.status.text()


def test_without_an_installable_copy_it_opens_the_release_page(monkeypatch):
    _app()
    opened = []
    monkeypatch.setattr(dialogs, "_open_url", opened.append)
    d = dialogs.UpdateDialog(REL, can_install=False)
    d.go.click()
    assert opened == [updater.PAGE]


def test_the_panel_shows_a_row_while_a_newer_version_is_out():
    _app()
    f = Flyout(theme.palette("light"))
    f.set_update("9.9.9")
    assert not f.update_banner.isHidden() and "9.9.9" in f.update_banner.text.text()
    f.set_update(None)
    assert f.update_banner.isHidden()
