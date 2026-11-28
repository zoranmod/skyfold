"""Skyfold's line icons (the 1.1 design): one set, drawn on a 20 x 20 grid with 1.6 px round strokes,
the same drawings as the design mockup. Rendered sharp at any scale from SVG; the colour is given
per use (service colours, accent, muted text)."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap

ICONS: dict[str, str] = {
    "photo": '<rect x="2.5" y="3.5" width="15" height="13" rx="2.5"/><circle cx="7.2" cy="8" r="1.6"/><path d="M17 13.5l-4-4-7.5 7"/>',
    "folder": '<path d="M2.5 6.2a2 2 0 0 1 2-2h3.3l1.8 1.8h5.9a2 2 0 0 1 2 2v6.8a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2z"/>',
    "note": '<rect x="4" y="2.5" width="12" height="15" rx="2"/><path d="M7 7h6M7 10h6M7 13h4"/>',
    "bookmark": '<path d="M6 3h8a1 1 0 0 1 1 1v13l-5-3.3L5 17V4a1 1 0 0 1 1-1z"/>',
    # a cog with eight teeth (the earlier rays read as a sun: "theme", not "settings")
    "gear": '<circle cx="10" cy="10" r="2.6"/><path d="M8.17 4.08 L8.63 2.02 L11.37 2.02 L11.83 4.08 L12.89 4.52 '
            'L14.68 3.39 L16.61 5.32 L15.48 7.11 L15.92 8.17 L17.98 8.63 L17.98 11.37 L15.92 11.83 L15.48 12.89 '
            'L16.61 14.68 L14.68 16.61 L12.89 15.48 L11.83 15.92 L11.37 17.98 L8.63 17.98 L8.17 15.92 L7.11 15.48 '
            'L5.32 16.61 L3.39 14.68 L4.52 12.89 L4.08 11.83 L2.02 11.37 L2.02 8.63 L4.08 8.17 L4.52 7.11 L3.39 5.32 '
            'L5.32 3.39 L7.11 4.52Z"/>',
    "sync": '<path d="M16 9a6 6 0 0 0-10.6-3.4M4 4v3h3M4 11a6 6 0 0 0 10.6 3.4M16 16v-3h-3"/>',
    "check": '<path d="M4.5 10.5l3.5 3.5 7.5-8"/>',
    "chevron": '<path d="M8 5l5 5-5 5"/>',
    "back": '<path d="M12 5l-5 5 5 5"/>',
    "down": '<path d="M5 8l5 5 5-5"/>',
    "pin": '<path d="M10 17.5s-5.5-4.8-5.5-9a5.5 5.5 0 0 1 11 0c0 4.2-5.5 9-5.5 9z"/><circle cx="10" cy="8.5" r="2"/>',
    "more": '<circle cx="5" cy="10" r=".9"/><circle cx="10" cy="10" r=".9"/><circle cx="15" cy="10" r=".9"/>',
    "close": '<path d="M5.5 5.5l9 9M14.5 5.5l-9 9"/>',
    "stop": '<rect x="5" y="5" width="10" height="10" rx="1.75"/>',  # (as tall as the capitals beside it)
    "upload": '<path d="M10 15V5M6 9l4-4 4 4"/>',
    "download": '<path d="M10 4v9M6 9.5l4 4 4-4M4.5 16h11"/>',
    "info": '<circle cx="10" cy="10" r="7.5"/><path d="M10 9v5M10 6.3v.2"/>',
    "error": '<circle cx="10" cy="10" r="7.5"/><path d="M10 6v5M10 13.8v.2"/>',
    "clock": '<circle cx="10" cy="10" r="7.5"/><path d="M10 6v4.3l2.8 1.7"/>',
    "bell": '<path d="M5.5 13.5V9a4.5 4.5 0 0 1 9 0v4.5l1.3 1.5H4.2z"/><path d="M8.3 17h3.4"/>',
    "globe": '<circle cx="10" cy="10" r="7.5"/><path d="M2.5 10h15M10 2.5c2 2.2 3 4.7 3 7.5s-1 5.3-3 7.5c-2-2.2-3-4.7-3-7.5s1-5.3 3-7.5z"/>',
    "trash": '<path d="M4 6h12M8 6V4h4v2M5.5 6l.8 10h7.4l.8-10"/>',
    "search": '<circle cx="9" cy="9" r="5"/><path d="M13 13l4 4"/>',
    "shield": '<path d="M10 2.5l6 2.3v4.6c0 3.9-2.6 6.7-6 8.1-3.4-1.4-6-4.2-6-8.1V4.8z"/>',
    "heart": '<path d="M10 16.5s-6.5-3.9-6.5-8.6A3.6 3.6 0 0 1 10 5.8a3.6 3.6 0 0 1 6.5 2.1c0 4.7-6.5 8.6-6.5 8.6z"/>',
    "pause": '<path d="M7.5 5.5v9M12.5 5.5v9"/>',
    "play": '<path d="M7 5l8 5-8 5z"/>',
    "cloud": '<path d="M6.5 15.5a3.5 3.5 0 0 1-.4-7A5 5 0 0 1 15.6 8a3.8 3.8 0 0 1-.3 7.5z"/>',
    "device": '<rect x="6" y="2.5" width="8" height="15" rx="2"/><path d="M9 14.8h2"/>',
    "file": '<path d="M5.5 2.5h6l3 3v11a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-13a1 1 0 0 1 1-1z"/><path d="M11.5 2.5v3h3"/>',
    "sliders": '<path d="M4 6h7M15 6h1M4 14h1M9 14h7"/><circle cx="13" cy="6" r="2"/><circle cx="7" cy="14" r="2"/>',
    "power": '<path d="M10 3v6"/><path d="M6 5.5a6 6 0 1 0 8 0"/>',
    "palette": '<path d="M10 2.8a7.2 7.2 0 1 0 0 14.4c1 0 1.5-.8 1.2-1.6-.5-1.2.2-2.3 1.5-2.3h1.7a2.8 2.8 0 0 0 2.8-2.8c0-4.3-3.2-7.7-7.2-7.7z"/><circle cx="6.5" cy="9" r=".9"/><circle cx="9" cy="6" r=".9"/><circle cx="12.6" cy="6.6" r=".9"/>',
    "speed": '<path d="M3.5 14a6.5 6.5 0 1 1 13 0"/><path d="M10 14l3.2-4.2"/>',
    "window": '<rect x="2.5" y="4" width="15" height="12" rx="2"/><path d="M2.5 7.5h15"/>',
    "layout": '<rect x="3" y="3" width="14" height="14" rx="2"/><path d="M3 8h14M8 8v9"/>',
}
FILLED = {"more", "play"}  # (dots and the triangle read better filled)


def svg(name: str, color: str, stroke: float = 1.6) -> bytes:
    body = ICONS[name]
    fill = color if name in FILLED else "none"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="{fill}" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


def pixmap(name: str, color: str, size: int, dpr: float = 2.0, stroke: float = 1.6) -> QPixmap:
    from PySide6.QtSvg import QSvgRenderer

    px = max(1, round(size * dpr))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    r = QSvgRenderer(QByteArray(svg(name, color, stroke)))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, px, px))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str, size: int = 20) -> QIcon:
    """Sharp on 100-300 % screens: drawn at 1x, 2x and 3x."""
    ic = QIcon()
    for dpr in (1.0, 2.0, 3.0):
        ic.addPixmap(pixmap(name, color, size, dpr))
    return ic
