"""The Notes window: notes read inside Skyfold, from the Notes database and the exported pages."""

import os
import time

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
<img src="https://example.com/track.png"><img src="_assets/n1/photo.png" alt="photo">
<audio controls class="attachment" src="_assets/n1/memo.m4a"></audio>
<object data="_assets/n1/doc.pdf#toolbar=0" type="application/pdf"><a href="_assets/n1/doc.pdf">doc.pdf</a></object>
<div class="sf-pdf">{pages}<a class="sf-open" href="_assets/n1/scan.pdf">Open the original PDF</a></div>
<a href="javascript:alert(1)">bad</a> <a href="https://apple.com">good</a>
</div></body></html>"""


def page(title="Groceries", text="Buy čaj and đuveč", pages=5):
    p = PAGE.format(title=title, text=text,
                    pages="".join(f'<img class="sf-page" src="_assets/n1/scan.pdf.p{i}.png" alt="">' for i in range(1, pages + 1)))
    return polish_note_html(p, "../Notes.html", "All notes", "Updated today")


def test_only_the_note_content_is_kept_and_nothing_runs_or_loads():
    body, hidden = nv.note_html(page())
    assert "<script" not in body and "alert" not in body
    assert "javascript:" not in body and "onclick" not in body and "<style" not in body
    assert "sf-nav" not in body and "All notes" not in body and "Updated today" not in body  # Skyfold's bar
    assert "example.com" not in body  # nothing from the internet
    assert '<img src="_assets/n1/photo.png"' in body
    assert nv.CHECK_ON + " milk" in body  # ticked stays ticked
    assert nv.CHECK_OFF + " bread" in body  # unticked stays unticked
    assert "color:#ff0000" not in body and "font-weight:bold" in body  # colour dropped (dark themes)
    assert 'class="hl2"' in body
    assert 'colspan="2"' in body and 'border="1"' in body
    assert "<audio" not in body and "<object" not in body
    assert '_assets/n1/memo.m4a' in body and '_assets/n1/doc.pdf' in body  # as links to open
    assert 'href="https://apple.com"' in body
    assert hidden == 2 and body.count('class="sf-page"') == 0 and body.count("scan.pdf.p") == 3
    assert "skyfold:all-pages" in body
    full, hidden = nv.note_html(page(), all_pages=True)
    assert hidden == 0 and full.count("scan.pdf.p") == 5 and "skyfold:all-pages" not in full


def test_text_for_search_and_first_line():
    body, _ = nv.note_html(page())
    text = nv.plain_text(body)
    assert text.splitlines()[0] == "Groceries"
    assert "Buy čaj and đuveč" in text and "cell" in text
    assert nv.snippet(text, "Groceries") == "Buy čaj and đuveč"
    assert "đuveč" in nv.snippet(text, "Groceries", "duvec")
    assert nv.fold("Čaj ĐUVEČ") == "caj duvec" and len(nv.fold("Ǆ")) == 1


def test_matching_ranks_title_folder_text():
    r = nv.Row("1", "a.html", "Šoping lista", "Kuća/Lists", None, False)
    assert nv.matches(r, None, ["soping"]) == 3
    assert nv.matches(r, None, ["kuca"]) == 2
    assert nv.matches(r, "mlijeko i hljeb", ["hljeb"]) == 1
    assert nv.matches(r, "mlijeko", ["soping", "mlijeko"]) == 1
    assert nv.matches(r, "mlijeko", ["soping", "kafa"]) == 0
    assert nv.matches(r, None, []) == 3


def test_assets_only_from_the_notes_folder(tmp_path):
    root = tmp_path / "Notes"
    (root / "Work" / "_assets" / "n1").mkdir(parents=True)
    (root / "_assets" / "n2").mkdir(parents=True)
    pg = root / "Work" / "Plan.html"
    ok = nv.resolve_asset(root, pg, "_assets/n1/a%20b.png")
    assert ok == (root / "Work" / "_assets" / "n1" / "a b.png").resolve()
    assert nv.resolve_asset(root, pg, "../_assets/n2/x.png") == (root / "_assets" / "n2" / "x.png").resolve()
    assert nv.resolve_asset(root, pg, "../../secret/_assets/x.png") is None  # outside the Notes folder
    assert nv.resolve_asset(root, pg, "../Other.html") is None  # not an attachment
    assert nv.resolve_asset(root, pg, "https://example.com/_assets/x.png") is None
    assert nv.resolve_asset(root, pg, "file:///C:/Windows/win.ini") is None
    assert nv.resolve_asset(root, pg, "C:/Windows/_assets/x.png") is None
    assert nv.resolve_asset(root, pg, "/_assets/x.png") is None


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
    (root / "Home" / "_assets" / "n1").mkdir(parents=True)
    (root / "Home" / "Groceries.html").write_text(page(), encoding="utf-8")
    (root / "Home" / "Old.html").write_text(page("Diary", "my private thoughts", 0), encoding="utf-8")
    img = QImage(3000, 1000, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    img.save(str(root / "Home" / "_assets" / "n1" / "photo.png"))
    (tmp_path / "outside.png").write_bytes((root / "Home" / "_assets" / "n1" / "photo.png").read_bytes())
    st = NotesState(tmp_path / "s.db")
    st.put("g", "Home/Groceries.html", "Groceries", "Home", "2026-11-20T10:00:00", False)
    st.put("d", "Home/Old.html", "Diary", "Home/Private", "2026-11-10T10:00:00", False)
    st.put("l", None, "Bank", "Notes", "2026-11-05T10:00:00", True)
    return st, root


def test_window_lists_reads_and_searches(qapp, library):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QTextDocument
    from skyfold.ui import theme
    from skyfold.ui.notes_window import NotesWindow
    st, root = library
    w = NotesWindow(theme.palette("dark"), lambda: (st, root))
    w.show()
    assert pump(qapp, lambda: not w.indexing)
    assert w.notes.count() == 3
    assert w.notes.currentItem().data(Qt_UserRole()) == "g"  # newest first, shown at once
    assert "Buy čaj" in w.reader.toPlainText()
    # pictures: only from _assets, fitted to the window, on white
    img = w.reader.loadResource(QTextDocument.ImageResource, QUrl("_assets/n1/photo.png"))
    assert not img.isNull() and img.width() < 3000 and not img.hasAlphaChannel()
    assert w.reader.loadResource(QTextDocument.ImageResource, QUrl("../../outside.png")) is None or \
        w.reader.loadResource(QTextDocument.ImageResource, QUrl("../../outside.png")).isNull()
    assert w.reader.loadResource(QTextDocument.ImageResource, QUrl("https://example.com/x.png")) is None or \
        w.reader.loadResource(QTextDocument.ImageResource, QUrl("https://example.com/x.png")).isNull()
    # folders
    names = [w.folders.topLevelItem(i).text(0) for i in range(w.folders.topLevelItemCount())]
    assert names[0].startswith("All notes") and any(n.startswith("Home") for n in names)
    # search the text of the notes, without accents
    w.search.setText("private thoughts")
    assert pump(qapp, lambda: w.notes.count() == 1)
    assert w.notes.currentItem().data(Qt_UserRole()) == "d"
    assert "my private thoughts" in w.reader.toPlainText()
    assert w.reader.extraSelections()  # the words are marked
    w.search.setText("DUVEC")
    assert pump(qapp, lambda: w.notes.count() == 1 and w.notes.currentItem().data(Qt_UserRole()) == "g")
    # a locked note: listed, never read
    w.search.clear()
    assert pump(qapp, lambda: w.notes.count() == 3)
    for i in range(w.notes.count()):
        if w.notes.item(i).data(Qt_UserRole()) == "l":
            w.notes.setCurrentRow(i)
    assert not w.reader.isVisible() and "locked" in w.empty.text()
    w.close()


def test_a_note_locked_while_open_disappears_at_once(qapp, library):
    from skyfold.ui import theme
    from skyfold.ui.notes_window import NotesWindow
    st, root = library
    w = NotesWindow(theme.palette("light"), lambda: (st, root))
    w.show()
    assert pump(qapp, lambda: not w.indexing)
    assert "Buy čaj" in w.reader.toPlainText()
    st.put("g", "Home/Groceries.html", "Groceries", "Home", "2026-11-20T10:00:00", True)  # locked, copy still there
    w.refresh()
    qapp.processEvents()
    assert "Buy" not in w.reader.toPlainText() and not w.reader.isVisible()
    assert "g" not in w.texts  # its text is out of search too
    w.search.setText("čaj")
    assert pump(qapp, lambda: w.notes.count() == 0)
    w.close()


def test_signed_out_or_notes_off_shows_nothing(qapp, library):
    from skyfold.ui import theme
    from skyfold.ui.notes_window import NotesWindow
    st, root = library
    src = {"v": (st, root)}
    w = NotesWindow(theme.palette("oled"), lambda: src["v"])
    w.show()
    assert pump(qapp, lambda: not w.indexing)
    src["v"] = None
    w.refresh()
    qapp.processEvents()
    assert w.notes.count() == 0 and not w.texts and not w.reader.isVisible()
    w.close()


def test_theme_change_and_keyboard(qapp, library):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from skyfold.ui import theme
    from skyfold.ui.notes_window import NotesWindow
    st, root = library
    w = NotesWindow(theme.palette("light"), lambda: (st, root))
    w.show()
    qapp.processEvents()
    w.apply_palette(theme.palette("dark"))
    assert theme.palette("dark")["text"] in w.reader.document().defaultStyleSheet()
    w.notes.setFocus()
    QTest.keyClick(w, Qt.Key_K, Qt.ControlModifier)
    assert pump(qapp, lambda: w.search.hasFocus())
    w.search.setText("x")
    QTest.keyClick(w.search, Qt.Key_Escape)
    assert w.search.text() == "" and w.isVisible()
    w.close()


def Qt_UserRole():  # noqa: N802
    from PySide6.QtCore import Qt
    return Qt.UserRole
