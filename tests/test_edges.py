"""Edges that were rough on the real screen: a line sticking out at the corners of the Photos pane
(seen in the viewer), and the keyboard focus ring of a button on a glass window. Drawn offscreen at
100 to 200 %; made-up photos only."""

from __future__ import annotations

import math

import pytest

from tests.test_photos_window import made_up, pump, qapp, window  # noqa: F401  (fixtures)
from tests.test_smooth_corners import drawn, glass  # noqa: F401  (fixture)

SCALES = [1.0, 1.25, 1.5, 1.75, 2.0]
# (at 125 %, 150 % and 175 % these put the pane's right and bottom edges between two screen pixels,
# as a maximized window does on the real screen; 1100 x 700 puts them on whole pixels)
SIZES = [(1100, 700), (1102, 701), (1101, 703)]


def apart(a, b) -> int:
    return max(abs(x - y) for x, y in zip(a.getRgb(), b.getRgb()))


def lines_out(img, scale: float, pane, win, clear: bool) -> list:
    """Along each straight edge of the pane, the pixels beside a corner (under the corner's overlay)
    against the same line further along (past the overlay): where they differ, a line sticks out."""
    from PySide6.QtCore import QPoint

    at = pane.mapTo(win, QPoint(0, 0))
    left, top = at.x() * scale, at.y() * scale
    right, bottom = (at.x() + pane.width()) * scale, (at.y() + pane.height()) * scale
    near, far = 10.25, 17.25  # from the corner: past the curve (radius 8) but under the overlay (12); past it
    found = []
    for name, vertical, edge, start, sign in (
            ("left, top corner", True, left, top, 1), ("left, bottom corner", True, left, bottom, -1),
            ("right, top corner", True, right, top, 1), ("right, bottom corner", True, right, bottom, -1),
            ("top, left corner", False, top, left, 1), ("top, right corner", False, top, right, -1),
            ("bottom, left corner", False, bottom, left, 1), ("bottom, right corner", False, bottom, right, -1)):
        a, b = int(start + sign * near * scale), int(start + sign * far * scale)
        # The window's last pixel outside the pane and the pane's first one (the one the edge falls
        # in, where it falls between two); where nothing reaches the edge, the next one inwards too.
        first = name.startswith(("left", "top"))
        outer = math.floor(edge) - 1 if first else math.ceil(edge)
        for step in range(3 if clear else 2):
            across = outer + step if first else outer - step
            one = img.pixelColor(across, a) if vertical else img.pixelColor(a, across)
            other = img.pixelColor(across, b) if vertical else img.pixelColor(b, across)
            if apart(one, other) > 6:
                found.append((name, step, one.getRgb(), other.getRgb()))
    return found


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("mode", ["dark", "light", "oled"])
def test_no_line_sticks_out_at_the_corners_of_the_photos_pane(qapp, made_up, glass, scale, mode):
    """The corner overlay put back only part of the window's veil on the last pixel of an edge that
    falls between two pixels: a lighter line as long as the corner, past the curve."""
    w = window(qapp, made_up, mode)
    try:
        for size in SIZES:
            w.resize(*size)
            w.close_viewer()
            w.grid.scrollToTop()  # (the title band at the top, nothing at the bottom corners yet)
            pump(qapp, lambda: False, 0.2)
            for page in ("grid", "viewer"):
                if page == "viewer":
                    w.open_viewer(len(w.model.items) - 1)
                    pump(qapp, lambda: False, 0.2)
                found = lines_out(drawn(w, scale), scale, w.pane, w, clear=page == "viewer")
                assert not found, (size, page, found)
    finally:
        w.close()


# The focus ring --------------------------------------------------------------------------------
def focused_message(qapp, monkeypatch, mode: str, buttons, default: str):
    from PySide6.QtCore import Qt

    from skyfold.ui import backdrop, dialogs, theme

    monkeypatch.setattr(backdrop, "glass_supported", lambda: True)
    monkeypatch.setattr(backdrop, "apply_backdrop", lambda *_a, **_k: True)
    c = theme.palette(mode)
    qapp.setStyleSheet(theme.stylesheet(c))
    box = dialogs.MessageDialog(None, "Skyfold", "It goes to the Recycle Bin.", heading="Delete this photo?",
                                kind="delete", palette=c, buttons=buttons)
    box.show()
    qapp.processEvents()
    box.activateWindow()
    button = box.button(default)
    button.setFocus(Qt.TabFocusReason)
    qapp.processEvents()
    assert box.glass and button.hasFocus()
    return box, button, c


def check_ring(img, scale: float, box, button, colour: str, radius: float = 6.0) -> None:
    """One solid, smooth line round the button: the ring's own colour along every side, the same all
    the way (no darker or lighter patches where a side meets a corner), partly covered pixels on the
    curve and nothing of the ring in the corner outside it."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor

    ring = QColor(colour)
    at = button.mapTo(box, QPoint(0, 0))
    w, h = button.width(), button.height()

    def px(x: float, y: float):
        return img.pixelColor(int((at.x() + x) * scale), int((at.y() + y) * scale))

    def is_ring(c) -> bool:
        return c.alpha() == 255 and apart(c, ring) <= 3

    for name, c in (("top", px(w / 2, 1)), ("bottom", px(w / 2, h - 1)), ("left", px(1, h / 2)),
                    ("right", px(w - 1, h / 2))):
        assert is_ring(c), (name, c.getRgb(), ring.getRgb())
    # along the top and the left side, between the corners: one colour
    for k in range(int((radius + 2) * scale), int((w - radius - 2) * scale)):
        c = img.pixelColor(int(at.x() * scale) + k, int((at.y() + 1) * scale))
        assert is_ring(c), ("top", k, c.getRgb())
    for k in range(int((radius + 2) * scale), int((h - radius - 2) * scale)):
        c = img.pixelColor(int((at.x() + 1) * scale), int(at.y() * scale) + k)
        assert is_ring(c), ("left", k, c.getRgb())
    # the corner: the ring runs through the middle of the curve, the tip outside it is free of it,
    # and between the two the edge is smooth
    reach = (radius - 1) * (1 - math.sqrt(0.5))  # (the middle of the 2 px line, on the diagonal)
    assert is_ring(px(1 + reach, 1 + reach)), px(1 + reach, 1 + reach).getRgb()
    outside = px(0.3, 0.3)
    assert apart(outside, ring) > 40, outside.getRgb()
    part = 0
    for yy in range(int(at.y() * scale), int((at.y() + radius) * scale)):
        for xx in range(int(at.x() * scale), int((at.x() + radius) * scale)):
            c = img.pixelColor(xx, yy)
            if not is_ring(c) and apart(c, outside) > 12 and apart(c, px(radius + 3, radius + 3)) > 12:
                part += 1
    assert part >= 3 * scale, part


@pytest.mark.parametrize("scale", SCALES)
@pytest.mark.parametrize("mode", ["dark", "light", "oled"])
def test_the_focus_ring_of_a_button_on_glass_is_one_smooth_solid_line(qapp, monkeypatch, scale, mode):
    """Cancel in "Delete this photo?": the glass window's see-through edge colour replaced the ring's
    own, and Qt's overlapping border pieces showed through it as steps round the corners."""
    old = qapp.styleSheet()
    box, cancel, c = focused_message(qapp, monkeypatch, mode, [("cancel", "Cancel", "cancel"),
                                                                ("delete", "Delete", "danger")], "cancel")
    try:
        check_ring(drawn(box, scale), scale, box, cancel, c["focus_ring"])
    finally:
        box.close()
        box.deleteLater()
        qapp.setStyleSheet(old)


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_the_main_button_on_glass_keeps_its_focus_ring(qapp, monkeypatch, mode):
    """(Its see-through accent edges took the ring away altogether.)"""
    old = qapp.styleSheet()
    box, ok, c = focused_message(qapp, monkeypatch, mode, [("ok", "OK", "primary")], "ok")
    try:
        check_ring(drawn(box, 2.0), 2.0, box, ok, c["text"])
    finally:
        box.close()
        box.deleteLater()
        qapp.setStyleSheet(old)


def test_every_glass_sheet_ends_with_the_solid_focus_rules():
    """A window's own sheet wins over the app's: one that tints the buttons' edges says the focus again."""
    from pathlib import Path

    from skyfold.ui import theme

    rules = theme.focus_rules(theme.DARK)
    assert theme.DARK["focus_ring"] in rules and "rgba" not in rules
    source = Path(theme.__file__).parent
    for name in ("dialogs.py", "flyout.py", "library_frame.py"):
        text = (source / name).read_text(encoding="utf-8")
        assert "border-color: {g['stroke2']}" in text.replace("border-color:{", "border-color: {"), name
        assert "focus_rules(" in text, name


def test_a_pill_button_focus_ring_is_smooth_without_a_layer_under_it(qapp, made_up, glass):
    """A pill that is neither chosen nor under the pointer drew nothing smooth before its ring: the
    ring came out with hard steps."""
    from PySide6.QtCore import Qt

    from skyfold.ui.pills import _PillButton

    w = window(qapp, made_up, "dark")
    try:
        w.activateWindow()
        pill = next(b for b in w.findChildren(_PillButton)
                    if b.isVisible() and b.isEnabled() and not b.isChecked() and not b.danger)
        pill.setFocus(Qt.TabFocusReason)
        qapp.processEvents()
        assert pill.hasFocus() and not pill.underMouse()
        img = drawn(pill, 2.0)
        ring = w._ui["focus"]
        part = sum(1 for y in range(img.height()) for x in range(img.width())
                   if 20 < img.pixelColor(x, y).alpha() < 235 and apart(img.pixelColor(x, y).toRgb(), ring) < 400
                   and abs(img.pixelColor(x, y).hue() - ring.hue()) < 12)
        assert part >= 12, part
    finally:
        w.close()
