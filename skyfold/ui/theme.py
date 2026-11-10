"""Light/dark palettes that follow the Windows theme.

One accent colour everywhere: blue #3b82f6. Status colours (green / amber / red / slate) use
the same lightness so they sit well next to it. Scales used throughout the UI:
radius 12 for containers, 8 for controls, 10 for popups, 6 for list items; controls 32 px high.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPalette, QPen, QPixmap

ACCENT = "#3b82f6"

LIGHT = {
    "bg": "#f5f7fa", "surface": "#ffffff", "surface2": "#f1f4f9", "hover": "#e9eef6",
    "border": "#e2e8f0", "border_strong": "#cbd5e1",
    "text": "#0f172a", "muted": "#556070", "disabled": "#94a3b8",
    "accent": ACCENT, "accent_hover": "#2563eb", "accent_pressed": "#1d4ed8",
    "btn": "#2563eb", "btn_hover": "#1d4ed8", "btn_pressed": "#1e40af", "focus": "#0f172a", "focus_ring": "#1d4ed8",
    "accent_fg": "#2563eb",  # accent used as text or thin icon on a light background
    "accent_text": "#ffffff", "accent_soft": "#eff6ff", "accent_soft_border": "#bfdbfe",
    "warn_bg": "#fffbeb", "warn_border": "#fcd34d", "warn_text": "#92400e",
    "err_bg": "#fef2f2", "err_border": "#fca5a5", "err_text": "#b91c1c",
    "ok": "#16a34a", "warn": "#f59e0b", "err": "#ef4444", "offline": "#94a3b8",
    "track": "#e2e8f0",
}

DARK = {  # true black for OLED screens
    "bg": "#000000", "surface": "#0b0b0d", "surface2": "#151518", "hover": "#1c1c21",
    "border": "#232327", "border_strong": "#3a3a41",
    "text": "#f3f4f6", "muted": "#9ca3af", "disabled": "#55555c",
    "accent": ACCENT, "accent_hover": "#4f8ff7", "accent_pressed": "#2563eb",
    "btn": "#2563eb", "btn_hover": "#1d4ed8", "btn_pressed": "#1e40af", "focus": "#ffffff", "focus_ring": "#93c5fd",
    "accent_fg": "#60a5fa",
    "accent_text": "#ffffff", "accent_soft": "#0d1a30", "accent_soft_border": "#1e3a6b",
    "warn_bg": "#1c1505", "warn_border": "#a16207", "warn_text": "#fcd34d",
    "err_bg": "#1f0b0c", "err_border": "#b91c1c", "err_text": "#fca5a5",
    "ok": "#22c55e", "warn": "#f59e0b", "err": "#ef4444", "offline": "#94a3b8",
    "track": "#26262b",
}


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
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
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
* {{ font-family: "Segoe UI Variable Text","Segoe UI",sans-serif; font-size: 9.5pt; color: {c['text']}; }}
QWidget#Root, QDialog {{ background: {c['bg']}; }}
QWidget#Footer {{ background: {c['bg']}; border-top: 1px solid {c['border']}; }}
QWidget#NavPane {{ background: {c['surface']}; border-right: 1px solid {c['border']}; }}
QFrame#Card, QFrame#Tile {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#Row {{ background: transparent; border: none; border-radius: 8px; }}
QFrame#Row:hover {{ background: {c['hover']}; }}
QFrame#BottomBar {{ background: {c['surface']}; border: none; border-top: 1px solid {c['border']};
    border-bottom-left-radius: 10px; border-bottom-right-radius: 10px; }}
QToolButton#Bar {{ background: transparent; border: none; border-radius: 8px; padding: 6px 2px 4px 2px;
    min-width: 64px; min-height: 52px; font-size: 8.5pt; color: {c['text']}; }}
QToolButton#Bar:hover {{ background: {c['hover']}; }}
QToolButton#Bar:pressed {{ background: {c['border']}; }}
QToolButton#Bar:disabled {{ color: {c['disabled']}; }}
QToolButton#Bar:focus {{ border: 2px solid {c['focus_ring']}; }}
QToolButton#Bar[primary="true"]:disabled {{ color: {c['disabled']}; font-weight: 600; }}
QToolButton#Bar[primary="true"] {{ color: {c['accent_fg']}; font-weight: 600; }}
QFrame#FileTile {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 8px; }}
QLabel#RowTitle {{ font-size: 9.5pt; font-weight: 600; }}
QLabel#StatusLine {{ font-size: 9pt; color: {c['muted']}; }}
QFrame#Sep {{ background: {c['border']}; border: none; max-height: 1px; min-height: 1px; }}
QFrame#Warn {{ background: {c['warn_bg']}; border: 1px solid {c['warn_border']}; border-radius: 12px; }}
QFrame#Err  {{ background: {c['err_bg']};  border: 1px solid {c['err_border']};  border-radius: 12px; }}
QFrame#Warn QLabel {{ color: {c['warn_text']}; }}
QFrame#Err QLabel {{ color: {c['err_text']}; }}
QFrame#Warn QPushButton {{ background: transparent; border: 1px solid {c['warn_border']}; color: {c['warn_text']}; }}
QFrame#Err QPushButton  {{ background: transparent; border: 1px solid {c['err_border']};  color: {c['err_text']}; }}
QFrame#Warn QPushButton:hover {{ border-color: {c['warn_text']}; }}
QFrame#Err QPushButton:hover  {{ border-color: {c['err_text']}; }}
QLabel#Title {{ font-size: 12.5pt; font-weight: 600; }}
QLabel#H2 {{ font-size: 10.5pt; font-weight: 600; }}
QLabel#Big {{ font-size: 14pt; font-weight: 600; }}
QLabel#Section {{ font-size: 8.5pt; font-weight: 600; color: {c['muted']}; letter-spacing: 0.6px; }}
QLabel#Small {{ font-size: 9pt; }}
QFrame#ActRow {{ background: transparent; border: none; border-bottom: 1px solid {c['border']}; border-radius: 0; }}
QFrame#ActRow[link="true"]:hover {{ background: {c['hover']}; border-radius: 6px; }}
QLabel#Muted, QLabel#Hint {{ font-size: 8.75pt; color: {c['muted']}; }}
QLabel#ErrorText {{ color: {c['err_text']}; }}
QLabel#WarnText {{ color: {c['warn_text']}; }}
QFrame#Option {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#Option:hover {{ border-color: {c['border_strong']}; }}
QFrame#Option[selected="true"] {{ border: 1px solid {c['accent']}; background: {c['accent_soft']}; }}
QLabel#DotOff {{ border: 2px solid {c['border_strong']}; border-radius: 9px; background: transparent; }}
QLabel#DotOn  {{ border: 5px solid {c['accent']}; border-radius: 9px; background: #ffffff; }}

QPushButton {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 8px;
    padding: 0 14px; min-height: 30px; font-weight: 500; outline: none; }}
QPushButton:hover {{ background: {c['hover']}; border-color: {c['border_strong']}; }}
QPushButton:pressed {{ background: {c['border']}; }}
QPushButton:focus {{ border-color: {c['accent']}; }}
QPushButton:disabled {{ color: {c['disabled']}; background: {c['surface2']}; border-color: {c['border']}; }}
QPushButton#Primary {{ background: {c['btn']}; color: {c['accent_text']}; border: 1px solid {c['btn']}; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {c['btn_hover']}; border-color: {c['btn_hover']}; }}
QPushButton#Primary:pressed {{ background: {c['btn_pressed']}; border-color: {c['btn_pressed']}; }}
QPushButton#Primary:focus {{ border: 2px solid {c['focus']}; }}
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

QLineEdit, QSpinBox, QComboBox {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px;
    padding: 0 10px; min-height: 30px; selection-background-color: {c['accent']}; selection-color: #ffffff; }}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: {c['border_strong']}; }}
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

QProgressBar {{ background: {c['track']}; border: none; border-radius: 3px; min-height: 6px; max-height: 6px; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ padding: 6px 4px; border-bottom: 1px solid {c['border']}; }}
QListWidget#Nav::item {{ border: none; border-radius: 6px; padding: 8px 10px; margin: 1px 0; }}
QListWidget#Nav::item:hover {{ background: {c['hover']}; }}
QListWidget#Nav::item:selected {{ background: {c['accent_soft']}; color: {c['accent_fg']}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border_strong']}; border-radius: 3px; min-height: 30px; margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
QMenu {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 4px; }}
QMenu::item {{ padding: 6px 26px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['hover']}; }}
QMenu::item:disabled {{ color: {c['muted']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 4px 8px; }}
"""
