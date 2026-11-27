"""Parts of the tray panel's 1.1 look: the app's own logo tile, the round status badge (a check, a
ring that fills while syncing, or a warning) and small thumbnails for activity about photos."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Property, QEvent, QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QSizePolicy, QToolButton, QWidget

from .icons import draw_logo, glyph

PHOTO_EXT = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".gif", ".webp", ".tif", ".tiff", ".dng", ".mov", ".mp4",
             ".m4v"}


def tint(color: str, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(alpha)
    return c


class LogoTile(QWidget):
    """The Skyfold icon itself (cloud with the two arrows on its dark tile), as on the taskbar."""

    def __init__(self, size: int = 36) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self._size = size

    def set_palette(self, _palette, _color=None) -> None:  # (the logo has its own colours)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        draw_logo(p, self._size)


class StateBadge(QWidget):
    """The status at a glance, 44 px round: a check when all is well, a ring that fills (with the
    percentage) while syncing, a spinning arc while the amount isn't known yet, a pause, a cloud when
    offline, or a warning that needs the user."""

    GLYPH = {"idle": "check", "paused": "pause", "offline": "cloud", "error": "error", "auth": "error",
             "setup": "folder", "attention": "shield"}

    def __init__(self, palette: dict[str, str], size: int = 44) -> None:
        super().__init__()
        self.c = palette
        self.state = "idle"
        self.fraction: float | None = None  # syncing: 0..1, None = not known yet
        self.angle = 0.0
        self.setFixedSize(size, size)

    def set_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        self.update()

    def set_state(self, state: str, fraction: float | None = None) -> None:
        self.state, self.fraction = state, fraction
        self.update()

    def spin(self, angle: float) -> None:
        self.angle = angle
        if self.state == "syncing" and self.fraction is None:
            self.update()

    def _colour(self) -> str:
        c = self.c
        return {"idle": c["ok"], "paused": c["warn"], "offline": c["offline"], "error": c["err"], "auth": c["err"],
                "setup": c["accent_fg"], "attention": c["warn"]}.get(self.state, c["accent_fg"])

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.width()
        if self.state == "syncing":
            w = 4.0
            r = QRectF(w / 2 + 1, w / 2 + 1, s - w - 2, s - w - 2)
            p.setPen(QPen(QColor(self.c["track"]), w))
            p.setBrush(QColor(self.c["bg"]))
            p.drawEllipse(r)
            p.setPen(QPen(QColor(self.c["accent"]), w, Qt.SolidLine, Qt.RoundCap))
            if self.fraction is None:  # amount not known yet: a quarter arc that turns
                p.drawArc(r, int((90 - self.angle) * 16), -90 * 16)
                glyph("sync", self.c["accent_fg"], 18).paint(p, int(s / 2 - 9), int(s / 2 - 9), 18, 18)
            else:
                p.drawArc(r, 90 * 16, -int(360 * 16 * max(0.0, min(1.0, self.fraction))))
                f = QFont(p.font())
                f.setFamilies(["Cascadia Mono", "Cascadia Code", "Consolas"])
                f.setPixelSize(11)
                f.setWeight(QFont.DemiBold)
                p.setFont(f)
                p.setPen(QColor(self.c["text"]))
                p.drawText(self.rect(), Qt.AlignCenter, f"{round(100 * self.fraction)}%")
            return
        col = self._colour()
        p.setPen(Qt.NoPen)
        p.setBrush(tint(col, 46))
        p.drawEllipse(QRectF(0.5, 0.5, s - 1, s - 1))
        g = 22
        glyph(self.GLYPH.get(self.state, "check"), col, g).paint(p, int((s - g) / 2), int((s - g) / 2), g, g)


def rounded(img: QImage, size: int, radius: float, dpr: float) -> QPixmap:
    """A square thumbnail, cut to fit, with rounded corners."""
    px = round(size * dpr)
    src = img
    short = min(src.width(), src.height())
    if short > 0:
        src = src.copy((src.width() - short) // 2, (src.height() - short) // 2, short, short)
    src = src.scaled(px, px, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    out = QPixmap(px, px)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, px, px), radius * dpr, radius * dpr)
    p.setClipPath(clip)
    p.drawImage(0, 0, src)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


class Thumbs(QObject):
    """Small pictures of photos named in the activity list, made in the background (Windows' own
    stored previews, like the Photos window). Asked for once per file; nothing is kept on disk."""

    ready = Signal(str, QImage)  # path, picture (null: none)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._loader = None
        self._asked: set[str] = set()

    @staticmethod
    def wanted(target: str) -> bool:
        return bool(target) and Path(target).suffix.lower() in PHOTO_EXT

    def request(self, target: str) -> None:
        if target in self._asked or not self.wanted(target) or not Path(target).is_file():
            return
        self._asked.add(target)
        if self._loader is None:
            from .photo_images import ImageLoader

            self._loader = ImageLoader(self, threads=2)
            self._loader.loaded.connect(lambda key, _size, _full, img: self.ready.emit(key, img))
        self._loader.request(target, Path(target), 80)

    def forget(self) -> None:
        self._asked.clear()


def thumb_size() -> QSize:
    return QSize(32, 32)


class FootTime(QWidget):
    """Beside the panel's main button: a small caption over its value ("Last checked" over
    "just now", "Started" over "22:04"), left-aligned and centred on the buttons' height."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QVBoxLayout

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.caption, self.value = QLabel(), QLabel()
        self.caption.setObjectName("FootCap")
        self.value.setObjectName("FootVal")
        lay.addStretch(1)
        for lbl in (self.caption, self.value):
            lbl.setTextFormat(Qt.PlainText)
            lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            lay.addWidget(lbl)
        lay.addStretch(1)

    def setText(self, text: str) -> None:  # noqa: N802 - "caption\nvalue", as the label it replaces
        cap, _, val = (text or "").partition("\n")
        self.caption.setText(cap)
        self.value.setText(val)
        self.value.setVisible(bool(val))

    def text(self) -> str:
        return self.caption.text() + ("\n" + self.value.text() if self.value.text() else "")


class TwoLineLabel(QLabel):
    """A label that shows at most two lines and ends the second with "…"; text() is still the
    whole text, which is also its tooltip. The status card keeps one height this way."""

    def __init__(self, text: str = "", lines: int = 2) -> None:
        super().__init__()
        self._full = ""
        self._lines = lines
        self.setTextFormat(Qt.PlainText)  # (status text names files from iCloud: never HTML)
        self.setWordWrap(False)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def text(self) -> str:  # noqa: D102 - the whole text, as set
        return self._full

    def setText(self, text: str, extra: str = "") -> None:  # noqa: N802
        self._full = text or ""
        self._extra = extra
        self._render()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._render()

    def _render(self) -> None:
        fm = QFontMetrics(self.font())
        width = max(60, self.width() or 240)
        words = self._full.replace("\n", " ").split(" ")
        lines: list[str] = []
        cur = ""
        for i, w in enumerate(words):
            nxt = f"{cur} {w}".strip()
            if fm.horizontalAdvance(nxt) <= width or not cur:
                cur = nxt
                continue
            lines.append(cur)
            cur = w
            if len(lines) == self._lines - 1:
                cur = " ".join([w] + words[i + 1:])
                break
        lines.append(fm.elidedText(cur, Qt.ElideRight, width) if len(lines) == self._lines - 1 else cur)
        shown = "\n".join(x for x in lines if x)
        super().setText(shown)
        from .safe_text import plain_tip

        clipped = shown.split() != self._full.split() or any(fm.horizontalAdvance(x) > width for x in lines)
        self.setToolTip(plain_tip(self._extra or (self._full if clipped else "")))

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self.font())
        n = max(1, super().text().count("\n") + 1) if super().text() else 1
        return QSize(super().sizeHint().width(), fm.lineSpacing() * n)


class CenteredButton(QToolButton):
    """A text button whose icon and text sit centred together (a tool button puts them at the
    left), the icon drawn in the text's colour at the text's weight: the panel's main button."""

    def paintEvent(self, _e) -> None:  # noqa: N802
        from PySide6.QtWidgets import QStyle, QStyleOptionToolButton, QStylePainter

        sp = QStylePainter(self)
        opt = QStyleOptionToolButton()
        self.initStyleOption(opt)
        opt.text = ""
        opt.icon = QIcon()
        sp.drawComplexControl(QStyle.CC_ToolButton, opt)  # the background and border from the style sheet
        colour = self.palette().buttonText().color()
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QFontMetricsF

        fm = QFontMetricsF(self.font())
        icon = 16 if self.icon_name else 0
        gap = 7 if icon else 0
        w = icon + gap + fm.horizontalAdvance(self.text())
        x = (self.width() - w) / 2
        mid = self.height() / 2
        if icon:
            from .svgicons import pixmap

            pm = pixmap(self.icon_name, colour.name(), icon, max(1.0, self.devicePixelRatioF()), stroke=1.8)
            sp.drawPixmap(QPointF(round(x), mid - icon / 2), pm)
        sp.setPen(colour)
        sp.setFont(self.font())
        # the capitals centred on the button's middle, like the icon (centring the whole line, with
        # room for descenders, put a word like "Stop" a pixel above its icon)
        sp.drawText(QPointF(round(x) + icon + gap, round(mid + fm.capHeight() / 2)), self.text())

    icon_name = ""

    def set_glyph(self, name: str) -> None:
        self.icon_name = name
        self.update()


class RowScroll(QScrollArea):
    """The panel's activity list: one wheel notch moves exactly one row, with a short glide, and it
    always stops on the edge of a row (Qt's own 60 px steps left rows cut in half and jumped by
    one and a half rows). A touchpad scrolls pixel by pixel as usual."""

    GLIDE_MS = 140

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtCore import QEasingCurve, QVariantAnimation

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(self.GLIDE_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(lambda v: self.verticalScrollBar().setValue(int(v)))
        self._target: int | None = None
        self._notches = 0

    def _row_tops(self) -> list[int]:
        box = self.widget()
        if box is None or box.layout() is None:
            return []
        lay = box.layout()
        tops = []
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is not None and w.isVisible():
                tops.append(w.y())
        return sorted(tops)

    def wheelEvent(self, e) -> None:  # noqa: N802
        bar = self.verticalScrollBar()
        if bar.maximum() <= 0:
            e.accept()  # nothing to scroll: the panel itself never moves either
            return
        if not e.pixelDelta().isNull():  # a precision touchpad: follow the fingers
            self._anim.stop()
            self._target = None
            bar.setValue(bar.value() - e.pixelDelta().y())
            e.accept()
            return
        self._notches += e.angleDelta().y()
        steps = int(self._notches / 120)
        if not steps:
            e.accept()
            return
        self._notches -= steps * 120
        start = self._target if self._target is not None and self._anim.state() else bar.value()
        tops = self._row_tops() + [bar.maximum()]
        target = start
        for _ in range(abs(steps)):
            if steps < 0:  # down: the next row's top
                target = next((y for y in tops if y > target + 1), bar.maximum())
            else:
                target = next((y for y in reversed(tops) if y < target - 1), 0)
        target = max(0, min(bar.maximum(), target))
        self._target = target
        self._anim.stop()
        self._anim.setStartValue(bar.value())
        self._anim.setEndValue(target)
        self._anim.start()
        e.accept()


class SoftCard(QFrame):
    """A rounded card drawn with smooth (antialiased) edges and a faint shadow under it. Qt's style
    sheets draw rounded backgrounds with jagged corners, so the colours come from the style sheet
    (qproperty-fill / -stroke / -shade on SoftCard) and the card paints itself."""

    def __init__(self, radius: float = 8.0) -> None:
        super().__init__()
        self.radius = radius
        self._fill = QColor(0, 0, 0, 0)
        self._stroke = QColor(0, 0, 0, 0)
        self._shade = QColor(0, 0, 0, 0)

    def _get_fill(self) -> QColor:
        return self._fill

    def _set_fill(self, c) -> None:
        self._fill = QColor(c)
        self.update()

    def _get_stroke(self) -> QColor:
        return self._stroke

    def _set_stroke(self, c) -> None:
        self._stroke = QColor(c)
        self.update()

    def _get_shade(self) -> QColor:
        return self._shade

    def _set_shade(self, c) -> None:
        self._shade = QColor(c)
        self.update()

    fill = Property(QColor, _get_fill, _set_fill)
    stroke = Property(QColor, _get_stroke, _set_stroke)
    shade = Property(QColor, _get_shade, _set_shade)

    def event(self, e) -> bool:
        done = super().event(e)
        if e.type() in (QEvent.Polish, QEvent.StyleChange):
            # the style sheet never paints it: its rounded corners are jagged, and a solid background
            # under a see-through card hid the window's material (Settings looked solid)
            self.setAttribute(Qt.WA_StyledBackground, False)
        return done

    def card_path(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -1.5), self.radius, self.radius)
        return path

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = self.card_path()
        if self._shade.alpha():  # a faint shadow along the bottom edge, outside the card only
            below = QPainterPath()
            below.addRoundedRect(path.boundingRect().translated(0, 1), self.radius, self.radius)
            p.fillPath(below.subtracted(path), self._shade)
        p.fillPath(path, self._fill)
        if self._stroke.alpha():
            p.setPen(QPen(self._stroke, 1))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)


class TintTile(QFrame):
    """A small rounded tile in a tint of a colour (the activity rows' icons), drawn smoothly."""

    def __init__(self, size: int = 32, radius: float = 9.0) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self.radius = radius
        self.colour: QColor | None = None

    def set_fill(self, colour: QColor | None) -> None:
        self.colour = colour
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        if self.colour is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.colour)
        p.drawRoundedRect(QRectF(self.rect()), self.radius, self.radius)
