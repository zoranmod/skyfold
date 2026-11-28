"""The Photos grid keeps its pictures: what is on screen is never dropped, and a window left alone
asks for nothing. (1.2 on a 4K screen: the pictures on screen and the ones kept ready around it did
not fit the fixed budget, so each arriving picture pushed out another, which was asked for again,
for ever: photos went blank and came back many times a second.) Made-up photos only."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from skyfold.remote import AssetInfo
from skyfold.state import FileRow, State

MB = 1024 * 1024


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def pump(qapp, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return False


# --- the cache by itself ---------------------------------------------------------------------------
def _thumbs(screen: set[str]):
    from skyfold.ui.photos_window import Thumbs

    asked = []
    th = Thumbs(SimpleNamespace(request=lambda key, path, size, ahead=False: asked.append((key, size, ahead))))
    th.on_screen = lambda: screen
    return th, asked


def _item(n):
    return SimpleNamespace(asset_id=f"A{n}", key=f"A{n}", path=None)


def _arrive(th, asked, done):
    """What the loader would do: every picture asked for and not yet made arrives."""
    from PySide6.QtGui import QImage

    n = 0
    for key, size, _ahead in asked:
        if (key, size) not in done:
            done.add((key, size))
            th.put(key, size, QImage(size * 4 // 3, size, QImage.Format_RGB32))
            n += 1
    del asked[:]
    return n


def test_a_small_budget_never_drops_what_is_on_screen(qapp):
    one = 560 * 746 * 4
    screen = {f"A{n}" for n in range(20)}
    th, asked = _thumbs(screen)
    th.BUDGET = {False: 3 * one, True: 3 * one}  # room for three: twenty are on screen
    th.CAP = 5 * one
    for n in range(20):
        assert th.get(_item(n), 560) is None  # painted: asked for
    assert _arrive(th, asked, set()) == 20
    assert all(th.get(_item(n), 560) is not None for n in range(20)) and not asked  # all there, none asked again
    # pictures of other photos come (kept ready, or asked for before the screen moved on): still all there
    for n in range(20, 60):
        th.get(_item(n), 560, ahead=True)
    _arrive(th, asked, set())
    assert all(th.get(_item(n), 560) is not None for n in range(20)) and not asked
    assert th.used[True] <= 21 * one  # and the others did not pile up: at most the one that just came


def test_looking_ahead_stops_when_the_pool_is_full_and_starts_again_when_the_screen_moves(qapp):
    one = 560 * 746 * 4
    screen = {f"A{n}" for n in range(10)}
    th, asked = _thumbs(screen)
    th.BUDGET = {False: 4 * one, True: 4 * one}
    th.CAP = 14 * one  # ten on screen and four more
    for n in range(10):
        th.get(_item(n), 560)
    made = set()
    _arrive(th, asked, made)
    around = [_item(n) for n in range(10, 30)]  # the nearest first
    total = 0
    for _ in range(50):  # the window: look ahead after every picture that came, until nothing comes
        th.look_ahead(around, 560)
        got = _arrive(th, asked, set())
        total += got
        if not got:
            break
    assert not got and total <= 6  # came to rest, without making pictures over and over
    kept = {k for k, _s in th.cache[True]} - screen
    assert kept and kept <= {f"A{n}" for n in range(10, 16)}  # the nearest are the ones kept ready
    assert all(th.get(_item(n), 560) is not None for n in range(10)) and not asked
    assert th.used[True] <= 15 * one
    # the screen moves on by five photos: those are asked for, the five that left make room
    screen.clear()
    screen.update(f"A{n}" for n in range(5, 15))
    for n in range(5, 15):
        th.get(_item(n), 560)
    _arrive(th, asked, set())
    for _ in range(50):
        th.look_ahead([_item(n) for n in range(15, 35)], 560)
        if not _arrive(th, asked, set()):
            break
    assert all(th.get(_item(n), 560) is not None for n in range(5, 15)) and not asked
    assert th.used[True] <= 15 * one
    assert not any(k == f"A{n}" for k, _s in th.cache[True] for n in range(0, 5))  # what left is freed first


def test_both_pools_together_stay_within_the_total(qapp):
    th, asked = _thumbs(set())
    th.BUDGET = {False: 20 * MB, True: 10 * MB}
    th.CAP, th.TOTAL = 30 * MB, 32 * MB
    from PySide6.QtGui import QImage

    for n in range(40):  # small ones, scrolled past: the small pool is full
        th.put(f"S{n}", 320, QImage(427, 320, QImage.Format_RGB32))
    assert th.used[False] <= 20 * MB + 427 * 320 * 4
    screen = {f"B{n}" for n in range(12)}  # then a screen of big ones: more than the big pool's own budget
    th.on_screen = lambda: screen
    for n in range(12):
        th.get(_item(n).__class__(asset_id=f"B{n}", key=f"B{n}", path=None), 560)
    _arrive(th, asked, set())
    assert len(th.cache[True]) == 12  # all on screen
    assert th.used[True] + th.used[False] <= 32 * MB  # the small pool gave way


# --- the window --------------------------------------------------------------------------------------
N = 240


@pytest.fixture
def many(tmp_path, qapp):
    """A library of N photos big enough for the largest tiles (PNG: read by Qt, the same on every PC)."""
    from PySide6.QtCore import QBuffer
    from PySide6.QtGui import QColor, QImage

    img = QImage(800, 600, QImage.Format_RGB32)
    img.fill(QColor(90, 140, 200))
    buf = QBuffer()
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    data = bytes(buf.data())
    root = tmp_path / "Photos"
    st = State(tmp_path / "s.db")
    t0 = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    infos = {}
    for i in range(N):
        when = t0 - timedelta(hours=i * 7)
        rel = f"{when:%Y/%m}/IMG_{1000 + i}.PNG"
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
        st.add_file(FileRow(f"A{i}", "original", rel, len(data), False, when.isoformat()), when.isoformat())
        infos[f"A{i}"] = AssetInfo(favorite=False, added=when, width=800, height=600, duration=None,
                                   screenshot=False)
    st.set_infos(infos)
    return st, root


class _Watch:
    """Counts what the window asks for, what it drops, and what it paints."""

    def __init__(self, w):
        from PySide6.QtCore import QEvent, QObject

        self.w = w
        self.requests = self.dropped = self.dropped_on_screen = self.paints = 0
        self.regions = []
        ask, put = w.loader.request, w.thumbs.put

        def request(*a, **k):
            self.requests += 1
            return ask(*a, **k)

        def arrived(key, size, img):
            before = {k for pool in w.thumbs.cache.values() for k in pool}
            put(key, size, img)
            gone = before - {k for pool in w.thumbs.cache.values() for k in pool}
            self.dropped += len(gone)
            self.dropped_on_screen += len({k for k, _s in gone} & self.on_screen())

        w.loader.request, w.thumbs.put = request, arrived
        watch = self

        class Paints(QObject):
            def eventFilter(self, _o, e):
                if e.type() == QEvent.Paint:
                    watch.paints += 1
                    watch.regions.append(e.region().boundingRect())
                return False

        self._filter = Paints(w)
        w.grid.viewport().installEventFilter(self._filter)

    def on_screen(self) -> set[str]:
        w = self.w
        vp = w.grid.viewport().rect()
        return {it.key for row, it in enumerate(w.model.items) if w.grid.visualRect(w.model.index(row)).intersects(vp)}

    def all_there(self) -> bool:
        w = self.w
        b = w.thumbs.bucket((w.tile - 2) * w.devicePixelRatioF())
        pool = w.thumbs.cache[b > 400]
        return all((key, b) in pool for key in self.on_screen())

    def reset(self) -> None:
        self.requests = self.dropped = self.dropped_on_screen = self.paints = 0
        self.regions = []

    def rest(self, qapp, timeout=20.0) -> bool:
        """Until the window has asked for nothing and painted nothing for a second and a half (the
        scroll bar over the photos fades out 1.1 s after the last scroll)."""
        end = time.time() + timeout
        while time.time() < end:
            self.reset()
            pump(qapp, lambda: False, 1.5)
            if not self.requests and not self.paints:
                return True
        return False


def _open(qapp, many, monkeypatch, budget, cap):
    from skyfold.ui import photos_window as pw, theme

    st, root = many
    monkeypatch.setattr(pw.PhotosWindow, "zoom_level", 0)  # the largest tiles: three in a row
    monkeypatch.setattr(pw.Thumbs, "BUDGET", {False: budget, True: budget})
    monkeypatch.setattr(pw.Thumbs, "CAP", cap, raising=False)
    w = pw.PhotosWindow(theme.palette("dark"), lambda: (st, root), lambda: SimpleNamespace(delete_pc_to_cloud=True))
    w.resize(1500, 1000)
    w.show()
    assert pump(qapp, lambda: len(w.model.items) == N, 20)
    return w, _Watch(w)


def test_a_grid_left_alone_asks_for_nothing_and_drops_nothing_on_screen(qapp, many, monkeypatch):
    """The budget holds what is on screen but not the screens kept ready around it too: as a 4K
    screen at 200 % with the 100 MB of 1.2."""
    w, watch = _open(qapp, many, monkeypatch, budget=20 * MB, cap=24 * MB)
    try:
        assert len(watch.on_screen()) >= 9
        assert pump(qapp, watch.all_there, 20)
        assert watch.rest(qapp), "the window never came to rest: it keeps asking for pictures"
        watch.reset()
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest

        QTest.mouseMove(w.zoom_pill, QPoint(w.zoom_pill.width() // 2, w.zoom_pill.height() // 2))  # resting there
        pump(qapp, lambda: False, 1.5)
        assert (watch.requests, watch.dropped) == (0, 0)
        # painted: the bit under the button the mouse is on and the strip under the scroll bar fading
        # out, never the photos
        vp = w.grid.viewport()
        assert all(r.width() * r.height() < vp.width() * vp.height() / 20 for r in watch.regions), watch.regions
        assert watch.all_there()
    finally:
        w.close()


def test_even_a_budget_far_too_small_keeps_the_screen_whole(qapp, many, monkeypatch):
    w, watch = _open(qapp, many, monkeypatch, budget=3 * MB, cap=3 * MB)  # two or three pictures
    try:
        assert pump(qapp, watch.all_there, 20)
        assert watch.rest(qapp)
        assert watch.all_there() and watch.dropped_on_screen == 0
        on_screen = len(watch.on_screen())
        kept = sum(len(pool) for pool in w.thumbs.cache.values())
        assert on_screen <= kept <= on_screen + 1  # nothing kept ready: there is no room for it
    finally:
        w.close()


def test_scrolling_loads_the_new_photos_and_frees_the_old_ones(qapp, many, monkeypatch):
    w, watch = _open(qapp, many, monkeypatch, budget=20 * MB, cap=24 * MB)
    try:
        assert pump(qapp, watch.all_there, 20) and watch.rest(qapp)
        first = watch.on_screen()
        sb = w.grid.verticalScrollBar()
        total_dropped_on_screen = 0
        for _ in range(8):  # four screens up, half a screen at a time
            watch.reset()
            sb.setValue(sb.value() - w.grid.viewport().height() // 2)
            assert pump(qapp, watch.all_there, 20)  # the photos now on screen all get their picture
            total_dropped_on_screen += watch.dropped_on_screen
        assert watch.rest(qapp)
        assert total_dropped_on_screen == 0
        now = watch.on_screen()
        assert not now & first and watch.all_there()
        cached = {k for pool in w.thumbs.cache.values() for k, _s in pool}
        assert not cached & first  # the photos four screens away are gone
        assert sum(w.thumbs.used.values()) <= 24 * MB + 2 * MB  # (at most one picture over)
        watch.reset()
        pump(qapp, lambda: False, 1.0)
        assert (watch.requests, watch.dropped, watch.paints) == (0, 0, 0)
    finally:
        w.close()


def test_a_picture_that_arrives_repaints_its_own_tile_only(qapp, many, monkeypatch):
    from PySide6.QtGui import QColor, QImage

    w, watch = _open(qapp, many, monkeypatch, budget=200 * MB, cap=320 * MB)
    try:
        assert pump(qapp, watch.all_there, 20) and watch.rest(qapp)
        vp = w.grid.viewport()
        row = next(r for r, it in enumerate(w.model.items)
                   if vp.rect().contains(w.grid.visualRect(w.model.index(r))))
        b = w.thumbs.bucket((w.tile - 2) * w.devicePixelRatioF())
        img = QImage(b * 4 // 3, b, QImage.Format_RGB32)
        img.fill(QColor(200, 60, 60))
        watch.reset()
        w._loaded(w.model.items[row].key, b, False, img)  # (as the loader delivers it)
        assert pump(qapp, lambda: watch.paints > 0)
        pump(qapp, lambda: False, 0.2)
        tile = w.grid.visualRect(w.model.index(row))
        assert watch.regions and all(tile.adjusted(-1, -1, 4, 2).contains(r) for r in watch.regions), (
            watch.regions, tile)
        shot = vp.grab(tile).toImage()
        got = shot.pixelColor(shot.width() // 2, shot.height() // 2)
        assert got.red() > 150 and got.blue() < 100  # and it shows the new picture
    finally:
        w.close()
