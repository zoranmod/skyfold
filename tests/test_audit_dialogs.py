"""Audit of Skyfold 1.1, the windows part: the Drive folder choice with an incomplete list from iCloud,
a subfolder that couldn't be read, the deletion question (every item shown, always fits the screen),
screen-reader names and roles in first run and on the help cards, Find My after a failed refresh,
and the real settings folder in About. Made-up data only; nothing is read from iCloud."""

from __future__ import annotations

import gc
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAccessible, QAccessibleActionInterface
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QPushButton, QVBoxLayout, QWidget

from skyfold import config, drive_remote, i18n
from skyfold.config import Settings
from skyfold.ui import dialogs, theme


@pytest.fixture
def app():
    a = QApplication.instance() or QApplication([])
    yield a


# iCloud Drive folders: an incomplete list ---------------------------------------------------
def _folder(path: str) -> SimpleNamespace:
    return SimpleNamespace(path=path)


def _drive_dialog(initial):
    return dialogs.DriveFoldersDialog(initial, theme.LIGHT, lambda: None)


def _top(d, name: str):
    return next(d.tree.topLevelItem(i) for i in range(d.tree.topLevelItemCount())
                if d.tree.topLevelItem(i).text(0) == name)


def test_an_incomplete_top_list_never_turns_a_choice_into_all_folders(app):
    d = _drive_dialog(["Garden"])
    try:
        d.add_children(None, [_folder("Garden")], incomplete=True)  # iCloud said 2 folders, named 1
        assert _top(d, "Garden").checkState(0) == Qt.Checked
        assert d.ok.isEnabled()
        assert d.selection() == ["Garden"]  # never None (= all, also future folders)
        assert d.info.text() == dialogs.t(
            "iCloud didn't list every folder. Folders that aren't shown keep their current setting.")
    finally:
        d.close()


def test_a_complete_list_keeps_todays_behaviour(app):
    d = _drive_dialog(["Garden"])
    try:
        d.add_children(None, [_folder("Archive"), _folder("Garden")])
        assert d.selection() == ["Garden"]
        _top(d, "Archive").setCheckState(0, Qt.Checked)
        assert d.selection() is None  # every folder ticked: all of iCloud Drive
        assert d.info.text() == dialogs.t("Open a folder to choose only some of the folders inside it.")
    finally:
        d.close()


def test_an_incomplete_top_list_keeps_chosen_folders_it_did_not_show(app):
    d = _drive_dialog(["Garden", "Orchard/Pears"])
    try:
        d.add_children(None, [_folder("Archive"), _folder("Garden")], incomplete=True)
        assert d.selection() == ["Garden", "Orchard/Pears"]
        _top(d, "Archive").setCheckState(0, Qt.Checked)  # everything shown ticked: still not "all"
        assert d.selection() == ["Archive", "Garden", "Orchard/Pears"]
        _top(d, "Garden").setCheckState(0, Qt.Unchecked)
        assert d.selection() == ["Archive", "Orchard/Pears"]
    finally:
        d.close()


def test_all_folders_with_an_incomplete_list_cannot_be_narrowed_blindly(app):
    d = _drive_dialog(None)  # all of iCloud Drive now
    try:
        d.add_children(None, [_folder("Archive"), _folder("Garden")], incomplete=True)
        assert d.ok.isEnabled() and d.selection() is None  # unchanged: still all
        _top(d, "Archive").setCheckState(0, Qt.Unchecked)
        # a list of chosen folders would also drop the folders iCloud didn't name
        assert not d.ok.isEnabled()
        assert "can't be saved now" in d.info.text()
        _top(d, "Archive").setCheckState(0, Qt.Checked)
        assert d.ok.isEnabled() and d.selection() is None
    finally:
        d.close()


def test_an_incomplete_subfolder_list_never_widens_to_the_whole_folder(app):
    d = _drive_dialog(["Garden/Roses", "Garden/Tulips"])
    try:
        d.add_children(None, [_folder("Archive"), _folder("Garden")])
        garden = _top(d, "Garden")
        assert garden.checkState(0) == Qt.PartiallyChecked
        d.add_children(garden, [_folder("Garden/Roses")], incomplete=True)  # Tulips left out
        # the one folder shown is ticked, but that doesn't make all of Garden ticked
        assert garden.checkState(0) == Qt.PartiallyChecked
        assert d.selection() == ["Garden/Roses", "Garden/Tulips"]
        roses = garden.child(0)
        roses.setCheckState(0, Qt.Unchecked)
        assert garden.checkState(0) == Qt.PartiallyChecked  # Tulips, not shown, is still chosen
        assert d.selection() == ["Garden/Tulips"]  # the folder not shown keeps its setting
        garden.setCheckState(0, Qt.Checked)  # ticking Garden: only what can be seen, plus the earlier choice
        assert d.selection() == ["Garden/Roses", "Garden/Tulips"]
        garden.setCheckState(0, Qt.Unchecked)  # unticking Garden as a whole: all of it goes
        assert d.selection() == []
        assert d.ok.isEnabled()
    finally:
        d.close()


def test_a_folder_synced_whole_with_an_incomplete_list_is_not_narrowed_blindly(app):
    d = _drive_dialog(None)
    try:
        d.add_children(None, [_folder("Garden"), _folder("Orchard")])
        garden = _top(d, "Garden")
        d.add_children(garden, [_folder("Garden/Roses"), _folder("Garden/Tulips")], incomplete=True)
        assert d.ok.isEnabled() and d.selection() is None
        garden.child(0).setCheckState(0, Qt.Unchecked)  # "all of Garden but Roses" can't be listed
        assert garden.checkState(0) == Qt.PartiallyChecked
        assert not d.ok.isEnabled()
        garden.child(0).setCheckState(0, Qt.Checked)
        assert garden.checkState(0) == Qt.Checked and d.ok.isEnabled() and d.selection() is None
        garden.setCheckState(0, Qt.Unchecked)  # all of Garden out: a list can say that
        assert d.ok.isEnabled() and d.selection() == ["Orchard"]
    finally:
        d.close()


def test_the_real_listing_marks_an_incomplete_answer_and_the_dialog_keeps_the_choice(app, monkeypatch):
    answers = {
        drive_remote.ROOT_ID: {"drivewsid": drive_remote.ROOT_ID, "numberOfItems": 2, "items": [
            {"drivewsid": "FOLDER::garden", "name": "Garden", "type": "FOLDER"}]},
    }
    monkeypatch.setattr(drive_remote.ICloudDrive, "_folders", lambda self, ids: [answers[i] for i in ids])
    monkeypatch.setattr(dialogs, "run_async", lambda fn, done, fail: done(fn()))
    d = dialogs.DriveFoldersDialog(["Garden"], theme.LIGHT, lambda: SimpleNamespace(drive=object()))
    try:
        assert d._incomplete == {""}
        assert [d.tree.topLevelItem(i).text(0) for i in range(d.tree.topLevelItemCount())] == ["Garden"]
        assert d.selection() == ["Garden"]
        # the same folders, now complete: today's behaviour
        answers[drive_remote.ROOT_ID]["numberOfItems"] = 1
        d2 = dialogs.DriveFoldersDialog(["Garden"], theme.LIGHT, lambda: SimpleNamespace(drive=object()))
        try:
            assert not d2._incomplete and d2.selection() is None
        finally:
            d2.close()
    finally:
        d.close()


def test_a_subfolder_that_could_not_be_read_says_so_and_is_read_again(app, monkeypatch):
    calls = []
    outcome = {"fail": True}

    def fake_run_async(fn, done, fail):
        calls.append(fn)
        if outcome["fail"]:
            fail("No connection")
        else:
            done(([_folder("Garden/Roses")], False))

    monkeypatch.setattr(dialogs, "run_async", fake_run_async)
    d = dialogs.DriveFoldersDialog(None, theme.LIGHT, lambda: object())
    try:
        calls.clear()
        outcome["fail"] = False
        d.add_children(None, [_folder("Garden")])
        garden = _top(d, "Garden")
        outcome["fail"] = True
        d.tree.expandItem(garden)
        assert len(calls) == 1
        note = garden.child(0)
        assert note.text(0) == dialogs.t("Couldn't read this folder. Close and open it again to try again.")
        assert not garden.data(0, d.LOADED)
        assert d.selection() is None  # the failed folder changes nothing
        d.tree.collapseItem(garden)
        outcome["fail"] = False
        d.tree.expandItem(garden)  # opened again: read again
        assert len(calls) == 2
        assert garden.childCount() == 1 and garden.child(0).data(0, d.PATH) == "Garden/Roses"
    finally:
        d.close()


def test_listing_marks_fewer_items_than_announced(monkeypatch):
    monkeypatch.setattr(drive_remote.ICloudDrive, "_folders", lambda self, ids: [
        {"drivewsid": i, "numberOfItems": 3, "items": [{"drivewsid": "FOLDER::a", "name": "A", "type": "FOLDER"}]}
        for i in ids])
    drive = drive_remote.ICloudDrive(SimpleNamespace(drive=object()))
    root = drive_remote.DriveItem("", drive_remote.ROOT_ID, "root", "com.apple.CloudDocs", None, True)
    listed = drive.walk(folders=[root])
    assert "A" in listed and drive.incomplete == {""}


# the deletion question ---------------------------------------------------------------------
def _pending(n_pc: int, n_cloud: int = 0, service: str = "drive"):
    to_pc = [f"pc{i}" for i in range(n_pc)]
    to_cloud = [f"cl{i}" for i in range(n_cloud)]
    names = {i: f"Synthetic folder {i}/a rather long made-up file name {i}.txt" for i in to_pc + to_cloud}
    return SimpleNamespace(service=service, to_pc=to_pc, to_cloud=to_cloud, names=names)


def test_the_whole_list_shows_every_item_the_answer_covers(app):
    pending = _pending(503)
    d = dialogs.PendingDialog(pending, theme.LIGHT, limit=50)
    seen = []
    d.decided.connect(lambda *a: seen.append(a))
    try:
        lst = d.findChild(QListWidget)
        assert lst.count() == 503
        assert [lst.item(i).text() for i in range(lst.count())] == [pending.names[i] for i in pending.to_pc]
        keep = next(b for b in d.findChildren(QPushButton) if b.text() == dialogs.t("Keep on this PC"))
        assert keep.objectName() == "Primary"  # keeping stays the highlighted answer
        keep.click()
        assert seen == [("reject", pending.to_pc)]
    finally:
        d.close()


def _fully_visible(d, button) -> bool:
    if not button.isVisible():
        return False
    top_left = button.mapTo(d, button.rect().topLeft())
    inside = d.rect().contains(top_left) and d.rect().contains(button.mapTo(d, button.rect().bottomRight()))
    return inside and button.visibleRegion().boundingRect() == button.rect()


@pytest.mark.parametrize("two", [False, True])
def test_the_question_fits_a_small_screen_and_the_answers_stay_in_view(app, monkeypatch, two):
    # 1366 x 768 at 150 %: about 911 x 512 logical pixels above the taskbar, the title bar included
    monkeypatch.setattr(dialogs, "_room_for", lambda _w: 512 - 32)
    try:
        d = dialogs.PendingDialog(_pending(503, 40 if two else 0), theme.LIGHT, limit=50)
        try:
            d.show()
            app.processEvents()
            answers = [b for b in d.findChildren(QPushButton)
                       if b.text() in (dialogs.t("Keep on this PC"), dialogs.t("Delete on this PC too"),
                                       dialogs.t("Delete in iCloud too"), dialogs.t("Bring them back to this PC"),
                                       dialogs.t("Decide later"))]
            assert len(answers) == (5 if two else 3)
            assert d.height() <= 480
            assert all(_fully_visible(d, b) for b in answers)
            for toggle in [b for b in d.findChildren(QPushButton) if b.text() == dialogs.t("Show the list")]:
                toggle.click()
            app.processEvents()
            assert d.height() <= 480
            assert all(_fully_visible(d, b) for b in answers)
            assert all(lst.isVisible() for lst in d.findChildren(QListWidget))
        finally:
            d.close()
    finally:
        i18n.set_language("en")


def test_a_short_question_stays_compact(app):
    d = dialogs.PendingDialog(_pending(2), theme.LIGHT, limit=1)
    try:
        d.show()
        app.processEvents()
        scroll = d.findChild(dialogs._FitScroll)
        assert scroll.verticalScrollBar().maximum() == 0  # nothing to scroll: the window is tall enough
        assert d.height() < 400
    finally:
        d.close()


# first run: screen readers ------------------------------------------------------------------
def test_the_layout_choice_has_names_and_says_which_is_chosen(app, tmp_path):
    """Each layout card has a name, the chosen one says "Chosen", and the keyboard chooses as before.
    (No accessibility interface of Skyfold's own: Qt deletes those, and Python ones could crash it.)"""
    d = dialogs.FolderSetupDialog(str(tmp_path / "Photos"), theme.LIGHT)
    try:
        d.show()
        app.processEvents()
        assert d.layout_group.accessibleName() == dialogs.t("Layout")
        flat, year = d.options["flat"], d.options["year"]
        assert all(opt.accessibleName() for opt in d.options.values())
        assert flat.accessibleDescription() == dialogs.t("Chosen") and not year.accessibleDescription()
        year.setFocus(Qt.TabFocusReason)
        QTest.keyClick(year, Qt.Key_Space)  # the keyboard works as before
        assert d.layout_choice == "year"
        assert year.accessibleDescription() == dialogs.t("Chosen") and not flat.accessibleDescription()
        assert d.path.accessibleName() == dialogs.t("Photos folder")
    finally:
        d.close()


# the help cards ----------------------------------------------------------------------------
def test_help_cards_have_a_name_and_description_and_work_with_the_keyboard(app):
    pressed = []
    host = QWidget()
    lay = QVBoxLayout(host)
    card = dialogs.SettingsDialog._link_card(SimpleNamespace(c=theme.LIGHT), lay, "file", "Read me", "example.test",
                                             lambda: pressed.append(1))
    try:
        host.show()
        assert card.accessibleName() == "Read me" and card.accessibleDescription() == "example.test"
        card.setFocus()
        QTest.keyClick(card, Qt.Key_Return)
        QTest.keyClick(card, Qt.Key_Space)
        assert pressed == [1, 1]
    finally:
        host.close()


def test_no_accessibility_interfaces_of_our_own(app):
    """Skyfold installs no QAccessible factory (Python-made interfaces that Qt deletes could crash it)."""
    import ast
    from pathlib import Path

    for py in Path(dialogs.__file__).parent.parent.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        names = {getattr(n.func, "attr", None) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert "installFactory" not in names, py
        bases = {ast.unparse(b) for n in ast.walk(tree) if isinstance(n, ast.ClassDef) for b in n.bases}
        assert not [b for b in bases if "QAccessible" in b], py  # no interface class written in Python either


def test_every_click_card_in_settings_has_a_name(app):
    from skyfold.ui.settings_parts import ClickCard

    d = dialogs.SettingsDialog(Settings(), theme.LIGHT)
    try:
        cards = d.findChildren(ClickCard)
        assert len(cards) >= 4  # the help links and "bring back photos"
        assert all(c.accessibleName() for c in cards)
        assert [c for c in cards if c.accessibleName() == dialogs.t("Bring back photos I deleted only on this PC")]
    finally:
        d.close()


# About: the real settings folder -----------------------------------------------------------
def test_about_names_the_settings_folder_really_in_use(app, monkeypatch, tmp_path):
    private = tmp_path / "private" / "data"
    monkeypatch.setattr(config, "DATA_DIR", private)
    d = dialogs.SettingsDialog(Settings(), theme.LIGHT)
    try:
        texts = [lb.text().replace(chr(0x200B), "") for lb in d.findChildren(QLabel)]  # (wrap points)
        assert any(str(private) in tx for tx in texts)
        assert not any("next to the app" in tx for tx in texts)
    finally:
        d.close()


# Find My after a failed refresh ------------------------------------------------------------
def test_find_my_marks_the_devices_as_from_the_last_refresh_when_one_fails(app, monkeypatch):
    now = datetime.now().astimezone()
    dev = SimpleNamespace(id="1", name="Phone", model="Phone", battery=0.5, charging=False, online=True,
                          located_at=(now - timedelta(minutes=3)).isoformat(), lat=1.0, lon=2.0, can_sound=True)
    answer = {"ok": True}
    monkeypatch.setattr(dialogs, "run_async",
                        lambda _task, done, fail: done([dev]) if answer["ok"] else fail("No internet connection"))
    d = dialogs.FindMyDialog(theme.LIGHT, lambda: object())
    try:
        assert d.rows.count() == 1 and not d.info.isVisibleTo(d)
        assert re.fullmatch(r"\d\d:\d\d", d._shown_at)
        answer["ok"] = False
        d.load()
        assert d.rows.count() == 1  # the earlier answer stays (its last location is still useful)…
        assert d.info.isVisibleTo(d)
        text = d.info.text()
        assert dialogs.t("Couldn't refresh: {error}", error="No internet connection") in text
        assert dialogs.t("Shown as of the last refresh at {time}.", time=d._shown_at) in text  # …but marked as old
    finally:
        d.close()


def test_find_my_without_earlier_devices_shows_just_the_error(app, monkeypatch):
    from skyfold import extras

    monkeypatch.setattr(dialogs, "run_async", lambda _task, _done, fail: fail(extras.sign_in_again_text()))
    d = dialogs.FindMyDialog(theme.LIGHT, lambda: object())
    try:
        assert d.info.text() == extras.sign_in_again_text()
        assert d.signin_btn.isVisibleTo(d)
    finally:
        d.close()


@pytest.mark.parametrize("n", [3, 40, 2000])
def test_show_the_list_really_shows_it(app, n):
    """After one click on "Show the list" the list is on screen, not below the fold of the text."""
    d = dialogs.PendingDialog(_pending(n), theme.LIGHT, limit=2)
    try:
        d.show()
        app.processEvents()
        toggle = next(b for b in d.findChildren(QPushButton) if b.text() == dialogs.t("Show the list"))
        lst = d.findChild(QListWidget)
        toggle.click()
        app.processEvents()
        app.processEvents()
        assert lst.isVisible() and lst.height() > 0  # (a list of no height would pass the next line unseen)
        assert lst.visibleRegion().boundingRect().height() >= min(lst.height(), 120)
    finally:
        d.close()
