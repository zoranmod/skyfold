"""OLED is optional, including for previously exported and user-edited notes."""
import pytest

from skyfold import notes_engine as notes
from skyfold.ui import theme
from tests.test_notes import env  # noqa: F401 -- offline exporter fixture
from tests.test_review27_fixes import _contrast


def test_oled_is_independent_of_windows_and_the_original_palettes(monkeypatch):
    for dark in (True, False):
        monkeypatch.setattr(theme, "is_dark", lambda: dark)
        assert theme.palette("system") is (theme.DARK if dark else theme.LIGHT)
        assert theme.palette("light") is theme.LIGHT and theme.palette("dark") is theme.DARK
        assert theme.palette("oled")["bg"] == "#000000"
    assert list(theme.THEMES) == ["system", "light", "dark", "oled"]


def test_oled_label_contrast():
    c = theme.OLED
    for surface in ("bg", "group", "tile", "hover", "accent_soft"):
        for ink in ("text", "muted", "accent_fg", "disabled", "err", "warn", "ok"):
            assert _contrast(c[ink], c[surface]) >= 4.5, (ink, surface)
    for fill in ("accent", "btn_hover", "btn_pressed"):
        assert _contrast(c["accent_text"], c[fill]) >= 4.5
    for ink, background in (("err_text", "err_bg"), ("warn_text", "warn_bg"), ("ok_text", "ok_bg")):
        assert _contrast(c[ink], c[background]) >= 4.5


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_restyle_upgrades_the_release_toggle_once(newline):
    old = ('<head><style id="skyfold" data-style="2">old</style>\n'
           + notes.LEGACY_THEME_SCRIPT + '</head><body>example</body>').replace("\n", newline)
    updated = notes.restyle_note_html(old, "oled")
    assert updated.count("window.sfToggleTheme=function") == 1
    assert "window.sfNextTheme" in updated and 'data-default-theme="oled"' in updated
    assert notes.restyle_note_html(updated, "oled") is None


def test_export_default_changes_in_owned_files_and_preserves_user_edits(env):
    sync, remote, root, _ = env
    remote.put("a", "Album")
    remote.put("b", "Edited")
    sync.run()
    pages = [root / sync.state.all()[key][1] for key in ("a", "b")]
    edited = pages[1].read_bytes() + b"<!--personal annotation-->"
    pages[1].write_bytes(edited)
    sync.theme = "oled"
    for _ in range(3):  # includes existing one-time index maintenance
        assert sync.run().exported == 0
    for page in (pages[0], root / "Notes.html"):
        assert 'data-default-theme="oled"' in page.read_text(encoding="utf-8")
    assert pages[1].read_bytes() == edited
    sync.theme = "dark"
    assert sync.run().exported == 0
    assert 'data-default-theme="dark"' in pages[0].read_text(encoding="utf-8")
    assert pages[1].read_bytes() == edited
