"""Fixes from the 1.1 audit: Stop ends the whole round, the panel keeps the reason a pass ended (and
the progress description across a theme change), a manual Drive request survives the Drive thread's
hand-over, the bookmarks cache stays gone after Sign out, the tray mark respects Windows' animation
setting, only a left click opens a service row, and the glass buttons' text stays readable.
Synthetic data only: fake services, temporary folders, no network."""

from __future__ import annotations

import re
import threading
from types import SimpleNamespace

import pytest

from skyfold import bookmarks
from skyfold import notes_engine as ne
from skyfold.config import Settings
from skyfold.engine import Cancelled
from skyfold.i18n import t
from skyfold.worker import SyncWorker
from tests.test_notes import FakeNotes
from tests.test_review27_codex_ui_probes import qapp  # noqa: F401


# --- worker helpers ----------------------------------------------------------------------------------

def _worker(**settings):
    w = SyncWorker(Settings(photos_dir_confirmed=True, notifications=False, **settings))
    w.api = object()
    w.syncer = SimpleNamespace(cancel_flag=threading.Event())
    w._run_photos = lambda full, problems: True
    w._refresh_storage = lambda force=False: None
    w._refresh_avatar = lambda: None
    w._emit_stats = lambda: None
    w._ensure_watchers = lambda: None
    states = []
    w.stateChanged.connect(lambda s, text: states.append((s, text)))
    return w, states


# --- 1. Stop ends the whole round -------------------------------------------------------------------

def test_stop_after_photos_starts_no_later_service(tmp_path):
    w, states = _worker(bookmarks_enabled=True)
    remote = FakeNotes()
    remote.put("n1", "Shopping")
    root = tmp_path / "Notes"
    w.notes = ne.NotesSyncer(ne.NotesState(tmp_path / "n.db"), remote, root)
    w.drive = SimpleNamespace(cancel_flag=threading.Event())
    started = []
    w._run_bookmarks = lambda problems: started.append("bookmarks") or True
    w._kick_drive = lambda full, once=False: started.append("drive")
    # Stop pressed while the storage check after Photos runs (as in the audit's probe)
    w._refresh_storage = lambda force=False: w.cancel()

    w.sync_now(True)

    assert remote.exports == 0 and not root.exists()  # Notes neither started nor wrote anything
    assert started == []
    assert states[-1] == ("idle", t("Sync stopped."))
    assert w.notes.cancel_flag.is_set()  # Notes did not clear the round's stop


def test_a_later_sync_now_runs_again_after_a_stop(tmp_path):
    w, states = _worker()
    remote = FakeNotes()
    remote.put("n1", "Shopping")
    root = tmp_path / "Notes"
    w.notes = ne.NotesSyncer(ne.NotesState(tmp_path / "n.db"), remote, root)
    w._refresh_storage = lambda force=False: w.cancel()
    w.sync_now(True)
    assert remote.exports == 0

    w._refresh_storage = lambda force=False: None
    w.sync_now(True)  # a deliberate new Sync now: a new round
    assert remote.exports == 1
    assert states[-1] == ("idle", t("Everything is in sync."))


def test_notes_pass_never_clears_a_stop_itself(tmp_path):
    remote = FakeNotes()
    remote.put("n1", "Shopping")
    root = tmp_path / "Notes"
    notes = ne.NotesSyncer(ne.NotesState(tmp_path / "n.db"), remote, root)
    notes.cancel_flag.set()
    with pytest.raises(Cancelled):
        notes.run()
    assert notes.cancel_flag.is_set() and remote.exports == 0 and not root.exists()


def test_stop_during_bookmarks_keeps_drive_from_starting():
    w, states = _worker(bookmarks_enabled=True)
    w.notes = None
    w.drive = SimpleNamespace(cancel_flag=threading.Event())
    kicked = []
    w._kick_drive = lambda full, once=False: kicked.append(full)
    w._run_bookmarks = lambda problems: w.cancel() or True
    w.sync_now(True)
    assert kicked == []
    assert states[-1] == ("idle", t("Sync stopped."))


def test_a_follow_up_asked_before_stop_is_dropped_with_it():
    w, states = _worker()
    w.notes = None
    rounds = []

    def photos(full, problems):
        rounds.append(full)
        if len(rounds) == 1:
            w._request(full=True, manual=False)  # (queued while busy)
            w.cancel()
        return True

    w._run_photos = photos
    w.sync_now(True)
    assert rounds == [False]  # the follow-up never ran
    assert states[-1] == ("idle", t("Sync stopped."))


def test_stop_while_only_drive_runs_is_what_the_panel_says():
    w, states = _worker()
    w._set_state("idle", t("Everything is in sync."))  # the last round finished
    w._set_state("syncing", t("Checking iCloud Drive…"), "drive")
    w._drive_stopped = True  # Stop pressed
    w._handle(Cancelled(), [], t("iCloud Drive"), "drive")
    assert states[-1] == ("idle", t("Sync stopped."))


# --- 2. the reason a pass ended ----------------------------------------------------------------------

def test_notes_with_more_to_come_is_not_reported_as_in_sync():
    w, states = _worker()
    w.notes = SimpleNamespace(cancel_flag=threading.Event(),
                              run=lambda full=False: ne.NotesResult(exported=15, more=True))
    w.sync_now(True)
    assert states[-1] == ("idle", t("Some notes are still to come. They follow with the next sync."))
    w.notes = SimpleNamespace(cancel_flag=threading.Event(), run=lambda full=False: ne.NotesResult(exported=1))
    w.sync_now(True)
    assert states[-1] == ("idle", t("Everything is in sync."))


@pytest.fixture()
def panel(qapp):  # noqa: F811
    from skyfold.ui import flyout, theme

    f = flyout.Flyout(theme.LIGHT)
    f.set_stats({"drive_enabled": True, "notes_enabled": True, "last_sync": None})
    yield f
    f.close()


def test_panel_keeps_the_reason_a_pass_ended(panel):
    panel.set_state("idle", t("Sync stopped."))
    assert panel.status_title.text() == t("Sync stopped.").rstrip(".")
    assert panel.status_line.text() == t("Sync stopped.").rstrip(".")
    assert panel.status_text.text() == "" and not panel.status_text.isVisibleTo(panel)
    panel.refresh_times()  # (the clock redraws the card every 30 s)
    assert panel.status_title.text() == t("Sync stopped.").rstrip(".")

    waiting = t("Downloads are up to date. Uploads to iCloud Photos are waiting.")
    panel.set_state("idle", waiting)
    head, _, rest = waiting.partition(". ")
    assert panel.status_title.text() == head
    assert panel.status_text.text() == rest
    assert panel.dot.toolTip() == waiting

    panel.set_state("idle", t("Everything is in sync."))  # the usual end: the usual look
    assert panel.status_title.text() == t("Up to date")
    assert panel.status_text.text() == t("{names} and {last} are in step with iCloud.",
                                         names=", ".join([t("Photos"), t("iCloud Drive")]), last=t("Notes"))


def _controller(flyout):
    from skyfold import app

    ctl = SimpleNamespace(state="idle", flyout=flyout, _pending=None, anim=SimpleNamespace(stop=lambda: None),
                          m_status=SimpleNamespace(setText=lambda _t: None),
                          tray=SimpleNamespace(setToolTip=lambda _t: None, setIcon=lambda _i: None))
    ctl._on_state = lambda s, text: app.Controller._on_state(ctl, s, text)
    return ctl


def test_a_deletion_question_answered_keeps_the_reason(panel):
    from skyfold import app

    panel.set_state("idle", t("Sync stopped."))
    app.Controller._on_pending(_controller(panel), None)
    assert panel.status_title.text() == t("Sync stopped.").rstrip(".")


def test_a_fit_still_waiting_when_the_panel_is_destroyed_never_runs(qapp, monkeypatch):  # noqa: F811
    import sys

    from PySide6.QtCore import QCoreApplication, QEvent

    from skyfold.ui import flyout, theme

    failures = []
    monkeypatch.setattr(sys, "excepthook", lambda typ, exc, tb: failures.append(str(exc)))
    f = flyout.Flyout(theme.LIGHT)
    f.add_activity("2026-11-27T10:00:00+00:00", "info", "Synthetic entry")  # (fits the list a moment later)
    f.deleteLater()  # quitting, or a test's teardown
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()
    assert failures == []  # ("Internal C++ object (Flyout) already deleted" in a later test's event loop)


# --- 3. a theme change keeps the progress description -----------------------------------------------

def test_theme_change_keeps_the_progress_description(panel):
    from skyfold.ui import theme

    panel.set_state("syncing", t("Checking for changes…"))
    panel.set_progress(5, 10, "Downloading photos · 5 of 10")
    assert panel.status_title.text() == "Downloading photos"
    panel.apply_palette(theme.DARK)
    assert panel.status_title.text() == "Downloading photos"
    assert panel.status_text.text().replace(chr(0xA0), " ") == "5 of 10"
    panel.set_state(panel.state, panel.state_text)  # (the tray's redraw half a second later)
    assert panel.status_title.text() == "Downloading photos"
    panel.set_state("syncing", t("Checking iCloud Drive…"))  # new words from the worker: shown as they are
    assert panel.status_title.text() == t("Syncing…")


# --- 4. a manual full Drive request at the Drive thread's hand-over ---------------------------------

def test_full_drive_request_while_the_old_thread_is_leaving_gets_a_runner():
    w, _states = _worker()
    w.drive = SimpleNamespace(cancel_flag=threading.Event())
    served = []
    w._run_drive = lambda problems, changed=None: served.append(changed) or True
    release = threading.Event()
    left = threading.Event()
    real = w._drive_passes

    def passes():
        real()
        left.set()
        release.wait(5)  # alive, but it has already found the queue empty

    w._drive_passes = passes
    w._kick_drive(full=True)
    first = w._drive_thread
    assert left.wait(5) and first.is_alive() and served == [None]

    left.clear()
    w._kick_drive(full=True)  # the manual request arrives now
    assert left.wait(5)  # a new thread took it ...
    assert served == [None, None] and not w._drive_want_full  # ... and ran it
    assert w._drive_thread is not first
    release.set()
    first.join(5)
    w._drive_thread.join(5)
    assert not w.drive_running() and w._drive_ui[0] == "idle"


def test_an_old_drive_thread_leaving_late_does_not_hide_the_new_pass():
    w, _states = _worker()
    w.drive = SimpleNamespace(cancel_flag=threading.Event())
    w._drive_thread = threading.Thread(target=lambda: None)  # a newer Drive thread has taken over
    w._drive_ui = ("syncing", t("Checking iCloud Drive…"))
    done = threading.Event()

    def old():
        w._drive_passes = lambda: None
        w._drive_loop()
        done.set()

    threading.Thread(target=old).start()
    assert done.wait(5)
    assert w._drive_ui[0] == "syncing" and not w._drive_exiting


# --- 5. the bookmarks cache after Sign out ------------------------------------------------------------

def _fake_bookmarks(monkeypatch, during_fetch=lambda: None):
    def fetch(_api):
        during_fetch()
        return []

    monkeypatch.setattr(bookmarks, "fetch", fetch)
    monkeypatch.setattr(bookmarks, "build", lambda _records: [])


def test_bookmarks_read_ending_after_sign_out_writes_no_cache(tmp_path, monkeypatch, _bookmarks_cache_in_tmp):
    cache = _bookmarks_cache_in_tmp
    w, _states = _worker(bookmarks_enabled=True, bookmarks_dir=str(tmp_path / "bm"))

    def sign_out():  # what the window side does, in its order, while the read is on its way
        cache.write_text("{}", encoding="utf-8")
        w.account_gen += 1  # (_close_photos)
        bookmarks.remove_cache()

    _fake_bookmarks(monkeypatch, sign_out)
    problems = []
    assert w._run_bookmarks(problems) and not problems
    assert not cache.exists()
    assert not (tmp_path / "bm" / bookmarks.FILE_NAME).exists()


def test_bookmarks_read_ending_after_quit_writes_no_cache(tmp_path, monkeypatch, _bookmarks_cache_in_tmp):
    w, _states = _worker(bookmarks_enabled=True, bookmarks_dir=str(tmp_path / "bm"))
    _fake_bookmarks(monkeypatch, lambda: setattr(w, "_closing", True))
    assert w._run_bookmarks([])
    assert not _bookmarks_cache_in_tmp.exists()


def test_bookmarks_cache_is_written_while_still_signed_in(tmp_path, monkeypatch, _bookmarks_cache_in_tmp):
    w, _states = _worker(bookmarks_enabled=True, bookmarks_dir=str(tmp_path / "bm"))
    _fake_bookmarks(monkeypatch)
    assert w._run_bookmarks([])
    assert _bookmarks_cache_in_tmp.exists()
    assert (tmp_path / "bm" / bookmarks.FILE_NAME).exists()


def test_cache_removal_waits_for_a_write_in_progress(_bookmarks_cache_in_tmp):
    cache = _bookmarks_cache_in_tmp
    cache.write_text("{}", encoding="utf-8")
    removed = threading.Event()
    with bookmarks.CACHE_LOCK:
        th = threading.Thread(target=lambda: (bookmarks.remove_cache(), removed.set()))
        th.start()
        assert not removed.wait(0.2) and cache.exists()
    th.join(5)
    assert removed.is_set() and not cache.exists()


# --- 6. the tray mark and Windows' animation setting -------------------------------------------------

@pytest.mark.parametrize("animate", [True, False])
def test_tray_mark_spins_only_when_windows_animates(qapp, monkeypatch, animate):  # noqa: F811
    from PySide6.QtCore import QTimer

    from skyfold import app

    monkeypatch.setattr(app, "animations_on", lambda: animate)
    icons = []
    ctl = SimpleNamespace(state="idle", _panel_gone=lambda: False, _pending=None, anim=QTimer(),
                          flyout=SimpleNamespace(set_state=lambda *a: None),
                          m_status=SimpleNamespace(setText=lambda _t: None),
                          tray=SimpleNamespace(setToolTip=lambda _t: None, setIcon=icons.append))
    ctl.anim.setInterval(120)
    app.Controller._on_state(ctl, "syncing", t("Checking for changes…"))
    assert ctl.anim.isActive() == animate
    assert bool(icons) == (not animate)  # still: one syncing mark, drawn once
    ctl.anim.stop()


# --- 7. only the left button opens a service row -------------------------------------------------------

def test_service_row_opens_on_left_click_and_keys_only(qapp):  # noqa: F811
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from skyfold.ui import flyout, theme

    row = flyout.ServiceRow("photo", "Photos", theme.LIGHT)
    hits = []
    row.clicked.connect(lambda: hits.append(1))
    row.show()
    QTest.mouseClick(row, Qt.RightButton)
    QTest.mouseClick(row, Qt.MiddleButton)
    assert hits == []
    QTest.mouseClick(row, Qt.LeftButton)
    assert hits == [1]
    QTest.keyClick(row, Qt.Key_Return)
    QTest.keyClick(row, Qt.Key_Space)
    assert hits == [1, 1, 1]
    row.close()


# --- 8. text on the glass Sync now / Stop buttons --------------------------------------------------------

def _lum(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def _contrast(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _over(top, alpha, under):
    return tuple(top[i] * alpha + under[i] * (1 - alpha) for i in range(3))


@pytest.mark.parametrize("name", ["LIGHT", "DARK", "OLED"])
def test_glass_button_text_reaches_4_5_to_1(qapp, monkeypatch, name):  # noqa: F811
    from PySide6.QtGui import QColor

    from skyfold.ui import backdrop, flyout, theme

    palette = theme.OLED if name == "OLED" else getattr(theme, name)
    monkeypatch.setattr(backdrop, "glass_supported", lambda: True)
    f = flyout.Flyout(palette)
    try:
        assert f.glass
        sheet = f.styleSheet()
        g = backdrop.glass_colours(palette, QColor(palette["bg"]).lightness() < 128)
        tint, foot = backdrop.rgba(g["tint"]), backdrop.rgba(g["foot"])
        for prop, text_key in (("primary", "accent_text"), ("wide", "stop_text")):
            m = re.search(r'QToolButton#Bar\[' + prop + r'="true"\] \{ background: qlineargradient\([^;]*?'
                          r'stop:0 rgba\((\d+), (\d+), (\d+), (\d+)\), stop:1 rgba\((\d+), (\d+), (\d+), (\d+)\)\)',
                          sheet)
            assert m, prop
            v = [int(x) for x in m.groups()]
            top, bottom = (v[0:3], v[3] / 255), (v[4:7], v[7] / 255)
            text = QColor(palette[text_key])
            text = (text.red(), text.green(), text.blue())
            for desktop in ((255, 255, 255), (0, 0, 0), (128, 128, 128), (240, 220, 40), (40, 80, 220)):
                under = _over((tint.red(), tint.green(), tint.blue()), tint.alphaF(), desktop)
                under = _over((foot.red(), foot.green(), foot.blue()), foot.alphaF(), under)
                # the text sits in the middle of the button's gradient
                upper, lower = _over(top[0], top[1], under), _over(bottom[0], bottom[1], under)
                mid = tuple((a + b) / 2 for a, b in zip(upper, lower))
                assert _contrast(text, mid) >= 4.5, (name, prop, desktop, round(_contrast(text, mid), 2))
    finally:
        f.close()
