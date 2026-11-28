"""The hidden flyout needs no animation frames, even when new sync events arrive."""
from skyfold.ui import flyout, theme
from tests.test_review27_codex_ui_probes import qapp  # noqa: F401


def test_sync_animation_timer_follows_visibility_and_keeps_phase(qapp, monkeypatch):  # noqa: F811
    monkeypatch.setattr(flyout, "animations_on", lambda: True)
    panel = flyout.Flyout(theme.DARK)
    try:
        panel.set_state("syncing", "Checking for changes.")
        assert not panel._spin.isActive()
        panel.show()
        assert panel._spin.isActive()
        panel.hide()
        assert not panel._spin.isActive()
        panel.set_state("syncing", "Downloading photos")
        assert not panel._spin.isActive()

        monkeypatch.setattr(panel._spin_clock, "elapsed", lambda: 1234)
        panel.show()
        assert panel._spin.isActive()
        assert panel.badge.angle == (1234 * flyout.RING_DEG_PER_MS) % 360

        panel.hide()
        monkeypatch.setattr(flyout, "animations_on", lambda: False)
        panel.show()
        assert not panel._spin.isActive()
        panel.set_state("idle", "")
        assert not panel._spin_clock.isValid()
    finally:
        panel.close()
