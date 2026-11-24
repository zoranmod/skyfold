"""The Photos window and what it reads: made-up photos only, never a real library."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from skyfold import photo_library as lib
from skyfold.remote import AssetInfo
from skyfold.state import FileRow, State


def _picture(path, hue):
    from PySide6.QtGui import QColor, QImage

    img = QImage(64, 48, QImage.Format_RGB32)
    img.fill(QColor.fromHsv(hue % 360, 150, 220))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), "JPG")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def made_up(tmp_path, qapp):
    root = tmp_path / "Photos"
    st = State(tmp_path / "s.db")
    t0 = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    infos = {}
    for i in range(30):
        when = t0 - timedelta(days=i * 20)
        rel = f"{when:%Y/%m}/IMG_{100 + i}.JPG"
        _picture(root / rel, i * 25)
        st.add_file(FileRow(f"A{i}", "original", rel, 1000, i == 4, when.isoformat()), when.isoformat())
        if i == 2:  # a Live Photo: its photo and its 3-second video
            (root / rel).with_suffix(".MOV").write_bytes(b"mov")
            st.add_file(FileRow("A2", "original_video", rel[:-4] + ".MOV", 3, True, when.isoformat()),
                        when.isoformat())
        infos[f"A{i}"] = AssetInfo(favorite=i in (1, 7), added=when if i != 20 else t0,
                                   width=4032, height=3024, duration=65.0 if i == 4 else None,
                                   screenshot=i == 9)
    st.set_infos(infos)
    st.add_album_link("Albums/Trips/Rome/IMG_105.JPG", "A5")
    st.add_album_link("Albums/Trips/Rome/IMG_106.JPG", "A6")
    # a file Skyfold synced but that is no longer on this PC is left out
    st.add_file(FileRow("GONE", "original", "2020/01/IMG_1.JPG", 1, False, "2020-01-01T00:00:00+00:00"), "x")
    return st, root


def test_library_groups_live_photos_albums_and_leaves_out_missing_files(made_up):
    st, root = made_up
    items = lib.load(st, root)
    assert len(items) == 30 and "GONE" not in {i.asset_id for i in items}
    assert items == sorted(items, key=lambda i: i.date)  # oldest first, newest at the bottom
    live = next(i for i in items if i.asset_id == "A2")
    assert live.live and live.live_video.suffix == ".MOV" and len(live.files) == 2 and live.size == 1003
    assert next(i for i in items if i.asset_id == "A4").is_video
    assert {i.asset_id for i in items if i.favorite} == {"A1", "A7"}
    assert list(lib.albums(items)) == ["Trips/Rome"]
    assert next(i for i in items if i.asset_id == "A9").screenshot


def test_recently_added_counts_when_it_reached_icloud(made_up):
    st, root = made_up
    items = lib.load(st, root)
    now = datetime(2026, 11, 24, tzinfo=timezone.utc)
    got = lib.recent(items, now)
    # an old photo added (scanned) lately is first, before the newest taken ones
    assert got[0].asset_id == "A20" and {"A0", "A1"} <= {i.asset_id for i in got}
    assert "A10" not in {i.asset_id for i in got}


def test_search_by_name_month_year_album_and_kind(made_up):
    st, root = made_up
    items = lib.load(st, root)
    by = lambda q: {i.asset_id for i in items if lib.matches(i, q)}  # noqa: E731
    assert by("IMG_105") == {"A5"}
    assert by("rome") == {"A5", "A6"}
    assert by("video") == {"A4"} and by("live") == {"A2"} and by("favourite") == {"A1", "A7"}
    assert by("2026-11") == {"A0"}
    assert "A0" in by("november 2026")


def test_state_details_survive_and_reset_forgets_them(tmp_path):
    st = State(tmp_path / "s.db")
    st.add_file(FileRow("X", "original", "a.jpg", 1, False, "2026-01-01T00:00:00+00:00"), "t")
    before = st.library_fingerprint()
    st.set_infos({"X": AssetInfo(favorite=True, added=datetime(2026, 2, 1, tzinfo=timezone.utc))})
    assert st.library()[0][6] == 1 and st.library_fingerprint() != before
    st.set_favorite("X", False)
    assert st.library()[0][6] == 0
    st.reset()
    assert st.library() == [] and st._db.execute("SELECT COUNT(*) FROM asset_info").fetchone()[0] == 0


def test_a_full_read_stores_details_and_is_asked_for_once(tmp_path):
    from skyfold.engine import INFO_KEY
    from tests.test_engine import FakeRemote

    from skyfold.config import Settings
    from skyfold.engine import Syncer

    remote = FakeRemote()
    remote.add("a1", "IMG_1.HEIC", b"one")
    real_list = remote.list_assets

    def with_info(*a, **kw):
        for asset in real_list(*a, **kw):
            asset.info = AssetInfo(favorite=True, width=10, height=8)
            yield asset

    remote.list_assets = with_info
    s = Settings(photos_dir=str(tmp_path / "P"), folder_structure="year_month")
    st = State(tmp_path / "s.db")
    st.set("cursor", remote.cursor())  # as if synced before this version: no details yet
    st.set("last_full_scan", datetime.now(timezone.utc).isoformat())
    sync = Syncer(st, remote, s)
    sync.run()
    assert st.get(INFO_KEY) == "1"
    assert st._db.execute("SELECT favorite, width FROM asset_info WHERE asset_id='a1'").fetchone() == (1, 10)


def pump(qapp, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def window(qapp, made_up, mode="dark", settings=None):
    from skyfold.ui import theme
    from skyfold.ui.photos_window import PhotosWindow

    st, root = made_up
    w = PhotosWindow(theme.palette(mode), lambda: (st, root),
                     lambda: settings or SimpleNamespace(delete_pc_to_cloud=True))
    w.resize(1200, 800)
    w.show()
    assert pump(qapp, lambda: len(w.model.items) == 30)
    return w


@pytest.mark.parametrize("mode", ["light", "dark", "oled"])
def test_window_shows_years_months_all_and_opens_a_photo(qapp, made_up, mode):
    w = window(qapp, made_up, mode)
    try:
        assert w.grid.verticalScrollBar().value() == w.grid.verticalScrollBar().maximum()  # newest in view
        w.set_view("months")
        assert w.inner.currentWidget() is w.cards_area
        w.set_view("years")
        assert w.inner.currentWidget() is w.cards_area
        w.set_view("all")
        w.open_viewer(len(w.model.items) - 1)
        assert w.pages.currentWidget() is w.viewer and "IMG_100" in w.v_time.text()
        w.step(-1)
        assert w.viewer_row == len(w.model.items) - 2
        assert pump(qapp, lambda: w.picture.pm is not None)  # the picture arrives off the window's thread
        w.close_viewer()
        assert w.pages.currentWidget() is w.browse
    finally:
        w.close()


def test_sidebar_sets_and_search(qapp, made_up):
    w = window(qapp, made_up)
    try:
        for key, n in (("fav", 2), ("videos", 1), ("live", 1), ("shots", 1), ("album:Trips/Rome", 2)):
            w.set_key = key
            w._show_set()
            assert len(w.model.items) == n, key
        w.set_key = "library"
        w.search.setText("rome")
        assert len(w.model.items) == 2 and w.title.text() in ("Search results", "Rezultati pretrage")
        w.search.setText("nothing like this")
        assert w.inner.currentWidget() is w.empty
    finally:
        w.close()


def test_heart_is_shown_at_once_and_undone_if_icloud_says_no(qapp, made_up):
    w = window(qapp, made_up)
    asked = []
    w.favoriteRequested.connect(lambda aid, v: asked.append((aid, v)))
    try:
        it = next(i for i in w.items if i.asset_id == "A3")
        w.set_favorite([it], True)
        assert it.favorite and asked == [("A3", True)]
        w.favorite_result("A3", False, "no network")
        assert not it.favorite and w.toast.isVisible()
    finally:
        w.close()


def test_delete_asks_first_and_moves_both_files_of_a_live_photo(qapp, made_up, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from skyfold import engine

    gone = []
    monkeypatch.setattr(engine, "recycle", lambda p: gone.append(p.name))
    answer = {"delete": False}

    def fake_exec(box):
        buttons = {b.text(): b for b in box.buttons()}
        assert box.defaultButton().text() in ("Cancel", "Odustani")  # Enter never deletes by itself
        target = [b for b in box.buttons() if box.buttonRole(b) == QMessageBox.DestructiveRole][0] \
            if answer["delete"] else box.defaultButton()
        box.clickedButton = lambda: target
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    w = window(qapp, made_up)
    try:
        live = next(i for i in w.items if i.asset_id == "A2")
        w.delete_items([live])
        assert gone == [] and len(w.items) == 30  # cancelled
        answer["delete"] = True
        w.delete_items([live])
        assert sorted(gone) == ["IMG_102.JPG", "IMG_102.MOV"] and len(w.items) == 29
    finally:
        w.close()


def test_worker_sets_the_favourite_in_icloud_then_in_the_database(tmp_path):
    from skyfold.worker import SyncWorker

    st = State(tmp_path / "s.db")
    calls, said = [], []
    remote = SimpleNamespace(set_favorite=lambda aid, v: calls.append((aid, v)))
    fake = SimpleNamespace(syncer=SimpleNamespace(remote=remote), state=st,
                           favoriteSet=SimpleNamespace(emit=lambda *a: said.append(a)))
    SyncWorker.set_favorite(fake, "Q", True)
    assert calls == [("Q", True)] and said == [("Q", True, "")]
    assert st._db.execute("SELECT favorite FROM asset_info WHERE asset_id='Q'").fetchone() == (1,)

    def refuse(aid, v):
        raise RuntimeError("offline")

    remote.set_favorite = refuse
    SyncWorker.set_favorite(fake, "Q", False)
    assert said[-1][0] == "Q" and said[-1][1] is True and said[-1][2]  # unchanged, with the reason


def test_video_length_from_icloud_is_read_as_milliseconds():
    from skyfold.remote import _info

    rec = {"fields": {"duration": {"value": 66282}, "isFavorite": {"value": 1}, "assetSubtypeV2": {"value": 0}}}
    asset = SimpleNamespace(asset_record=rec, added_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
                            dimensions=(1920, 1080))
    info = _info(asset)
    assert info is not None and round(info.duration) == 66 and info.favorite and not info.screenshot
