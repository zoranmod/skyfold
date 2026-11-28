"""Every question and message of the app is one window in the 1.1 design (dialogs.MessageDialog):
on glass, a tile with a line icon, a clear title, a quiet text, the answers in the footer. An answer
that deletes is red and never the one Enter picks by itself. Made-up names only."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QWidget

from skyfold.ui import backdrop, dialogs, theme
from skyfold.ui.photos_window import PhotosWindow

SOURCE = Path(__file__).resolve().parent.parent / "skyfold"


@pytest.fixture
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyleSheet(theme.stylesheet(theme.LIGHT))
    yield a
    a.setStyleSheet("")


class Host(QWidget):
    """Stands in for the Photos window: only what its delete question needs."""

    _deleting = False

    def __init__(self, to_cloud: bool = True) -> None:
        super().__init__()
        self.c = theme.LIGHT
        self.to_cloud = to_cloud
        self.asked_source = 0

    def settings(self):
        return SimpleNamespace(delete_pc_to_cloud=self.to_cloud)

    def message(self, *_a) -> None:
        pass

    def source(self):
        self.asked_source += 1  # (reached only after "Delete" was chosen)
        return None


def photo(n: int, video: bool = False, name: str | None = None):
    return SimpleNamespace(name=name or f"IMG_{n:04d}." + ("MOV" if video else "HEIC"), asset_id=str(n), is_video=video)


def answering(app, act):
    """Runs act(box) on the message once it is open (never leaves it open: the run would hang)."""
    seen = []

    def look():
        box = app.activeModalWidget()
        if box is None:
            return
        try:
            seen.append(act(box))
        finally:
            if box.isVisible():
                box.reject()

    QTimer.singleShot(0, look)
    return seen


def test_the_delete_question_is_the_glass_message_with_a_red_delete_and_cancel_focused(app, monkeypatch):
    monkeypatch.setattr(backdrop, "glass_supported", lambda: True)
    monkeypatch.setattr(backdrop, "apply_backdrop", lambda *a, **k: True)
    host = Host()

    def act(box):
        assert isinstance(box, dialogs.MessageDialog) and box.GLASS and box.glass
        assert box.testAttribute(Qt.WA_TranslucentBackground)
        assert 420 <= box.width() <= 460
        assert box.kind == "delete" and not box.tile.pixmap().isNull()
        assert box.heading.text() == "Delete this photo?"
        assert box.detail.text().replace("\u200b", "") == "IMG_0001.HEIC"
        assert "Recycle Bin" in box.text.text() and "30 days" in box.text.text()
        delete, cancel = box.button("delete"), box.button("cancel")
        assert delete.text() == "Delete" and cancel.text() == "Cancel"
        red = theme.accent_fill(theme.LIGHT["stop"], 0.80)
        assert red in delete.styleSheet() and theme.LIGHT["stop_text"] in delete.styleSheet()
        assert cancel.isDefault() and not delete.isDefault()
        assert box.focusWidget() is cancel
        assert delete.x() > cancel.x()  # the answers on the right, in the footer band
        assert box.findChild(QWidget, "Footer").isAncestorOf(delete)
        assert box.accessibleName() == "Delete this photo?" and "IMG_0001.HEIC" in box.accessibleDescription()
        return True

    seen = answering(app, act)
    PhotosWindow.delete_items(host, [photo(1)])
    assert seen == [True] and host.asked_source == 0  # closed without an answer: nothing deleted


@pytest.mark.parametrize("keys, deleted", [
    ((Qt.Key_Escape,), False),
    ((Qt.Key_Return,), False),  # Enter picks the focused answer: Cancel
    ((Qt.Key_Tab, Qt.Key_Return), True),  # ... and Delete only once the focus is on it
])
def test_escape_cancels_and_enter_presses_only_the_focused_answer(app, keys, deleted):
    host = Host()

    def act(box):
        box.activateWindow()
        app.processEvents()
        for key in keys:
            QTest.keyClick(app.focusWidget() or box, key)
        return box.answer

    seen = answering(app, act)
    PhotosWindow.delete_items(host, [photo(1)])
    assert seen == ["delete" if deleted else "cancel"]
    assert host.asked_source == (1 if deleted else 0)


@pytest.mark.parametrize("items, heading, word", [
    ([photo(1)], "Delete this photo?", "It goes"),
    ([photo(1, video=True)], "Delete this video?", "It goes"),
    ([photo(1), photo(2), photo(3)], "Delete 3 photos?", "They go"),
    ([photo(1, video=True), photo(2, video=True)], "Delete 2 videos?", "They go"),
    ([photo(1), photo(2, video=True)], "Delete 2 items?", "They go"),
])
def test_the_title_counts_what_is_deleted(app, items, heading, word):
    for to_cloud in (True, False):
        seen = answering(app, lambda box: (box.heading.text(), box.text.text(), box.detail is None))
        PhotosWindow.delete_items(Host(to_cloud), items)
        (got, text, no_name), = seen
        assert got == heading and text.startswith(word)
        assert ("Recently Deleted" in text) == to_cloud
        assert no_name == (len(items) > 1)  # one photo is named, several are counted


def test_a_name_from_icloud_is_never_read_as_html(app):
    evil = "<img src='\\\\host\\share\\x.png'><b>IMG_0001</b>.HEIC"

    def act(box):
        labels = [lb for lb in box.findChildren(QLabel) if lb.text()]
        assert labels and all(lb.textFormat() == Qt.PlainText for lb in labels)
        assert box.detail.text().replace("\u200b", "") == evil  # shown as it is, letter for letter
        return True

    seen = answering(app, act)
    PhotosWindow.delete_items(Host(), [photo(1, name=evil)])
    assert seen == [True]


def test_a_long_path_wraps_inside_the_window(app):
    path = "C:\\Users\\you\\Pictures\\iCloud Photos\\2026\\11\\" + "A_made_up_name_without_spaces_" * 4 + "IMG_0001.HEIC"

    def act(box):
        app.processEvents()
        assert box.width() == dialogs.MessageDialog.WIDTH
        assert box.text.width() <= box.width() - 48 and box.text.height() > box.text.fontMetrics().height() * 2
        assert box.accessibleDescription() == "Could not open " + path  # read out without the hidden breaks
        return True

    seen = answering(app, act)
    dialogs.warn(None, "Skyfold", "Could not open " + path)
    assert seen == [True]


def test_sign_out_is_a_red_answer_and_cancel_is_the_default(app):
    signed_out = []
    host = Host()
    host.signOut = SimpleNamespace(emit=lambda: signed_out.append(1))
    host.reject = lambda: None

    def act(box):
        assert isinstance(box, dialogs.MessageDialog)
        assert box.heading.text() == "Sign out of iCloud?" and box.text.text() == "Photos on this PC stay."
        yes, no = box.button("yes"), box.button("no")
        assert (yes.text(), no.text()) == ("Sign out", "Cancel")
        assert yes.objectName() == "Destructive" and no.isDefault() and box.focusWidget() is no
        QTest.keyClick(box, Qt.Key_Escape)
        return True

    seen = answering(app, act)
    dialogs.SettingsDialog._sign_out(host)
    assert seen == [True] and not signed_out
    seen = answering(app, lambda box: box.button("yes").click())
    dialogs.SettingsDialog._sign_out(host)
    assert signed_out == [1]


def test_messages_have_one_ok_and_questions_keep_their_answers(app):
    for fn, kind in ((dialogs.inform, "info"), (dialogs.warn, "warning")):
        def act(box, kind=kind):
            buttons = box.findChildren(QPushButton)
            assert [b.text() for b in buttons] == ["OK"] and buttons[0].objectName() == "Primary"
            assert buttons[0].isDefault() and box.kind == kind and box.heading.text() == "iCloud Drive"
            QTest.keyClick(box, Qt.Key_Escape)
            return box.answer

        seen = answering(app, act)
        assert fn(None, "iCloud Drive", "Text") is None
        assert seen == ["ok"]
    # the app's name as the title says nothing: the text is the message, with no title over it
    seen = answering(app, lambda box: box.heading is None and box.windowTitle() == "Skyfold")
    dialogs.inform(None, "Skyfold", "Text")
    assert seen == [True]
    seen = answering(app, lambda box: box.button("yes").click())
    assert dialogs.ask(None, "T", "Question?") == QMessageBox.Yes
    seen = answering(app, lambda box: QTest.keyClick(box, Qt.Key_Escape))
    assert dialogs.ask(None, "T", "Question?") == QMessageBox.No


def test_a_message_belongs_to_the_window_it_is_about(app):
    host = Host()
    host.show()
    try:
        seen = answering(app, lambda box: box.parentWidget() is host and bool(box.windowFlags() & Qt.Dialog))
        dialogs.inform(host, "T", "Text")
        assert seen == [True]
    finally:
        host.close()
    seen = answering(app, lambda box: box.parentWidget() is None)  # a hidden window owns nothing
    dialogs.inform(host, "T", "Text")
    assert seen == [True]


def test_a_message_at_start_up_brings_the_design_with_it(app):
    app.setStyleSheet("")  # (the app's style sheet is set later than the first possible message)
    seen = answering(app, lambda box: "QPushButton#Primary" in box.styleSheet())
    dialogs.warn(None, "Skyfold", "The system tray is not available.", kind="error")
    assert seen == [True]


@pytest.mark.parametrize("mode", ["light", "dark", "oled"])
def test_every_kind_draws_its_tile_in_every_theme(app, mode):
    c = theme.palette(mode)
    for kind in dialogs._KINDS:
        box = dialogs.MessageDialog(None, "T", "Text", heading="Title", kind=kind, palette=c,
                                    buttons=[("cancel", "Cancel", "cancel"), ("go", "Go", "danger")])
        try:
            image = box.tile.pixmap().toImage()
            assert image.pixelColor(image.width() // 2, image.height() // 6).alpha() > 0  # the tinted tile
            assert c["stop_text"] in box.button("go").styleSheet()
            box.apply_palette(theme.DARK)  # the theme changed while it was open
            assert theme.DARK["stop_text"] in box.button("go").styleSheet()
        finally:
            box.deleteLater()


def test_no_plain_qt_message_box_is_left_in_the_app():
    """Every message goes through the app's own window: nothing builds a QMessageBox (the name stays
    only for the Yes / No values ask() answers with)."""
    made = re.compile(r"QMessageBox\s*\(|QMessageBox\.(critical|warning|information|question|about|aboutQt)\s*\(|"
                      r"QErrorMessage|QInputDialog")
    found = [f"{p.name}:{i}" for p in SOURCE.rglob("*.py")
             for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if made.search(line)]
    assert not found, found
