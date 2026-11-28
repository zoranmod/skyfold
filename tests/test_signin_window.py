"""Signing in is step 3 of first run: the same see-through window as steps 1 and 2 (same size, same
footer band), and one statement about the password. Made-up data only."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from skyfold import app as appmod, auth, i18n
from skyfold.ui import backdrop, dialogs, theme

EMAIL = "river@example.test"


@pytest.fixture
def app():
    a = QApplication.instance() or QApplication([])
    yield a
    i18n.set_language("en")
    for w in list(a.topLevelWidgets()):
        if isinstance(w, (dialogs.LoginDialog, dialogs.FolderSetupDialog)):
            w.hide()
            w.deleteLater()
    a.processEvents()


def _to_code_page(d, monkeypatch) -> None:
    """Apple "answers" that a code is needed (nothing is sent anywhere)."""
    ran = []
    monkeypatch.setattr(dialogs, "run_async", lambda fn, done, fail: ran.append((done, fail)))
    monkeypatch.setattr(d, "_bring_forward", lambda: None)
    d.password.setText("not a real password")
    d._submit_credentials()
    ran[0][0](auth.LoginStep(object(), True, "Enter the test code"))
    assert d.pages.currentWidget().isAncestorOf(d.code)


def test_sign_in_is_as_large_as_first_run_on_both_pages_and_with_an_error(app, tmp_path, monkeypatch):
    setup = dialogs.FolderSetupDialog(str(tmp_path / "Photos"), theme.DARK)
    d = dialogs.LoginDialog(EMAIL, theme.DARK)
    setup.show()
    d.show()
    app.processEvents()
    assert not setup.msg.isVisible()  # (nothing to say about that folder: the window's usual size)
    assert d.size() == setup.size()
    d._show_error("Enter your Apple ID and password.")  # an error moves the text, never the window
    app.processEvents()
    assert d.size() == setup.size()
    _to_code_page(d, monkeypatch)
    app.processEvents()
    assert d.size() == setup.size()
    assert d.minimumSize() == d.maximumSize() == setup.size()  # (and it can't be dragged to another size)


def test_sign_in_has_first_runs_footer_band_and_field_margins(app, tmp_path):
    setup = dialogs.FolderSetupDialog(str(tmp_path / "Photos"), theme.DARK)
    d = dialogs.LoginDialog("", theme.DARK, first_run=True)
    setup.show()
    d.show()
    app.processEvents()

    def at(w: QWidget, top: QWidget):
        return w.mapTo(top, QPoint(0, 0))

    foot, setup_foot = d.cancel.parentWidget(), setup.back.parentWidget()
    assert foot.objectName() == setup_foot.objectName() == "Footer" and foot is d.ok.parentWidget()
    assert foot.geometry() == setup_foot.geometry()
    assert d.cancel.objectName() == "Link" and d.ok.objectName() == "Primary"
    assert at(d.cancel, d).x() == at(setup.back, setup).x()  # Cancel where Later / Back is
    assert at(d.ok, d).x() + d.ok.width() == at(setup.ok, setup).x() + setup.ok.width()  # Sign in where Continue is
    # the fields from the folder field's left edge to the Browse button's right edge
    browse = next(b for b in setup.findChildren(type(setup.ok)) if b.text() == "Browse…")
    for field in (d.email, d.password):
        assert at(field, d).x() == at(setup.path, setup).x()
        assert at(field, d).x() + field.width() == at(browse, setup).x() + browse.width()
    assert at(d.hero.title, d).y() == at(setup.hero.title, setup).y()  # the same top


def test_sign_in_is_see_through_in_every_theme_where_windows_allows_it(app, monkeypatch):
    asked = []
    monkeypatch.setattr(backdrop, "glass_supported", lambda: True)
    monkeypatch.setattr(backdrop, "apply_backdrop", lambda w, kind, dark: asked.append((kind, dark)) or True)
    for palette, dark in ((theme.LIGHT, False), (theme.DARK, True), (theme.OLED, True)):
        asked.clear()
        d = dialogs.LoginDialog(EMAIL, palette)
        try:
            assert d.GLASS and d.glass and d.testAttribute(Qt.WA_TranslucentBackground)
            d.show()
            app.processEvents()
            assert asked and asked[-1] == ("acrylic", dark)  # the material (and title bar) of the app's theme
            assert "QWidget#Root" in d.styleSheet() and "background: transparent" in d.styleSheet()
            assert backdrop.glass_colours(palette, dark)["foot"] in d.styleSheet()  # the footer as a layer over it
        finally:
            d.close()


def test_sign_in_is_solid_without_glass_and_its_title_bar_follows_the_apps_theme(app, monkeypatch):
    frames = []
    monkeypatch.setattr(backdrop, "glass_supported", lambda: False)
    monkeypatch.setattr(backdrop, "match_frame", lambda w, bg=None, fg=None, border=None: frames.append((bg, fg)))
    d = dialogs.LoginDialog(EMAIL, theme.LIGHT)  # (the app's choice, whatever Windows' own theme is)
    try:
        assert not d.glass and not d.testAttribute(Qt.WA_TranslucentBackground)
        d.show()
        app.processEvents()
        assert frames and frames[-1] == (theme.LIGHT["bg"], theme.LIGHT["text"])
        assert d.styleSheet() == ""  # no see-through layers
    finally:
        d.close()


def test_sign_in_says_once_that_the_password_is_not_stored(app):
    d = dialogs.LoginDialog(EMAIL, theme.DARK)
    try:
        texts = [w.text() for w in d.findChildren(QLabel) if w.text()]
        assert len(texts) == len(set(texts))
        said = [x for x in texts if "never stored" in x.lower()]
        assert len(said) == 1 and said[0] == d.hero.text.text(), said
        assert sum("password" in x.lower() for x in texts) == 1  # the line under the fields is about the session
        session = [x for x in texts if "session" in x.lower()]
        assert len(session) == 1 and session[0] != d.hero.text.text()
    finally:
        d.close()


def test_the_step_bars_show_only_in_first_run(app):
    alone = dialogs.LoginDialog(EMAIL, theme.DARK)
    first = dialogs.LoginDialog("", theme.DARK, first_run=True)
    try:
        for d in (alone, first):
            d.show()
        app.processEvents()
        assert first.hero.dots.isVisible() and first.hero.dots.current == 2
        assert not alone.hero.dots.isVisible()
        # (their room is kept, so the window looks the same otherwise)
        assert alone.email.mapTo(alone, QPoint(0, 0)) == first.email.mapTo(first, QPoint(0, 0))
    finally:
        alone.close()
        first.close()


def test_after_first_run_sign_in_takes_the_setup_windows_place(app):
    """The app opens it as step 3 right after the two steps, as large as that window was and where it
    stood; opened again later (from Settings, a session that ran out) it is the plain window."""
    shown = {}
    fake = SimpleNamespace(settings=SimpleNamespace(apple_id=""), _dialogs={}, c=theme.OLED,
                           flyout=SimpleNamespace(hide=lambda: None),
                           _show_dialog=lambda key, dlg: (shown.__setitem__(key, dlg), dlg.show()),
                           _first_run_login=(QSize(540, 603), QPoint(120, 80)))
    appmod.Controller._open_login(fake)
    d = shown["login"]
    try:
        app.processEvents()
        assert d.c is theme.OLED  # the app's theme
        assert d.size() == QSize(540, 603) and d.pos() == QPoint(120, 80)
        assert d.hero.dots.isVisible()
        d._cancel_sign_in()
        appmod.Controller._open_login(fake)
        again = shown["login"]
        app.processEvents()
        assert again is not d and not again.hero.dots.isVisible()
        assert again.size() == dialogs.FolderSetupDialog.usual_size(theme.OLED)
    finally:
        for w in (d, shown["login"]):
            w._cancel_sign_in()
