"""Regression tests for the scenarios from the independent (Codex) review."""

import os

from skyfold import drive_engine as de
from tests.test_drive import env, write  # noqa: F401
from tests.test_engine import env as photos_env  # noqa: F401
from tests.test_notes import env as notes_env  # noqa: F401


def test_pc_edit_saved_during_a_download_is_kept(env):  # noqa: F811
    s, d, root, _ = env
    d.put("a.txt", b"v1")
    s.run()
    d.put("a.txt", b"phone edit")
    real = d.download

    def download_while_user_saves(item, dest, cb):
        write(root, "a.txt", b"PC edit saved meanwhile", age=0)
        real(item, dest, cb)

    d.download = download_while_user_saves
    s.run()
    texts = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file() and not p.name.startswith(".")}
    assert texts["a.txt"] == b"phone edit"
    assert b"PC edit saved meanwhile" in texts.values()  # kept as a "(PC conflict …)" copy


def test_collapse_keeps_top_entries_only():
    paths = ["A", "A b", "A/b", "A/b/c", "B/x", "B", "C/d"]
    assert de._collapse(paths) == ["A", "A b", "B", "C/d"]


def test_album_folder_never_replaces_a_file_skyfold_did_not_put_there(photos_env):  # noqa: F811
    s, r, root, _ = photos_env
    mine = root / "Albums/Trip/IMG_1.JPG"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"my own picture")
    r.add("A1", "IMG_1.JPG", b"cloud photo")
    r.album_map = {"Trip": ["A1"]}
    s.run()
    assert mine.read_bytes() == b"my own picture"


def test_note_never_overwrites_an_html_file_it_did_not_write(notes_env):  # noqa: F811
    s, r, root, trashed = notes_env
    mine = root / "Notes/Example.html"
    mine.parent.mkdir(parents=True)
    mine.write_text("<html>my own page</html>", encoding="utf-8")
    r.put("n1", "Example")
    s.run()
    assert mine.read_text(encoding="utf-8") == "<html>my own page</html>"
    assert (root / "Notes/Example (2).html").exists() and trashed == []


def test_empty_full_notes_read_does_not_remove_the_export(notes_env):  # noqa: F811
    s, r, root, trashed = notes_env
    for i in range(26):
        r.put(f"n{i}", f"Note {i}")
    while s.run().more:  # a big first export is split over several runs
        pass
    assert len(list((root / "Notes").glob("Note *.html"))) == 26
    saved = dict(r.notes) if hasattr(r, "notes") else None
    r.__dict__.update({k: type(v)() for k, v in r.__dict__.items() if isinstance(v, dict)})  # Apple answers empty
    s.run(full=True)
    assert trashed == [] and len(list((root / "Notes").glob("Note *.html"))) == 26
    if saved is not None:
        r.notes = saved
