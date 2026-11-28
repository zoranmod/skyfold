"""Quit and the update hand-over wait for the sync worker (1.2.1).

Quit used to wait 3 s with the windows frozen and then left, also with a transfer still running. Now
it tells the worker to stop, keeps the event loop running while what was started comes to an end, and
leaves only then; an update is handed to the installer only once the worker is at rest, and is put off
otherwise. A made-up slow worker in a real QThread stands in for a pass in the middle of a transfer."""
import logging
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from skyfold import app as appmod, updater
from skyfold.app import Controller
from skyfold.ui import dialogs


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class SlowWorker(QObject):
    """Stays inside one slot (a transfer that takes its time) until `release` is set."""

    go = Signal()

    def __init__(self):
        super().__init__()
        self._closing = False
        self.cancelled = 0
        self.entered, self.release = threading.Event(), threading.Event()
        self.drive = threading.Event()  # set: the Drive thread is still running
        self.go.connect(self.work)

    @Slot()
    def work(self):
        self.entered.set()
        self.release.wait(20)

    def cancel(self):
        self.cancelled += 1

    def _stop_watchers(self):
        pass

    def drive_running(self):
        return self.drive.is_set()


def pump(app, until, seconds=8.0):
    end = time.monotonic() + seconds
    while not until() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    return until()


@pytest.fixture()
def busy(qapp, monkeypatch):
    """(controller stand-in, worker, its thread, what happened) with the worker inside its slow slot."""
    monkeypatch.setattr(dialogs, "stop_tasks", lambda: None)
    worker, thread = SlowWorker(), QThread()
    worker.moveToThread(thread)
    thread.start()
    worker.go.emit()
    assert worker.entered.wait(5)
    seen = {"exit": [], "hard": []}
    ctl = SimpleNamespace(worker=worker, thread=thread, tray=SimpleNamespace(hide=lambda: None),
                          app=SimpleNamespace(quit=lambda: seen["exit"].append(
                              (thread.isRunning(), worker.drive_running()))))
    monkeypatch.setattr(appmod, "_exit_now", lambda: seen["hard"].append(thread.isRunning()))
    try:
        yield ctl, worker, thread, seen
    finally:
        worker.release.set()
        worker.drive.clear()
        thread.quit()
        assert thread.wait(5000)
        pump(qapp, lambda: False, 0.15)  # (a look still scheduled runs out here, not in a later test)


def test_quit_waits_for_the_worker_and_the_windows_keep_answering(qapp, busy):
    ctl, worker, thread, seen = busy
    ticks = []
    clock = QTimer()
    clock.setInterval(10)
    clock.timeout.connect(lambda: ticks.append(time.monotonic()))
    clock.start()
    QTimer.singleShot(400, worker.release.set)  # the transfer ends by itself a little later
    began = time.monotonic()
    Controller.quit(ctl)
    assert time.monotonic() - began < 0.3  # Quit comes straight back: nothing is frozen
    assert worker._closing and worker.cancelled == 1  # the worker was told to stop
    assert not seen["exit"]  # and Skyfold hasn't left: the worker is still in its transfer
    assert pump(qapp, lambda: seen["exit"])
    clock.stop()
    assert seen["exit"] == [(False, False)]  # left once, with the worker's thread ended
    assert time.monotonic() - began >= 0.35 and not seen["hard"]
    assert len(ticks) >= 10  # the event loop ran all the while (the release itself came from a timer)


def test_quit_also_waits_for_the_drive_thread(qapp, busy):
    ctl, worker, thread, seen = busy
    worker.drive.set()
    worker.release.set()  # the worker's own thread can end at once; Drive's can't yet
    Controller.quit(ctl)
    assert pump(qapp, lambda: thread.isFinished())
    pump(qapp, lambda: False, 0.2)
    assert not seen["exit"]
    worker.drive.clear()
    assert pump(qapp, lambda: seen["exit"]) and seen["exit"] == [(False, False)]


def test_quit_pressed_twice_leaves_once(qapp, busy):
    ctl, worker, thread, seen = busy
    Controller.quit(ctl)
    Controller.quit(ctl)
    assert worker.cancelled == 1
    worker.release.set()
    assert pump(qapp, lambda: seen["exit"])
    pump(qapp, lambda: False, 0.2)
    assert len(seen["exit"]) == 1


def test_a_worker_that_never_ends_is_logged_and_left_behind_without_qts_exit(qapp, busy, monkeypatch, caplog):
    ctl, worker, thread, seen = busy
    monkeypatch.setattr(appmod, "QUIT_WAIT", 0.3)
    with caplog.at_level(logging.WARNING, logger="skyfold"):
        Controller.quit(ctl)
        assert pump(qapp, lambda: seen["hard"])
    # Qt's own exit would destroy the thread while it runs; that is never asked for
    assert seen["hard"] == [True] and not seen["exit"]
    assert any("still busy" in r.getMessage() for r in caplog.records)


def test_an_idle_worker_lets_quit_leave_at_once(qapp, busy):
    ctl, worker, thread, seen = busy
    worker.release.set()
    assert pump(qapp, lambda: not worker.entered.is_set() or True)
    began = time.monotonic()
    Controller.quit(ctl)
    assert pump(qapp, lambda: seen["exit"]) and time.monotonic() - began < 2


# the update hand-over ------------------------------------------------------------------------------
class Release:
    version, notes, page = "9.9.9", "Made-up notes", "https://example.invalid/release"


class RoundWorker:
    """What the update looks at: is a round (or a Drive pass) running?"""

    def __init__(self, busy):
        self.busy, self._closing, self.cancelled = busy, False, 0

    def working(self):
        return self.busy

    def cancel(self):
        self.cancelled += 1


@pytest.fixture()
def update(qapp, monkeypatch, tmp_path):
    """(controller stand-in, worker, the Update window, what happened); the download is made up."""
    happened = []
    worker = RoundWorker(busy=True)
    monkeypatch.setattr(updater, "can_install", lambda: True)
    monkeypatch.setattr(updater, "start_install",
                        lambda new: happened.append(("handed over", worker.busy, worker._closing)))
    shown = []
    ctl = SimpleNamespace(_release=Release(), _show_dialog=lambda _key, dlg: shown.append(dlg), worker=worker,
                          req=SimpleNamespace(syncNow=SimpleNamespace(emit=lambda manual: happened.append(("sync", manual)))),
                          quit=lambda: happened.append(("quit", worker.busy)))
    Controller.open_update(ctl)
    dlg = shown[0]
    dlg.go.setEnabled(False)  # as while it downloads and installs
    try:
        yield ctl, worker, dlg, happened
    finally:
        dlg.close()
        pump(qapp, lambda: False, 0.15)


def test_the_update_is_put_off_while_the_worker_is_busy(qapp, update, monkeypatch, tmp_path):
    ctl, worker, dlg, happened = update
    monkeypatch.setattr(appmod, "UPDATE_WAIT", 0.3)
    dlg.ready.emit(tmp_path)  # downloaded and checked
    assert worker.cancelled == 1 and not happened  # the round is asked to stop; nothing is handed over yet
    assert pump(qapp, lambda: dlg.go.isEnabled())
    assert happened == [("sync", False)]  # never handed over, Skyfold didn't quit, and syncing goes on
    assert not worker._closing
    assert "still finishing" in dlg.status.text()  # and the window says why


def test_the_update_is_handed_over_once_the_worker_is_at_rest(qapp, update, tmp_path):
    ctl, worker, dlg, happened = update
    ticks = []
    clock = QTimer()
    clock.setInterval(10)
    clock.timeout.connect(lambda: ticks.append(1))
    clock.start()
    QTimer.singleShot(300, lambda: setattr(worker, "busy", False))  # the transfer ends
    began = time.monotonic()
    dlg.ready.emit(tmp_path)
    assert time.monotonic() - began < 0.2 and not happened  # (waiting never blocks the window)
    assert pump(qapp, lambda: len(happened) == 2)
    clock.stop()
    # handed over only with the worker at rest and closed to new work, then Quit
    assert happened == [("handed over", False, True), ("quit", False)]
    assert len(ticks) >= 10


def test_an_update_window_closed_while_waiting_installs_nothing(qapp, update, tmp_path):
    ctl, worker, dlg, happened = update
    dlg.ready.emit(tmp_path)
    dlg.reject()  # "Later"
    worker.busy = False
    pump(qapp, lambda: False, 0.3)
    assert not happened and not worker._closing


def test_a_failed_hand_over_leaves_this_version_syncing(qapp, update, monkeypatch, tmp_path):
    ctl, worker, dlg, happened = update
    worker.busy = False

    def fails(_new):
        raise OSError(5, "made-up installer failure")

    monkeypatch.setattr(updater, "start_install", fails)
    monkeypatch.setattr(appmod, "warn", lambda *a: happened.append("warned"))
    dlg.ready.emit(tmp_path)
    assert happened == ["warned"] and dlg.go.isEnabled() and not worker._closing
