"""A note that gets locked: whose file is the page on this PC? (1.2.1)

Skyfold withdraws the readable copy of a note once it is locked in Apple Notes. Only the page exactly
as Skyfold wrote it (the fingerprint written down when it was put in place) is deleted for good, also
when it was made read-only. A page changed or replaced on this PC goes to the Recycle Bin and the user
is told; the note is hidden in the Notes window and Notes.html at once either way. Synthetic notes only."""
import os
import stat

import pytest

from skyfold import notes_engine as ne, notes_view
from skyfold.engine import Callbacks
from tests.test_notes import FakeNotes


class Seen(Callbacks):
    def __init__(self):
        self.entries = []

    def activity(self, kind, text, target=""):
        self.entries.append((kind, str(text)))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """(syncer, fake Notes, notes folder, the stand-in Recycle Bin, what the panel was told)"""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    monkeypatch.setattr(ne.NotesSyncer, "_trash", lambda self, p: os.replace(p, bin_ / p.name))
    remote, seen = FakeNotes(), Seen()
    root = tmp_path / "Notes"
    s = ne.NotesSyncer(ne.NotesState(tmp_path / "n.db"), remote, root, seen)
    yield s, remote, root, bin_, seen
    for p in list(root.rglob("*")) + list(bin_.rglob("*")):  # (so pytest can clear its folder)
        if p.is_file():
            os.chmod(p, stat.S_IWRITE)


def exported(s, r, root, nid="garden", title="Garden plan", body="synced note text"):
    r.put(nid, title, body=body)
    assert s.run().exported == 1
    page = root / s.state.all()[nid][1]
    assert page.exists()
    return page


def lock(s, r, nid="garden", title="Garden plan"):
    r.put(nid, title, locked=True)
    return s.run()


def test_an_untouched_export_is_deleted_for_good(env):
    s, r, root, bin_, seen = env
    page = exported(s, r, root)
    lock(s, r)
    assert not page.exists() and not list(bin_.iterdir())  # (not readable from the Recycle Bin either)
    assert s.state.all()["garden"][1] is None and s.state.all()["garden"][5]


def test_another_page_put_in_its_place_is_never_deleted_for_good(env):
    s, r, root, bin_, seen = env
    page = exported(s, r, root)
    # Someone restores a different exported page to this name and writes their own remarks into it:
    # it looks like a page of Skyfold's (the style block), but it isn't the one Skyfold wrote here.
    replacement = '<html><style id="skyfold">body{}</style>The only copy of my own remarks</html>'
    page.write_text(replacement, encoding="utf-8")
    res = lock(s, r)
    assert not page.exists()  # out of the Notes folder: the note is locked now
    assert (bin_ / page.name).read_text(encoding="utf-8") == replacement  # but kept, to be put back
    assert not res.errors and s.state.all()["garden"][1] is None
    assert any(kind == "trash" and "Recycle Bin" in text and "Garden plan" in text for kind, text in seen.entries)


def test_remarks_added_to_skyfolds_own_page_are_kept_too(env):
    s, r, root, bin_, seen = env
    page = exported(s, r, root)
    annotated = page.read_text(encoding="utf-8").replace("synced note text", "synced note text. MY REMARK")
    page.write_text(annotated, encoding="utf-8")
    lock(s, r)
    assert not page.exists() and "MY REMARK" in (bin_ / page.name).read_text(encoding="utf-8")


def test_a_read_only_export_is_withdrawn(env):
    s, r, root, bin_, seen = env
    page = exported(s, r, root)
    os.chmod(page, stat.S_IREAD)
    res = lock(s, r)
    assert not page.exists() and not list(bin_.iterdir())
    assert not res.errors and s.state.all()["garden"][1] is None
    assert not any(kind == "error" for kind, _ in seen.entries)


def test_a_changed_read_only_page_keeps_its_mark_and_is_reported_when_it_cant_be_recycled(env, monkeypatch):
    s, r, root, bin_, seen = env
    page = exported(s, r, root)
    mine = '<html><style id="skyfold">body{}</style>my own page now</html>'
    page.write_text(mine, encoding="utf-8")
    os.chmod(page, stat.S_IREAD)

    def no_bin(self, p):
        raise OSError(5, "no Recycle Bin on this drive")

    monkeypatch.setattr(ne.NotesSyncer, "_trash", no_bin)
    res = lock(s, r)
    # not Skyfold's file any more: neither deleted nor made writable, and the user hears about it
    assert page.read_text(encoding="utf-8") == mine and not os.access(page, os.W_OK)
    assert res.errors == 1 and any(kind == "error" and page.name in text for kind, text in seen.entries)
    # the copy that is still there is hidden everywhere Skyfold shows notes
    row = next(x for x in notes_view.rows_from(s.state) if x.id == "garden")
    assert row.locked and row.rel is None and notes_view.page_path(root, row) is None
    index = (root / "Notes.html").read_text(encoding="utf-8")
    assert "Garden plan" in index and page.name not in index
    # and it is tried again: once it can go to the Recycle Bin, it does
    monkeypatch.setattr(ne.NotesSyncer, "_trash", lambda self, p: os.replace(p, bin_ / p.name))
    s.run()
    assert not page.exists() and (bin_ / page.name).read_text(encoding="utf-8") == mine


def test_a_locked_note_leaves_the_index_at_once_even_when_the_pass_is_stopped_right_after(env):
    s, r, root, bin_, seen = env
    page = exported(s, r, root)
    exported(s, r, root, "other", "Shopping")
    assert page.name in (root / "Notes.html").read_text(encoding="utf-8")
    r.put("garden", "Garden plan", locked=True)
    r.put("other", "Shopping", body="changed")
    real = r.export

    def stopped(note_id, *args):  # Stop (or Quit) arrives while the next note is downloading
        raise ne.Cancelled()

    r.export = stopped
    with pytest.raises(ne.Cancelled):
        s.run()
    r.export = real
    index = (root / "Notes.html").read_text(encoding="utf-8")
    assert not page.exists() and page.name not in index and "Garden plan" in index
    row = next(x for x in notes_view.rows_from(s.state) if x.id == "garden")
    assert row.locked and row.rel is None


def test_the_notes_window_drops_a_locked_notes_text_at_once(env, tmp_path):
    from PySide6.QtWidgets import QApplication

    from skyfold.ui import theme
    from skyfold.ui.notes_window import NotesWindow
    from tests.test_notes_window import pump

    app = QApplication.instance() or QApplication([])
    s, r, root, bin_, seen = env
    exported(s, r, root, body="the private plan")
    w = NotesWindow(theme.palette("dark"), lambda: (s.state, root))
    try:
        w.show()
        assert pump(app, lambda: not w.indexing)
        assert "garden" in w.texts
        lock(s, r)
        w.refresh()
        assert pump(app, lambda: not w.indexing)
        assert "garden" not in w.texts and w.row_by_id["garden"].locked and w.row_by_id["garden"].rel is None
    finally:
        w.close()
