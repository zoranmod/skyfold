"""The browser extension's copy of the Safari bookmarks belongs to one Apple ID (1.2.1).

A session can expire and another person sign in without Sign out ever being pressed. The copy the
extension reads names its owner (a fingerprint, never the address), the native host hands it out only
to that Apple ID, and signing in as someone else removes it. Synthetic accounts and bookmarks only."""
import json
from types import SimpleNamespace

import pytest

from skyfold import auth, bookmarks, native_host
from skyfold.config import Settings
from tests.test_bookmarks import FakeApi, leaf

FIRST = "first@example.test"
SECOND = "second@example.test"


def export_for(apple_id, folder):
    api = FakeApi([{"records": [leaf("garden", "Garden plan", "https://garden.example.test", "", 1)]}])
    api.account_name = apple_id
    bookmarks.export(api, folder)


def ask():
    return native_host.answer({"cmd": "bookmarks"})


REFUSED = {"ok": False, "error": "no-bookmarks"}


def test_the_same_account_gets_its_bookmarks(tmp_path):
    Settings(apple_id=FIRST.upper(), bookmarks_enabled=True).save()  # (however the address was typed)
    export_for(FIRST, tmp_path)
    assert ask()["tree"][0]["t"] == "Garden plan"


def test_another_account_never_gets_the_previous_accounts_bookmarks(tmp_path):
    Settings(apple_id=FIRST, bookmarks_enabled=True).save()
    export_for(FIRST, tmp_path)
    assert ask()["ok"]
    # The session expired and someone else signed in; Sign out was never pressed, the copy is still there.
    auth.commit_login(SimpleNamespace(account_name=SECOND))
    Settings(apple_id=SECOND, bookmarks_enabled=True).save()
    assert not auth.signed_out() and native_host.cache_file().exists()
    assert ask() == REFUSED


def test_a_read_that_ends_for_the_earlier_session_keeps_its_real_owner(tmp_path):
    Settings(apple_id=SECOND, bookmarks_enabled=True).save()
    export_for(FIRST, tmp_path)  # (begun before the other sign-in, finished after it)
    assert ask() == REFUSED
    export_for(SECOND, tmp_path)
    assert ask()["tree"][0]["t"] == "Garden plan"


@pytest.mark.parametrize("apple_id", [FIRST, ""])
def test_a_copy_from_before_owners_were_written_waits_for_the_next_read(tmp_path, apple_id):
    Settings(apple_id=apple_id, bookmarks_enabled=True).save()
    native_host.cache_file().write_text(json.dumps({"updated": "x", "tree": [{"t": "Garden plan"}]}), encoding="utf-8")
    assert ask() == REFUSED
    if apple_id:
        export_for(apple_id, tmp_path)  # the next read of the bookmarks names the owner
        assert ask()["tree"][0]["t"] == "Garden plan"


def test_the_copy_names_no_address(tmp_path):
    export_for(FIRST, tmp_path)
    text = native_host.cache_file().read_text(encoding="utf-8")
    assert json.loads(text)["owner"] == native_host.cache_owner(" First@Example.test ")
    assert "first" not in text.lower() and "example.test/" not in json.loads(text)["owner"]


def test_no_copy_is_handed_out_while_safari_bookmarks_are_off(tmp_path):
    Settings(apple_id=FIRST, bookmarks_enabled=False).save()
    export_for(FIRST, tmp_path)
    assert ask() == REFUSED


def test_signing_in_as_someone_else_removes_the_copy(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from skyfold.app import Controller, Requests
    from skyfold.ui import theme

    QApplication.instance() or QApplication([])
    settings = Settings(apple_id=FIRST, bookmarks_enabled=True, paused=True, notifications=False)
    settings.save()
    export_for(FIRST, tmp_path)
    assert native_host.cache_file().exists()
    monkeypatch.setattr(auth, "commit_login", lambda api: None)
    worker = SimpleNamespace(cancel=lambda: None, api=None)
    c = SimpleNamespace(settings=settings, worker=worker, req=Requests(), c=theme.palette("light"), _dialogs={},
                        tray=SimpleNamespace(geometry=lambda: None),
                        flyout=SimpleNamespace(hide=lambda: None, set_account=lambda _: None, show_at=lambda _: None))
    c._show_dialog = lambda key, dlg: Controller._show_dialog(c, key, dlg)
    Controller.open_login(c)
    try:
        c._dialogs["login"].loggedIn.emit(SimpleNamespace(account_name=SECOND), SECOND)
        assert c.settings.apple_id == SECOND
        assert not native_host.cache_file().exists()
        assert ask() == REFUSED
    finally:
        for dlg in list(c._dialogs.values()):
            dlg.close()


def test_signing_in_again_as_the_same_account_keeps_the_copy(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from skyfold.app import Controller, Requests
    from skyfold.ui import theme

    QApplication.instance() or QApplication([])
    settings = Settings(apple_id=FIRST, bookmarks_enabled=True, paused=True, notifications=False)
    settings.save()
    export_for(FIRST, tmp_path)
    monkeypatch.setattr(auth, "commit_login", lambda api: None)
    c = SimpleNamespace(settings=settings, worker=SimpleNamespace(cancel=lambda: None, api=None), req=Requests(),
                        c=theme.palette("light"), _dialogs={}, tray=SimpleNamespace(geometry=lambda: None),
                        flyout=SimpleNamespace(hide=lambda: None, set_account=lambda _: None, show_at=lambda _: None))
    c._show_dialog = lambda key, dlg: Controller._show_dialog(c, key, dlg)
    Controller.open_login(c)
    try:
        c._dialogs["login"].loggedIn.emit(SimpleNamespace(account_name=FIRST), FIRST.upper())
        assert ask()["tree"][0]["t"] == "Garden plan"  # the expired session renewed: nothing to wait for
    finally:
        for dlg in list(c._dialogs.values()):
            dlg.close()
