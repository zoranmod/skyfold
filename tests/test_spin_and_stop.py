"""The syncing ring turns smoothly, Stop is the red button, and the panel never shows "done" for a
moment between the Photos pass and the Drive pass. Made-up data only."""
from __future__ import annotations

import threading

from PySide6.QtGui import QColor

from skyfold import worker
from skyfold.config import Settings
from skyfold.ui import flyout, theme
from tests.test_review27_codex_ui_probes import qapp  # noqa: F401


def test_the_ring_turns_at_a_steady_speed_without_jumping_back(qapp, monkeypatch):  # noqa: F811
    f = flyout.Flyout(theme.DARK)
    try:
        f.set_state("syncing", "Checking for changes…")
        monkeypatch.setattr(f, "isVisible", lambda: True)
        f._spin_base = f._spin_base or f.status_icon.pixmap()
        angles = []
        for ms in range(0, 4000, 16):  # four seconds, frame by frame
            monkeypatch.setattr(f._spin_clock, "elapsed", lambda ms=ms: ms)
            monkeypatch.setattr(f._spin_clock, "isValid", lambda: True)
            f._spin_tick()
            angles.append(f.badge.angle)
        steps = [(b - a) % 360 for a, b in zip(angles, angles[1:])]
        assert max(steps) - min(steps) < 0.5  # every frame moves the same amount, wrapping included
        assert f._spin.interval() <= 17
    finally:
        f.close()


def test_stop_is_the_red_button_with_a_white_stop_mark(qapp):  # noqa: F811
    before = qapp.styleSheet()
    for pal in (theme.LIGHT, theme.DARK, theme.OLED):
        qapp.setStyleSheet(theme.stylesheet(pal))  # (as the app does)
        f = flyout.Flyout(pal)
        try:
            f.set_state("syncing", "")
            assert f.sync_btn.text() and f.sync_btn.icon_name == "stop"
            img = f.sync_btn.grab().toImage()
            mid = QColor(img.pixelColor(img.width() // 5, img.height() // 2))
            assert mid.red() > 150 and mid.green() < 90 and mid.blue() < 90, mid.name()
            f.set_state("idle", "")
            assert f.sync_btn.icon_name == "sync"
        finally:
            f.close()
            qapp.setStyleSheet(before)


def test_drive_counts_as_syncing_from_the_moment_it_is_started(qapp, monkeypatch, tmp_path):  # noqa: F811
    w = worker.SyncWorker(Settings(drive_enabled=True, drive_dir=str(tmp_path)))
    shown = []
    w.stateChanged.connect(lambda s, _t: shown.append(s))
    go = threading.Event()
    monkeypatch.setattr(w, "_drive_passes", lambda: go.wait(5))
    w._kick_drive(full=True)
    w._set_state("idle", "Everything is in sync.")  # the Photos pass ends while Drive starts
    assert shown[-1] == "syncing"  # never a moment of "done" (Sync now) in between
    go.set()
    w._drive_thread.join(5)
    qapp.processEvents()  # (the state comes from Drive's thread)
    assert shown[-1] == "idle"  # Drive's thread ended: the panel is told
