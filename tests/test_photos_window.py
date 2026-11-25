"""The Photos window and what it reads: made-up photos only, never a real library."""

from __future__ import annotations

import os
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
        assert pump(qapp, lambda: w.query == "rome")
        assert len(w.model.items) == 2 and w.title.text() in ("Search results", "Rezultati pretrage")
        w.search.setText("nothing like this")
        w.search.returnPressed.emit()  # Enter searches at once (and never clicks a button)
        assert w.inner.currentWidget() is w.empty and not w.selecting
    finally:
        w.close()


def test_heart_is_shown_at_once_and_undone_if_icloud_says_no(qapp, made_up):
    w = window(qapp, made_up)
    asked = []
    w.favoriteRequested.connect(lambda ids, v: asked.append((ids, v)))
    try:
        it = next(i for i in w.items if i.asset_id == "A3")
        w.set_favorite([it], True)
        assert it.favorite and asked == [(["A3"], True)]
        w.reload(now=True)  # the database doesn't know yet: the heart stays as asked
        st, _root = made_up
        st.set("bump", "x")
        st.add_album_link("Albums/X/IMG_103.JPG", "A3")
        w.reload(now=True)
        assert pump(qapp, lambda: not w._loading and w.by_id["A3"] is not it)
        assert w.by_id["A3"].favorite
        w.favorite_result(["A3"], True, ["A3"], "no network")
        assert not w.by_id["A3"].favorite and w.toast.isVisible() and not w._pending_fav
    finally:
        w.close()


def test_many_hearts_go_as_one_request(qapp, made_up):
    w = window(qapp, made_up)
    asked = []
    w.favoriteRequested.connect(lambda ids, v: asked.append((sorted(ids), v)))
    try:
        items = [i for i in w.items if i.asset_id in ("A3", "A4", "A1")]  # A1 already is one
        w.set_favorite(items, True)
        assert asked == [(["A3", "A4"], True)]
        w.favorite_result(["A3", "A4"], True, [], "")
        assert not w._pending_fav and all(i.favorite for i in items)
    finally:
        w.close()


def _answer_delete(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    answer = {"delete": False}

    def fake_exec(box):
        assert box.defaultButton().text() in ("Cancel", "Odustani")  # Enter never deletes by itself
        target = [b for b in box.buttons() if box.buttonRole(b) == QMessageBox.DestructiveRole][0] \
            if answer["delete"] else box.defaultButton()
        box.clickedButton = lambda: target
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return answer


def test_delete_asks_first_and_moves_both_files_of_a_live_photo(qapp, made_up, monkeypatch):
    from skyfold import engine

    gone = []
    monkeypatch.setattr(engine, "recycle", lambda p: gone.append(p.name))
    answer = _answer_delete(monkeypatch)
    w = window(qapp, made_up)
    try:
        live = next(i for i in w.items if i.asset_id == "A2")
        w.delete_items([live])
        assert gone == [] and len(w.items) == 30  # cancelled
        answer["delete"] = True
        w.delete_items([live])
        assert pump(qapp, lambda: len(w.items) == 29)
        assert gone == ["IMG_102.MOV", "IMG_102.JPG"]  # the video first, the photo last
    finally:
        w.close()


def test_delete_leaves_a_file_that_was_replaced_since_the_sync(tmp_path, made_up, monkeypatch):
    from skyfold import engine

    st, root = made_up
    gone = []
    monkeypatch.setattr(engine, "recycle", lambda p: gone.append(p.name))
    rel = st._db.execute("SELECT rel_path FROM files WHERE asset_id='A5'").fetchone()[0]
    p = root / rel
    real = os.stat(p)
    st.set("fid:A5#original", f"{real.st_dev}:{real.st_ino + 1}")  # Skyfold synced another file there
    done, failed = engine.delete_on_pc(st, root, ["A5", "A6"])
    assert done == ["A6"] and [f[0] for f in failed] == ["A5"] and "IMG_105" not in " ".join(gone)
    (root / "2026" / "x").mkdir(parents=True, exist_ok=True)
    st.add_file(FileRow("D", "original", "2026/x", 1, False, "2026-01-01T00:00:00+00:00"), "x")
    done, failed = engine.delete_on_pc(st, root, ["D"])  # a folder now has that name: left alone
    assert done == [] and failed and (root / "2026" / "x").is_dir()


def test_half_a_live_photo_never_counts_as_deleted(made_up, monkeypatch):
    from skyfold import engine

    st, root = made_up

    def refuse_photo(p):
        if p.suffix == ".JPG":
            raise OSError("in use")

    monkeypatch.setattr(engine, "recycle", refuse_photo)
    done, failed = engine.delete_on_pc(st, root, ["A2"])
    assert done == [] and failed
    keys = {r[0] for r in st._db.execute("SELECT resource_key FROM files WHERE asset_id='A2'")}
    assert keys == {"original"}  # the video is forgotten (brought back by the sync), the photo stays
    assert not st._db.execute("SELECT 1 FROM excluded").fetchall()


def test_photos_from_before_1970_are_shown(tmp_path, qapp):
    root = tmp_path / "P"
    st = State(tmp_path / "s.db")
    _picture(root / "1965/IMG_1.JPG", 10)
    st.add_file(FileRow("OLD", "original", "1965/IMG_1.JPG", 1, False, "1965-06-01T10:00:00+00:00"), "x")
    _picture(root / "2020/IMG_2.JPG", 20)
    st.add_file(FileRow("NEW", "original", "2020/IMG_2.JPG", 1, False, None), "x")
    items = lib.load(st, root)
    assert [i.asset_id for i in items] == ["NEW", "OLD"] or [i.asset_id for i in items] == ["OLD", "NEW"]
    assert next(i for i in items if i.asset_id == "OLD").date.year == 1965


def test_revision_moves_only_with_what_the_window_shows(tmp_path):
    st = State(tmp_path / "s.db")
    r0 = st.library_fingerprint()
    st.add_file(FileRow("A", "original", "2020/a.jpg", 1, False, None), "x")
    r1 = st.library_fingerprint()
    assert r1 != r0
    st.set_infos({"A": AssetInfo(favorite=True)})
    r2 = st.library_fingerprint()
    st.set_infos({"A": AssetInfo(favorite=True)})  # nothing new: nothing written
    assert st.library_fingerprint() == r2 != r1
    st.set("cursor", "abc")
    assert st.library_fingerprint() == r2
    st.move_file("A", "original", "2020/b.jpg")  # a rename of the same length is seen too
    assert st.library_fingerprint() != r2


def test_selection_survives_a_reload_and_arrows_never_select(qapp, made_up):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    w = window(qapp, made_up)
    try:
        w.set_selecting(True)
        w.select_all()
        n = len(w.chosen_items())
        st, _root = made_up
        st.add_album_link("Albums/Y/IMG_110.JPG", "A10")
        w.reload(now=True)
        assert pump(qapp, lambda: not w._loading and w._fingerprint[1] == st.library_fingerprint())
        assert len(w.chosen_items()) == n == 30
        w.set_selecting(False)
        w.grid.setFocus()
        w.grid.selectionModel().setCurrentIndex(w.model.index(3), w.grid.selectionCommand(w.model.index(3)))
        QTest.keyClick(w.grid, Qt.Key_Right)
        assert not w.chosen_items() and not w.selecting
    finally:
        w.close()


def test_worker_sets_the_favourites_in_icloud_then_in_the_database(tmp_path):
    from skyfold.worker import SyncWorker

    st = State(tmp_path / "s.db")
    calls, said = [], []
    remote = SimpleNamespace(set_favorite=lambda aid, v: calls.append((aid, v)))
    fake = SimpleNamespace(syncer=SimpleNamespace(remote=remote), state=st, account_gen=0,
                           favoritesSet=SimpleNamespace(emit=lambda *a: said.append(a)))
    SyncWorker.set_favorites(fake, ["Q", "R"], True, 0)
    assert calls == [("Q", True), ("R", True)] and said == [(["Q", "R"], True, [], "")]
    assert st._db.execute("SELECT favorite FROM asset_info WHERE asset_id='Q'").fetchone() == (1,)

    def refuse(aid, v):
        raise RuntimeError("offline")

    remote.set_favorite = refuse
    SyncWorker.set_favorites(fake, ["Q"], False, 0)
    assert said[-1][2] == ["Q"] and said[-1][3]  # unchanged, with the reason
    calls.clear()
    remote.set_favorite = lambda aid, v: calls.append((aid, v))
    fake.account_gen = 1  # signed out after the click: nothing goes to iCloud
    SyncWorker.set_favorites(fake, ["Q"], True, 0)
    assert calls == [] and said[-1][2] == ["Q"]


def test_video_length_from_icloud_is_read_as_milliseconds():
    from skyfold.remote import _info

    rec = {"fields": {"duration": {"value": 66282}, "isFavorite": {"value": 1}, "assetSubtypeV2": {"value": 0}}}
    asset = SimpleNamespace(asset_record=rec, added_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
                            dimensions=(1920, 1080))
    info = _info(asset)
    assert info is not None and round(info.duration) == 66 and info.favorite and not info.screenshot


def test_a_lost_connection_stops_the_downloads_instead_of_parking_them_for_a_day(env_photos):
    import requests

    syncer, remote = env_photos
    for i in range(8):
        remote.add(f"n{i}", f"IMG_{i}.JPG", b"x" * (i + 1), date=datetime(2025, 1, 1 + i, tzinfo=timezone.utc))
    calls = []

    def offline(res, dest, on_bytes):
        calls.append(res.filename)
        raise requests.ConnectionError("network is gone")

    remote.download = offline
    with pytest.raises(requests.ConnectionError):
        syncer.run()
    assert len(calls) == 3  # stopped after three in a row, not one attempt per photo
    assert not syncer.state.items("failed:")  # nothing put aside: all tried again at the next poll
    assert syncer.state.get("cursor") is None


@pytest.fixture()
def env_photos(tmp_path, monkeypatch):
    from skyfold import engine as eng
    from skyfold.config import Settings
    from tests.test_engine import FakeRemote

    settings = Settings(photos_dir=str(tmp_path / "Slike"), delete_threshold=3, folder_structure="year_month")
    remote = FakeRemote()
    return eng.Syncer(State(tmp_path / "s2.db"), remote, settings), remote


def test_a_photo_left_out_of_one_full_listing_is_not_recycled(env_photos, monkeypatch):
    from skyfold import engine as eng

    syncer, remote = env_photos
    trashed = []
    monkeypatch.setattr(eng.Syncer, "_trash", lambda self, p: trashed.append(p.name))
    remote.add("k1", "IMG_1.JPG", b"one")
    remote.add("k2", "IMG_2.JPG", b"two")
    syncer.run()
    every = remote.list_assets

    def leaves_one_out(on_page=None):
        yield from (a for a in every(on_page) if a.id != "k2")

    remote.list_assets = leaves_one_out
    syncer.run(force_full=True)
    assert trashed == [] and "k2" in syncer.state.asset_ids()  # asked about by itself: still there


def test_a_photo_hidden_in_icloud_stays_on_the_pc(env_photos, monkeypatch):
    from skyfold import engine as eng

    syncer, remote = env_photos
    trashed = []
    monkeypatch.setattr(eng.Syncer, "_trash", lambda self, p: trashed.append(p.name))
    remote.add("h1", "IMG_7.JPG", b"seven")
    syncer.run()
    remote.remove("h1")
    remote.hidden = {"h1"}
    syncer.run()
    assert trashed == [] and "h1" not in syncer.state.asset_ids()  # kept as an ordinary file
    assert any(p.name == "IMG_7.JPG" for p in syncer.root.rglob("*.JPG"))


def test_notes_names_windows_keeps_for_devices_get_a_prefix():
    from skyfold.notes_engine import _safe

    assert _safe("Con", "x") == "_Con" and _safe("aux.html", "x") == "_aux.html"
    assert _safe("Console", "x") == "Console" and _safe("Nul ", "x") == "_Nul"


def test_a_partial_download_left_by_a_crash_is_cleaned_up(env_photos):
    syncer, remote = env_photos
    remote.add("t1", "IMG_1.JPG", b"one")
    syncer.run()
    old = syncer.root / ".skyfold-tmp-abc1_3xy"
    fresh = syncer.root / ".skyfold-tmp-def4_6xy"
    stage = syncer.root / ".skyfold-tmp-album-x.JPG"
    yours = syncer.root / ".skyfold-tmp-personal.jpg"
    for p in (old, fresh, stage, yours):
        p.write_bytes(b"part")
    past = time.time() - 7 * 3600
    os.utime(old, (past, past))
    os.utime(stage, (past, past))
    os.utime(yours, (past, past))
    syncer._temps_cleaned = False  # (as at the next start of Skyfold)
    syncer.run()
    assert not old.exists() and fresh.exists() and stage.exists() and yours.exists()


def test_a_photo_that_cant_go_to_the_recycle_bin_is_kept_not_retried_forever(env_photos, monkeypatch):
    from skyfold import engine as eng

    syncer, remote = env_photos
    remote.add("r1", "IMG_9.JPG", b"nine")
    syncer.run()

    def no_bin(self, p):
        raise eng.NoRecycleBin("no Recycle Bin on that drive")

    monkeypatch.setattr(eng.Syncer, "_trash", no_bin)
    remote.remove("r1")
    res = syncer.run()
    assert res.errors == 0 and "r1" not in syncer.state.asset_ids()
    assert syncer.state.get("cursor") == remote.cursor()  # the change feed moves on


def test_thumbnails_are_kept_at_about_the_size_shown_and_big_ones_never_push_out_the_grid(qapp):
    from PySide6.QtGui import QImage

    from skyfold.ui.photos_window import Thumbs

    asked = []
    th = Thumbs(SimpleNamespace(request=lambda key, path, size: asked.append((key, size))))
    assert [th.bucket(px) for px in (40, 160, 161, 343, 400, 401, 2000)] == [160, 160, 240, 400, 400, 480, 640]
    th.BUDGET = {False: 10 * 400 * 534 * 4, True: 2 * 640 * 854 * 4}
    item = lambda n: SimpleNamespace(asset_id=f"A{n}", key=f"A{n}", path=None)  # noqa: E731
    for n in range(10):
        th.put(f"A{n}", 400, QImage(3000, 4000, QImage.Format_RGB32))
    assert all(th.get(item(n), 343) is not None for n in range(10))
    assert th.get(item(0), 343).width() == 400  # not the 3000 pixels it came with
    for n in range(10, 20):  # the viewer's neighbours, big
        th.put(f"A{n}", 640, QImage(640, 854, QImage.Format_RGB32))
    assert all(th.get(item(n), 343) is not None for n in range(10)) and not asked  # the grid stays
    assert len(th.cache[True]) == 2


def test_a_jpeg_thumbnail_comes_from_windows_stored_preview_first(tmp_path, qapp, monkeypatch):
    from PySide6.QtGui import QImage

    from skyfold.ui import photo_images as pi

    p = tmp_path / "IMG_1.JPG"
    _picture(p, 10)
    calls = []

    def shell(path, size, thumbnail=True):
        calls.append(size)
        return QImage(77, 55, QImage.Format_RGB32)

    monkeypatch.setattr(pi, "shell_image", shell)
    assert pi.load_image(p, 320).width() == 77 and calls == [512]
    monkeypatch.setattr(pi, "shell_image", lambda *a, **k: QImage())  # thumbnails off: Qt reads it
    assert pi.load_image(p, 320).size().width() == 64
    assert pi.load_image(p, 0, full=True).width() == 64  # the viewer: the photo itself, as before


def test_the_wheel_glides_and_a_notch_after_another_adds_up(qapp, made_up, monkeypatch):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    from skyfold.ui import photos_window as pw

    monkeypatch.setattr(pw, "_animations_on", lambda: True)
    w = window(qapp, made_up)
    try:
        w.resize(900, 400)
        pw.PhotosWindow.zoom_level = 0  # big tiles: many rows in a short window
        w._layout_grid()
        qapp.processEvents()
        bar, smooth = w.grid.verticalScrollBar(), w.grid.smooth
        assert pump(qapp, lambda: bar.maximum() > 1000), bar.maximum()
        bar.setValue(bar.maximum())
        start = bar.value()

        def wheel(dy, pixels=0, mods=Qt.NoModifier):
            e = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, pixels), QPoint(0, dy), Qt.NoButton,
                            mods, Qt.NoScrollPhase, False)
            return smooth.eventFilter(w.grid.viewport(), e)

        assert wheel(120) and wheel(120)  # two notches up, quickly
        assert smooth.target is not None and bar.value() > start - 10  # not there at once: it glides
        assert pump(qapp, lambda: smooth.target is None)
        notch = qapp.wheelScrollLines() * smooth.step
        assert bar.value() == max(bar.minimum(), round(start - 2 * notch))
        here = bar.value()
        assert wheel(-24, pixels=-30) and bar.value() == min(bar.maximum(), here + 30)  # a touchpad: as it is
        assert not wheel(120, mods=Qt.ControlModifier)  # Ctrl + wheel: the size, left to the grid
        wheel(-120)
        bar.setValue(bar.minimum())  # the window moves it meanwhile: the glide stops there
        assert smooth.target is None and bar.value() == bar.minimum()
        monkeypatch.setattr(pw, "_animations_on", lambda: False)  # Windows' animations off: no glide
        wheel(-120)
        assert smooth.target is None and bar.value() == min(bar.maximum(), bar.minimum() + notch)
    finally:
        pw.PhotosWindow.zoom_level = 2
        w.close()


def test_the_mouse_back_and_forward_buttons_go_out_of_a_photo_and_back_into_it(qapp, made_up):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    w = window(qapp, made_up)
    try:
        def press(target, button):
            e = QMouseEvent(QMouseEvent.MouseButtonPress, QPointF(20, 20), QPointF(20, 20), button, button,
                            Qt.NoModifier)
            QApplication.sendEvent(target, e)

        assert w.view == "all" and w.inner.currentWidget() is w.grid
        w.open_viewer(5)
        shown = w.viewer_items[w.viewer_row].asset_id
        press(w.picture, Qt.BackButton)  # the picture takes clicks itself: caught before it
        assert w.pages.currentWidget() is w.browse
        press(w.grid.viewport(), Qt.BackButton)
        assert w.view == "months"
        press(w.cards_area.viewport(), Qt.ForwardButton)
        assert w.view == "all"
        press(w.grid.viewport(), Qt.ForwardButton)
        assert w.pages.currentWidget() is w.viewer and w.viewer_items[w.viewer_row].asset_id == shown
        press(w.picture, Qt.ForwardButton)  # nothing more ahead
        assert w.pages.currentWidget() is w.viewer
        press(w.picture, Qt.BackButton)
        w.set_view("years")  # elsewhere meanwhile: Forward has nothing to go back into
        press(w.cards_area.viewport(), Qt.ForwardButton)
        assert w.view == "years" and w.pages.currentWidget() is w.browse and not w._forward
    finally:
        w.close()


def test_a_press_and_drag_beside_the_photos_selects_nothing(qapp, made_up):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    w = window(qapp, made_up)
    try:
        w.resize(900, 600)
        w.show()
        qapp.processEvents()
        vp = w.grid.viewport()

        def send(kind, x, y, buttons):
            e = QMouseEvent(kind, QPointF(x, y), QPointF(x, y), Qt.LeftButton, buttons, Qt.NoModifier)
            QApplication.sendEvent(vp, e)

        w.grid.scrollToBottom()
        qapp.processEvents()
        x, y = vp.width() - 3, vp.height() - 3  # past the last photo of the last row
        assert not w.grid.indexAt(QPointF(x, y).toPoint()).isValid()
        send(QMouseEvent.MouseButtonPress, x, y, Qt.LeftButton)
        send(QMouseEvent.MouseMove, 5, 5, Qt.LeftButton)
        send(QMouseEvent.MouseButtonRelease, 5, 5, Qt.NoButton)
        assert not w.chosen_items() and not w.selecting
        idx = w.model.index(3)  # a press on a photo, dragged away: nothing selected, nothing opened
        c = w.grid.visualRect(idx).center()
        send(QMouseEvent.MouseButtonPress, c.x(), c.y(), Qt.LeftButton)
        send(QMouseEvent.MouseMove, c.x() + 300, c.y() + 200, Qt.LeftButton)
        send(QMouseEvent.MouseButtonRelease, c.x() + 300, c.y() + 200, Qt.NoButton)
        assert not w.chosen_items() and w.pages.currentWidget() is w.browse
    finally:
        w.close()


def test_the_same_photos_stay_in_view_when_the_window_is_maximized(qapp, made_up):
    from skyfold.ui import photos_window as pw

    w = window(qapp, made_up)
    try:
        pw.PhotosWindow.zoom_level = 0
        w.resize(700, 500)
        w.show()
        qapp.processEvents()
        w._restore_anchor((w.model.items[12].asset_id, 84))
        qapp.processEvents()
        before = w._anchor()
        assert before[0] == w.model.items[12].asset_id or before[0] in {i.asset_id for i in w.model.items[9:15]}
        w.resize(1400, 900)
        qapp.processEvents()
        after = w._anchor()
        assert after[0] == before[0]
        vp = w.grid.viewport().width()
        cols = vp // w.tile
        assert vp - cols * w.tile < w.tile  # the photos fill the width: no empty column on the right
    finally:
        pw.PhotosWindow.zoom_level = 2
        w.close()


def test_pictures_are_ready_again_when_the_window_opens_soon_after_and_gone_after_a_sign_out(qapp, made_up):
    from PySide6.QtGui import QImage

    from skyfold.ui import photos_window as pw

    w = window(qapp, made_up)
    aid = w.model.items[0].asset_id if w.model.items else w.items[0].asset_id
    w.thumbs.put(aid, 160, QImage(160, 120, QImage.Format_RGB32))
    w.close()
    w2 = window(qapp, made_up)
    try:
        assert (aid, 160) in w2.thumbs.cache[False]  # kept warm for the same folder
        w2.clear()  # signed out: nothing kept
        assert not pw._WARM
        w3 = window(qapp, made_up)
        assert not w3.thumbs.cache[False]
        w3.close()
    finally:
        pw.drop_warm()


def test_a_picture_asked_ahead_and_then_on_screen_is_made_once_and_first(qapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QImage

    from skyfold.ui import photo_images as pi

    made = []
    monkeypatch.setattr(pi, "load_image", lambda path, size, full=False: (made.append(path.name), QImage(8, 8, QImage.Format_RGB32))[1])
    lo = pi.ImageLoader(threads=1)
    got = []
    lo.loaded.connect(lambda k, s, f, img: got.append(k))
    lo._pool.setMaxThreadCount(0) if hasattr(lo._pool, "setMaxThreadCount") else None
    lo.request("A", tmp_path / "a.jpg", 160, ahead=True)
    lo.request("A", tmp_path / "a.jpg", 160)  # now on screen: asked again, urgently
    lo.request("A", tmp_path / "a.jpg", 160)  # (and not a third time)
    lo._pool.setMaxThreadCount(1)
    assert pump(qapp, lambda: got == ["A"])
    lo._pool.waitForDone(2000)
    qapp.processEvents()
    assert made == ["a.jpg"] and got == ["A"]
    lo.stop()


def test_the_floating_bars_draw_smooth_shapes(qapp, made_up):
    w = window(qapp, made_up)
    try:
        w.resize(900, 600)
        w.show()
        qapp.processEvents()
        for bar in (w.pill, w.zoom_pill):
            img = bar.grab().toImage()
            assert not img.isNull()
        w.message("Hello")
        assert not w.toast.grab().toImage().isNull()
    finally:
        w.close()


def test_a_double_click_on_a_photo_only_opens_it(qapp, made_up):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    w = window(qapp, made_up)
    try:
        w.resize(900, 600)
        w.show()
        qapp.processEvents()
        vp = w.grid.viewport()
        c = w.grid.visualRect(w.model.index(4)).center()

        def send(target, kind, buttons):
            e = QMouseEvent(kind, QPointF(c), QPointF(c), Qt.LeftButton, buttons, Qt.NoModifier)
            QApplication.sendEvent(target, e)

        send(vp, QMouseEvent.MouseButtonPress, Qt.LeftButton)
        send(vp, QMouseEvent.MouseButtonRelease, Qt.NoButton)
        assert w.pages.currentWidget() is w.viewer
        send(w.picture, QMouseEvent.MouseButtonDblClick, Qt.LeftButton)  # the second click lands on the photo
        send(w.picture, QMouseEvent.MouseButtonRelease, Qt.NoButton)
        pump(qapp, lambda: False, timeout=QApplication.doubleClickInterval() / 1000 + 0.2)
        assert not w.picture.zoom and w.vtop.isVisible()  # not zoomed in, the bars not hidden
    finally:
        w.close()


def test_a_right_click_and_enter_never_select_by_themselves(qapp, made_up):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QKeyEvent, QMouseEvent
    from PySide6.QtWidgets import QApplication

    w = window(qapp, made_up)
    try:
        w.resize(900, 600)
        w.show()
        qapp.processEvents()
        c = QPointF(w.grid.visualRect(w.model.index(2)).center())
        QApplication.sendEvent(w.grid.viewport(), QMouseEvent(QMouseEvent.MouseButtonPress, c, c, Qt.RightButton,
                                                              Qt.RightButton, Qt.NoModifier))
        assert not w.chosen_items() and not w.selecting
        w.search.setFocus()
        QApplication.sendEvent(w.search, QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        qapp.processEvents()
        assert not w.selecting  # a dialog would press its default button: none here
    finally:
        w.close()


def test_only_the_photo_in_the_viewer_is_made_full_size(qapp, tmp_path):
    from skyfold.ui import photo_images as pi

    lo = pi.ImageLoader(threads=1)
    lo.request("A", tmp_path / "a.jpg", 0, full=True)
    lo.request("B", tmp_path / "b.jpg", 0, full=True)  # stepped on: A is no longer wanted
    assert not lo._still_wanted("A", 0, True) and lo._still_wanted("B", 0, True)
    lo.keep_only({"X"})
    lo.request("Y", tmp_path / "y.jpg", 160)  # asked for while scrolling: wanted
    assert lo._still_wanted("Y", 160, False)
    lo.stop()


def test_a_list_read_before_a_delete_finished_keeps_the_deleted_photos_out(qapp, made_up):
    w = window(qapp, made_up)
    try:
        items = list(w.items)
        w._load_started = 1.0
        w._gone = {"A3"}
        w._deleted_at = 2.0  # the delete finished after this read started
        w._loading = True
        w._library_loaded((w._fingerprint[0], ("x",)), items)
        assert "A3" not in w.by_id and len(w.items) == len(items) - 1
    finally:
        w.close()


def test_the_viewer_keeps_its_photos_when_the_library_is_read_again(qapp, made_up):
    w = window(qapp, made_up)
    try:
        w.set_key = "fav"
        w._show_set()
        w.open_viewer(0)
        shown = [i.asset_id for i in w.viewer_items]
        w.by_id[shown[0]].favorite = False  # unhearted, and iCloud agreed
        w._refresh_viewer_list()
        assert [i.asset_id for i in w.viewer_items] == shown
    finally:
        w.close()


def test_numbers_and_dates_follow_the_language(qapp):
    from datetime import datetime

    from skyfold import i18n
    from skyfold.ui import photos_window as pw

    try:
        i18n.set_language("en")
        assert pw._num(12345) == "12,345" and pw._day(datetime(2024, 3, 5)) == "5 March 2024"
        i18n.set_language("bs")
        assert pw._num(12345) == "12.345" and pw._day(datetime(2024, 3, 5)).startswith("5. ")
    finally:
        i18n.set_language("en")


def test_a_narrow_window_puts_the_sidebar_aside_and_shortens_the_bar(qapp, made_up):
    w = window(qapp, made_up)
    try:
        w.resize(1100, 700)
        w.show()
        qapp.processEvents()
        assert w.side.isVisible() == w.sidebar_open
        w.resize(640, 600)
        qapp.processEvents()
        assert not w.side.isVisible()
        qapp.processEvents()
        assert w.grid.width() >= w.browse.width() - 2 and w.browse.width() > 600  # the photos take its room
        w.set_selecting(True)
        assert all(not b.isVisible() for b in w._sel_extra)
        assert w.selbar.x() >= 0 and w.selbar.x() + w.selbar.width() <= w.browse.width()
    finally:
        w.close()


def test_a_file_changed_in_place_gets_a_new_picture(made_up):
    st, root = made_up
    before = {i.asset_id: i.key for i in lib.load(st, root)}
    rel = st._db.execute("SELECT rel_path FROM files WHERE asset_id='A5'").fetchone()[0]
    p = root / rel
    _picture(p, 200)  # overwritten in place: same file id, other content
    os.utime(p, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
    after = {i.asset_id: i.key for i in lib.load(st, root)}
    assert after["A5"] != before["A5"] and after["A6"] == before["A6"]


def test_closing_never_waits_for_a_slow_picture(qapp, tmp_path, monkeypatch):
    import threading as th

    from skyfold.ui import photo_images as pi

    gate = th.Event()

    def slow(path, size, full=False):
        gate.wait(5)
        from PySide6.QtGui import QImage
        return QImage(8, 8, QImage.Format_RGB32)

    monkeypatch.setattr(pi, "load_image", slow)
    holder = __import__("PySide6.QtCore", fromlist=["QObject"]).QObject()
    lo = pi.ImageLoader(holder, threads=1)
    lo.request("A", tmp_path / "a.jpg", 160)
    pump(qapp, lambda: lo._pool.activeThreadCount() == 1, timeout=2)
    t0 = time.monotonic()
    lo.stop()
    assert time.monotonic() - t0 < 0.5  # the tray isn't held up
    del holder  # the window goes; the loader lives on until its thread is done
    gate.set()
    assert pump(qapp, lambda: lo._pool.activeThreadCount() == 0, timeout=3)


def test_hearts_stop_at_the_first_lost_connection(tmp_path):
    from skyfold.worker import SyncWorker

    st = State(tmp_path / "s.db")
    calls, said = [], []

    def offline(aid, v):
        calls.append(aid)
        raise ConnectionError("no network")

    fake = SimpleNamespace(syncer=SimpleNamespace(remote=SimpleNamespace(set_favorite=offline)), state=st,
                           account_gen=0, favoritesSet=SimpleNamespace(emit=lambda *a: said.append(a)))
    SyncWorker.set_favorites(fake, [f"P{i}" for i in range(1000)], True, 0)
    assert calls == ["P0"] and len(said[-1][2]) == 1000


def test_two_failed_hearts_go_back_to_what_icloud_has(qapp, made_up):
    w = window(qapp, made_up)
    try:
        it = w.by_id["A3"]
        assert not it.favorite
        w.set_favorite([it], True)
        w.set_favorite([it], False)  # changed the mind before iCloud answered
        w.favorite_result(["A3"], True, ["A3"], "offline")
        w.favorite_result(["A3"], False, ["A3"], "offline")
        assert not w.by_id["A3"].favorite and not w._pending_fav
    finally:
        w.close()


def test_favourites_shows_only_favourites_once_icloud_agrees(qapp, made_up):
    w = window(qapp, made_up)
    try:
        w.set_key = "fav"
        w._show_set()
        assert len(w.model.items) == 2
        it = w.model.items[0]
        w.set_favorite([it], False)
        w.favorite_result([it.asset_id], False, [], "")
        assert len(w.model.items) == 1 and it.asset_id not in {i.asset_id for i in w.model.items}
    finally:
        w.close()


def test_back_to_a_photo_whose_big_picture_is_still_queued_gets_it(qapp, tmp_path):
    from skyfold.ui import photo_images as pi

    lo = pi.ImageLoader(threads=1)
    lo._pool.setMaxThreadCount(1)
    lo.request("A", tmp_path / "a.jpg", 0, full=True)
    lo.request("B", tmp_path / "b.jpg", 0, full=True)
    lo.request("A", tmp_path / "a.jpg", 0, full=True)  # back to A: its job is still queued
    assert lo._still_wanted("A", 0, True) and not lo._still_wanted("B", 0, True)
    lo.stop()


def test_a_photo_put_back_from_the_recycle_bin_shows_again(qapp, made_up):
    w = window(qapp, made_up)
    try:
        st, root = made_up
        it = w.by_id["A5"]
        w._gone_paths["A5"] = list(it.files)
        w._gone.add("A5")  # deleted here, the sync hasn't seen it yet; the file is back
        w._loaded_at = 0.0
        w.reload()
        assert pump(qapp, lambda: not w._loading and "A5" not in w._gone)
        assert "A5" in w.by_id
    finally:
        w.close()
