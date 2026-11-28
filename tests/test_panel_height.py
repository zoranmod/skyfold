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


@pytest.mark.parametrize("lang", ["en", "bs"])
@pytest.mark.parametrize("rows", [0, 1, 2, 10])
def test_sync_now_and_the_bell_never_change_the_open_panels_height(qapp, panel, lang, rows):  # noqa: F811
    i18n.set_language(lang)
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


@pytest.mark.parametrize("lang", ["en", "bs"])
def test_the_panel_opens_with_the_same_height_in_every_state(qapp, panel, lang):  # noqa: F811
    i18n.set_language(lang)
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
