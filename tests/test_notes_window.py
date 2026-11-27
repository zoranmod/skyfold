"""The Notes window: notes read inside Skyfold, from the Notes database and the exported pages."""

import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from skyfold import notes_view as nv  # noqa: E402
from skyfold.notes_engine import NotesState, polish_note_html  # noqa: E402

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font:16px}} :root{{--hl1-bg:#BA55D380}}</style><script>alert(1)</script></head><body>
<style>.x{{}}</style><div class="note-content"><h1>{title}</h1><p>{text}</p>
<ul><li><input type="checkbox" disabled checked> milk</li><li><input type="checkbox" disabled> bread</li></ul>
<p><span style="font-weight:bold;color:#ff0000">bold red</span> <span style="background-color:var(--hl2-bg)">marked</span></p>
<table><tr><td colspan="2" onclick="x()">cell</td></tr></table>
<img src="https://example.com/track.png"><img src="../_assets/n1/photo.png" alt="photo">
<audio controls class="attachment" src="../_assets/n1/memo.m4a"></audio>
<object data="../_assets/n1/doc.pdf#toolbar=0" type="application/pdf"><a href="../_assets/n1/doc.pdf">doc.pdf</a></object>
{scans}
<a href="javascript:alert(1)">bad</a> <a href="https://apple.com">good</a>
</div></body></html>"""


def scan(name, pages):
    imgs = "".join(f'<img class="sf-page" src="../_assets/n1/{name}.p{i}.png" alt="">' for i in range(1, pages + 1))
    return f'<div class="sf-pdf">{imgs}<a class="sf-open" href="../_assets/n1/{name}">Open the original PDF</a></div>'


def page(title="Groceries", text="Buy čaj and đuveč", scans=None):
    p = PAGE.format(title=title, text=text, scans=scan("scan.pdf", 5) if scans is None else scans)
    return polish_note_html(p, "../Notes.html", "All notes", "Updated today")


def test_only_the_note_content_is_kept_and_nothing_runs_or_loads():
    clean = nv.note_html(page())
    body = clean.body
    assert "<script" not in body and "alert" not in body
    assert "javascript:" not in body and "onclick" not in body and "<style" not in body
    assert "sf-nav" not in body and "All notes" not in body and "Updated today" not in body  # Skyfold's bar
    assert "example.com" not in body  # nothing from the internet
    assert '<img src="sfimg:0" alt="photo">' in body and clean.images[0] == "../_assets/n1/photo.png"
    assert nv.CHECK_ON + " milk" in body  # ticked stays ticked
    assert nv.CHECK_OFF + " bread" in body  # unticked stays unticked
    assert "color:#ff0000" not in body and "font-weight:bold" in body  # colour dropped (dark themes)
    assert 'class="hl2"' in body
    assert 'colspan="2"' in body and 'border="1"' in body
    assert "<audio" not in body and "<object" not in body
    assert "_assets/n1/memo.m4a" in body and "_assets/n1/doc.pdf" in body  # as links to open
    assert 'href="https://apple.com"' in body
    assert clean.hidden == 0 and len(clean.images) == 1 + 5 and "skyfold:more" not in body  # every page
    full = nv.note_html(page(), all_pages=True)
    assert full.hidden == 0 and len(full.images) == 1 + 5 and "skyfold:more" not in full.body


def test_pictures_reach_the_viewer_only_as_ids():
    body = nv.note_html(page()).body
    srcs = re.findall(r'<img src="([^"]*)"', body)
    assert srcs and all(src.startswith("sfimg:") for src in srcs)


def test_more_pages_of_one_scan_only(monkeypatch):
    monkeypatch.setattr(nv, "SCAN_PAGES", 3)  # (60 in Skyfold: only a very long scan has a "show more")
    two = scan("a.pdf", 8) + scan("b.pdf", 8)
    first = nv.note_html(page(scans=two))
    assert first.hidden == 10 and "skyfold:more:0" in first.body and "skyfold:more:1" in first.body
    more = nv.note_html(page(scans=two), shown={0: 6})
    assert len([s for s in more.images if "a.pdf" in s]) == 6
    assert len([s for s in more.images if "b.pdf" in s]) == 3


def test_cleaner_edge_cases():
    single = "<p>header</p><div class='x note-content'><p>inside</p></div><p>footer</p>"
    body = nv.note_html(single).body
    assert "inside" in body and "header" not in body and "footer" not in body
    stray = '<div class="note-content"><p>one</p></span></b><p>two</p></div><p>after</p>'
    body = nv.note_html(stray).body
    assert "one" in body and "two" in body and "after" not in body
    ol = '<div class="note-content"><ol start="4"><li>four</li></ol></div>'
    assert '<ol start="4">' in nv.note_html(ol).body


def test_text_for_search_and_first_line():
    text = nv.plain_text(nv.note_html(page()).body)
    assert text.splitlines()[0] == "Groceries"
    assert "Buy čaj and đuveč" in text and "cell" in text
    folded = nv.fold(text)
    assert nv.snippet(text, folded, "Groceries") == "Buy čaj and đuveč"
    assert "đuveč" in nv.snippet(text, folded, "Groceries", "duvec")
    assert nv.fold("Čaj ĐUVEČ") == "caj duvec"
    assert nv.fold("C\u030caj") == "caj"  # decomposed č too
    f, pos = nv.fold_map("😀 Plan")
    i = f.find("plan")
    assert (pos[i], pos[i + 4]) == (3, 7)  # Qt counts the emoji as two


def test_search_snippets_stay_fast():
    text = ("riječ " * 1750)[:10_500]
    folded = nv.fold(text)
    start = time.perf_counter()
    for _ in range(240):
        nv.snippet(text, folded, "Title", "rijec")
    assert time.perf_counter() - start < 0.2


def test_matching_ranks_title_folder_text():
    r = nv.Row("1", "a.html", "Šoping lista", "Kuća/Lists", None, False)
    assert nv.matches(r, None, ["soping"]) == 3
    assert nv.matches(r, None, ["kuca"]) == 2
    assert nv.matches(r, "mlijeko i hljeb", ["hljeb"]) == 1
    assert nv.matches(r, "mlijeko", ["soping", "mlijeko"]) == 1
    assert nv.matches(r, "mlijeko", ["soping", "kafa"]) == 0
    assert nv.matches(r, None, []) == 3


def test_assets_only_from_the_notes_folder_assets(tmp_path):
    root = tmp_path / "Notes"
    (root / "Work" / "_assets" / "n1").mkdir(parents=True)
    (root / "_assets" / "n2").mkdir(parents=True)
    pg = root / "Work" / "Plan.html"
    top = root / "Plan.html"
    assert nv.resolve_asset(root, pg, "../_assets/n2/a%20b.png") == (root / "_assets" / "n2" / "a b.png").resolve()
    assert nv.resolve_asset(root, top, "_assets/n2/x.png") == (root / "_assets" / "n2" / "x.png").resolve()
    assert nv.resolve_asset(root, pg, "_assets/n1/x.png") is None  # an _assets folder elsewhere isn't Skyfold's
    for bad in ("../../secret/_assets/x.png", "../Other.html", "https://example.com/_assets/x.png",
                "file:///C:/Windows/win.ini", "C:/Windows/_assets/x.png", "/_assets/x.png", "\\\\server\\share\\x.png",
                "%2F%2Fserver%2Fshare%2Fx.png", "..%2F..%2Fx.png", "C%3A/x.png", "../_assets/../../x.png",
                "../_assets/n2/x.png%00.png", "//?/C:/x.png"):
        assert nv.resolve_asset(root, pg, bad) is None, bad


def test_a_locked_note_never_has_a_page(tmp_path):
    st = NotesState(tmp_path / "s.db")
    st.put("a", "Old copy.html", "Secret", "Notes", "2026-11-01T10:00:00", True)  # readable copy still there
    st.put("b", "Open.html", "Open", "Notes", "2026-11-02T10:00:00", False)
    rows = nv.rows_from(st)
    assert [r.id for r in rows] == ["b", "a"]  # newest first
    locked = rows[1]
    assert locked.locked and locked.rel is None and nv.page_path(tmp_path, locked) is None


# --- the window ---------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _defaults():
    from skyfold.ui.notes_window import NotesWindow
    NotesWindow.zoom, NotesWindow.sort, NotesWindow.own_theme = 1.0, "new", None
    NotesWindow.resume = None
    yield
    NotesWindow.zoom, NotesWindow.sort, NotesWindow.own_theme = 1.0, "new", None
    NotesWindow.resume = None


def pump(qapp, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def library(tmp_path):
    from PySide6.QtGui import QColor, QImage
    root = tmp_path / "Notes"
    (root / "Home").mkdir(parents=True)
    (root / "_assets" / "n1").mkdir(parents=True)
    (root / "Home" / "Groceries.html").write_text(page(), encoding="utf-8")
    (root / "Home" / "Old.html").write_text(page("Diary", "my private thoughts 😀 Plan", ""), encoding="utf-8")
    img = QImage(3000, 1000, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    img.save(str(root / "_assets" / "n1" / "photo.png"))
    (tmp_path / "outside.png").write_bytes((root / "_assets" / "n1" / "photo.png").read_bytes())
    st = NotesState(tmp_path / "s.db")
    st.put("g", "Home/Groceries.html", "Groceries", "Home", "2026-11-20T10:00:00", False)
    st.put("d", "Home/Old.html", "Diary", "Home/Private", "2026-11-10T10:00:00", False)
    st.put("l", None, "Bank", "Notes", "2026-11-05T10:00:00", True)
    return st, root


def window(qapp, library, mode="dark", source=None):
    from skyfold.ui import theme
    from skyfold.ui.notes_window import NotesWindow
    st, root = library
    w = NotesWindow(theme.palette(mode), source or (lambda: (st, root)))
    w.show()
    assert pump(qapp, lambda: not w.indexing)
    return w


def ids(w):
    from PySide6.QtCore import Qt
    return [w.notes.item(i).data(Qt.UserRole) for i in range(w.notes.count())]


def select(w, nid):
    for i, x in enumerate(ids(w)):
        if x == nid:
            w.notes.setCurrentRow(i)


def test_folders_look_like_apple_notes(qapp, library):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QImage, QMouseEvent
    from skyfold.ui.notes_window import FOLDER_COUNT, FOLDER_PATH
    st, root = library
    st.put("x", None, "Gone", "Recently Deleted", "2026-11-01T10:00:00", True)
    w = window(qapp, library)
    tops = [w.folders.topLevelItem(i) for i in range(w.folders.topLevelItemCount())]
    # name and count apart (count drawn on the right); Notes first, Recently Deleted last
    assert [i.text(0) for i in tops] == ["All notes", "Notes", "Home", "Recently Deleted"]
    assert [i.data(0, FOLDER_COUNT) for i in tops] == [4, 1, 1, 1]
    assert w.folders.indentation() == 0 and not w.folders.rootIsDecorated()  # one highlight per row
    home = tops[2]
    assert home.childCount() == 1 and home.isExpanded()
    w.folders.setCurrentItem(home.child(0))
    img = QImage(w.folders.size(), QImage.Format_ARGB32)
    w.folders.render(img)  # the delegate paints without errors
    # a click on the chevron folds a folder; the fold survives a refresh
    rect = w.folders.visualItemRect(home)
    pos = QPointF(w.folders.viewport().width() - 12, rect.center().y())
    ev = QMouseEvent(QEvent.MouseButtonPress, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    w.folders.mousePressEvent(ev)
    assert not home.isExpanded() and w.folders.currentItem() is home and w.folder == "Home"
    w._fill_folders()
    home = [w.folders.topLevelItem(i) for i in range(w.folders.topLevelItemCount())][2]
    assert home.data(0, FOLDER_PATH) == "Home" and not home.isExpanded()
    w.close()


def test_window_lists_reads_and_searches(qapp, library):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QTextDocument
    w = window(qapp, library)
    assert ids(w) == ["g", "d", "l"]  # newest first, the first shown at once
    assert "Buy čaj" in w.reader.toPlainText()
    # pictures: by id only, fitted to the window, on white; a file name is never loaded
    img = w.reader.loadResource(QTextDocument.ImageResource, QUrl("sfimg:0"))
    assert not img.isNull() and img.width() < 3000 and not img.hasAlphaChannel()
    for bad in ("../_assets/n1/photo.png", "../../outside.png", "https://example.com/x.png", "sfimg:99"):
        assert w.reader.loadResource(QTextDocument.ImageResource, QUrl(bad)) is None, bad
    names = [w.folders.topLevelItem(i).text(0) for i in range(w.folders.topLevelItemCount())]
    assert names[0].startswith("All notes") and any(n.startswith("Home") for n in names)
    w.search.setText("private thoughts")
    assert pump(qapp, lambda: ids(w) == ["d"])
    assert "my private thoughts" in w.reader.toPlainText() and w.reader.extraSelections()
    w.search.setText("DUVEC")
    assert pump(qapp, lambda: ids(w) == ["g"])
    w.search.setText("plan")  # after an emoji: exactly the word is marked
    assert pump(qapp, lambda: ids(w) == ["d"])
    marks = [s.cursor.selectedText() for s in w.reader.extraSelections()]
    assert marks and all(m == "Plan" for m in marks)
    w.search.clear()
    assert pump(qapp, lambda: len(ids(w)) == 3)
    select(w, "l")
    assert not w.reader.isVisible() and "locked" in w.empty.text()
    w.close()


def test_a_note_locked_while_open_disappears(qapp, library):
    st, root = library
    w = window(qapp, library, "light")
    assert "Buy čaj" in w.reader.toPlainText()
    st.put("g", "Home/Groceries.html", "Groceries", "Home", "2026-11-20T10:00:00", True)  # copy still there
    w.refresh()
    qapp.processEvents()
    assert "Buy" not in w.reader.toPlainText() and not w.reader.isVisible()
    assert "g" not in w.texts
    w.search.setText("čaj")
    assert pump(qapp, lambda: w.notes.count() == 0)
    w.close()


def test_old_index_results_never_come_back(qapp, library):
    st, root = library
    w = window(qapp, library)
    old_gen = w.indexer.gen
    stale = {"g": (("Home/Groceries.html", "2026-11-20T10:00:00"), "deleted secret", "deleted secret")}
    for nid in ("g", "d", "l"):
        st.drop(nid)
    w.refresh()
    assert not w.rows and not w.texts and not w.indexing
    w._on_batch(old_gen, stale)  # arrives late
    assert not w.texts
    # same id, changed page: a result for the old page is refused
    st.put("g", "Home/Groceries.html", "Groceries", "Home", "2026-11-21T10:00:00", False)
    w.refresh()
    w._on_batch(w.indexer.gen, stale)
    assert "deleted secret" not in str(w.texts)
    w.close()


def test_every_way_of_closing_forgets_the_notes(qapp, library):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    w = window(qapp, library)
    assert w.texts and w.reader.toPlainText()
    gen = w.indexer.gen
    QTest.keyClick(w, Qt.Key_Escape)  # search empty: Esc closes
    assert w.closed and not w.texts and not w.rows and w.reader.page is None
    assert w.indexer.gen != gen and not w.reader.toPlainText()
    w2 = window(qapp, library)
    w2.close()  # the close button
    assert w2.closed and not w2.texts
    w3 = window(qapp, library)
    w3.clear()  # sign-out / another Apple ID
    assert w3.closed and not w3.texts and not w3.isVisible()


def test_signed_out_the_notes_are_not_shown(qapp, library, tmp_path, monkeypatch):
    from skyfold import app as app_mod, auth
    st, root = library
    monkeypatch.setattr(auth, "SIGNED_OUT", tmp_path / "signed-out")
    ctl = SimpleNamespace(worker=SimpleNamespace(notes_state=st),
                          settings=SimpleNamespace(notes_enabled=True, notes_dir=str(root)))
    assert app_mod.Controller._notes_source(ctl) is not None
    (tmp_path / "signed-out").write_text("")
    assert app_mod.Controller._notes_source(ctl) is None
    opened = []
    ctl.open_login = lambda: opened.append("login")
    ctl.open_settings = lambda page=0: opened.append("settings")
    ctl._dialogs = {}
    ctl._show_dialog = lambda *a: opened.append("notes")
    app_mod.Controller.open_notes(ctl)
    assert opened == ["login"]


def test_temporarily_unreadable_note_comes_back(qapp, library, monkeypatch):
    w = window(qapp, library)
    real = Path.read_text
    broken = {"on": True}

    def read_text(self, *a, **kw):
        if broken["on"] and self.name == "Groceries.html":
            raise PermissionError("locked by another program")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", read_text)
    w.current_key = None
    w._show_current()
    assert "can't be read right now" in w.empty.text() and w._note_failed
    broken["on"] = False
    w._note_failed -= 60
    w._retry()
    assert "Buy čaj" in w.reader.toPlainText()
    w.close()


def test_sort_zoom_full_screen_and_keys(qapp, library):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from skyfold.ui.notes_window import NotesWindow
    w = window(qapp, library)
    w.sorter.setCurrentIndex(w.sorter.findData("old"))
    assert ids(w) == ["l", "d", "g"]
    w.sorter.setCurrentIndex(w.sorter.findData("title"))
    assert ids(w) == ["l", "d", "g"]  # Bank, Diary, Groceries
    w.sorter.setCurrentIndex(w.sorter.findData("new"))
    before = w.reader.document().defaultFont().pointSizeF()
    # the size is shown for a moment ("110%") and in the "More" menu, which also changes it
    assert not w.zoom_hint.isVisible() and not w.zoom_reset.isEnabled()
    QTest.keyClick(w, Qt.Key_Plus, Qt.ControlModifier)
    assert w.reader.document().defaultFont().pointSizeF() > before and w.zoom_hint.text() == "110%"
    assert w.zoom_hint.isVisible() and "110%" in w.zoom_reset.text() and w.zoom_reset.isEnabled()
    assert pump(qapp, lambda: not w.zoom_hint.isVisible())  # then it fades away
    for _ in range(30):
        w.set_zoom(1)
    assert w.zoom_hint.text() == "240%" and "Buy" in w.reader.toPlainText() and not w.zoom_in.isEnabled()
    QTest.keyClick(w, Qt.Key_0, Qt.ControlModifier)
    assert w.zoom_hint.text() == "100%" and not w.zoom_reset.isEnabled()
    assert abs(w.reader.document().defaultFont().pointSizeF() - before) < 0.01
    w.zoom_out.trigger()
    assert w.zoom_hint.text() == "90%" and NotesWindow.zoom == 0.9
    w.zoom_reset.trigger()
    assert NotesWindow.zoom == 1.0
    assert all(a in w.more.menu().actions() for a in (w.zoom_in, w.zoom_out, w.zoom_reset))
    # Enter in the search box: to the results, never a button
    w.search.setFocus()
    QTest.keyClick(w.search, Qt.Key_Return)
    assert NotesWindow.zoom == 1.0 and pump(qapp, lambda: w.notes.hasFocus())
    # F6 from a locked note moves on
    select(w, "l")
    w.notes.setFocus()
    QTest.keyClick(w.notes, Qt.Key_F6)
    assert pump(qapp, lambda: not w.notes.hasFocus())
    assert w.windowFlags() & Qt.WindowMaximizeButtonHint
    w.toggle_full_screen()
    assert w.isFullScreen()
    QTest.keyClick(w, Qt.Key_Escape)
    assert not w.isFullScreen() and w.isVisible()
    w.close()


def test_the_zoom_hint_does_not_fade_with_windows_animations_off(qapp, library, monkeypatch):
    from skyfold.ui import notes_window
    w = window(qapp, library)
    hint = w.zoom_hint
    monkeypatch.setattr(notes_window, "animations_on", lambda: False)
    hint.flash("110%")
    assert hint.isVisible()
    hint.timer.timeout.emit()  # its moment is over
    assert not hint.isVisible() and hint.fade.state() != hint.fade.State.Running
    monkeypatch.setattr(notes_window, "animations_on", lambda: True)
    hint.flash("120%")
    hint.timer.timeout.emit()
    assert hint.isVisible() and hint.fade.state() == hint.fade.State.Running  # (with them on, it fades)
    w.close()


@pytest.mark.parametrize("lang", ["en", "bs"])
def test_fits_a_small_screen(qapp, library, lang):
    from skyfold import i18n
    from skyfold.ui import theme
    i18n.set_language(lang)
    c = theme.palette("light")
    qapp.setStyleSheet(theme.stylesheet(c))
    try:
        w = window(qapp, library, "light")
        assert w.minimumSizeHint().width() < 900, w.minimumSizeHint()
        w.resize(800, 600)
        qapp.processEvents()
        assert w.width() <= 820
        w.close()
    finally:
        i18n.set_language("en")
        qapp.setStyleSheet("")


def test_the_window_can_have_its_own_theme(qapp, library):
    from skyfold.ui import theme
    from skyfold.ui.notes_window import NotesWindow
    w = window(qapp, library, "light")
    light = w.app_c
    seen = []
    for _ in range(4):
        w._cycle_theme()
        seen.append((NotesWindow.own_theme, w.c["bg"]))
    assert seen == [("light", theme.LIGHT["bg"]), ("dark", theme.DARK["bg"]), ("oled", theme.OLED["bg"]),
                    (None, light["bg"])]
    w.set_theme("dark")
    assert w.theme_actions["dark"].isChecked()
    w.apply_palette(theme.palette("oled"))  # Skyfold's theme changes: the window keeps its own
    assert w.c is theme.DARK and theme.DARK["text"] in w.reader.document().defaultStyleSheet()
    w.close()


def test_pictures_follow_zoom_and_screen_scaling(qapp, library, monkeypatch):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QColor, QImage, QTextDocument
    from skyfold.ui.notes_window import NotesWindow
    st, root = library
    small = QImage(300, 200, QImage.Format_RGB32)
    small.fill(QColor("#336699"))
    small.save(str(root / "_assets" / "n1" / "photo.png"))
    w = window(qapp, library)
    monkeypatch.setattr(w.reader, "devicePixelRatioF", lambda: 2.0)
    load = lambda: w.reader.loadResource(QTextDocument.ImageResource, QUrl("sfimg:0"))  # noqa: E731
    img = load()
    assert img.width() == 600 and img.deviceIndependentSize().width() == 300  # as big as in a browser
    w.set_zoom(1)
    w.set_zoom(1)  # 125 %
    img = load()
    assert round(img.deviceIndependentSize().width()) == 375
    NotesWindow.zoom = 2.4
    assert load().deviceIndependentSize().width() <= w.reader.viewport().width()  # never wider than the note
    w.close()


def test_first_line_skips_attachment_labels():
    page = ('<div class="note-content"><h1>Kitchen</h1><p><a class="sf-open" href="x.pdf">Open the original PDF</a></p>'
            '<audio src="../_assets/a/memo.m4a"></audio><p>Ukupno 200 KM</p></div>')
    text = nv.plain_text(nv.note_html(page, all_pages=True).body)
    assert nv.snippet(text, nv.fold(text), "Kitchen") == "Ukupno 200 KM"
    own = nv.plain_text(nv.note_html('<div class="note-content"><p>\U0001F4CE Bring a passport</p></div>').body)
    assert nv.snippet(own, nv.fold(own), "Trip") == "\U0001F4CE Bring a passport"  # the note's own line stays


def test_search_preview_contains_the_match_after_lone_accents():
    text = "Č" * 200 + " the passport is here"
    snip = nv.snippet(text, nv.fold(text), "T", query="passport")
    assert "passport" in snip


def test_positions_for_a_long_paragraph_are_cheap_and_right():
    text = "\U0001F600 Čaj " * 50_000 + "kraj"
    folded = nv.fold(text)
    i = folded.find("kraj")
    a, b = nv.fold_positions(text, [i, i + 4])
    _f, pos = nv.fold_map(text)
    assert (a, b) == (pos[i], pos[i + 4])
    assert nv.fold_positions("", [0]) == [0]


def test_unquoted_wrapper_hides_the_page_header():
    page = '<p>Header text</p><div class=note-content><p>Body</p></div><p>Footer text</p>'
    body = nv.note_html(page).body
    assert "Body" in body and "Header" not in body and "Footer" not in body


def test_assets_folder_that_is_a_link_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "Notes"
    (root / "_assets" / "n1").mkdir(parents=True)
    (root / "_assets" / "n1" / "p.png").write_bytes(b"x")
    pg = root / "a.html"
    assert nv.resolve_asset(root, pg, "_assets/n1/p.png") is not None
    monkeypatch.setattr(nv, "_is_junction", lambda p: p.name == "_assets")  # _assets is a junction
    assert nv.resolve_asset(root, pg, "_assets/n1/p.png") is None


def test_more_pages_never_show_another_picture(qapp, library, monkeypatch):
    """After "show more" the same picture id may point to another file: the right one is shown."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QColor, QImage, QTextDocument
    st, root = library
    d = root / "_assets" / "scan"
    d.mkdir()
    for i in range(5):
        img = QImage(40, 40, QImage.Format_RGB32)
        img.fill(QColor("#ff0000"))
        img.save(str(d / f"p{i}.png"))
    blue = QImage(40, 40, QImage.Format_RGB32)
    blue.fill(QColor("#0000ff"))
    blue.save(str(d / "blue.png"))
    pages = "".join(f'<img class="sf-page" src="../_assets/scan/p{i}.png">' for i in range(5))
    (root / "Home" / "Groceries.html").write_text(
        f'<div class="note-content"><h1>Groceries</h1><div class="sf-pdf">{pages}</div>'
        f'<img src="../_assets/scan/blue.png"></div>', encoding="utf-8")
    monkeypatch.setattr(nv, "SCAN_PAGES", 3)
    w = window(qapp, library)
    load = lambda n: w.reader.loadResource(QTextDocument.ImageResource, QUrl(f"sfimg:{n}"))  # noqa: E731
    assert load(3).pixelColor(5, 5) == QColor("#0000ff")  # three pages, then the blue picture
    assert "Show 2 more pages" in w.reader.toPlainText()
    w._on_link(QUrl("skyfold:more:0"))
    qapp.processEvents()  # the note is shown again right after the click is handled
    assert load(3).pixelColor(5, 5) == QColor("#ff0000")  # now the fourth page of the scan
    assert load(5).pixelColor(5, 5) == QColor("#0000ff")  # all 5 pages now, as the link said
    assert "more pages" not in w.reader.toPlainText()
    w.close()


def test_many_big_pictures_get_less_sharp_then_a_note(qapp, library, monkeypatch):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QColor, QImage, QTextDocument
    from skyfold.ui import notes_window as nw
    st, root = library
    d = root / "_assets" / "many"
    d.mkdir()
    for i in range(12):
        img = QImage(600, 850, QImage.Format_RGB32)
        img.fill(QColor("#00aa00"))
        img.save(str(d / f"{i}.png"))
    imgs = "".join(f'<img src="../_assets/many/{i}.png">' for i in range(12))
    (root / "Home" / "Groceries.html").write_text(f'<div class="note-content"><p>Pics</p>{imgs}</div>',
                                                  encoding="utf-8")
    monkeypatch.setattr(nw, "IMAGE_BUDGET", 30_000_000)
    w = window(qapp, library)
    w.resize(1100, 700)
    qapp.processEvents()
    monkeypatch.setattr(w.reader, "devicePixelRatioF", lambda: 2.0)
    w.reader._drop_cache()
    got = [w.reader.loadResource(QTextDocument.ImageResource, QUrl(f"sfimg:{i}")) for i in range(12)]
    assert all(g is not None and not g.isNull() for g in got)  # nothing simply missing
    sizes = [(g.width(), round(g.deviceIndependentSize().width())) for g in got]
    assert sizes[0][0] == 2 * sizes[0][1]  # sharp while there is room
    assert any(px == pt for px, pt in sizes) and len({pt for _px, pt in sizes}) == 1  # then less sharp, same size
    assert w.reader._bytes <= 30_000_000
    monkeypatch.setattr(nw, "IMAGE_BUDGET", w.reader._bytes)  # full: a visible note instead of the picture
    w.reader._cache.clear()
    note = w.reader.loadResource(QTextDocument.ImageResource, QUrl("sfimg:0"))
    assert note is not None and not note.isNull() and note.devicePixelRatio() == 0.5  # cheap, picture-sized
    assert not w.reader._cache  # not kept: tried again next time
    w.close()


def test_closing_forgets_search_and_titles(qapp, library):
    w = window(qapp, library)
    w.search.setText("private thoughts")
    assert pump(qapp, lambda: ids(w) == ["d"])
    w.close()
    assert w._hl_query == "" and w._fingerprint is None and not w.texts and w.folder == ""


def test_a_note_that_comes_back_is_shown_again(qapp, library, monkeypatch):
    from skyfold.ui import notes_window as nw
    st, root = library
    w = window(qapp, library)
    page = root / "Home" / "Groceries.html"
    saved = page.read_text(encoding="utf-8")
    page.unlink()
    w.current_key = None
    w._show_current()
    assert not w.reader.isVisible()
    page.write_text(saved, encoding="utf-8")
    monkeypatch.setattr(nw, "RETRY_NOTE", -1.0)
    w._retry()
    assert w.reader.isVisible() and "Buy" in w.reader.toPlainText()
    w.close()


def test_deep_folders_keep_their_names_visible(qapp, library):
    from PySide6.QtCore import Qt
    st, root = library
    deep = "/".join(f"Level{i}" for i in range(18))
    st.put("z", None, "Deep", deep, "2026-11-01T10:00:00", True)
    w = window(qapp, library)
    leaf = [it for it in w._folder_items() if it.text(0) == "Level17"][0]
    assert "Level0 › Level1" in leaf.data(0, Qt.ToolTipRole)
    assert leaf.data(0, Qt.AccessibleDescriptionRole) == leaf.data(0, Qt.ToolTipRole)
    w.folders.setFixedWidth(190)
    w.folders.setCurrentItem(leaf)
    qapp.processEvents()
    assert not w.folders.grab().toImage().isNull()
    w.close()


def test_sign_out_holds_even_if_its_mark_cannot_be_written(tmp_path, monkeypatch):
    from pathlib import Path
    from skyfold import auth
    monkeypatch.setattr(auth, "SIGNED_OUT", tmp_path / "signed-out")
    monkeypatch.setattr(auth, "SESSION_DIR", tmp_path / "session")
    monkeypatch.setattr(auth, "_CURRENT", auth._CURRENT)  # put back afterwards (Sign out replaces it)

    def refuse(*a, **k):
        raise PermissionError("no")

    assert not auth.signed_out()
    with monkeypatch.context() as m:
        m.setattr(Path, "write_text", refuse)
        auth.logout()
    assert auth.signed_out() and not (tmp_path / "signed-out").exists()


def test_crash_log_is_set_up(tmp_path, monkeypatch):
    import faulthandler
    from skyfold import app
    calls = []
    monkeypatch.setattr(app, "LOG_FILE", tmp_path / "skyfold.log")
    monkeypatch.setattr(faulthandler, "enable", lambda **k: calls.append(k))
    monkeypatch.setattr(app, "_CRASH_FILE", None)
    app.setup_crash_log()
    assert calls and calls[0]["all_threads"] and calls[0]["file"] is app._CRASH_FILE
    assert "started" in (tmp_path / "crash.log").read_text(encoding="utf-8")  # (conftest closes it)


def test_account_change_closes_notes_on_the_ui_thread_and_only_for_another_account(qapp):
    """A worker signal handled by a lambda ran in the worker's thread: closing the Notes window from
    there froze or crashed Skyfold (1.0.27). The handler is a method of a UI-thread object."""
    import re
    import threading
    from pathlib import Path
    from PySide6.QtCore import QObject, QThread, Qt, Signal
    from skyfold import app

    src = Path(app.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\b(?:w|self\.worker)\.\w+\.connect\(\s*lambda", src)  # every worker signal: a method

    class Receiver(QObject):
        _on_account_changed = app.Controller._on_account_changed

        def __init__(self):
            super().__init__()
            self._notes_account = "a@example.invalid"
            self.closed_in = []

        def _close_notes(self):
            self.closed_in.append(threading.current_thread() is threading.main_thread())

        def _close_photos(self):  # (the Photos window closes with it; checked in test_photos_window)
            pass

    class Sender(QObject):
        accountChanged = Signal(str)

    rec, snd, th = Receiver(), Sender(), QThread()
    snd.moveToThread(th)
    snd.accountChanged.connect(rec._on_account_changed, Qt.QueuedConnection)
    th.start()
    try:
        for who in ("a@example.invalid", "b@example.invalid", "b@example.invalid", ""):
            threading.Thread(target=snd.accountChanged.emit, args=(who,)).start()
            assert pump(qapp, lambda: rec._notes_account == who, 2.0)
            qapp.processEvents()
    finally:
        th.quit()
        th.wait()
    assert rec.closed_in == [True, True]  # same account at start: kept; another one, and signed out: closed


def test_a_long_scan_opens_whole_and_loads_pages_as_they_come_into_view(qapp, library, monkeypatch):
    from PySide6.QtGui import QColor, QImage
    st, root = library
    d = root / "_assets" / "long"
    d.mkdir()
    img = QImage(600, 850, QImage.Format_RGB32)
    img.fill(QColor("#ffffff"))
    for i in range(44):
        img.save(str(d / f"p{i}.png"))
    pages = "".join(f'<img class="sf-page" src="../_assets/long/p{i}.png">' for i in range(44))
    (root / "Home" / "Groceries.html").write_text(
        f'<div class="note-content"><h1>Groceries</h1><div class="sf-pdf">{pages}</div></div>', encoding="utf-8")
    w = window(qapp, library)
    w.resize(1000, 700)
    calls = []
    real = w.reader.loadResource
    monkeypatch.setattr(w.reader, "loadResource", lambda kind, url: (calls.append(url.toString()), real(kind, url))[1])
    w.current_key = None
    w._show_current()
    w.reader.viewport().repaint()
    qapp.processEvents()
    assert "more pages" not in w.reader.toPlainText()  # all 44 pages, no "show more"
    assert 0 < len(set(calls)) < 10  # only the pages in view were read
    w.reader.verticalScrollBar().setValue(w.reader.verticalScrollBar().maximum())
    w.reader.viewport().repaint()
    qapp.processEvents()
    assert "sfimg:43" in calls  # the last page, once scrolled to
    w.close()


def test_the_window_comes_back_where_it_was_until_sign_out(qapp, library):
    from PySide6.QtCore import Qt
    from skyfold.ui.notes_window import NotesWindow
    w = window(qapp, library)
    for it in w._folder_items():
        if it.data(0, Qt.UserRole) == "Home/Private":
            w.folders.setCurrentItem(it)
    assert ids(w) == ["d"]
    w.close()
    assert NotesWindow.resume and NotesWindow.resume["id"] == "d"
    w2 = window(qapp, library)
    assert w2.folder == "Home/Private" and w2.current == "d" and "my private thoughts" in w2.reader.toPlainText()
    w2.clear()  # Sign out / another Apple ID
    assert NotesWindow.resume is None
    w3 = window(qapp, library)
    assert w3.folder == "" and ids(w3)[0] == "g"
    w3.close()


def test_reopened_window_is_at_the_same_place_at_once(qapp, library):
    from skyfold.ui.notes_window import NotesWindow
    st, root = library
    paras = "".join(f"<p>Line {i} of a long note</p>" for i in range(400))
    (root / "Home" / "Groceries.html").write_text(f'<div class="note-content"><h1>Groceries</h1>{paras}</div>',
                                                  encoding="utf-8")
    w = window(qapp, library)
    bar = w.reader.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    qapp.processEvents()
    pos = w._top_position()
    assert pos > 1000
    w.close()
    assert NotesWindow.resume["pos"] == pos
    w2 = NotesWindow(w.app_c, w.source)
    seen = []
    w2.reader.verticalScrollBar().valueChanged.connect(seen.append)
    w2.show()
    qapp.processEvents()
    assert abs(w2._top_position() - pos) < 60  # the same line (or the one next to it)
    assert len(set(seen)) <= 2  # put there, not scrolled there step by step
    w2.set_zoom(1)  # zooming keeps the same text in view
    qapp.processEvents()
    assert abs(w2._top_position() - pos) < 60
    w2.close()


def test_reader_uses_one_literal_heading_and_keeps_the_original_body(qapp, library):
    from PySide6.QtGui import QTextCursor

    st, root = library
    title = "Dinner <menu>"
    (root / "Home" / "Dinner.html").write_text(
        '<div class="note-content"><ul><li><a href="https://example.invalid/menu"><b>First course</b>'
        '</a></li></ul></div>', encoding="utf8")
    st.put("new", "Home/Dinner.html", title, "Home", "2026-11-25T13:00:00", False)
    w = window(qapp, library)
    try:
        select(w, "new")
        first = w.reader.document().begin()
        assert first.text() == title and first.blockFormat().headingLevel() == 1
        assert first.textList() is None and first.next().textList() is not None
        heading = QTextCursor(first)
        heading.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor)
        assert not heading.charFormat().isAnchor()
        assert first.next().text() == "First course"
        cursor = QTextCursor(first.next())
        cursor.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor)
        assert cursor.charFormat().fontWeight() >= 600 and cursor.charFormat().isAnchor()
        select(w, "g")
        assert w.reader.toPlainText().splitlines().count("Groceries") == 1
    finally:
        w.close()


def test_list_and_reader_match_the_same_utf16_ranges():
    from skyfold.ui.notes_window import _match_ranges

    text = "😀 Cafe\u0301 café"
    ranges = _match_ranges(text, ["cafe"], 4)
    utf16 = text.encode("utf-16-le")
    matches = [utf16[2 * a:2 * b].decode("utf-16-le") for a, b in ranges]
    assert matches == ["Cafe\u0301", "café"]
    assert _match_ranges(text, ["cafe"], 1) == ranges[:1]


def test_f6_skips_collapsed_folder_pane(qapp, library):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    w = window(qapp, library)
    try:
        w.split.setSizes([0, w.width()])
        w.search.setFocus()
        qapp.processEvents()
        QTest.keyClick(w.search, Qt.Key_F6)
        assert w.notes.hasFocus()
        QTest.keyClick(w.notes, Qt.Key_Return)
        assert w.reader.hasFocus()
    finally:
        w.close()


# --- the 1.1 design ----------------------------------------------------------------------------

def _blocks(doc):
    b = doc.begin()
    while b.isValid():
        yield b
        b = b.next()


def test_the_note_reads_like_the_design(qapp, library):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QImage, QTextFormat, QTextTable
    from skyfold.ui.notes_window import HEAD_MARK, _layers
    st, root = library
    (root / "Home" / "Garden.html").write_text(page("Garden", "Jobs for spring.", "").replace(
        '<table><tr><td colspan="2" onclick="x()">cell</td></tr></table>',
        "<h3>Budget</h3><table><tr><th>Item</th><th>Price</th></tr><tr><td>Seedlings</td><td>12</td></tr></table>"),
        encoding="utf-8")
    st.put("z", "Home/Garden.html", "Garden", "Home", "2026-11-30T10:00:00", False)
    w = window(qapp, library)
    try:
        select(w, "z")
        doc = w.reader.document()
        blocks = list(_blocks(doc))
        title = blocks[0].begin().fragment().charFormat()
        # the title: 26 px, 600, without Qt's own h1 size step on top
        assert blocks[0].text() == "Garden" and abs(title.fontPointSize() - 19.5) < 0.01
        assert title.fontWeight() == QFont.DemiBold and title.property(QTextFormat.FontSizeAdjustment) is None
        budget = next(b for b in blocks if b.text() == "Budget").begin().fragment().charFormat()
        assert abs(budget.font().pointSizeF() - 10.5) < 0.01  # section headings as big as the text, 600
        # a ticked item struck through and dimmed, an open one not
        dim = _layers(w.c)["text3"]
        milk = next(b for b in blocks if "milk" in b.text())
        bread = next(b for b in blocks if "bread" in b.text())
        it = milk.begin()
        it += 1
        assert it.fragment().charFormat().fontStrikeOut() and it.fragment().charFormat().foreground().color() == dim
        it = bread.begin()
        it += 1
        assert not it.fragment().charFormat().fontStrikeOut()
        assert milk.begin().fragment().charFormat().toImageFormat().name() == "sfbox:on"
        # tables: no grid, the head row known (painted on a lighter layer), no marker colour left
        tables = [f for f in doc.rootFrame().childFrames() if isinstance(f, QTextTable)]
        head = next(t for t in tables if t.rows() == 2 and t.columns() == 2)
        assert head.format().border() == 0 and w.reader.table_heads[head.firstPosition()] == 1
        assert all(head.cellAt(r, c).format().background().style() == Qt.NoBrush for r in range(2) for c in range(2))
        assert all(f.charFormat().background().color().name() != HEAD_MARK
                   for b in blocks for f in [b.begin().fragment()] if f.isValid())
        assert w.reader._table_cards()  # drawn as cards
        img = QImage(w.reader.size(), QImage.Format_ARGB32)
        w.reader.render(img)
        # the meta line: folder and the long date
        assert w.meta.text().startswith("Home · 30 November 2026")
    finally:
        w.close()


def test_sidebar_list_and_controls_like_the_design(qapp, library):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from skyfold.ui import theme
    from skyfold.ui.notes_window import GROUP_LABEL, ROW_COMPACT, _layers
    st, root = library
    (root / "Home" / "Empty.html").write_text(page("Empty", "", "").split("<p>")[0] + "</div></body></html>",
                                              encoding="utf-8")
    st.put("e", "Home/Empty.html", "Empty", "Home", "2026-11-21T10:00:00", False)
    w = window(qapp, library)
    try:
        # "All notes" first, then the "iCloud" label above the folders (it is not a folder itself)
        tree = w.folders
        first, second = tree.topLevelItem(0), tree.topLevelItem(1)
        assert first.text(0) == "All notes"
        r0, r1 = tree.visualItemRect(first), tree.visualItemRect(second)
        assert r1.height() == r0.height() + GROUP_LABEL
        tree.setCurrentItem(first)
        pos = QPointF(40, r1.top() + 8)
        ev = QMouseEvent(QEvent.MouseButtonPress, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        tree.mousePressEvent(ev)
        assert tree.currentItem() is first  # a click on the label chooses nothing
        pos = QPointF(40, r1.bottom() - 8)
        ev = QMouseEvent(QEvent.MouseButtonPress, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        tree.mousePressEvent(ev)
        assert tree.currentItem() is second
        tree.setCurrentItem(first)
        # a note without text after its title: a one-line row, the others two lines
        rows = {w.notes.item(i).data(Qt.UserRole): w.notes.item(i) for i in range(w.notes.count())}
        assert pump(qapp, lambda: rows["e"].data(ROW_COMPACT))
        assert not rows["g"].data(ROW_COMPACT) and not rows["l"].data(ROW_COMPACT)  # (locked: "Locked note")
        assert w.notes.visualItemRect(rows["e"]).height() < w.notes.visualItemRect(rows["g"]).height()
        # no visible zoom buttons; the quiet "More" button and the order of the list stay
        assert w.more.isVisible() and w.sorter.isVisible() and w.sorter.height() <= 28
        assert w.search.placeholderText() == "Search notes" and "Ctrl+K" in w.search.toolTip()
        # the layers are see-through in every theme, OLED as Dark (over the window's material)
        for mode in ("light", "dark", "oled"):
            k = _layers(theme.palette(mode))
            assert all(k[n].alpha() < 255 for n in ("layer2", "hover", "stroke", "stroke2", "sel"))
        assert _layers(theme.OLED)["layer2"] == _layers(theme.DARK)["layer2"]
    finally:
        w.close()


def test_list_dates_are_short():
    from datetime import datetime
    from skyfold.ui.notes_window import _list_date
    now = datetime(2026, 11, 24, 12, 0)
    assert ":" in _list_date("2026-11-24T07:15:00", now)  # today: the time
    assert _list_date("2026-11-22T09:15:00", now) == "Sun"  # this week: the day
    assert _list_date("2026-11-18T18:40:00", now) == "18 Nov"  # this year: day and month
    assert "Nov" not in _list_date("2025-11-18T18:40:00", now)  # before: the date as Windows writes it
