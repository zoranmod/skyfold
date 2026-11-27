"""The notifications of this session, so one that went away can be found again in the tray panel.

* ``Notice`` / ``NoticeLog`` - the list itself (newest first, at most 30, kept only while Skyfold
  runs) and how many came since the panel last showed it.
* ``BellButton`` - the bell in the panel's header, with the number of new ones on a small badge.
* ``NoticesPage`` - the panel's page that lists them, with the buttons each notification had.

What a button does is asked of the app each time (``Resolver``): a button that no longer applies
(the update is installed, the deletions were reviewed) is not shown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from ..i18n import t
from ..worker import fmt_time
from .glow import GlowTile
from .icons import glyph
from .theme import service_color

LIMIT = 30  # kept while Skyfold runs; the oldest go first

Resolver = Callable[[str], "tuple[str, Callable[[], None], bool] | None"]  # key -> (label, action, applies now)


@dataclass
class Notice:
    title: str
    text: str
    icon: str = "cloud"
    tone: str | None = None
    actions: tuple[str, ...] = ()  # keys the app knows (see Controller._notice_action)
    ts: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))


class NoticeLog:
    """This session's notifications, newest first."""

    def __init__(self) -> None:
        self.items: list[Notice] = []
        self.unseen = 0  # came since the list was last shown
        self._listeners: list[Callable[[], None]] = []

    def on_change(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def _changed(self) -> None:
        for fn in list(self._listeners):
            fn()

    def add(self, notice: Notice) -> None:
        self.items.insert(0, notice)
        del self.items[LIMIT:]
        self.unseen = min(self.unseen + 1, LIMIT)
        self._changed()

    def mark_seen(self) -> None:
        if self.unseen:
            self.unseen = 0
            self._changed()

    def clear(self) -> None:
        self.items.clear()
        self.unseen = 0
        self._changed()


def notice_color(c: dict[str, str], icon: str, tone: str | None) -> str:
    """The colour of a notification: its tone (warn, err), else its service, else the accent."""
    if tone in ("warn", "err"):
        return c[tone]
    return service_color(c, icon) or c["accent_fg"]


class BellButton(QToolButton):
    """The bell beside the Settings gear; a small accent badge counts the new notifications."""

    def __init__(self, palette: dict[str, str]) -> None:
        super().__init__()
        self.c = palette
        self.count = 0
        self.setIconSize(QSize(18, 18))
        self.setCursor(Qt.PointingHandCursor)
        self.set_count(0)

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        self.setIcon(glyph("bell", palette["muted"]))
        self.update()

    def set_count(self, n: int) -> None:
        self.count = max(0, int(n))
        name = t("Notifications")
        if self.count:
            name = t("Notifications, {n} new", n=self.count)
        self.setToolTip(name)
        self.setAccessibleName(name)
        self.apply_palette(self.c)

    def badge_text(self) -> str:
        return "" if not self.count else ("9+" if self.count > 9 else str(self.count))

    def paintEvent(self, e) -> None:  # noqa: N802 - Qt naming
        super().paintEvent(e)
        text = self.badge_text()
        if not text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont(self.font())
        f.setPixelSize(9)
        f.setWeight(QFont.Bold)
        p.setFont(f)
        w = max(14.0, p.fontMetrics().horizontalAdvance(text) + 7.0)
        r = QRectF(self.width() / 2 + 1, self.height() / 2 - 13, w, 14)
        p.setPen(QColor(self.c["surface"]))  # a ring in the panel's colour keeps it apart from the bell
        p.setBrush(QColor(self.c["accent"]))
        p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 8, 8)
        p.setPen(QColor(self.c["accent_text"]))
        p.drawText(r, Qt.AlignCenter, text)
        p.end()


def short_time(ts: str) -> str:
    """The time on a row, short as in the panel's activity: "5 min ago", today's "14:32", else the date."""
    try:
        dt = datetime.fromisoformat(ts).astimezone()
    except (TypeError, ValueError):
        return fmt_time(ts)
    now = datetime.now().astimezone()
    if (now - dt).total_seconds() < 3600 or dt.date() != now.date():
        if dt.date() != now.date():
            from PySide6.QtCore import QDate, QLocale

            return QLocale.system().toString(QDate(dt.year, dt.month, dt.day), QLocale.ShortFormat)
        return fmt_time(ts)
    from PySide6.QtCore import QLocale, QTime

    return QLocale.system().toString(QTime(dt.hour, dt.minute), QLocale.ShortFormat)


def _is_today(ts: str) -> bool:
    try:
        return datetime.fromisoformat(ts).astimezone().date() == datetime.now().astimezone().date()
    except (TypeError, ValueError):
        return True


class NoticeRow(QFrame):
    """One notification in the list, as the panel's activity rows: a 32 px tile tinted with its colour,
    the title (600), the text (12.5 px), the short time in Cascadia Mono on the right, and the
    buttons that still apply as quiet links."""

    def __init__(self, notice: Notice, palette: dict[str, str], resolve: Resolver) -> None:
        super().__init__()
        self.notice = notice
        self.setObjectName("NoticeRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 7, 4, 7)
        lay.setSpacing(12)
        self.tile = GlowTile(notice.icon, palette, notice_color(palette, notice.icon, notice.tone),
                             size=32, icon=16, radius=9)
        lay.addWidget(self.tile, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.title = QLabel(notice.title)
        self.title.setObjectName("RowTitle")
        self.title.setWordWrap(True)
        self.when = QLabel(short_time(notice.ts))
        self.when.setObjectName("ActWhen")
        self.when.setToolTip(fmt_time(notice.ts))
        top.addWidget(self.title, 1)
        top.addWidget(self.when, 0, Qt.AlignTop)
        col.addLayout(top)
        self.text = QLabel(notice.text)
        self.text.setObjectName("Muted")
        self.text.setWordWrap(True)
        for lbl in (self.title, self.text, self.when):
            lbl.setTextFormat(Qt.PlainText)  # file names can contain anything: never read as markup
            lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            lbl.setMinimumWidth(0)
        self.when.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        col.addWidget(self.text)
        self.buttons: list[QPushButton] = []
        row = QHBoxLayout()
        row.setContentsMargins(0, 3, 0, 0)
        row.setSpacing(14)
        for key in notice.actions:
            found = resolve(key)
            if not found or not found[2]:  # no longer applies: not offered
                continue
            label, fn, _ok = found
            b = QPushButton(label)
            b.setObjectName("ListLink")
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("min-height: 22px; max-height: 22px;")  # a compact row of links
            b.setAccessibleName(f"{label}: {notice.title}")
            b.clicked.connect(fn)
            row.addWidget(b)
            self.buttons.append(b)
        if self.buttons:
            row.addStretch(1)
            col.addLayout(row)
        lay.addLayout(col, 1)
        self.setAccessibleName(", ".join((notice.title, notice.text, fmt_time(notice.ts))))

    def refresh(self) -> None:
        self.when.setText(short_time(self.notice.ts))
        self.when.setToolTip(fmt_time(self.notice.ts))


class NoticesPage(QWidget):
    """The panel's notifications page: Back, the list (newest first, under "Today" and "Earlier")
    and Clear."""

    back = Signal()

    def __init__(self, log: NoticeLog, palette: dict[str, str], resolve: Resolver) -> None:
        super().__init__()
        self.setObjectName("Root")
        self.setFocusPolicy(Qt.StrongFocus)  # takes the keyboard when shown: Tab reaches Back and the buttons
        self.setAccessibleName(t("Notifications"))
        self.log = log
        self.c = palette
        self.resolve = resolve
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 12, 16, 10)
        root.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(4)
        self.back_button = QToolButton()
        self.back_button.setIconSize(QSize(18, 18))
        self.back_button.setToolTip(t("Back"))
        self.back_button.setAccessibleName(t("Back"))
        self.back_button.setCursor(Qt.PointingHandCursor)
        self.back_button.clicked.connect(self.back)
        title = QLabel(t("Notifications"))
        title.setObjectName("PanelTitle")
        self.clear_button = QPushButton(t("Clear"))
        self.clear_button.setObjectName("ListLink")
        self.clear_button.setCursor(Qt.PointingHandCursor)
        self.clear_button.setToolTip(t("Empty this list. Nothing else changes."))
        self.clear_button.setAccessibleName(t("Clear all"))
        self.clear_button.clicked.connect(log.clear)
        head.addWidget(self.back_button)
        head.addWidget(title, 1)
        head.addWidget(self.clear_button)
        root.addLayout(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollBar:vertical { width: 6px; margin: 0; }")  # slim, as the panel's list
        box = QWidget()
        box.setObjectName("Root")
        self._rows = QVBoxLayout(box)
        self._rows.setContentsMargins(6, 0, 0, 0)
        self._rows.setSpacing(0)
        self._rows.addStretch(1)
        self.scroll.setWidget(box)
        root.addWidget(self.scroll, 1)
        self.empty = QLabel(t("No notifications yet. The ones Skyfold shows while it runs are kept here."))
        self.empty.setObjectName("Muted")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignCenter)
        root.addWidget(self.empty, 1)
        log.on_change(self._on_change)
        self.apply_palette(palette)

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.c = palette
        self.back_button.setIcon(glyph("back", palette["muted"]))
        if self.isVisible():
            self.rebuild()

    def rows(self) -> list[NoticeRow]:
        return [w for w in self._widgets() if isinstance(w, NoticeRow)]

    def _widgets(self) -> list[QWidget]:
        return [self._rows.itemAt(i).widget() for i in range(self._rows.count() - 1)]

    def rebuild(self) -> None:
        """Rows for what the list holds now, with the buttons that still apply, under "Today" and
        "Earlier" (the group labels, as the panel's "Recent activity")."""
        for w in self._widgets():
            self._rows.removeWidget(w)
            w.hide()  # gone at once (deleteLater alone leaves it painted until the event loop runs)
            w.deleteLater()
        group = None
        for notice in self.log.items:
            today = _is_today(notice.ts)
            if today != group:
                group = today
                head = QLabel(t("Today") if today else t("Earlier"))
                head.setObjectName("ListHead")
                head.setContentsMargins(4, 8 if self._rows.count() > 1 else 2, 0, 4)
                self._rows.insertWidget(self._rows.count() - 1, head)
            row = NoticeRow(notice, self.c, self.resolve)
            for b in row.buttons:
                b.clicked.connect(self.back)  # (the action usually opens a window; the panel goes home)
            self._rows.insertWidget(self._rows.count() - 1, row)
        has = bool(self.log.items)
        self.scroll.setVisible(has)
        self.empty.setVisible(not has)
        self.clear_button.setEnabled(has)

    def _on_change(self) -> None:
        if self.isVisible():
            self.rebuild()

    def showEvent(self, e) -> None:  # noqa: N802 - Qt naming
        self.rebuild()
        super().showEvent(e)
