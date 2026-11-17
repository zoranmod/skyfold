"""Notes.html written by Skyfold 1.0.5 or earlier (no ownership record back then) is taken back."""
from skyfold import notes_engine as ne
from tests.test_notes import env as notes_env  # noqa: F401 - fixture

LEGACY = ('<!doctype html><html><head><meta charset="utf-8"><title>Apple Notes</title>'
          "<script>const KEY='skyfold-collapsed';localStorage.getItem('skyfold-theme')</script></head><body>"
          '<script>const DATA=[{"t": "A", "f": "Notes", "p": "%s", "m": "1.11.2026. 10:00", "l": false}]'
          '</script></body></html>')


def test_an_index_from_an_earlier_skyfold_listing_these_notes_is_updated(notes_env):
    s, remote, root, trashed = notes_env
    remote.put("n1", "A")
    s.run()
    index = root / ne.INDEX
    index.write_text(LEGACY % "Notes/A.html", encoding="utf-8")  # as 1.0.5 left it
    s.state.set("index_owned", None)
    s.state.set("index_digest", None)
    remote.put("n2", "B")
    s.run()
    text = index.read_text(encoding="utf-8")
    assert ne.INDEX_MARK in text and '"B"' in text  # the new index, with the new note
    assert any("index" in n for n in trashed)  # the old one is in the Recycle Bin, not gone
    assert s.state.get("index_owned") == "1"


def test_an_old_style_index_of_other_notes_is_left_alone(notes_env):
    s, remote, root, trashed = notes_env
    remote.put("n1", "A")
    s.run()
    index = root / ne.INDEX
    index.write_text(LEGACY % "Elsewhere/Someone else.html", encoding="utf-8")
    s.state.set("index_owned", None)
    s.state.set("index_digest", None)
    remote.put("n2", "B")
    s.run()
    assert "Someone else" in index.read_text(encoding="utf-8") and "Notes.html" not in trashed
