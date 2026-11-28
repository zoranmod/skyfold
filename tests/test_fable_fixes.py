"""Fixes from the final review: the footer's short time, a neutral sign when a pass ended for a reason,
the Safari bookmarks copy going when bookmarks are switched off, note titles that can't break the
Notes.html index, reserved names with a space, and long paths in Settings → About. Made-up data only."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from tests.test_review27_codex_ui_probes import qapp  # noqa: F401


def test_the_footer_time_is_short_enough(qapp):  # noqa: F811
    from skyfold.ui.flyout import _footer_when

    now = datetime.now().astimezone()
    old = _footer_when((now - timedelta(days=20)).isoformat())
    assert len(old) <= 7 and ":" not in old  # "8 Nov": no full date and time that gets cut off
    week = _footer_when((now - timedelta(days=2)).isoformat())
    assert len(week) <= 10 and ":" in week  # "Thu 20:17"


def test_a_long_footer_value_is_elided_not_cut(qapp):  # noqa: F811
    from skyfold.ui.panel_parts import FootTime

    f = FootTime()
    f.resize(40, 34)
    f.show()
    f.setText("Last checked\n27.11.2026. 20:17")
    assert f.value.text().endswith("…") and f.text() == "Last checked\n27.11.2026. 20:17"
    f.close()


def test_a_pass_that_ended_for_a_reason_shows_no_green_tick(qapp):  # noqa: F811
    from skyfold.i18n import t
    from skyfold.ui import flyout, theme

    f = flyout.Flyout(theme.DARK)
    try:
        f.set_state("idle", t("Sync stopped."))
        assert f.badge.state == "note" and f._dot_state() == "note"
        f.set_state("idle", t("Everything is in sync."))
        assert f.badge.state == "idle"
    finally:
        f.close()


def test_switching_bookmarks_off_removes_the_extensions_copy(tmp_path, monkeypatch):
    from skyfold import native_host, worker
    from skyfold.config import Settings

    copy = tmp_path / "safari-bookmarks.json"
    copy.write_text('{"items": []}', encoding="utf-8")
    monkeypatch.setattr(native_host, "cache_file", lambda: copy)
    w = worker.SyncWorker(Settings(bookmarks_enabled=True))
    for name in ("_make_drive", "_make_notes", "_ensure_watchers", "_apply_timers", "_emit_stats", "sync_now"):
        monkeypatch.setattr(w, name, lambda *a, **k: None)
    off = w.settings.copy()
    off.bookmarks_enabled = False
    w.apply_settings(off, False)
    assert not copy.exists()


def test_reserved_names_with_a_space_are_caught():
    from skyfold.drive_engine import windows_name_problem

    assert windows_name_problem("con .jpg", Path("C:/x")) and not windows_name_problem("Garden/a.jpg", Path("C:/x"))
