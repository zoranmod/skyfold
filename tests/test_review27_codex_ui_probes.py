"""Codex's real-Qt probes from review 27 (of 7fc9baa), kept as tests; offline, no file deletion.

The two characterize_* tests (B6 focus, B7 live theme) assert the fixed behaviour here (fixed_*).
Fonts are loaded when the machine has them; Qt is shut down by conftest.py, not here.
"""
import pytest
from types import SimpleNamespace

@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase, QFont
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    import os
    for f in ('SegUIVar.ttf', 'segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf'):
        if os.path.exists('C:/Windows/Fonts/' + f):
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + f)
    app.setFont(QFont('Segoe UI Variable Text', 10))
    yield app


@pytest.mark.parametrize('state,expected', [('idle','syncNow'), ('syncing','cancelSync'),
    ('paused','togglePause'), ('offline','syncNow'), ('auth','login'), ('error','syncNow'), ('setup','setup')])
def test_primary_panel_action_keeps_correct_signal(qapp, state, expected):
    from skyfold.ui import flyout, theme
    f = flyout.Flyout(theme.LIGHT)
    events = []
    for name in ('syncNow','cancelSync','togglePause','login','setup'):
        getattr(f, name).connect(lambda n=name: events.append(n))
    f.set_state(state, '')
    f.sync_btn.click()
    assert events == [expected]
    f.close()


@pytest.mark.parametrize('state,allowed', [('idle',True),('paused',True),('auth',False),('setup',False)])
def test_more_menu_preserves_pause_and_web_actions(qapp, monkeypatch, state, allowed):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMenu
    from skyfold.ui import flyout, theme
    f = flyout.Flyout(theme.LIGHT); f.set_state(state, '')
    seen = []
    f.togglePause.connect(lambda: seen.append('pause'))
    f.web_btn.clicked.connect(lambda: seen.append('web'))
    monkeypatch.setattr(flyout, '_open_url', lambda url: None)
    checked = []
    def inspect_menu():
        menu = f.findChildren(QMenu)[-1]
        actions = menu.actions()
        checked.append((len(actions), actions[1].isEnabled()))
        actions[0].trigger()
        if allowed: actions[1].trigger()
        menu.close()
    QTimer.singleShot(0, inspect_menu)
    f._more_menu()
    assert checked == [(2, allowed)]
    assert seen == (['web','pause'] if allowed else ['web'])
    f.close()


@pytest.mark.parametrize('action', ['close','escape','later','keep','delete'])
def test_deletion_dialog_requires_the_existing_explicit_action(qapp, action):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton
    from skyfold.ui import dialogs, theme
    from skyfold.i18n import t
    pending = SimpleNamespace(to_pc=['p1'], to_cloud=['c1'], names={'p1':'one','c1':'two'})
    d = dialogs.PendingDialog(pending, theme.LIGHT); seen = []
    d.decided.connect(lambda *args: seen.append(args)); d.show(); qapp.processEvents()
    assert not seen
    if action == 'close': d.close()
    elif action == 'escape': QTest.keyClick(d, Qt.Key_Escape)
    else:
        text = {'later':t('Decide later'), 'keep':t('Keep on this PC'), 'delete':t('Delete on this PC too')}[action]
        next(b for b in d.findChildren(QPushButton) if b.text() == text).click()
    assert seen == ([('reject',['p1'])] if action == 'keep' else [('approve',['p1'])] if action == 'delete' else [])
    if action in ('keep','delete'):
        assert d._open_sections == 1  # the other direction still needs its own explicit decision
    d.close()


@pytest.mark.parametrize('mode', ['light','dark'])
def test_fixed_storage_keyboard_focus_is_visible(qapp, mode):
    from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from skyfold.ui import flyout, theme
    c = theme.palette(mode)
    qapp.setPalette(theme.qpalette(c)); qapp.setStyleSheet(theme.stylesheet(c))
    window = QWidget(); layout = QVBoxLayout(window)
    btn = QPushButton('Other focus target'); layout.addWidget(btn)
    bar = flyout.StorageBar(c); layout.addWidget(bar)
    bar.set(88, 200, [('Photos',44,'photos'),('Drive',36,'docs')])
    window.show(); btn.setFocus(); qapp.processEvents()
    before = bar.grab().toImage()
    bar.setFocus(Qt.TabFocusReason); qapp.processEvents()
    assert bar.hasFocus()
    assert before != bar.grab().toImage()  # the focus frame shows
    assert not bar.details.isVisible()
    QTest.keyClick(bar, Qt.Key_Space); qapp.processEvents()
    assert bar.details.isVisible()  # interactive keyboard stop, not a decorative frame
    window.close(); qapp.setStyleSheet('')


def test_fixed_open_settings_toggles_take_the_new_theme(qapp):
    from skyfold.ui import dialogs, theme
    from skyfold.config import Settings
    qapp.setPalette(theme.qpalette(theme.LIGHT)); qapp.setStyleSheet(theme.stylesheet(theme.LIGHT))
    d = dialogs.SettingsDialog(Settings(), theme.LIGHT)
    d.show(); qapp.processEvents()
    # as app._on_theme_changed does: application, then every open window's painted widgets
    qapp.setStyleSheet(theme.stylesheet(theme.DARK)); qapp.setPalette(theme.qpalette(theme.DARK))
    dialogs.refresh_palette(theme.DARK)
    qapp.processEvents()
    assert d.instant.c['text'] == theme.DARK['text']
    d.close(); qapp.setStyleSheet('')
