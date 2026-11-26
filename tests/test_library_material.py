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
            if palette is theme.OLED:
                assert corner.getRgb() == (0, 0, 0, 255)
            else:
                assert 1 < corner.alpha() < 255
                assert (corner.lightness() > 128) == (palette is theme.LIGHT)
    finally:
        window.close()
        app.processEvents()
