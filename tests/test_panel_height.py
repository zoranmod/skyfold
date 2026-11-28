"""The tray panel has one height: in every state, on the notifications page, opened again, with or
without entries. (It was sized by the wrong part whenever the list of recent activity was not on screen:
the panel closed, or the bell's page open. It then shrank or grew at the next change, e.g. "Sync now".)
Made-up data only."""
from __future__ import annotations

import time

import pytest

from skyfold import i18n
from skyfold.ui import flyout, theme
from skyfold.ui.notices import NoticeLog
from tests.test_review27_codex_ui_probes import qapp  # noqa: F401

PROGRESS = "Downloading photos · 812 of 1,284 · 4.2 MB/s · ~3 min"
REASON = "Downloads are up to date. Uploads of 3 photos are waiting for the next pass because iCloud is busy."


def _pump(app, seconds: float = 0.05) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()


@pytest.fixture
def panel(qapp):  # noqa: F811
    before = qapp.styleSheet()
    c = theme.palette("dark")
    qapp.setStyleSheet(theme.stylesheet(c))
    made = []

    def make(rows: int, two_lines: bool = True):
        f = flyout.Flyout(c)
        f.attach_notices(NoticeLog(), lambda key: None)
        f.set_stats({"photos": 1284, "videos": 12, "bytes": 10**9, "last_sync": "2026-11-28T08:00:00+00:00",
                     "drive_enabled": True, "notes_enabled": True})
        for i in range(rows):
            text = f"Drive: downloaded: Docs/file{i}.txt" if two_lines else f"Checked {i} things"
            f.add_activity("2026-11-10T08:00:00+00:00", "download", text, "x" if two_lines else "")
        made.append(f)
        return f

    yield make
    for f in made:
        f.close()
    qapp.setStyleSheet(before)
    i18n.set_language("en")


@pytest.mark.parametrize("rows", [0, 1, 2, 10])
def test_sync_now_and_the_bell_never_change_the_open_panels_height(qapp, panel, rows):  # noqa: F811
    f = panel(rows)
    _pump(qapp)
    f.hide()
    f.set_state("idle", i18n.t("Everything is in sync."))  # (the state arrives while the panel is closed)
    f.show_at(None)
    heights = {"opened": f.height()}

    def step(name: str, *calls) -> None:
        for call in calls:
            call()
        _pump(qapp)
        heights[name] = f.height()

    step("settled")
    step("Sync now", lambda: f.set_state("syncing", i18n.t("Checking for changes…")))
    step("progress", lambda: f.set_progress(3, 10, "Reading iCloud Drive… 7912"))
    step("progress on two lines", lambda: f.set_progress(812, 1284, PROGRESS))
    step("an entry", lambda: f.add_activity("2026-11-28T09:00:00+00:00", "download", "Photos: downloaded IMG_1.HEIC", "y"))
    step("the bell", f.show_notices)
    step("the bell, progress", lambda: f.set_progress(900, 1284, PROGRESS))
    step("the bell, an entry",
         lambda: f.add_activity("2026-11-28T09:00:01+00:00", "download", "Photos: downloaded IMG_2.HEIC", "z"))
    step("the bell, another state", lambda: f.set_state("syncing", "Uploading to iCloud Drive…"))
    step("back", f.show_home)
    step("stopped", lambda: f.set_state("idle", "Sync stopped."))
    step("ended for a reason", lambda: f.set_state("idle", REASON))
    step("the bell again", f.show_notices)
    step("the bell, in sync", lambda: f.set_state("idle", i18n.t("Everything is in sync.")))
    step("back again", f.show_home)
    step("an error", lambda: f.set_state("error", "Could not reach iCloud. Check your internet connection."))
    step("cleared", f._clear_activity)
    step("syncing, empty", lambda: f.set_state("syncing", "x"))
    step("the first entry", lambda: f.add_activity("2026-11-28T09:00:02+00:00", "download", "Photos: downloaded I.HEIC", "q"))
    assert len(set(heights.values())) == 1, heights


def test_the_panel_opens_with_the_same_height_in_every_state(qapp, panel):  # noqa: F811
    states = [("idle", ""), ("idle", "Sync stopped."), ("idle", REASON), ("syncing", "Checking for changes…"),
              ("syncing", PROGRESS), ("paused", ""), ("offline", "No internet connection."),
              ("error", "Could not reach iCloud. Check your internet connection and try again in a few minutes."),
              ("auth", "Sign in to iCloud again to continue syncing."), ("setup", "")]
    heights = {}
    for rows, two_lines in ((0, True), (1, True), (1, False), (10, True)):
        f = panel(rows, two_lines)
        for settled in (False, True):  # opened before and after the deferred sizing has run
            for state, text in states:
                f.hide()
                _pump(qapp, 0.01)
                f.set_state(state, text)
                if text == PROGRESS:
                    f.set_progress(812, 1284, PROGRESS)
                if settled:
                    _pump(qapp, 0.01)
                f.show_at(None)
                key = (rows, two_lines, settled, state, text[:12])
                heights[key + ("opened",)] = f.height()
                _pump(qapp, 0.02)
                heights[key + ("settled",)] = f.height()
                f.show_notices()  # the bell: the same panel
                f.set_state(state, text)
                _pump(qapp, 0.02)
                heights[key + ("bell",)] = f.height()
        f.hide()
    assert len(set(heights.values())) == 1, {k: v for k, v in heights.items() if v != heights[next(iter(heights))]}


def test_the_open_panel_keeps_its_height_when_a_banner_comes_and_goes(qapp, panel):  # noqa: F811
    f = panel(10)
    f.show_at(None)
    _pump(qapp)
    h = f.height()
    f.set_update("9.9.9")  # needs room: the list gives up whole rows, the panel stays
    _pump(qapp)
    assert f.height() == h and f.update_banner.isVisible()
    step = f._activity_rows()[0].height()
    assert f.list.height() % step == 0  # never a row cut in half
    f.set_update(None)
    _pump(qapp)
    assert f.height() == h
    f.hide()


# Long texts: a service's detail is one line ---------------------------------------------------------
# No "last_sync": "· first sync in progress" / "· first read pending" is added to already long lines.
LONG_STATS = {"photos": 1_234_567, "videos": 234_567, "bytes": 1_234_567_890_123,
              "drive_enabled": True, "drive_files": 2_345_678, "drive_bytes": 9_876_543_210_987,
              "notes_enabled": True, "notes_count": 123_456_789}


def test_long_service_details_stay_on_one_line_and_never_change_the_panels_height(qapp, panel):  # noqa: F811
    """A detail that wrapped onto a second line made its row, and with it the whole panel, a few pixels
    taller (measured at 125 %: 631, 650 or 672 px, depending on the texts)."""
    import html

    f = panel(3)
    f.show_at(None)
    _pump(qapp)
    usual = f.height()
    row_height = f.row_photos.height()
    f.hide()
    g = panel(3)
    g.set_stats(LONG_STATS)
    g.show_at(None)
    _pump(qapp)
    seen = {"opened": g.height()}
    for row in (g.row_photos, g.row_drive, g.row_notes):
        assert row.height() == row_height
        assert row.detail.height() < 2 * row.detail.fontMetrics().lineSpacing()  # one line
        assert "\n" not in row.detail.text()
    # cut short with "…": the whole line is in the row's tooltip, as plain text
    assert g.row_photos.detail.clipped()
    whole = g.row_photos.detail.text()
    assert "1,234,567" in whole and whole.endswith("first sync in progress")
    assert html.escape(whole) in g.row_photos.toolTip() and g.row_photos.toolTip().startswith("<qt>")
    assert whole in g.row_photos.accessibleName()
    # the texts change while the panel is open: still the same panel
    g.set_stats({"photos": 3, "videos": 0, "bytes": 10**6, "last_sync": "2026-11-28T08:00:00+00:00"})
    _pump(qapp)
    seen["short"] = g.height()
    assert not g.row_photos.detail.clipped()
    assert g.row_photos.toolTip() and "3 photos" not in g.row_photos.toolTip()  # (only when it was checked)
    g.set_state("syncing", "Checking for changes…")
    g.set_stats(LONG_STATS)
    _pump(qapp)
    seen["long again, syncing"] = g.height()
    g.set_state("auth", "Sign in to iCloud again to continue syncing.")
    _pump(qapp)
    seen["waiting for sign-in"] = g.height()
    g.hide()
    g.set_state("idle", "")
    g.show_at(None)
    _pump(qapp)
    seen["opened again"] = g.height()
    assert set(seen.values()) == {usual}, (usual, seen)
    g.hide()


# Small screens and display scales: whole rows only, and the list is what gives way ---------------------------
class _Screen:
    """A made-up screen in logical pixels (what Qt reports: a 1280 x 720 display at 150 % is 853 x 480),
    with a taskbar along the bottom."""

    def __init__(self, width: int, height: int, taskbar: int = 48) -> None:
        from PySide6.QtCore import QRect

        self._full = QRect(0, 0, width, height)
        self._avail = QRect(0, 0, width, height - taskbar)

    def geometry(self):
        return self._full

    def availableGeometry(self):  # noqa: N802
        return self._avail


# 1920x1080 at 100 %, 125 %, 150 %; 1366x768 at 125 % and 150 %; 1280x720 at 150 %; 1024x600 at 125 %
SCREENS = [(1920, 1080), (1536, 864), (1280, 720), (1093, 614), (1024, 600), (911, 512), (853, 480), (819, 480)]


@pytest.mark.parametrize("rows", [0, 1, 10])
@pytest.mark.parametrize("size", SCREENS)
def test_on_any_screen_the_list_shows_whole_rows_and_the_panel_keeps_one_height(qapp, panel, size, rows):  # noqa: F811
    f = panel(rows)
    screen = _Screen(*size)
    f._screen = lambda: screen
    f.show_at(None)
    _pump(qapp)
    limit = screen.availableGeometry().height() - 24
    seen = {}

    def check(name: str) -> None:
        _pump(qapp, 0.02)
        seen[name] = f.height()
        assert f.height() <= limit, name
        assert f.geometry().bottom() <= screen.availableGeometry().bottom(), name
        shown = f._activity_rows()
        if shown:
            step = shown[0].height()
            assert f.list.height() >= step and f.list.height() % step == 0, (name, f.list.height(), step)

    check("opened")
    filler = f.list if rows else f.empty
    row = f._activity_rows()[0].height() if rows else 30
    if f._needed_height() - filler.height() - f._list_pad.height() + 2 * row <= limit:
        # there is room for the rest and a row or two: only the list gave way, the content does not scroll
        assert f._scroll.verticalScrollBar().maximum() == 0
    f.set_update("9.9.9")
    check("an update")
    f.set_state("syncing", "Checking for changes…")
    f.set_progress(812, 1284, PROGRESS)
    check("syncing")
    f.add_activity("2026-11-28T09:00:00+00:00", "download", "Photos: downloaded IMG_1.HEIC", "y")
    check("an entry")
    f.show_notices()
    check("the bell")
    f.show_home()
    f.set_update(None)
    f.set_state("idle", "")
    check("back")
    assert len(set(seen.values())) == 1, seen
    if size[1] >= 720:  # a normal screen: the full panel
        assert seen["opened"] > 600 and f.list.height() >= 3 * f._activity_rows()[0].height()
    f.hide()


@pytest.mark.parametrize("scale", ["1.25", "1.5"])
def test_the_panel_tests_hold_at_every_display_scale(scale):
    """The banner, the screens and the long texts above again in a process of its own at 125 % and 150 % (the
    offscreen screen is 800 px tall: 640 and 533 logical pixels at the larger scales, a small screen)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    if os.environ.get("SKYFOLD_PANEL_SCALE_RUN"):
        pytest.skip("the scaled run itself")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=scale, SKYFOLD_PANEL_SCALE_RUN="1")
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", "-k",
                          "banner_comes or on_any_screen or long_service_details", str(Path(__file__))],
                         cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-1000:]
