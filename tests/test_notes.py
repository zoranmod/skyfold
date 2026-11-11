"""Apple Notes export against a fake Notes service (no network)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from skyfold import notes_engine as ne


class FakeNotes:
    def __init__(self) -> None:
        self.v = 0
        self.notes: dict[str, ne.NoteInfo] = {}
        self.body: dict[str, str] = {}
        self.log: list[tuple[int, str]] = []
        self.dirs = {"F1": ("Notes", None), "F2": ("Recepti", None), "F3": ("Kolači", "F2")}
        self.exports = 0

    def put(self, nid, title, folder="F1", body="text", locked=False):
        self.v += 1
        self.notes[nid] = ne.NoteInfo(nid, title, folder, datetime(2026, 1, 1, tzinfo=timezone.utc).replace(minute=self.v % 60),
                                      False, locked)
        self.body[nid] = body
        self.log.append((self.v, nid))

    def delete(self, nid):
        self.v += 1
        n = self.notes.pop(nid)
        self.log.append((self.v, nid))
        self.gone = getattr(self, "gone", {})
        self.gone[nid] = n

    def cursor(self):
        return f"c{self.v}"

    def changes(self, since):
        if since is None:
            return list(self.notes.values())
        start = int(since[1:])
        out = []
        for v, nid in self.log:
            if v > start:
                if nid in self.notes:
                    out.append(self.notes[nid])
                else:
                    g = self.gone[nid]
                    out.append(ne.NoteInfo(nid, g.title, g.folder_id, g.modified, True, False))
        return out

    def folders(self):
        return self.dirs

    def export(self, note_id, out_dir: Path, filename, assets_dir):
        self.exports += 1
        (out_dir / filename).write_text(f"<html>{self.body[note_id]}</html>", encoding="utf-8")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    trashed = []
    monkeypatch.setattr(ne.NotesSyncer, "_trash", lambda self, p: (trashed.append(p.name), p.unlink()))
    remote = FakeNotes()
    root = tmp_path / "Notes"
    s = ne.NotesSyncer(ne.NotesState(tmp_path / "n.db"), remote, root)
    return s, remote, root, trashed


def test_export_with_folders_and_index(env):
    s, r, root, _ = env
    r.put("n1", "Kupovina")
    r.put("n2", "Palačinke", folder="F3")
    r.put("n3", "Tajno", locked=True)
    res = s.run()
    assert res.exported == 2
    assert (root / "Notes/Kupovina.html").exists()
    assert (root / "Recepti/Kolači/Palačinke.html").exists()
    idx = (root / "Notes.html").read_text(encoding="utf-8")
    assert "Palačinke" in idx and "Tajno" in idx
    assert s.run().exported == 0  # nothing changed


def test_only_changed_notes_are_exported_again(env):
    s, r, root, _ = env
    r.put("n1", "A")
    r.put("n2", "B")
    s.run()
    r.exports = 0
    r.put("n2", "B", body="edited")
    s.run()
    assert r.exports == 1 and "edited" in (root / "Notes/B.html").read_text(encoding="utf-8")


def test_rename_and_move_replace_old_file(env):
    s, r, root, trashed = env
    r.put("n1", "Old title")
    s.run()
    r.put("n1", "New title", folder="F2")
    s.run()
    assert (root / "Recepti/New title.html").exists() and not (root / "Notes/Old title.html").exists()


def test_deleted_note_goes_to_recycle_bin(env):
    s, r, root, trashed = env
    r.put("n1", "A")
    r.put("n2", "B")
    s.run()
    r.delete("n1")
    res = s.run()
    assert res.removed == 1 and trashed == ["A.html"] and (root / "Notes/B.html").exists()


def test_same_title_gets_number(env):
    s, r, root, _ = env
    r.put("n1", "Ideja")
    r.put("n2", "Ideja")
    s.run()
    assert (root / "Notes/Ideja.html").exists() and (root / "Notes/Ideja (2).html").exists()


def test_unsafe_title_is_cleaned(env):
    s, r, root, _ = env
    r.put("n1", 'a/b:c*d?"e"')
    s.run()
    assert len(list((root / "Notes").glob("*.html"))) == 1


def test_big_first_export_is_split_over_runs(env):
    s, r, root, _ = env
    s.MAX_PER_RUN = 3
    for i in range(7):
        r.put(f"n{i}", f"Note {i}")
    assert s.run().more
    assert s.run().more
    res = s.run()
    assert not res.more and len(list((root / "Notes").glob("*.html"))) == 7
    assert s.run().exported == 0


def test_note_page_gets_back_link_and_clean_pdf():
    page = ('<html><head><title>x</title></head><body><h1>Cjenovnik</h1>'
            '<object data="../_assets/abc/file.pdf" type="application/pdf"></object></body></html>')
    out = ne.polish_note_html(page, "../Notes.html", "All notes", "1.1.2026")
    assert 'href="../Notes.html"' in out and "All notes" in out
    assert 'file.pdf#toolbar=0&navpanes=0&view=FitH"' in out
    assert ne.polish_note_html(out, "../Notes.html", "All notes", "") == out  # idempotent


def test_exported_pages_link_back_to_index_from_any_depth(env):
    s, r, root, _ = env
    r.put("n1", "Palačinke", folder="F3")  # Recepti/Kolači/
    s.run()
    page = (root / "Recepti/Kolači/Palačinke.html").read_text(encoding="utf-8")
    assert 'href="../../Notes.html"' in page


def test_format_upgrade_reexports_everything_once(env):
    s, r, root, _ = env
    r.put("n1", "A")
    r.put("n2", "B")
    s.run()
    s.state.set("format", "1")  # an older Skyfold exported these
    r.exports = 0
    s.run()
    assert r.exports == 2
    r.exports = 0
    s.run()
    assert r.exports == 0


def test_embedded_pdf_becomes_page_images(tmp_path):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QPageSize, QPainter, QPdfWriter
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    pdf = tmp_path / "_assets" / "doc.pdf"
    pdf.parent.mkdir()
    w = QPdfWriter(str(pdf))
    w.setPageSize(QPageSize(QPageSize.A4))
    painter = QPainter(w)
    painter.drawText(100, 100, "Page one")
    w.newPage()
    painter.drawText(100, 100, "Page two")
    painter.end()
    page = ('<p>before</p><object class="attachment pdf" data-uti="com.apple.paper.doc.pdf" '
            'data="_assets/doc.pdf" type="application/pdf"><a href="_assets/doc.pdf">doc.pdf</a></object><p>after</p>')
    out = ne.pdfs_to_images(page, tmp_path, "Open PDF")
    assert "<object" not in out and "</object>" not in out
    assert out.count('class="sf-page"') == 2 and "before" in out and "after" in out
    assert (tmp_path / "_assets" / "doc.pdf.p1.png").exists()
    assert 'href="_assets/doc.pdf"' in out


def test_drawing_preview_is_swapped_for_full_bitmap(tmp_path):
    from PySide6.QtGui import QImage

    assets = tmp_path / "_assets" / "n1"
    assets.mkdir(parents=True)
    QImage(768, 768, QImage.Format.Format_RGB32).save(str(assets / "prev1"), "PNG")
    QImage(768, 768, QImage.Format.Format_RGB32).save(str(assets / "prev2"), "PNG")
    page = tmp_path / "Note.html"
    page.write_text('<img class="attachment image" data-uti="com.apple.paper" data-id="D1" src="_assets\\n1\\prev1">'
                    '<img class="attachment image" data-uti="com.apple.paper" data-id="D2" src="_assets\\n1\\prev2">',
                    encoding="utf-8")
    sizes = {"big": 2048, "small": 300}

    def download(url, folder):
        target = Path(folder) / f"asset-{url}"  # saved without an extension, like pyicloud does
        QImage(sizes[url], sizes[url], QImage.Format.Format_RGB32).save(str(target), "PNG")
        return str(target)

    assert ne.upgrade_sketches(page, {"D1": "big", "D2": "small"}, download) == 1
    html = page.read_text(encoding="utf-8")
    assert 'src="_assets/n1/asset-big.png"' in html and not (assets / "prev1").exists()
    assert 'src="_assets\\n1\\prev2"' in html and not (assets / "asset-small").exists()  # smaller: keep preview


def test_dates_follow_windows_region():
    from PySide6.QtCore import QDateTime, QLocale

    dt = datetime(2026, 9, 16, 12, 32, tzinfo=timezone.utc)
    expected = QLocale.system().toString(QDateTime(dt.astimezone()), QLocale.FormatType.ShortFormat)
    assert ne.local_datetime(dt) == expected == ne.local_datetime(dt.isoformat())


def test_index_has_theme_switch_and_collapsible_folders(env):
    syncer = env[0] if isinstance(env, tuple) else env
    page = ne.INDEX_TEMPLATE.format(
        title="Apple Notes", search="s", locked="l", count="1", updated="u",
        theme_script=ne.THEME_SCRIPT, theme_vars=ne.THEME_VARS, theme_button=ne.THEME_BUTTON.format(label="x"),
        collapse_all='"Collapse all"', expand_all='"Expand all"', data="[]")
    assert "sfToggleTheme" in page and "skyfold-collapsed" in page and "[data-theme=dark]" in page


def test_leftover_numbered_pages_are_cleaned_up(env):
    s, r, root, trashed = env
    r.put("n1", "Dijana")
    r.put("n2", "Dijana")
    s.run()
    import shutil

    shutil.copy2(root / "Notes/Dijana (2).html", root / "Notes/Dijana (3).html")  # ours, left behind: a page Skyfold wrote
    shutil.copy2(root / "Notes/Dijana.html", root / "Notes/Dijana - my copy.html")  # the user's copy: another name
    annotated = root / "Notes/Dijana (4).html"  # named like ours, but changed by the user
    annotated.write_text((root / "Notes/Dijana.html").read_text(encoding="utf-8") + "my notes", encoding="utf-8")
    (root / "Notes/my own file.txt").write_text("not ours", encoding="utf-8")
    (root / "Notes/my report.html").write_text("<html>the user's own page</html>", encoding="utf-8")
    s.state.set("orphan_sweep", None)
    s.run()
    assert trashed == ["Dijana (3).html"]
    assert (root / "Notes/Dijana.html").exists() and (root / "Notes/Dijana (2).html").exists()
    assert (root / "Notes/my own file.txt").exists() and (root / "Notes/my report.html").exists()
    assert (root / "Notes/Dijana - my copy.html").exists() and annotated.exists()


def test_no_cleanup_while_notes_are_still_downloading(env):
    s, r, root, trashed = env
    s.MAX_PER_RUN = 1
    r.put("n1", "A")
    r.put("n2", "B")
    (root / "Notes").mkdir(parents=True)
    (root / "Notes/Stray.html").write_text("x", encoding="utf-8")
    assert s.run().more
    assert trashed == []


def test_check_without_changes_still_updates_checked_time(env):
    s, r, _, _ = env
    r.put("n1", "A")
    s.run()
    s.state.set("last_sync", "2000-01-01T00:00:00+00:00")
    s.run()  # nothing changed in iCloud
    assert s.state.get("last_sync") != "2000-01-01T00:00:00+00:00"
