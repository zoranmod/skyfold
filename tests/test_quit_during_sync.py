"""Quitting while a pass runs is a stop, never a sync error in the panel."""

import threading
from types import SimpleNamespace

from skyfold import worker as w
from skyfold.i18n import t


def _worker():
    wk = w.SyncWorker.__new__(w.SyncWorker)
    wk.syncer = wk.notes = None
    wk.drive = SimpleNamespace(cancel_flag=threading.Event())
    wk.api = None
    states, logged = [], []
    wk._set_state = lambda *a: states.append(a)
    wk.log = lambda *a: logged.append(a)
    return wk, states, logged


def test_an_error_after_quit_was_asked_is_not_shown_as_a_sync_error():
    wk, states, logged = _worker()
    wk.drive.cancel_flag.set()  # Quit: the pass is cut short
    problems = []
    wk._handle(RuntimeError("cannot schedule new futures after shutdown"), problems, t("iCloud Drive"), "drive")
    assert not problems and not logged
    assert states and states[-1][1] == t("Sync stopped.")


def test_a_real_error_is_still_shown():
    wk, states, logged = _worker()
    problems = []
    wk._handle(RuntimeError("something broke"), problems, t("iCloud Drive"), "drive")
    assert problems and logged and logged[-1][0] == "error"
