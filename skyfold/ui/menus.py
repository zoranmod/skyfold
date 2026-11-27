"""Skyfold's menus in the 1.1 design (the tray icon's right-click menu and the panel's "More" menu).

Rounded 8 px with a hairline stroke, 4 px inside, items about 32 px high with a 16 px line icon, hairline
separators and a light hover layer. ``Menu`` is an ordinary QMenu (its actions, keyboard and
accessibility are Qt's own); it re-tints its icons and colours when the theme changes (apply_palette,
called by dialogs.refresh_palette for a menu that is a window of its own).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import QMenu, QWidget

from .icons import glyph

GLYPH = "skyfold_glyph"  # the action's icon name, kept on it so a new theme can draw it again


def menu_style(c: dict[str, str], glass: bool = False) -> str:
    """glass: the menu is see-through (Windows blurs the desktop behind it and lays the theme's tint
    over it, as on the tray panel and the notifications), so it has no fill of its own; Windows also
    draws its rounded edge and shadow then. The fill is 2/255, not 0, so a click there still reaches it
    (and not 1: a style sheet reads an alpha of 1 as fully opaque)."""
    dark = QColor(c["bg"]).lightness() < 128
    oled = c.get("bg", "").lower() == "#000000"
    fill = "#0b0b0c" if oled else c["surface"]
    stroke = "rgba(255, 255, 255, 30)" if dark else "rgba(0, 0, 0, 26)"
    radius = 8
    if glass:  # (no radius of Qt's own either: it would cut the corners with a window mask, and
        # Windows draws no blur on a masked window; Windows rounds the corners itself then)
        fill, stroke, radius = "rgba(0, 0, 0, 2)", "transparent", 0
    hover = "rgba(255, 255, 255, 22)" if dark else "rgba(0, 0, 0, 12)"
    line = "rgba(255, 255, 255, 22)" if dark else "rgba(0, 0, 0, 18)"
    return (
        f"QMenu {{ background: {fill}; border: 1px solid {stroke}; border-radius: {radius}px; padding: 4px; }}"
        f"QMenu::item {{ min-height: 20px; padding: 6px 28px 6px 10px; border-radius: 4px; color: {c['text']};"
        " font-size: 10.5pt; }"
        "QMenu::icon { padding-left: 10px; }"
        f"QMenu::item:selected {{ background: {hover}; color: {c['text']}; }}"
        f"QMenu::item:disabled {{ color: {c.get('muted2', c['muted'])}; background: transparent; }}"
        f"QMenu::separator {{ height: 1px; background: {line}; margin: 4px 6px; }}")


class Menu(QMenu):
    """A QMenu in the design. add() puts an action with a line icon in the theme's colour."""

    def __init__(self, palette: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # no square native frame or shadow: the rounded edge shows (as make_popup_round does)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.c = palette
        self.glass = False  # decided each time it opens (Windows' transparency switch may change)
        self.apply_palette(palette)

    def add(self, glyph_name: str | None, text: str, slot=None) -> QAction:
        action = QAction(text, self)
        if slot is not None:
            action.triggered.connect(lambda _checked=False: slot())
        self.addAction(action)
        self.set_glyph(action, glyph_name)
        return action

    def set_glyph(self, action: QAction, glyph_name: str | None) -> None:
        action.setProperty(GLYPH, glyph_name or "")
        if glyph_name:
            action.setIcon(glyph(glyph_name, self._icon_colour(action), 16))

    def _icon_colour(self, action: QAction) -> str:
        return self.c["muted"] if action.isEnabled() else self.c.get("muted2", self.c["muted"])

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        self.setStyleSheet(menu_style(palette, self.glass))
        for action in self.actions():
            name = action.property(GLYPH)
            if name:
                action.setIcon(glyph(name, self._icon_colour(action), 16))

    def showEvent(self, e) -> None:  # noqa: N802
        from .backdrop import blur_behind, glass_colours, glass_supported, rgba
        from .flyout import round_corners

        glass = glass_supported()
        if glass != self.glass:
            self.glass = glass
            self.setStyleSheet(menu_style(self.c, glass))
        super().showEvent(e)
        if glass:  # the same see-through material as the tray panel and the notifications
            dark = QColor(self.c["bg"]).lightness() < 128
            self.glass = blur_behind(self, rgba(glass_colours(self.c, dark)["tint"]))
            round_corners(self)
            if not self.glass:  # (Windows refused the blur: the solid menu, as before)
                self.setStyleSheet(menu_style(self.c))
