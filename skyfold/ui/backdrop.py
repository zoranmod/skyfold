"""Windows' own frame around Skyfold's windows, in Skyfold's colours.

On Windows 11 the title bar takes the window's own background colour and a hairline border, and a
dark theme gets a dark title bar (Windows 10 2004+ honours the dark title bar too). Without this, a
dark Skyfold window had a white Windows title bar on top: the clearest "not quite finished" sign.
Everything here is cosmetic: any failure leaves Windows' default frame.
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


def _colorref(c: QColor) -> int:
    return c.red() | (c.green() << 8) | (c.blue() << 16)


def match_frame(widget: QWidget, background: str | None = None, text: str | None = None,
                border: str | None = None) -> bool:
    """Colour the window's title bar and border like its content. background/text/border are
    '#rrggbb' (default: the widget's palette). Returns whether Windows took it."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        pal = widget.palette()
        bg = QColor(background) if background else pal.window().color()
        fg = QColor(text) if text else pal.windowText().color()
        dark = ctypes.c_int(1 if bg.lightness() < 128 else 0)
        ok = dwm.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(dark),
                                       ctypes.sizeof(dark)) == 0
        for attr, colour in ((DWMWA_CAPTION_COLOR, bg), (DWMWA_TEXT_COLOR, fg),
                             (DWMWA_BORDER_COLOR, QColor(border) if border else bg.darker(118) if dark.value
                              else bg.darker(112))):
            value = ctypes.c_uint(_colorref(colour))
            # Windows 10 doesn't know the caption/text/border colours: it says so, nothing changes
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
        return ok
    except Exception:  # noqa: BLE001 - purely cosmetic
        return False
