"""Icons drawn in code, so the app ships without image files and stays crisp at any DPI."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

STATE_COLORS = {
    "idle": "#16a34a",
    "syncing": "#2563eb",
    "paused": "#b45309",
    "offline": "#64748b",
    "note": "#64748b",  # idle, but the pass ended for a reason (stopped, uploads waiting): neutral, not green
    "error": "#dc2626",
    "auth": "#dc2626",
    "setup": "#b45309",
    "attention": "#b45309",  # all synced, but a large deletion waits for the user's OK
}


LOGO_LINE = "#3b82f6"  # exactly the app accent (theme.ACCENT)
LOGO_BG_TOP = "#1e293b"
LOGO_BG_BOTTOM = "#0b1220"


# Cloud = chain of four circles (left end, small bump, big bump, right end) over a flat base,
# in units of the cloud width. Only true arcs are used, so the outline stays perfectly smooth
# at any size (merging shapes with QPainterPath.simplified() flattens curves into polygons).
_CLOUD_CIRCLES = [((0.155, 0.465), 0.155), ((0.31, 0.34), 0.19), ((0.60, 0.25), 0.25), ((0.845, 0.465), 0.155)]
_CLOUD_BOTTOM = 0.62


def _upper_intersection(c1, r1, c2, r2) -> tuple[float, float]:
    (x1, y1), (x2, y2) = c1, c2
    dx, dy = x2 - x1, y2 - y1
    d = math.hypot(dx, dy)
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h = math.sqrt(max(r1 * r1 - a * a, 0.0))
    mx, my = x1 + a * dx / d, y1 + a * dy / d
    p1 = (mx + h * dy / d, my - h * dx / d)
    p2 = (mx - h * dy / d, my + h * dx / d)
    return p1 if p1[1] < p2[1] else p2  # the outer junction is the higher one


def _angle(c, pt) -> float:
    """Qt arc angle (degrees, counter-clockwise, y up) of point pt on a circle centred at c."""
    return math.degrees(math.atan2(-(pt[1] - c[1]), pt[0] - c[0]))


_CLOUD_FILLET = 0.07  # radius of the rounding where two bumps meet


def _arc(path: QPainterPath, c, r: float, a: tuple, b: tuple, clockwise: bool) -> None:
    """Append a circular arc from a to b as cubic Béziers.

    QPainterPath.arcTo() inserts a zero-length line before every arc, and the stroker draws
    a visible notch there; chaining cubics keeps the outline one seamless curve.
    """
    a0, a1 = _angle(c, a), _angle(c, b)
    sweep = -((a0 - a1) % 360) if clockwise else (a1 - a0) % 360
    n = max(1, math.ceil(abs(sweep) / 90))
    step = math.radians(sweep / n)
    k = 4 / 3 * math.tan(step / 4)
    t = math.radians(a0)
    for _ in range(n):
        t2 = t + step
        # points on the circle in screen coordinates (Qt angles are counter-clockwise, y up)
        p0 = (c[0] + r * math.cos(t), c[1] - r * math.sin(t))
        p3 = (c[0] + r * math.cos(t2), c[1] - r * math.sin(t2))
        d0 = (-math.sin(t), -math.cos(t))  # tangent at t (screen coords)
        d3 = (-math.sin(t2), -math.cos(t2))
        c1 = QPointF(p0[0] + k * r * d0[0], p0[1] + k * r * d0[1])
        c2 = QPointF(p3[0] - k * r * d3[0], p3[1] - k * r * d3[1])
        path.cubicTo(c1, c2, QPointF(*p3))
        t = t2


def cloud_outline(x: float, y: float, w: float) -> QPainterPath:
    """One continuous, smooth cloud contour of width w and height 0.62·w, top-left at (x, y).

    Bumps are joined by small tangent fillet arcs instead of sharp notches.
    """
    circles = [((x + cx * w, y + cy * w), r * w) for (cx, cy), r in _CLOUD_CIRCLES]
    f = _CLOUD_FILLET * w
    fillets = []  # (fillet centre, tangent point on left circle, tangent point on right circle)
    for (c1, r1), (c2, r2) in zip(circles, circles[1:]):
        fc = _upper_intersection(c1, r1 + f, c2, r2 + f)
        t1 = (c1[0] + (fc[0] - c1[0]) * r1 / (r1 + f), c1[1] + (fc[1] - c1[1]) * r1 / (r1 + f))
        t2 = (c2[0] + (fc[0] - c2[0]) * r2 / (r2 + f), c2[1] + (fc[1] - c2[1]) * r2 / (r2 + f))
        fillets.append((fc, t1, t2))
    bottom = y + _CLOUD_BOTTOM * w
    start = (circles[0][0][0], bottom)
    path = QPainterPath(QPointF(*start))
    pos = start
    for i, (c, r) in enumerate(circles):
        end = fillets[i][1] if i < len(fillets) else (c[0], bottom)
        _arc(path, c, r, pos, end, clockwise=True)  # up the left side and over the top
        if i < len(fillets):
            fc, t1, t2 = fillets[i]
            _arc(path, fc, f, t1, t2, clockwise=False)  # the concave rounding between bumps
            pos = t2
    path.closeSubpath()  # straight flat bottom back to the start
    return path


def draw_mark(p: QPainter, s: float, color: str, line_w: float, arrow_w: float, inset: float = 0.08) -> None:
    """The Skyfold mark: a line cloud with a thin down/up arrow pair inside."""
    p.setRenderHint(QPainter.Antialiasing)
    cw = s * (1 - 2 * inset)
    top = (s - cw * 0.62) / 2 - s * 0.02
    p.setPen(QPen(QColor(color), line_w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(cloud_outline(s * inset, top, cw))
    # arrows sit in the body of the cloud, clear of the bottom edge
    body_bottom = top + 0.03 * cw + 0.59 * cw
    y_hi, y_lo = body_bottom - cw * 0.40, body_bottom - cw * 0.13
    head = cw * 0.075
    p.setPen(QPen(QColor(color), arrow_w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    for x, down in ((s / 2 - cw * 0.10, True), (s / 2 + cw * 0.10, False)):
        a, b = (y_hi, y_lo) if down else (y_lo, y_hi)
        p.drawLine(QPointF(x, a), QPointF(x, b))
        d = head if down else -head
        p.drawPolyline([QPointF(x - head, b - d), QPointF(x, b), QPointF(x + head, b - d)])


def draw_logo(p: QPainter, size: float, glyph_only: bool = False) -> None:
    p.setRenderHint(QPainter.Antialiasing)
    s = size
    if not glyph_only:
        grad = QLinearGradient(0, 0, 0, s)
        grad.setColorAt(0, QColor(LOGO_BG_TOP))
        grad.setColorAt(1, QColor(LOGO_BG_BOTTOM))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(0, 0, s, s), s * 0.23, s * 0.23)
    draw_mark(p, s, LOGO_LINE, line_w=max(1.1, s * 0.045), arrow_w=max(0.9, s * 0.027), inset=0.13)


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        draw_logo(p, size)
        p.end()
        icon.addPixmap(pm)
    return icon


def taskbar_is_light() -> bool:
    """Windows keeps the taskbar theme separately from the app theme."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "SystemUsesLightTheme")[0] == 1
    except (OSError, ImportError):
        return False


def tray_icon(state: str, phase: int = 0) -> QIcon:
    """Monochrome mark matching the taskbar, like Windows' own tray icons.

    All good: just the mark. Anything else: a small coloured status dot in the corner.
    """
    fg = "#1c1c1c" if taskbar_is_light() else "#ffffff"
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        draw_mark(p, size, fg, line_w=max(1.15, size * 0.065), arrow_w=max(0.9, size * 0.042), inset=0.03)
        if state != "idle":
            d = size * 0.40
            rect = QRectF(size - d, size - d, d, d)
            gap = size * 0.08
            p.setCompositionMode(QPainter.CompositionMode_Clear)  # cut a clean ring around the dot
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("black"))
            p.drawEllipse(rect.adjusted(-gap, -gap, gap, gap))
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            color = QColor(STATE_COLORS.get(state, "#22c55e"))
            if state == "syncing":
                p.setPen(QPen(color, max(1.2, size * 0.09), Qt.SolidLine, Qt.RoundCap))
                inner = rect.adjusted(d * 0.12, d * 0.12, -d * 0.12, -d * 0.12)
                p.drawArc(inner, -(phase * 45) * 16, 270 * 16)
            else:
                p.setBrush(color)
                p.drawEllipse(rect)
        p.end()
        icon.addPixmap(pm)
    return icon


def glyph(name: str, color: str, size: int = 20) -> QIcon:
    """Small line icons for buttons and activity rows (the 1.1 icon set, see svgicons)."""
    from . import svgicons

    if name in svgicons.ICONS:
        return svgicons.icon(name, color, size)
    pm = QPixmap(size * 2, size * 2)  # a name the set doesn't have: an empty icon of that size
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    return QIcon(pm)
