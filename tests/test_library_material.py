"""The native compositor is absent offscreen. Exercise Qt's side of Mica with a synthetic window."""
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from skyfold.ui import backdrop, theme
from skyfold.ui.notes_window import NotesWindow
from skyfold.ui.photos_window import PhotosWindow


@pytest.mark.parametrize("kind", ["notes", "photos"])
def test_translucent_client_has_no_click_through_pixels(kind, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(backdrop, "glass_supported", lambda: True)
    monkeypatch.setattr(backdrop, "apply_backdrop", lambda *_: True)
    window = (NotesWindow(theme.DARK, lambda: None) if kind == "notes" else
              PhotosWindow(theme.DARK, lambda: None, lambda: None))
    try:
        window.resize(900, 550)
        window.show()
        for palette in (theme.LIGHT, theme.DARK, theme.OLED):
            window.apply_palette(palette)
            app.processEvents()
            image = window.grab().toImage().convertToFormat(QImage.Format_RGBA8888)
            alpha = bytes(image.constBits())[3::4]
            assert min(alpha) > 0
            corner = image.pixelColor(0, 0)
            # (1.1: OLED too is see-through over the material, at the user's choice)
            assert 1 < corner.alpha() < 255
            assert (corner.lightness() > 128) == (palette is theme.LIGHT)
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("kind", ["notes", "photos"])
def test_maximized_the_content_keeps_a_gap_under_the_title_bar(kind):
    app = QApplication.instance() or QApplication([])
    window = (NotesWindow(theme.DARK, lambda: None) if kind == "notes" else
              PhotosWindow(theme.DARK, lambda: None, lambda: None))
    try:
        window.show()
        app.processEvents()
        normal = window.layout().contentsMargins()
        window.showMaximized()
        app.processEvents()
        assert window.layout().contentsMargins().top() >= 10  # (not right up under Windows' caption buttons)
        window.showNormal()
        app.processEvents()
        assert window.layout().contentsMargins() == normal
    finally:
        window.close()
        app.processEvents()


def test_the_material_never_extends_the_frame_from_the_top(monkeypatch):
    """With the whole window made frame (-1 all round), Windows drew a maximized window's caption
    buttons as tall as a normal window's: lower than the thinner title bar, hanging over the content
    (measured at 200 %: the close sign 6 px lower, buttons 57 px in a 45 px title bar)."""
    import ctypes
    from types import SimpleNamespace

    from PySide6.QtWidgets import QWidget

    QApplication.instance() or QApplication([])
    asked = []

    class Dwm:
        def DwmSetWindowAttribute(self, *_a):
            return 0

        def DwmExtendFrameIntoClientArea(self, _hwnd, margins):
            m = margins._obj
            asked.append((m.l, m.r, m.t, m.b))
            return 0

    monkeypatch.setattr(backdrop, "native_window", lambda: True)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(dwmapi=Dwm()), raising=False)
    widget = QWidget()
    assert backdrop.apply_backdrop(widget, "acrylic", True)
    (left, right, top, bottom), = asked
    assert top == 0  # nothing from the top: the caption buttons stay where Windows puts them
    assert (left, right) == (0, 0) and bottom >= 16384  # from the bottom, over the whole window
    assert backdrop.frame_margins() == (left, right, top, bottom)
