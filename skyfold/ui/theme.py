"""Light/dark palettes that follow the Windows theme.

Design tokens (1.1 redesign, "Skyfold as part of Windows 11"): a window is one calm surface (Mica on
Windows 11 where a window turns it on); content sits on quiet cards with a hairline stroke instead
of boxes; one accent for actions, focus and the current place; pink, blue, amber and violet only
mark the four services. Type: Segoe UI Variable, 12/14/16/20/28 px; spacing on a 4 px rhythm;
corners 6 (controls), 8 (cards), 12 (windows); shadows only on what floats.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path
from tempfile import mkdtemp

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPalette, QPen, QPixmap

from ..oled import OLED

ACCENT = "#0f5cc8"

# Windows 11-like: the window a soft grey (light) or graphite (dark), cards and fields a step apart
# with hairline strokes; the light theme's cards are white on the grey, as in Windows Settings.
LIGHT = {
    "bg": "#f3f4f7", "surface": "#ffffff", "surface2": "#f8f9fb", "hover": "#eaedf1",
    "group": "#ffffff", "tile": "#eef0f4",
    "border": "#e2e5ea", "border_strong": "#8a93a0",
    "text": "#1a1c21", "muted": "#566070", "muted2": "#5f6773", "disabled": "#959daa",
    "accent": ACCENT, "accent_hover": "#1667d9", "accent_pressed": "#0b4fae",
    "stop": "#c42b1c", "stop_hover": "#d23a2a", "stop_pressed": "#a72417", "stop_text": "#ffffff",
    "btn": ACCENT, "btn_hover": "#1667d9", "btn_pressed": "#0b4fae", "focus": ACCENT, "focus_ring": ACCENT,
    "accent_fg": ACCENT,  # accent used as text or thin icon on a light background
    "accent_text": "#ffffff", "accent_soft": "#e6effc", "accent_soft_border": "#c9dbf7",
    "warn_bg": "#fff4dc", "warn_border": "#f3dfb0", "warn_text": "#7a5000",
    "err_bg": "#fdeeee", "err_border": "#f3c9cc", "err_text": "#b0283a",
    "ok_bg": "#e9f6ee", "ok_text": "#1f7341",
    "ok": "#1f7341", "warn": "#7a5000", "err": "#b0283a", "offline": "#566070",
    "track": "#e4e7ec",
    "c_photos": "#cc2f58", "c_drive": "#1767c8", "c_other": "#9a6700",  # iCloud storage bar (the service colours)
}

DARK = {
    "bg": "#1f2228", "surface": "#2a2e35", "surface2": "#262a30", "hover": "#31363e",
    "group": "#272b31", "tile": "#30353c",
    "border": "#33383f", "border_strong": "#7a8492",
    "text": "#f3f5f8", "muted": "#b3bbc6", "muted2": "#959ca6", "disabled": "#6e7682",
    "accent": "#7db0ff", "accent_hover": "#97c0ff", "accent_pressed": "#679ff0",
    "stop": "#f25f55", "stop_hover": "#f5736a", "stop_pressed": "#e2534a", "stop_text": "#250605",
    "btn": "#4a90ff", "btn_hover": "#5c9bff", "btn_pressed": "#3f82ea", "focus": "#97c0ff", "focus_ring": "#97c0ff",
    "accent_fg": "#97c0ff",
    "accent_text": "#06142b", "accent_soft": "#283a55", "accent_soft_border": "#35507a",
    "warn_bg": "#3a3122", "warn_border": "#4d3f26", "warn_text": "#f3c969",
    "err_bg": "#432a2f", "err_border": "#5a343b", "err_text": "#ff9ba4",
    "ok_bg": "#213429", "ok_text": "#7fd9a4",
    "ok": "#7fd9a4", "warn": "#f3c969", "err": "#ff9ba4", "offline": "#b3bbc6",
    "track": "#3a3f47",
    "c_photos": "#f6809e", "c_drive": "#7dacff", "c_other": "#f3c969",
}


# Each service keeps one colour everywhere (panel tiles, Settings, the website): Photos pink, Drive
# blue, Notes amber, Safari bookmarks violet. Darker shades on the light theme keep icons readable.
SERVICE_COLORS = {
    "light": {"photo": "#cc2f58", "folder": "#1767c8", "note": "#9a6700", "bookmark": "#6247c9"},
    "dark": {"photo": "#f6809e", "folder": "#7dacff", "note": "#f3c969", "bookmark": "#b6a3ff"},
    "oled": {"photo": "#ff6b8a", "folder": "#64b5ff", "note": "#ffd60a", "bookmark": "#bf9fff"},
}


def theme_kind(c: dict[str, str]) -> str:
    if c is OLED:
        return "oled"
    return "light" if c is LIGHT or c.get("bg") == LIGHT["bg"] else "dark"


def service_color(c: dict[str, str], glyph_name: str) -> str | None:
    """The colour of a service glyph (photo, folder, note, bookmark) on this palette; None for others."""
    return SERVICE_COLORS[theme_kind(c)].get(glyph_name)


def state_color(c: dict[str, str], state: str) -> str:
    """Text/icon colour of a sync state on this palette (readable on its surfaces)."""
    return {"idle": c["ok"], "syncing": c["accent_fg"], "paused": c["warn"], "offline": c["offline"],
            "error": c["err"], "auth": c["err"], "setup": c["warn"], "attention": c["warn"]}.get(state, c["ok"])


def is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


THEMES = {"system": "System", "light": "Light", "dark": "Dark", "oled": "OLED"}  # English keys, translated where shown


def palette(mode: str = "system") -> dict[str, str]:
    if mode == "oled":
        return OLED
    if mode == "light":
        return LIGHT
    if mode == "dark":
        return DARK
    return DARK if is_dark() else LIGHT


_image_dir: Path | None = None


def _images() -> Path:
    """A folder of our own (new and private to this run) for the small images style sheets need: a
    file someone else has in the temp folder is never written over."""
    global _image_dir
    if _image_dir is None or not _image_dir.is_dir():
        _image_dir = Path(mkdtemp(prefix="skyfold-ui-", dir=tempfile.gettempdir()))
        atexit.register(shutil.rmtree, _image_dir, True)
    return _image_dir


def chevron_file(color: str) -> str:
    """Combo-box arrow drawn once into a PNG of our own (QSS can only reference image files)."""
    path = _images() / f"chevron_{color.lstrip('#')}.png"
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
    path = _images() / f"{kind}_{color.lstrip('#')}.png"
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


def _rgba(c: QColor, alpha: float) -> str:
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {max(0, min(255, int(alpha * 255)))})"


def accent_fill(colour: str, alpha: float = 1.0) -> str:
    """The main buttons' fill: the accent with a little light at the top, as on Windows 11's accent
    buttons (alpha < 1: see-through over Windows' blur)."""
    base = QColor(colour)
    return (f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {_rgba(base.lighter(112), alpha)}, "
            f"stop:1 {_rgba(base, alpha)})")


def accent_edges(colour: str, alpha: float = 1.0) -> str:
    """Their edge: lighter along the top, darker along the bottom (a button that stands up a little)."""
    base = QColor(colour)
    return (f"border: 1px solid {_rgba(base, alpha)}; border-top-color: {_rgba(base.lighter(135), min(1.0, alpha + 0.1))}; "
            f"border-bottom-color: {_rgba(base.darker(122), min(1.0, alpha + 0.1))};")


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
* {{ font-family: "Segoe UI Variable Text","Segoe UI Variable","Segoe UI",sans-serif; font-size: 10.5pt; color: {c['text']}; }}
QWidget#Root, QDialog {{ background: {c['bg']}; }}
QWidget#Footer {{ background: {c['bg']}; border-top: 1px solid {c['border']}; }}
QWidget#NavPane {{ background: {c['bg']}; border: none; }}
QDialog QFrame#Card {{ background: {c['group']}; border: 1px solid {c['border']}; border-radius: 8px; }}
QDialog QLabel#Section {{ font-size: 7.5pt; letter-spacing: 1.2px; }}
QFrame#Card, QFrame#Tile {{ background: {c['group']}; border: 1px solid {c['border']}; border-radius: 8px; }}
QFrame#Card[hot="true"] {{ background: {c['hover']}; }}
SoftCard {{ qproperty-fill: {c['group']}; qproperty-stroke: {c['border']};
  qproperty-shade: {'rgba(0, 0, 0, 16)' if QColor(c['bg']).lightness() > 128 else 'rgba(0, 0, 0, 70)'}; }}
SoftCard[hot="true"] {{ qproperty-fill: {c['hover']}; }}
SoftCard#SetCard[hot="true"] {{ qproperty-fill: {c['surface2'] if c['bg'] != c['group'] else c['hover']}; }}
QFrame#Row {{ background: transparent; border: none; border-radius: 6px; }}
QFrame#Row:hover {{ background: {c['hover']}; }}
QFrame#Row:focus {{ border: 2px solid {c['focus_ring']}; }}
QFrame#Storage {{ background: transparent; border: 2px solid transparent; border-radius: 8px; }}
QFrame#Storage:focus {{ border-color: {c['focus_ring']}; }}
QLabel#Brand {{ background: {c['accent_soft']}; border-radius: 10px; }}
QLabel#SvcTile {{ background: {c['tile']}; border-radius: 9px; }}
QFrame#BottomBar {{ background: {c['bg']}; border: none; border-top: 1px solid {c['border']};
    border-bottom-left-radius: 12px; border-bottom-right-radius: 12px; }}
QToolButton#Bar {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 0;
    min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px; color: {c['muted']}; }}
QToolButton#Bar:hover {{ background: {c['hover']}; color: {c['text']}; }}
QToolButton#Bar:pressed {{ background: {c['surface2']}; color: {c['muted']}; }}
QToolButton#Bar:disabled {{ color: {c['disabled']}; }}
QToolButton#Bar:focus {{ border: 2px solid {c['focus_ring']}; }}
QToolButton#Bar::menu-indicator {{ image: none; width: 0; }}
QToolButton#Bar[primary="true"], QToolButton#Bar[wide="true"] {{ font-size: 9.75pt; font-weight: 600;
    padding: 0 12px; min-width: 106px; max-width: 106px; }}
QToolButton#Bar[primary="true"] {{ background: {accent_fill(c['btn'])}; color: {c['accent_text']}; {accent_edges(c['btn'])} }}
QToolButton#Bar[wide="true"] {{ color: {c['stop_text']}; background: {accent_fill(c['stop'])}; {accent_edges(c['stop'])} }}
QToolButton#Bar[wide="true"]:hover {{ background: {accent_fill(c['stop_hover'])}; {accent_edges(c['stop_hover'])} color: {c['stop_text']}; }}
QToolButton#Bar[wide="true"]:pressed {{ background: {c['stop_pressed']}; color: {c['stop_text']}; }}
QToolButton#Bar[wide="true"]:focus {{ border: 2px solid {c['text']}; }}
QToolButton#Bar[primary="true"]:hover {{ background: {accent_fill(c['btn_hover'])}; {accent_edges(c['btn_hover'])} color: {c['accent_text']}; }}
QToolButton#Bar[primary="true"]:pressed {{ background: {c['btn_pressed']}; color: {c['accent_text']}; }}
QToolButton#Bar[primary="true"]:focus {{ border: 2px solid {c['text']}; }}
QToolButton#Bar[primary="true"]:disabled {{ background: {c['track']}; border-color: {c['track']}; color: {c['disabled']}; }}
QFrame#FileTile {{ background: transparent; border: none; }}
QLabel#RowTitle {{ font-size: 9.75pt; font-weight: 600; }}
QLabel#ActTitle {{ font-size: 10.5pt; font-weight: 500; }}
QLabel#ActWhen {{ font-family: "Cascadia Mono","Cascadia Code","Consolas"; font-size: 8.75pt; color: {c.get('muted2', c['muted'])}; }}
QLabel#ListHead {{ font-size: 9pt; font-weight: 600; color: {c.get('muted2', c['muted'])}; }}
QPushButton#ListLink {{ background: transparent; border: none; color: {c['accent_fg']}; font-size: 9.5pt; font-weight: 600;
    padding: 0; min-height: 0; }}
QPushButton#ListLink:hover {{ text-decoration: underline; }}
QLabel#PanelTitle {{ font-family: "Segoe UI Variable Display","Segoe UI"; font-size: 12pt; font-weight: 600; }}
QLabel#HeroTitle {{ font-family: "Segoe UI Variable Display","Segoe UI"; font-size: 10.5pt; font-weight: 600; }}
QLabel#Amount {{ font-family: "Cascadia Mono","Cascadia Code","Consolas"; font-size: 9.25pt; font-weight: 600; color: {c['muted']}; }}
QLabel#StatusLine {{ font-size: 9pt; color: {c['muted']}; }}
QLabel#HeroText {{ font-size: 9.25pt; color: {c['muted']}; }}
QLabel#HeroMono {{ font-family: "Cascadia Mono","Cascadia Code","Consolas"; font-size: 9.25pt; color: {c['muted']}; }}
QLabel#Lead {{ font-size: 10.5pt; color: {c['muted']}; }}
QLabel#HeroBig {{ font-family: "Segoe UI Variable Display","Segoe UI"; font-size: 18pt; font-weight: 600; }}
QLabel#OptionTitle {{ font-size: 9.75pt; font-weight: 600; }}
QLineEdit#PathField {{ font-family: "Cascadia Mono","Consolas"; font-size: 9.25pt; min-height: 34px;
    border-bottom: 2px solid {c['accent']}; }}
QLabel#Mono {{ font-family: "Cascadia Mono","Cascadia Code","Consolas"; font-size: 9pt; color: {c.get('muted2', c['muted'])}; }}
QFrame#Sep {{ background: {c['border']}; border: none; max-height: 1px; min-height: 1px; }}
QFrame#Warn {{ background: {c['warn_bg']}; border: 1px solid {c['warn_border']}; border-radius: 8px; }}
QFrame#Err  {{ background: {c['err_bg']};  border: 1px solid {c['err_border']};  border-radius: 8px; }}
QFrame#Info {{ background: {c['accent_soft']}; border: 1px solid {c['accent_soft_border']}; border-radius: 8px; }}
QFrame#Info QLabel {{ color: {c['accent_fg']}; }}
QFrame#Info QPushButton {{ background: transparent; border: 1px solid {c['accent_fg']}; color: {c['accent_fg']}; }}
QFrame#Info QPushButton:hover {{ background: {c['surface']}; }}
QFrame#Warn QLabel {{ color: {c['warn_text']}; }}
QFrame#Err QLabel {{ color: {c['err_text']}; }}
QFrame#Warn QPushButton {{ background: transparent; border: 1px solid {c['warn_text']}; color: {c['warn_text']}; }}
QFrame#Err QPushButton  {{ background: transparent; border: 1px solid {c['err_text']};  color: {c['err_text']}; }}
QFrame#Warn QPushButton:hover {{ background: {c['surface']}; }}
QFrame#Err QPushButton:hover  {{ background: {c['surface']}; }}
QLabel#Title {{ font-family: "Segoe UI Variable Display","Segoe UI"; font-size: 15pt; font-weight: 600; }}
QLabel#H2 {{ font-family: "Segoe UI Variable Display","Segoe UI"; font-size: 11pt; font-weight: 600; }}
QLabel#Big {{ font-family: "Segoe UI Variable Display","Segoe UI"; font-size: 15pt; font-weight: 600; }}
QLabel#Section {{ font-size: 7.75pt; font-weight: 600; color: {c['muted']}; }}
QLabel#Small {{ font-size: 9pt; }}
QLabel#Legend {{ font-size: 9pt; color: {c['muted']}; }}
QLabel#FootCap {{ font-size: 8.25pt; color: {c['muted']}; }}
QLabel#FootVal {{ font-size: 9.5pt; font-weight: 600; color: {c['text']}; }}
QFrame#ActRow {{ background: transparent; border: none; border-radius: 6px; }}
QFrame#ActRow[link="true"]:hover {{ background: {c['hover']}; }}
QFrame#ActRow:focus {{ border: 2px solid {c['focus_ring']}; }}
QLabel#Muted, QLabel#Hint {{ font-size: 9.25pt; color: {c.get('muted2', c['muted'])}; }}
QLabel#ErrorText {{ color: {c['err_text']}; }}
QLabel#WarnText {{ color: {c['warn_text']}; }}
QFrame#Option {{ background: {c['group']}; border: 1px solid {c['border']}; border-radius: 8px; }}
QFrame#Option:hover {{ background: {c['hover']}; }}
QFrame#Option[selected="true"] {{ border: 2px solid {c['accent']}; background: {c['accent_soft']}; }}
QFrame#Option:focus {{ border: 2px solid {c['focus_ring']}; }}
QFrame#Device {{ background: transparent; border: none; border-radius: 8px; }}
QLabel#DotOff {{ border: 2px solid {c['border_strong']}; border-radius: 9px; background: transparent; }}
QLabel#DotOn  {{ border: 5px solid {c['accent']}; border-radius: 9px; background: #ffffff; }}

QPushButton {{ background: {c['surface']}; border: 1px solid {c['border']}; border-bottom-color: {c['border_strong']};
    border-radius: 6px; padding: 0 14px; min-height: 32px; font-size: 9pt; font-weight: 600; outline: none; }}
QPushButton:hover {{ background: {c['hover']}; }}
QPushButton:pressed {{ background: {c['surface2']}; color: {c['muted']}; }}
QPushButton:focus {{ border: 2px solid {c['focus_ring']}; }}
QPushButton:disabled {{ color: {c['disabled']}; background: {c['surface2']}; border-color: {c['border']}; }}
QPushButton#Primary {{ background: {accent_fill(c['btn'])}; color: {c['accent_text']}; {accent_edges(c['btn'])} font-weight: 600; }}
QPushButton#Primary:hover {{ background: {accent_fill(c['btn_hover'])}; {accent_edges(c['btn_hover'])} }}
QPushButton#Primary:pressed {{ background: {c['btn_pressed']}; border-color: {c['btn_pressed']}; color: {c['accent_text']}; }}
QPushButton#Primary:focus {{ border: 2px solid {c['text']}; }}
QPushButton#Primary:disabled {{ background: {c['track']}; border-color: {c['track']}; color: {c['disabled']}; }}
QPushButton#Danger {{ color: {c['err_text']}; }}
QPushButton#Danger:hover {{ border-color: {c['err_border']}; background: {c['err_bg']}; }}
QPushButton#Danger:disabled {{ color: {c['disabled']}; }}
QLabel:disabled {{ color: {c['disabled']}; }}
QPushButton#Link {{ background: transparent; border: none; color: {c['accent_fg']}; padding: 0 2px; text-align: left; }}
QPushButton#Link:hover {{ text-decoration: underline; }}
QPushButton#Link:disabled {{ color: {c['disabled']}; }}
QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 0; min-width: 32px; min-height: 32px; }}
QToolButton:hover {{ background: {c['hover']}; }}
QToolButton:pressed {{ background: {c['surface2']}; }}
QToolButton:focus {{ border: 1px solid {c['accent']}; }}
QLabel#Hint:disabled, QLabel#Muted:disabled {{ color: {c['disabled']}; }}

QLineEdit, QSpinBox, QComboBox {{ background: {c['surface']}; border: 1px solid {c['border']};
    border-bottom-color: {c['border_strong']}; border-radius: 6px; padding: 0 10px; min-height: 32px;
    selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ background: {c['surface2']}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QComboBox:on {{ border-bottom: 2px solid {c['accent']}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {c['disabled']}; background: {c['surface2']};
    border-color: {c['border']}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
QSpinBox QLineEdit {{ border: none; padding: 0; min-height: 0; background: transparent; }}
QComboBox {{ padding-right: 30px; combobox-popup: 0; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 28px; border: none; }}
QComboBox::down-arrow {{ image: url("{chev}"); width: 12px; height: 12px; }}
QComboBoxPrivateContainer {{ background: transparent; border: none; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px;
    padding: 4px; outline: none; }}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 0 10px; border-radius: 4px; }}
QComboBox QAbstractItemView::item:hover {{ background: {c['hover']}; }}
QComboBox QAbstractItemView::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}

QProgressBar {{ background: {c['track']}; border: none; border-radius: 2px; min-height: 4px; max-height: 4px; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 2px; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ padding: 6px 4px; border-bottom: 1px solid {c['border']}; }}
QListWidget#Nav {{ font-size: 10.5pt; }}
QLabel#StorageHead {{ font-size: 10.5pt; color: {c['muted']}; }}
QLabel#PageTitle {{ font-family: "Segoe UI Variable Display","Segoe UI"; font-size: 20pt; font-weight: 600; padding-bottom: 2px; }}
QLabel#GroupLabel {{ font-size: 10.5pt; font-weight: 600; }}
QLabel#SetTitle {{ font-size: 10.5pt; font-weight: 600; }}
QLabel#SetHead {{ font-family: "Segoe UI Variable Display","Segoe UI"; font-size: 11.5pt; font-weight: 600; }}
QLabel#SetSub {{ font-size: 9.25pt; color: {c.get('muted2', c['muted'])}; }}
QLabel#InfoLine {{ font-size: 9.75pt; color: {c['muted']}; }}
QLabel#WhoName {{ font-size: 10.5pt; font-weight: 600; }}
QLabel#WhoSub {{ font-size: 9pt; color: {c.get('muted2', c['muted'])}; }}
QLabel#SetTitle:disabled, QLabel#SetHead:disabled, QLabel#GroupLabel:disabled, QLabel#SetSub:disabled,
QLabel#InfoLine:disabled {{ color: {c['disabled']}; }}
QListWidget#Nav::item {{ border: none; border-radius: 6px; padding: 0 12px; margin: 1px 0; color: {c['text']}; }}
QListWidget#Nav::item:hover {{ background: {c['hover']}; }}
QListWidget#Nav::item:selected {{ background: {c['surface'] if QColor(c['bg']).lightness() > 128 else c['tile']}; color: {c['text']}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border_strong']}; border-radius: 3px; min-height: 30px; margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: {c['muted']}; margin: 0; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
QMenu {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 7px 28px 7px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {c['hover']}; }}
QMenu::item:disabled {{ color: {c['muted']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
""" + (f"""
QDialog QFrame#Card {{ border-color: transparent; background: {c['group']}; }}
SoftCard {{ qproperty-stroke: transparent; }}
QFrame#BottomBar {{ background: {c['bg']}; }}
QLabel#DotOn {{ background: {c['accent_text']}; }}
""" if c is OLED else "")
