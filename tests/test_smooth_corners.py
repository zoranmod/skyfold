"""Rounded edges are smooth (antialiased) wherever something is cut to a rounded shape: the panes of
Photos and Notes over what reaches their corners, thumbnails, the marks on the photo tiles. Drawn
offscreen at 100, 125, 150 and 200 %; made-up photos and notes only."""

from __future__ import annotations

import math

import pytest

from tests.test_notes_window import library  # noqa: F401  (fixture)
from tests.test_notes_window import window as notes_window
from tests.test_photos_window import made_up, pump, qapp, window  # noqa: F401  (fixtures)

SCALES = [1.0, 1.25, 1.5, 2.0]


@pytest.fixture
def glass(monkeypatch):
    """The material as on Windows 11 with transparency on (offscreen there is none: Qt's side only)."""
    from skyfold.ui import backdrop

    monkeypatch.setattr(backdrop, "glass_supported", lambda: True)
    monkeypatch.setattr(backdrop, "apply_backdrop", lambda *_a, **_k: True)


def drawn(widget, scale: float):
    """The widget as drawn on a screen at that scaling (its own pixels, with their alpha)."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QImage, QPixmap, QRegion
    from PySide6.QtWidgets import QWidget

    pm = QPixmap(math.ceil(widget.width() * scale), math.ceil(widget.height() * scale))
    pm.setDevicePixelRatio(scale)
    pm.fill(Qt.transparent)
    widget.render(pm, QPoint(), QRegion(), QWidget.DrawChildren)
    return pm.toImage().convertToFormat(QImage.Format_RGBA8888)


def near(a, b, by: int = 3) -> bool:
    return all(abs(x - y) <= by for x, y in zip(a.getRgb(), b.getRgb()))


def check_corner(img, scale: float, pane, win, radius: float, right: bool) -> None:
    """The pane's bottom corner with something solid under it: nothing of it outside the pane, the
    hairline unbroken round the corner, a smooth edge between the two."""
    from PySide6.QtCore import QPoint

    at = pane.mapTo(win, QPoint(0, 0))
    w, h = pane.width(), pane.height()

    def px(x: float, y: float):  # (x from the corner's own side)
        x = w - x if right else x
        return img.pixelColor(min(img.width() - 1, int((at.x() + x) * scale)),
                              min(img.height() - 1, int((at.y() + y) * scale)))

    outside = px(0.3, h - 0.3)
    assert outside.alpha() < 255  # the window's see-through tint, nothing of the content
    side = 0.5 if not right else 0.5 + 1e-3  # (the middle of the hairline along the straight side)
    hairline = px(side, h - radius - 22)
    assert hairline.alpha() < 255
    # the hairline is the same just above the corner as far from it (it was drawn twice there)
    assert near(px(side, h - radius - 3), hairline), (px(side, h - radius - 3).getRgb(), hairline.getRgb())
    # ...and runs on round the corner: on the curve there is no content, only the pane's own layers
    cx, cy = 0.5 + radius, h - 1.5 - radius
    reach = (radius + 0.2) * math.sqrt(0.5)
    on_curve = px(cx - reach, cy + reach)
    assert on_curve.alpha() <= max(outside.alpha(), hairline.alpha()) + 12, (on_curve.getRgb(), hairline.getRgb())
    # between the content and the hairline: a smooth edge (pixels partly of each)
    solid = px(radius + 6, h - radius - 6)
    assert solid.alpha() == 255
    lo = max(outside.alpha(), hairline.alpha()) + 8
    part = set()
    for yy in range(int((at.y() + h - radius - 2) * scale), int((at.y() + h) * scale)):
        for xx in range(int(radius * scale) + 2):
            x = int((at.x() + (w if right else 0)) * scale) + (-1 - xx if right else xx)
            a = img.pixelColor(x, yy).alpha()
            if lo < a < 248:
                part.add((x, yy))
    assert len(part) >= 3 * scale, len(part)


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("mode", ["dark", "light"])
def test_photos_pane_corner_is_smooth_over_the_photos(qapp, made_up, glass, scale, mode):
    w = window(qapp, made_up, mode)
    try:
        w.resize(1100, 700)
        w.grid.scrollToBottom()
        qapp.processEvents()
        pump(qapp, lambda: False, 0.3)  # (the thumbnails arrive)
        check_corner(drawn(w, scale), scale, w.pane, w, 8, right=False)
    finally:
        w.close()


@pytest.mark.parametrize("scale", SCALES)
def test_notes_pane_corners_are_smooth_over_what_reaches_them(qapp, library, glass, scale):
    from PySide6.QtWidgets import QWidget

    w = notes_window(qapp, library)
    try:
        pane = w.split.widget(1)
        cover = QWidget(pane)  # stands for a picture or a scan reaching the pane's edges
        cover.setStyleSheet("background:#d04060;")
        cover.setGeometry(pane.rect().adjusted(1, 1, -1, -2))
        cover.show()
        corners = getattr(w, "corners", None)
        if corners is not None:
            corners.place()  # (the corners stay over everything in the pane)
        qapp.processEvents()
        img = drawn(w, scale)
        check_corner(img, scale, pane, w, 8, right=False)
        check_corner(img, scale, pane, w, 12, right=True)  # (the bottom right corner follows the window's)
    finally:
        w.close()


def test_the_corners_keep_the_see_through_pane_as_it_is(qapp, library, glass):
    """Where nothing reaches a corner the window looks exactly as without the corners."""
    w = notes_window(qapp, library)
    try:
        qapp.processEvents()
        with_corners = drawn(w, 2.0)
        for part in w.corners.parts:
            part.hide()
        qapp.processEvents()
        without = drawn(w, 2.0)
        pane = w.pane
        from PySide6.QtCore import QPoint

        at = pane.mapTo(w, QPoint(0, 0))
        worst = 0
        for x in range(0, 32):
            for y in range(0, 32):
                a = with_corners.pixelColor(at.x() * 2 + x, at.y() * 2 + y)
                b = without.pixelColor(at.x() * 2 + x, at.y() * 2 + y)
                worst = max(worst, *(abs(m - n) for m, n in zip(a.getRgb(), b.getRgb())))
        assert worst <= 2, worst
    finally:
        w.close()


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
def test_a_rounded_thumbnail_has_smooth_corners(qapp, scale):
    from PySide6.QtGui import QColor, QImage

    from skyfold.ui.panel_parts import rounded

    src = QImage(120, 90, QImage.Format_RGB32)
    src.fill(QColor("#40c040"))
    img = rounded(src, 32, 6, scale).toImage().convertToFormat(QImage.Format_RGBA8888)
    n = int(6 * scale) + 1
    alphas = {img.pixelColor(x, y).alpha() for x in range(n) for y in range(n)}
    assert 0 in alphas and 255 in alphas
    assert len([a for a in alphas if 20 < a < 235]) >= 3  # partly covered pixels along the curve


def test_the_heart_on_a_favourite_photo_is_smooth(qapp, made_up):
    from PySide6.QtGui import QColor

    from skyfold.ui.photos_window import GAP

    w = window(qapp, made_up)
    try:
        w.resize(1100, 700)
        w.grid.scrollToBottom()
        qapp.processEvents()
        pump(qapp, lambda: False, 0.3)
        row = next(i for i, it in enumerate(w.model.items) if it.favorite)
        w.grid.scrollTo(w.model.index(row))
        qapp.processEvents()
        pump(qapp, lambda: False, 0.3)
        vp = w.grid.viewport()
        r = w.grid.visualRect(w.model.index(row)).adjusted(0, 0, -GAP, -GAP)
        assert r.width() >= 90
        img = drawn(vp, 2.0)
        photo = img.pixelColor((r.left() + 30) * 2, (r.bottom() - 12) * 2)
        white = QColor("#ffffff")
        part = whites = 0
        for x in range((r.left() + 5) * 2, (r.left() + 20) * 2):
            for y in range((r.bottom() - 19) * 2, (r.bottom() - 5) * 2):
                c = img.pixelColor(x, y)
                if near(c, white, 12):
                    whites += 1
                elif not near(c, photo, 24):
                    part += 1
        assert whites > 40  # the heart is there
        assert part >= 12, part  # ...with a soft edge, not white or photo only
    finally:
        w.close()
