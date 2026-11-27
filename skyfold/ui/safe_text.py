"""Text from iCloud (note titles, file, photo, album and device names) shown safely.

Qt guesses whether a label's, tooltip's or message box's text is HTML, and shows HTML with its
images: a note titled <img src="\\\\host\\share\\a.png"> would make Windows connect to that host
over the network (and offer the user's Windows sign-in to it) as soon as the title is shown. Text
that can come from iCloud is therefore always shown as plain text.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel


def plain_tip(text: str) -> str:
    """A tooltip that shows the text exactly as it is (never read as HTML)."""
    if not text:
        return ""
    return "<qt>" + html.escape(text).replace("\n", "<br>") + "</qt>"


def plain(*labels: QLabel) -> None:
    """These labels never read their text as HTML."""
    for label in labels:
        label.setTextFormat(Qt.PlainText)
