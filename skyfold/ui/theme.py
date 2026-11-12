"""Light/dark palettes that follow the Windows theme.

Design tokens (1.0.6 redesign): flat, calm surfaces. A window is one plain surface; related rows sit
in a slightly tinted, rounded group without a border; colour means state (green ok, amber waiting,
red error) and there is one accent. Type 10/11/12/13/17 px, spacing 4/8/12/16, radii 8 (controls),
11 (groups), 15 (windows). The only category colours are the three of the iCloud storage bar.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPalette, QPen, QPixmap

ACCENT = "#075bcd"

LIGHT = {
    "bg": "#ffffff", "surface": "#ffffff", "surface2": "#f3f5f8", "hover": "#e7ebf0",
    "group": "#f3f5f8", "tile": "#e7ebf0",
    "border": "#dce2e9", "border_strong": "#7b8797",
    "text": "#20252b", "muted": "#59636f", "disabled": "#98a2ae",
    "accent": ACCENT, "accent_hover": "#064fb3", "accent_pressed": "#05449a",
    "btn": ACCENT, "btn_hover": "#064fb3", "btn_pressed": "#05449a", "focus": ACCENT, "focus_ring": ACCENT,
    "accent_fg": ACCENT,  # accent used as text or thin icon on a light background
    "accent_text": "#ffffff", "accent_soft": "#e7f0ff", "accent_soft_border": "#e7f0ff",
    "warn_bg": "#fff4dc", "warn_border": "#fff4dc", "warn_text": "#805300",
    "err_bg": "#fff0f0", "err_border": "#fff0f0", "err_text": "#b23035",
    "ok_bg": "#edf6ef", "ok_text": "#237344",
    "ok": "#237344", "warn": "#805300", "err": "#b23035", "offline": "#59636f",
    "track": "#dce2e9",
    "c_photos": "#ee4268", "c_drive": "#1685ee", "c_other": "#e9bc38",  # iCloud storage bar
}

DARK = {
    "bg": "#191e25", "surface": "#191e25", "surface2": "#222932", "hover": "#303944",
    "group": "#222932", "tile": "#303944",
    "border": "#36404d", "border_strong": "#78879a",
    "text": "#f0f3f7", "muted": "#b0bac7", "disabled": "#6b7786",
    "accent": "#99c2ff", "accent_hover": "#b3d1ff", "accent_pressed": "#80b3ff",
    "btn": "#99c2ff", "btn_hover": "#b3d1ff", "btn_pressed": "#80b3ff", "focus": "#99c2ff", "focus_ring": "#99c2ff",
    "accent_fg": "#99c2ff",
    "accent_text": "#10223b", "accent_soft": "#243b59", "accent_soft_border": "#243b59",
    "warn_bg": "#3b3020", "warn_border": "#3b3020", "warn_text": "#f1c475",
    "err_bg": "#43292e", "err_border": "#43292e", "err_text": "#ff9ba4",
    "ok_bg": "#20382a", "ok_text": "#85d7a1",
    "ok": "#85d7a1", "warn": "#f1c475", "err": "#ff9ba4", "offline": "#b0bac7",
    "track": "#36404d",
    "c_photos": "#f2577b", "c_drive": "#489df7", "c_other": "#e9c650",
}


def state_color(c: dict[str, str], state: str) -> str:
    """Text/icon colour of a sync state on this palette (readable on its surfaces)."""
    return {"idle": c["ok"], "syncing": c["accent_fg"], "paused": c["warn"], "offline": c["offline"],
            "error": c["err"], "auth": c["err"], "setup": c["warn"], "attention": c["warn"]}.get(state, c["ok"])


def is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


THEMES = {"system": "System", "light": "Light", "dark": "Dark"}  # English keys, translated where shown


def palette(mode: str = "system") -> dict[str, str]:
    if mode == "light":
        return LIGHT
    if mode == "dark":
        return DARK
    return DARK if is_dark() else LIGHT


def chevron_file(color: str) -> str:
    """Combo-box arrow drawn once into a temp PNG (QSS can only reference image files)."""
    path = Path(tempfile.gettempdir()) / f"skyfold_chevron_{color.lstrip('#')}.png"
    if not path.exists():
        pm = QPixmap(24, 24)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(color), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline([QPointF(6, 9), QPointF(12, 15), QPointF(18, 9)])
        p.end()
        pm.save(str(path))
    return path.as_posix()


def mark_file(color: str, kind: str) -> str:
    """Tick ("check") or dash ("part") for tree check boxes, drawn into a temp PNG like the chevron."""
    path = Path(tempfile.gettempdir()) / f"skyfold_{kind}_{color.lstrip('#')}.png"
    if not path.exists():
        pm = QPixmap(32, 32)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(color), 3.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if kind == "check":
            p.drawPolyline([QPointF(8, 16.5), QPointF(13.5, 22), QPointF(24, 10.5)])
        else:
            p.drawLine(QPointF(9, 16), QPointF(23, 16))
        p.end()
        pm.save(str(path))
    return path.as_posix()


def qpalette(c: dict[str, str]) -> QPalette:
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "surface"), (QPalette.AlternateBase, "surface2"),
                      (QPalette.Button, "surface2"), (QPalette.Text, "text"), (QPalette.WindowText, "text"),
                      (QPalette.ButtonText, "text"), (QPalette.Highlight, "accent"), (QPalette.Link, "accent_fg"),
                      (QPalette.ToolTipBase, "surface"), (QPalette.ToolTipText, "text"),
                      (QPalette.PlaceholderText, "muted")):
        pal.setColor(role, QColor(c[key]))
    pal.setColor(QPalette.HighlightedText, QColor(c["accent_text"]))
    return pal


def stylesheet(c: dict[str, str]) -> str:
    chev = chevron_file(c["muted"])
    tick, dash = mark_file(c["accent_text"], "check"), mark_file(c["accent_text"], "part")
    return f"""
QTreeView {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 4px; }}
QTreeView {{ outline: 0; }}
QTreeView::item {{ padding: 4px 2px; border: none; }}
QTreeView::item:hover, QTreeView::item:selected {{ background: {c['hover']}; color: {c['text']}; border-radius: 6px; }}
QTreeView::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1.5px solid {c['border_strong']}; background: transparent; }}
QTreeView::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({tick}); }}
QTreeView::indicator:indeterminate {{ background: {c['accent']}; border-color: {c['accent']}; image: url({dash}); }}
* {{ font-family: "Segoe UI Variable Text","Segoe UI Variable","Segoe UI",sans-serif; font-size: 9.75pt; color: {c['text']}; }}
QWidget#Root, QDialog {{ background: {c['bg']}; }}
QWidget#Footer {{ background: {c['group']}; border-top: 1px solid {c['border']}; }}
QWidget#NavPane {{ background: {c['surface']}; border-right: 1px solid {c['border']}; }}
QFrame#Card, QFrame#Tile {{ background: {c['group']}; border: none; border-radius: 11px; }}
QFrame#Row {{ background: transparent; border: none; border-radius: 9px; }}
QFrame#Row:hover {{ background: {c['tile']}; }}
QFrame#Row:focus {{ border: 2px solid {c['focus_ring']}; }}
QLabel#Brand {{ background: {c['accent_soft']}; border-radius: 11px; }}
QLabel#SvcTile {{ background: {c['tile']}; border-radius: 9px; }}
QFrame#BottomBar {{ background: {c['surface']}; border: none; border-top: 1px solid {c['border']};
    border-bottom-left-radius: 10px; border-bottom-right-radius: 10px; }}
QToolButton#Bar {{ background: {c['group']}; border: 1px solid transparent; border-radius: 9px; padding: 0;
    min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; color: {c['muted']}; }}
QToolButton#Bar:hover {{ background: {c['tile']}; border-color: {c['border_strong']}; }}
QToolButton#Bar:pressed {{ background: {c['border']}; }}
QToolButton#Bar:disabled {{ color: {c['disabled']}; }}
QToolButton#Bar:focus {{ border: 2px solid {c['focus_ring']}; }}
QToolButton#Bar::menu-indicator {{ image: none; width: 0; }}
QToolButton#Bar[primary="true"] {{ background: {c['accent_soft']}; color: {c['accent_fg']}; font-size: 9pt;
    font-weight: 600; padding: 0 12px; min-width: 60px; max-width: 16777215px; }}
QToolButton#Bar[primary="true"]:hover {{ border-color: {c['accent_fg']}; }}
QToolButton#Bar[primary="true"]:disabled {{ background: {c['group']}; color: {c['disabled']}; }}
QFrame#FileTile {{ background: transparent; border: none; }}
QLabel#RowTitle {{ font-size: 9.75pt; font-weight: 600; }}
QLabel#ActTitle {{ font-size: 8.5pt; }}
QLabel#StatusLine {{ font-size: 8.25pt; color: {c['muted']}; }}
QFrame#Sep {{ background: {c['border']}; border: none; max-height: 1px; min-height: 1px; }}
QFrame#Warn {{ background: {c['warn_bg']}; border: none; border-radius: 10px; }}
QFrame#Err  {{ background: {c['err_bg']};  border: none;  border-radius: 10px; }}
QFrame#Warn QLabel {{ color: {c['warn_text']}; }}
QFrame#Err QLabel {{ color: {c['err_text']}; }}
QFrame#Warn QPushButton {{ background: transparent; border: 1px solid {c['warn_text']}; color: {c['warn_text']}; }}
QFrame#Err QPushButton  {{ background: transparent; border: 1px solid {c['err_text']};  color: {c['err_text']}; }}
QFrame#Warn QPushButton:hover {{ background: {c['surface']}; }}
QFrame#Err QPushButton:hover  {{ background: {c['surface']}; }}
QLabel#Title {{ font-size: 12.75pt; font-weight: 600; }}
QLabel#H2 {{ font-size: 10.5pt; font-weight: 600; }}
QLabel#Big {{ font-size: 14pt; font-weight: 600; }}
QLabel#Section {{ font-size: 7.75pt; font-weight: 600; color: {c['muted']}; }}
QLabel#Small {{ font-size: 9pt; }}
QLabel#Legend {{ font-size: 7.75pt; color: {c['muted']}; }}
QFrame#ActRow {{ background: transparent; border: none; border-radius: 6px; }}
QFrame#ActRow[link="true"]:hover {{ background: {c['group']}; }}
QFrame#ActRow:focus {{ border: 2px solid {c['focus_ring']}; }}
QLabel#Muted, QLabel#Hint {{ font-size: 8.25pt; color: {c['muted']}; }}
QLabel#ErrorText {{ color: {c['err_text']}; }}
QLabel#WarnText {{ color: {c['warn_text']}; }}
QFrame#Option {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 11px; }}
QFrame#Option:hover {{ border-color: {c['border_strong']}; }}
QFrame#Option[selected="true"] {{ border: 1px solid {c['accent']}; background: {c['accent_soft']}; }}
QFrame#Option:focus {{ border: 2px solid {c['focus_ring']}; }}
QFrame#Device {{ background: transparent; border: none; border-radius: 8px; }}
QLabel#DotOff {{ border: 2px solid {c['border_strong']}; border-radius: 9px; background: transparent; }}
QLabel#DotOn  {{ border: 5px solid {c['accent']}; border-radius: 9px; background: #ffffff; }}

QPushButton {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 8px;
    padding: 0 12px; min-height: 32px; font-size: 9pt; font-weight: 600; outline: none; }}
QPushButton:hover {{ background: {c['group']}; }}
QPushButton:pressed {{ background: {c['border']}; }}
QPushButton:focus {{ border: 2px solid {c['focus_ring']}; }}
QPushButton:disabled {{ color: {c['disabled']}; background: {c['surface2']}; border-color: {c['border']}; }}
QPushButton#Primary {{ background: {c['btn']}; color: {c['accent_text']}; border: 1px solid {c['btn']}; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {c['btn_hover']}; border-color: {c['btn_hover']}; }}
QPushButton#Primary:pressed {{ background: {c['btn_pressed']}; border-color: {c['btn_pressed']}; }}
QPushButton#Primary:focus {{ border: 2px solid {c['text']}; }}
QPushButton#Primary:disabled {{ background: {c['track']}; border-color: {c['track']}; color: {c['disabled']}; }}
QPushButton#Danger {{ color: {c['err_text']}; }}
QPushButton#Danger:hover {{ border-color: {c['err_border']}; background: {c['err_bg']}; }}
QPushButton#Danger:disabled {{ color: {c['disabled']}; }}
QLabel:disabled {{ color: {c['disabled']}; }}
QPushButton#Link {{ background: transparent; border: none; color: {c['accent_fg']}; padding: 0 2px; text-align: left; }}
QPushButton#Link:hover {{ text-decoration: underline; }}
QPushButton#Link:disabled {{ color: {c['disabled']}; }}
QToolButton {{ background: transparent; border: none; border-radius: 8px; padding: 0; min-width: 32px; min-height: 32px; }}
QToolButton:hover {{ background: {c['hover']}; }}
QToolButton:pressed {{ background: {c['border']}; }}
QToolButton:focus {{ border: 1px solid {c['accent']}; }}
QLabel#Hint:disabled, QLabel#Muted:disabled {{ color: {c['disabled']}; }}

QLineEdit, QSpinBox, QComboBox {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 8px;
    padding: 0 10px; min-height: 32px; selection-background-color: {c['accent']}; selection-color: #ffffff; }}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: {c['text']}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QComboBox:on {{ border-color: {c['accent']}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {c['disabled']}; background: {c['surface2']}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
QSpinBox QLineEdit {{ border: none; padding: 0; min-height: 0; background: transparent; }}
QComboBox {{ padding-right: 30px; combobox-popup: 0; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 28px; border: none; }}
QComboBox::down-arrow {{ image: url("{chev}"); width: 12px; height: 12px; }}
QComboBoxPrivateContainer {{ background: transparent; border: none; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px;
    padding: 4px; outline: none; }}
QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 0 10px; border-radius: 6px; }}
QComboBox QAbstractItemView::item:hover {{ background: {c['hover']}; }}
QComboBox QAbstractItemView::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}

QProgressBar {{ background: {c['track']}; border: none; border-radius: 2px; min-height: 5px; max-height: 5px; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 2px; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ padding: 6px 4px; border-bottom: 1px solid {c['border']}; }}
QListWidget#Nav::item {{ border: none; border-radius: 6px; padding: 8px 10px; margin: 1px 0; }}
QListWidget#Nav::item:hover {{ background: {c['hover']}; }}
QListWidget#Nav::item:selected {{ background: {c['accent_soft']}; color: {c['accent_fg']}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border_strong']}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['text']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
QMenu {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 4px; }}
QMenu::item {{ padding: 6px 26px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['hover']}; }}
QMenu::item:disabled {{ color: {c['muted']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 4px 8px; }}
"""
