"""The Photos window drawn as in the 1.1 design: see-through over the window's material in every theme
(OLED too, the viewer too), the photos under a title band, a film strip that is never cut off.
Made-up photos only (the fixtures of test_photos_window)."""

from __future__ import annotations

import pytest

from tests.test_photos_window import made_up, pump, qapp, window  # noqa: F401  (fixtures)


@pytest.fixture
def glass(monkeypatch):
    """The material as on Windows 11 with transparency on (offscreen there is none: Qt's side only)."""
    from skyfold.ui import backdrop

    monkeypatch.setattr(backdrop, "glass_supported", lambda: True)
    monkeypatch.setattr(backdrop, "apply_backdrop", lambda *_a, **_k: True)


@pytest.mark.parametrize("mode", ["dark", "light", "oled"])
def test_the_viewer_stays_see_through_in_every_theme(qapp, made_up, glass, mode):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage

    w = window(qapp, made_up, mode)
    try:
        w.resize(1100, 700)
        w.open_viewer(3)
        qapp.processEvents()
        assert pump(qapp, lambda: w.picture.pm is not None)
        img = w.grab().toImage().convertToFormat(QImage.Format_RGBA8888)
        # beside the photo (its 60 px margin) the window's material shows through, as in the grid
        beside = w.picture.mapTo(w, QPoint(20, w.picture.height() // 2))
        assert 0 < img.pixelColor(beside).alpha() < 255
        bar = w.vtop.mapTo(w, QPoint(w.vtop.width() // 3, w.vtop.height() // 2))
        assert 0 < img.pixelColor(bar).alpha() < 255
        # the photo itself is solid
        centre = w.picture.mapTo(w, w.picture._target().center().toPoint())
        assert img.pixelColor(centre).alpha() == 255
        w._set_bare(True)  # only the photo: still on the material, never a black sheet
        qapp.processEvents()
        img = w.grab().toImage().convertToFormat(QImage.Format_RGBA8888)
        assert 0 < img.pixelColor(w.picture.mapTo(w, QPoint(4, 4))).alpha() < 255
    finally:
        w.close()


def test_the_film_strip_is_centred_and_never_cut_off(qapp, made_up):
    w = window(qapp, made_up)
    try:
        for width, row in ((1200, 15), (700, 15), (620, 0), (1200, 29)):
            w.resize(width, 700)
            w.open_viewer(row)
            qapp.processEvents()
            w.strip.repaint()
            hits = w.strip._hits
            assert hits, width
            lo, hi = w.strip.span()
            assert lo <= row <= hi and hi - lo <= 2 * w.strip.AROUND
            assert all(r.left() >= 0 and r.right() <= w.strip.width() for r, _k in hits)  # none cut at the edge
            cur = [r for r, k in hits if k == row][0]
            assert cur.width() == 40 and cur.height() == 30
            assert all(r.width() == 30 for r, k in hits if k != row)
            left, right = hits[0][0].left(), w.strip.width() - hits[-1][0].right()
            assert abs(left - right) <= 1  # the group is centred
            w.close_viewer()
    finally:
        w.close()


def test_photos_start_under_the_title_band_and_reach_the_pane_edge(qapp, made_up):
    from PySide6.QtCore import Qt
    from skyfold.ui.photos_window import HEAD

    w = window(qapp, made_up)
    try:
        w.resize(1100, 700)
        qapp.processEvents()
        g = w.grid
        assert g.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff  # the bar floats over the photos
        assert w.inner.geometry().top() == 0 and w.header.height() == HEAD  # they scroll on under the band
        g.scrollToTop()
        assert g.visualRect(w.model.index(0)).top() == HEAD
        g.scrollToBottom()
        assert g.verticalScrollBar().value() == g.verticalScrollBar().maximum()
        first = w.model.index(0)
        g.setCurrentIndex(first)
        g.scrollTo(first)  # (the arrow keys): never left under the band
        assert g.visualRect(first).top() >= HEAD
        w.grid_bar.setValue(w.grid_bar.minimum())  # dragging the floating bar moves the photos
        assert g.verticalScrollBar().value() == g.verticalScrollBar().minimum()
        cols = g.viewport().width() // w.tile
        assert cols == 5  # the design's size: five columns, all of the width
        assert g.viewport().width() - cols * w.tile < cols + 5  # (the last column is drawn to the edge)
    finally:
        w.close()


def test_viewer_buttons_have_the_designs_sizes(qapp, made_up):
    w = window(qapp, made_up)
    try:
        w.open_viewer(0)
        qapp.processEvents()
        for b in (w.v_fav, w.v_info, w.v_del, w.v_more):
            assert (b.width(), b.height()) == (34, 34)
        assert w.back.height() == 30 and w.back.text()
        assert "Cascadia Mono" in w.styleSheet() and w.v_time.objectName() == "PhotosDateSub"  # (mono, quiet)
        for b in (w.prev, w.next):
            assert (b.width(), b.height()) == (40, 40)
    finally:
        w.close()


@pytest.mark.parametrize("mode", ["dark", "oled"])
def test_the_floating_switch_is_see_through_and_the_chosen_view_not_blue(qapp, made_up, glass, mode):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage

    w = window(qapp, made_up, mode)
    try:
        w.resize(1100, 700)
        qapp.processEvents()
        u = w._ui
        assert u["mica"].alpha() < 255 and u["layer2"].alpha() < 255 and u["hover"].alpha() < 255
        chosen = w.view_buttons["all"]
        assert chosen.isChecked()
        img = w.grab().toImage().convertToFormat(QImage.Format_RGBA8888)
        px = img.pixelColor(chosen.mapTo(w, QPoint(4, chosen.height() // 2)))
        assert px.blue() - px.red() < 30  # neutral, not the accent
        assert u["chosen"] == QColor(30, 33, 39)
    finally:
        w.close()


def test_the_panes_corners_stay_round_over_the_photos(qapp, made_up, glass):
    from PySide6.QtGui import QImage

    w = window(qapp, made_up)
    try:
        w.resize(1100, 700)
        w.grid.scrollToBottom()
        qapp.processEvents()
        img = w.grab().toImage().convertToFormat(QImage.Format_RGBA8888)
        pane = w.pane
        corner = pane.mapTo(w, pane.rect().bottomLeft())
        tint = img.pixelColor(corner)  # outside the rounded corner: the window's see-through tint, no photo
        assert tint.alpha() < 255
    finally:
        w.close()
