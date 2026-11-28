"""The shared material, content layer and search field for the two library windows."""

import math

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QTransform
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLineEdit, QStyle, QStyleOption, QWidget

from .backdrop import glass_colours
from .dialogs import _Dialog
from .panel_parts import SoftCard
from .pills import _ui
from .settings_parts import IconLabel
from .theme import focus_rules


def count_font(base: QFont) -> QFont:
    font = QFont(base)
    font.setFamilies(["Cascadia Mono", "Consolas"])
    font.setPixelSize(12)
    return font


class LibraryDialog(_Dialog):
    GLASS = True

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._fit_maximized()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._fit_maximized()

    def _fit_maximized(self) -> None:
        """Maximized, Windows makes its title bar thinner and the window reaches the screen's edges:
        the content layer then got right up under the caption buttons. It keeps a gap from the title
        bar there, as in a normal window."""
        lay = self.layout()
        if lay is None:
            return
        if not hasattr(self, "_base_margins"):
            self._base_margins = lay.contentsMargins()
        m = self._base_margins
        if self.isMaximized():
            lay.setContentsMargins(m.left(), max(m.top(), 10), max(m.right(), 10), max(m.bottom(), 10))
        else:
            lay.setContentsMargins(m)

    def _glass_style(self) -> None:
        # _Dialog owns the native backdrop. These windows have their own pane and controls.
        # Their stylesheet is also rebuilt by apply_palette, including for Notes' local theme.
        pass

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.glass:
            # A translucent top-level dialog does not automatically draw its styled background.
            # Even a gap between children must receive pointer events on Windows.
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(0, 0, 0, 1))
            option = QStyleOption()
            option.initFrom(self)
            self.style().drawPrimitive(QStyle.PE_Widget, option, p, self)

    def frame_style(self) -> str:
        c = self.c
        material = self.glass  # (OLED too: the user chose the see-through look for every theme)
        g = glass_colours(c, self._dark()) if material else {}
        # The root tint has nonzero alpha everywhere, including under splitter handles.
        return (
            # a light veil over Windows' acrylic (which brings its own tint): what is behind shows
            # through as in the design; the old 150/255 veil made the windows look nearly solid
            f"QDialog#Root {{ background:{'transparent' if material else c['bg']}; }}"  # (_Dialog paints the veil)
            f"SoftCard#LibraryPane {{ qproperty-fill:{g.get('card', c['surface'])};"
            f" qproperty-stroke:{g.get('stroke', c['border'])}; qproperty-shade:transparent; }}"
            f"SoftCard#LibrarySearch {{ qproperty-fill:{g.get('control', c['surface2'])};"
            f" qproperty-stroke:{g.get('stroke2', c['border'])}; qproperty-shade:transparent; }}"
            "SoftCard#LibrarySearch QLineEdit { background:transparent; border:none; padding:0; min-height:0; }"
            "QSplitter::handle { background:transparent; }"
            # over the material, buttons and lists are light layers too (as in Settings)
            + (f"QPushButton, QComboBox {{ background:{g['control']}; border-color:{g['stroke2']}; }}"
               f"QPushButton:hover, QComboBox:hover {{ background:{g['hover']}; }}"
               f"QPushButton#Primary {{ background:{c['btn']}; border-color:{c['btn']}; }}"
               # (the keyboard focus after them, in its solid colours: see focus_rules)
               + focus_rules(c) if material else "") +
            "QSplitter, QStackedWidget, QAbstractScrollArea, QAbstractScrollArea > QWidget"
            " { background:transparent; border:none; }"
        )


class _ClearButton(QAbstractButton):
    """The search field's clear button: a thin "×" in the app's line icons, quiet at rest, the text's
    colour on a soft round layer under the mouse."""

    SIZE = 22

    def __init__(self, palette: dict) -> None:
        super().__init__()
        from ..i18n import t

        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)  # (Esc and the keyboard stay in the field)
        self.setAttribute(Qt.WA_Hover)
        self.setToolTip(t("Clear search"))
        self.setAccessibleName(t("Clear search"))
        self.apply_palette(palette)

    def apply_palette(self, palette: dict) -> None:
        self.c = palette
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        from PySide6.QtCore import QRectF

        from .svgicons import pixmap

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hot = self.underMouse() or self.isDown()
        if hot:
            layer = QColor(self.c["text"])
            layer.setAlpha(28 if not self.isDown() else 40)
            p.setPen(Qt.NoPen)
            p.setBrush(layer)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 5, 5)
        dpr = max(1.0, self.devicePixelRatioF())
        pm = pixmap("close", self.c["text"] if hot else self.c["muted"], 16, dpr, stroke=1.6)
        p.drawPixmap(int((self.width() - 16) / 2), int((self.height() - 16) / 2), pm)
        p.end()


class SearchField(SoftCard):
    """Keep the native editable field, shortcuts, accessible name and clear action."""
    def __init__(self, edit: QLineEdit, palette: dict) -> None:
        super().__init__(6)
        self.setObjectName("LibrarySearch")
        self.edit = edit
        self.icon = IconLabel("search", palette, size=16)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 1, 8, 1)
        layout.setSpacing(8)
        layout.addWidget(self.icon)
        layout.addWidget(edit, 1)
        # The clear button is the app's own, in the row with the magnifier and the text, so it sits
        # in the middle like them. (Qt's built-in one was the style's heavy "×", matched nothing
        # else, and hung 7 px below the middle of the field.) Shown only while there is text.
        self.clear_button = None
        if edit.isClearButtonEnabled():
            edit.setClearButtonEnabled(False)
            self.clear_button = _ClearButton(palette)
            self.clear_button.clicked.connect(lambda: (edit.clear(), edit.setFocus()))
            layout.addWidget(self.clear_button, 0, Qt.AlignVCenter)
            edit.textChanged.connect(lambda text: self.clear_button.setVisible(bool(text)))
            self.clear_button.setVisible(bool(edit.text()))
        self.setFixedHeight(34)
        self.setFocusProxy(edit)
        edit.installEventFilter(self)
        self.apply_palette(palette)

    def apply_palette(self, palette: dict) -> None:
        self.focus_color = QColor(palette["focus_ring"])
        self.icon.apply_palette(palette)
        if self.clear_button is not None:
            self.clear_button.apply_palette(palette)
        self.update()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.FocusIn, QEvent.FocusOut):
            self.update()
        return super().eventFilter(obj, event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.edit.hasFocus():
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setClipPath(self.card_path())
            p.fillRect(1, self.height() - 3, self.width() - 2, 2, self.focus_color)


class PaneCorners(QObject):
    """The content pane's rounded corners over what reaches its edges (photos, a note's pictures, a
    scroll bar): a child widget can't be clipped to a rounded shape, so the four corners are drawn
    again on top of the pane's children. What lies outside the pane or on its hairline is taken out
    and the window as it looks without it is put back: its tint, the pane's own layer and hairline.
    Both along the same smooth (antialiased) edge, so the hairline runs round the corner unbroken and
    the material still shows through."""

    def __init__(self, pane: SoftCard, size: int = 12) -> None:
        super().__init__(pane)
        self.pane = pane
        self.size = size  # (the pane's largest corner radius and a little more)
        self.parts = [_Corner(pane) for _ in range(4)]
        pane.installEventFilter(self)
        self.place()

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() in (QEvent.Resize, QEvent.Show):
            self.place()
        return False

    def place(self) -> None:
        w, h = self.pane.width(), self.pane.height()
        s = self.size
        for k, part in enumerate(self.parts):
            part.setGeometry(0 if k in (0, 2) else w - s, 0 if k < 2 else h - s, s, s)
            part.raise_()

    def refresh(self) -> None:
        for part in self.parts:
            part.update()


class _Corner(QWidget):
    def __init__(self, pane: SoftCard) -> None:
        super().__init__(pane)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._key = None
        self._cut = self._under = QImage()

    def paintEvent(self, _e) -> None:  # noqa: N802
        pane = self.parentWidget()
        p = QPainter(self)
        # Where this corner lands on the pixels it is drawn to, and how many of them a point is
        # (the screen's scaling; a capture may have its own). At 125 % or 150 % a corner can start
        # between two pixels: the two pictures are made on the pixel grid itself, so they stay sharp.
        to_device = p.deviceTransform()
        dpr = to_device.m11()
        fx, fy = to_device.dx() % 1.0, to_device.dy() % 1.0
        if fx > 1 - 1e-6:  # (a whole pixel, but for rounding)
            fx = 0.0
        if fy > 1 - 1e-6:
            fy = 0.0
        tint = _ui(self)["tint"]
        key = (self.geometry(), pane.size(), dpr, fx, fy, tint.rgba(), pane.fill.rgba(), pane.stroke.rgba(),
               pane.shade.rgba())
        if key != self._key:  # (kept: while photos scroll, a corner is two small pictures to draw)
            self._key = key
            self._cut, self._under = self._pictures(pane, dpr, fx, fy, tint)
        origin = QPointF(-fx / dpr, -fy / dpr)
        p.setCompositionMode(QPainter.CompositionMode_DestinationOut)
        p.drawImage(origin, self._cut)
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        p.drawImage(origin, self._under)

    def _pictures(self, pane: SoftCard, dpr: float, fx: float, fy: float, tint: QColor) -> tuple[QImage, QImage]:
        """cut: how much of each pixel is taken out (everything but the inside of the pane's
        hairline). under: the window without the pane's children, as much of it as was taken out."""
        size = (math.ceil(self.width() * dpr + fx), math.ceil(self.height() * dpr + fy))
        # the pane's coordinates to this picture's pixels (which are the screen's own)
        to_pixels = (QTransform.fromTranslate(-self.x(), -self.y()) * QTransform.fromScale(dpr, dpr)
                     * QTransform.fromTranslate(fx, fy))

        def canvas() -> tuple[QImage, QPainter]:
            img = QImage(*size, QImage.Format_ARGB32_Premultiplied)
            img.fill(Qt.transparent)
            q = QPainter(img)
            q.setRenderHint(QPainter.Antialiasing)
            return img, q

        # The edge to cut along: the inside of the hairline. Its straight sides are moved onto whole
        # screen pixels (less than half a pixel), where Qt ends the pane's children: at 125 % and
        # 150 % a side between two pixels would take a faint line off the photos beside the corner.
        card = to_pixels.map(pane.card_path())
        box, half = card.boundingRect(), dpr / 2
        left, right = math.floor(box.left() + half + 0.5), math.floor(box.right() - half + 0.5)
        top, bottom = math.floor(box.top() + half + 0.5), math.floor(box.bottom() - half + 0.5)
        fit = (QTransform.fromTranslate(-box.left(), -box.top())
               * QTransform.fromScale((right - left + dpr) / box.width(), (bottom - top + dpr) / box.height())
               * QTransform.fromTranslate(left - half, top - half))
        edge = fit.map(card)
        solid = QColor(0, 0, 0)
        cut, q = canvas()
        q.fillRect(cut.rect(), solid)
        q.setCompositionMode(QPainter.CompositionMode_DestinationOut)
        q.fillPath(edge, solid)  # what is inside the pane stays...
        q.setCompositionMode(QPainter.CompositionMode_SourceOver)
        q.strokePath(edge, QPen(solid, dpr))  # ...up to its hairline, which the layout leaves free too
        q.end()
        under, q = canvas()
        # The window's veil lies over the whole window, not only under the pane: every pixel of this
        # picture gets all of it. Filled as the pane's rectangle, an edge of the pane between two
        # pixels (125 %, 150 %, 175 %: a maximized window, most sizes of a normal one) left the last
        # pixel along that edge with only part of the veil: a thin line, lighter than everything
        # around it, as long as this corner and sticking out past the curve.
        q.fillRect(under.rect(), tint)
        q.setTransform(to_pixels)
        pane.paint_card(q)
        q.resetTransform()
        q.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        q.drawImage(0, 0, cut)
        q.end()
        cut.setDevicePixelRatio(dpr)
        under.setDevicePixelRatio(dpr)
        return cut, under
