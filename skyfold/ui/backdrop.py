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


DWMWA_SYSTEMBACKDROP_TYPE = 38
BACKDROPS = {"mica": 2, "acrylic": 3, "mica_alt": 4}


def glass_supported() -> bool:
    """Windows 11 22H2 or later, with Windows' own "Transparency effects" switched on (Settings →
    Personalization → Colors): only then do Skyfold's windows show the desktop through them."""
    if sys.platform != "win32":
        return False
    try:
        from PySide6.QtGui import QGuiApplication

        if QGuiApplication.platformName() != "windows":  # (offscreen renders: screenshots, tests)
            return False
        if sys.getwindowsversion().build < 22621:
            return False
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "EnableTransparency")[0] == 1
    except (OSError, AttributeError, ValueError):
        return False


def apply_backdrop(widget: QWidget, kind: str = "acrylic", dark: bool = True) -> bool:
    """Ask Windows for its acrylic (a popup: the tray panel) or Mica (a window) material behind the
    widget, which must have Qt.WA_TranslucentBackground. Returns whether Windows took it."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        class MARGINS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]

        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        mode = ctypes.c_int(1 if dark else 0)
        dwm.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(mode), ctypes.sizeof(mode))
        m = MARGINS(-1, -1, -1, -1)
        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))
        value = ctypes.c_int(BACKDROPS.get(kind, 3))
        return dwm.DwmSetWindowAttribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, ctypes.byref(value),
                                         ctypes.sizeof(value)) == 0
    except Exception:  # noqa: BLE001 - purely cosmetic
        return False


def glass_colours(c: dict[str, str], dark: bool) -> dict[str, str]:
    """The layers over the window material: a tint over the whole window, then cards, the footer,
    hover and control fills, all partly see-through (rgba)."""
    if dark:
        return {"tint": "rgba(30, 33, 39, 150)", "card": "rgba(255, 255, 255, 13)", "stroke": "rgba(255, 255, 255, 20)",
                "shade": "rgba(0, 0, 0, 70)",
                "stroke2": "rgba(255, 255, 255, 30)", "foot": "rgba(0, 0, 0, 34)", "hover": "rgba(255, 255, 255, 20)",
                "control": "rgba(255, 255, 255, 18)"}
    return {"tint": "rgba(243, 244, 247, 150)", "card": "rgba(255, 255, 255, 170)", "stroke": "rgba(0, 0, 0, 15)",
            "shade": "rgba(0, 0, 0, 22)",
            "stroke2": "rgba(0, 0, 0, 26)", "foot": "rgba(255, 255, 255, 70)", "hover": "rgba(0, 0, 0, 10)",
            "control": "rgba(255, 255, 255, 200)"}


def blur_behind(widget: QWidget, tint: QColor | None = None) -> bool:
    """Windows' own blur of what is behind a frameless window (the tray panel and the notification
    cards): the accent policy behind Windows' acrylic, which, unlike the system backdrop, also
    works on windows without a frame. tint: the colour laid over the blur (its alpha is the amount)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class ACCENT(ctypes.Structure):
            _fields_ = [("state", ctypes.c_int), ("flags", ctypes.c_int), ("color", ctypes.c_uint),
                        ("anim", ctypes.c_int)]

        class DATA(ctypes.Structure):
            _fields_ = [("attr", ctypes.c_int), ("data", ctypes.c_void_p), ("size", ctypes.c_size_t)]

        c = tint or QColor(32, 34, 40, 120)
        abgr = (c.alpha() << 24) | (c.blue() << 16) | (c.green() << 8) | c.red()
        accent = ACCENT(4, 2, abgr, 0)  # ACCENT_ENABLE_ACRYLICBLURBEHIND, draw the tint colour
        data = DATA(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent))  # WCA_ACCENT_POLICY
        fn = ctypes.windll.user32.SetWindowCompositionAttribute
        fn.argtypes = [wintypes.HWND, ctypes.POINTER(DATA)]
        return bool(fn(int(widget.winId()), ctypes.byref(data)))
    except Exception:  # noqa: BLE001 - purely cosmetic
        return False
