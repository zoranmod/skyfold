"""The shared material, content layer and search field for the two library windows."""

from PySide6.QtCore import QEvent
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QStyle, QStyleOption

from .backdrop import glass_colours
from .dialogs import _Dialog
from .panel_parts import SoftCard
from .settings_parts import IconLabel


def count_font(base: QFont) -> QFont:
    font = QFont(base)
    font.setFamilies(["Cascadia Mono", "Consolas"])
    font.setPixelSize(12)
    return font


class LibraryDialog(_Dialog):
    GLASS = True

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

    def _veil(self) -> str:
        return glass_colours(self.c, self._dark())["tint"]  # (the same veil as every other window)

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
               f"QPushButton#Primary {{ background:{c['btn']}; border-color:{c['btn']}; }}" if material else "") +
            "QSplitter, QStackedWidget, QAbstractScrollArea, QAbstractScrollArea > QWidget"
            " { background:transparent; border:none; }"
        )


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
        self.setFixedHeight(34)
        self.setFocusProxy(edit)
        edit.installEventFilter(self)
        self.apply_palette(palette)

    def apply_palette(self, palette: dict) -> None:
        self.focus_color = QColor(palette["focus_ring"])
        self.icon.apply_palette(palette)
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
