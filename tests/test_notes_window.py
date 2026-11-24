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
    assert clean.hidden == 2 and len(clean.images) == 1 + 3 and "skyfold:more:0" in body
    full = nv.note_html(page(), all_pages=True)
    assert full.hidden == 0 and len(full.images) == 1 + 5 and "skyfold:more" not in full.body


def test_pictures_reach_the_viewer_only_as_ids():
    body = nv.note_html(page()).body
    srcs = re.findall(r'<img src="([^"]*)"', body)
    assert srcs and all(src.startswith("sfimg:") for src in srcs)


def test_more_pages_of_one_scan_only():
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
    yield
    NotesWindow.zoom, NotesWindow.sort, NotesWindow.own_theme = 1.0, "new", None


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
    w = window(qapp, library)
    w.sorter.setCurrentIndex(w.sorter.findData("old"))
    assert ids(w) == ["l", "d", "g"]
    w.sorter.setCurrentIndex(w.sorter.findData("title"))
    assert ids(w) == ["l", "d", "g"]  # Bank, Diary, Groceries
    w.sorter.setCurrentIndex(w.sorter.findData("new"))
    before = w.reader.document().defaultFont().pointSizeF()
    QTest.keyClick(w, Qt.Key_Plus, Qt.ControlModifier)
    assert w.reader.document().defaultFont().pointSizeF() > before and w.zoom_reset.text() == "110%"
    for _ in range(30):
        w.set_zoom(1)
    assert w.zoom_reset.text() == "240%" and "Buy" in w.reader.toPlainText()
    QTest.keyClick(w, Qt.Key_0, Qt.ControlModifier)
    assert w.zoom_reset.text() == "100%"
    assert abs(w.reader.document().defaultFont().pointSizeF() - before) < 0.01
    # Enter in the search box: to the results, never a button
    w.search.setFocus()
    QTest.keyClick(w.search, Qt.Key_Return)
    assert w.zoom_reset.text() == "100%" and pump(qapp, lambda: w.notes.hasFocus())
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
    text = "Kitchen\nOpen the original PDF\n\U0001F4CE Audio recording memo.m4a\nUkupno 200 KM"
    assert nv.snippet(text, nv.fold(text), "Kitchen") == "Ukupno 200 KM"
