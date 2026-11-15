"""Review 27: an identity or a lookup that can't be read proves nothing; UI contrast and focus."""
import os
from pathlib import Path

from skyfold import engine as pe
from tests.test_engine import env as photos_env  # noqa: F401
from tests.test_notes import env as notes_env  # noqa: F401


def test_gone_only_for_a_path_that_is_really_missing(tmp_path, monkeypatch):
    f = tmp_path / "x.txt"
    assert pe.known_missing(f) and pe.known_missing(tmp_path / "x.txt" / "below")
    f.write_bytes(b"x")
    assert not pe.known_missing(f)
    real = os.lstat
    monkeypatch.setattr(os, "lstat", lambda p, *a, **k: (_ for _ in ()).throw(PermissionError(13, "denied"))
                        if Path(p) == f else real(p, *a, **k))
    assert not pe.known_missing(f)  # couldn't look: not gone


def test_two_unknown_identities_are_never_the_same_file(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"1")
    os.link(a, b)
    assert pe.Syncer._same_file(a, b)
    monkeypatch.setattr(pe.Syncer, "_file_id", staticmethod(lambda p: None))
    assert not pe.Syncer._same_file(a, b)


def test_notes_record_is_kept_while_its_file_cant_be_looked_at(notes_env, monkeypatch):  # noqa: F811
    s, r, root, trashed = notes_env
    r.put("n1", "A")
    s.run()
    p = root / "Notes" / ".skyfold-tmp-note-left.html"
    p.write_bytes(b"<html>left</html>")
    s._own(p, "stage")
    real = os.lstat
    monkeypatch.setattr(os, "lstat", lambda q, *a, **k: (_ for _ in ()).throw(PermissionError(13, "denied"))
                        if Path(q) == p else real(q, *a, **k))
    s._tidy_leftovers()
    assert p.exists() and s.state.items("tmp:")
    monkeypatch.setattr(os, "lstat", real)
    s._tidy_leftovers()
    assert not p.exists() and not s.state.items("tmp:")


def test_legacy_marker_without_files_tracked_is_left_alone(photos_env):  # noqa: F811
    s, r, root, _ = photos_env
    root.mkdir(parents=True, exist_ok=True)
    legacy = root / pe.LEGACY_MARKERS[1]
    legacy.write_bytes(b"MINE")
    r.add("A1", "photo.jpg", b"one")
    s.run()
    assert legacy.read_bytes() == b"MINE" and (root / pe.MARKER).is_file()


def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        rgb = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        rgb = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_selected_text_and_switch_knob_contrast_in_both_themes():
    from skyfold.ui import theme

    for c in (theme.LIGHT, theme.DARK):
        css = theme.stylesheet(c)
        assert f"selection-color: {c['accent_text']}" in css
        assert _contrast(c["accent_text"], c["accent"]) >= 4.5
        assert _contrast(c["accent_text"], c["accent_hover"]) >= 3


def test_switch_knob_uses_the_accent_text_colour():
    import inspect

    from skyfold.ui import dialogs

    src = inspect.getsource(dialogs.Toggle.paintEvent)
    assert '"#ffffff"' not in src and 'c["accent_text"]' in src
