"""Review 28: markers and migrated files are only ever created new; versions are proven, not assumed."""
import os
from pathlib import Path

import pytest

from skyfold import config, engine as pe
from tests.test_drive import env as drive_env  # noqa: F401


def _deny(monkeypatch, path):
    for name in ("stat", "lstat"):
        real = getattr(os, name)

        def wrapped(p, *a, _real=real, **k):
            if not isinstance(p, int) and Path(p) == path:
                raise OSError(0, "synthetic", str(path), 21)
            return _real(p, *a, **k)
        monkeypatch.setattr(os, name, wrapped)


def test_a_marker_that_cant_be_looked_at_stops_the_sync(tmp_path, monkeypatch):
    m = tmp_path / ".skyfold"
    assert not pe.marker_present(m)
    m.write_text("x", encoding="utf-8")
    assert pe.marker_present(m)
    _deny(monkeypatch, m)
    with pytest.raises(pe.SyncError):
        pe.marker_present(m)


def test_a_new_marker_never_replaces_a_file(tmp_path):
    m = tmp_path / ".skyfold"
    pe.create_marker(m, "new\n")
    assert m.read_text(encoding="utf-8") == "new\n"
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(m), 0x80)
    m.write_text("MINE", encoding="utf-8")
    with pytest.raises(pe.SyncError):
        pe.create_marker(m, "new\n")
    assert m.read_text(encoding="utf-8") == "MINE"


def test_copy_new_never_replaces_an_existing_file(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_bytes(b"OLD")
    dst.write_bytes(b"CURRENT")
    assert config._copy_new(src, dst) is False
    assert dst.read_bytes() == b"CURRENT" and src.read_bytes() == b"OLD"
    dst.unlink()
    assert config._copy_new(src, dst) is True and dst.read_bytes() == b"OLD"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a", "b"]  # no private copy left behind


def test_move_new_keeps_the_source_when_the_destination_exists(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_bytes(b"OLD")
    dst.write_bytes(b"CURRENT")
    config._move_new(src, dst)
    assert dst.read_bytes() == b"CURRENT" and src.read_bytes() == b"OLD"


def test_unknown_destination_is_not_missing(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    f.write_text("{}", encoding="utf-8")
    _deny(monkeypatch, f)
    assert not config._missing(f)


def test_folder_deletion_is_not_proven_without_item_identity(drive_env):  # noqa: F811
    s, d, root, _ = drive_env
    d.put("Trip/a.txt", b"A")
    s.run()
    remote = d.walk()
    item = remote["Trip"]
    from dataclasses import replace
    blank = {p: (replace(r, docwsid="", drivewsid="") if p == "Trip/a.txt" else r) for p, r in remote.items()}
    assert s._unchanged_in_icloud("Trip", remote)  # known tags and identities: proven
    real_walk = d.walk
    d.walk = lambda *a, **k: {p: (replace(r, docwsid="", drivewsid="") if p == "Trip/a.txt" else r)
                              for p, r in real_walk(*a, **k).items()}
    assert not s._unchanged_in_icloud("Trip", blank)  # an item without identity proves nothing
    assert item.is_dir


def test_theme_refresh_reaches_switches_and_settings_icons():
    from PySide6.QtWidgets import QApplication

    from skyfold.config import Settings
    from skyfold.ui import dialogs, theme

    app = QApplication.instance() or QApplication([])
    d = dialogs.SettingsDialog(Settings(), theme.LIGHT)
    d.show()
    app.processEvents()
    before = d.nav.item(0).icon().pixmap(16, 16).toImage()
    dialogs.refresh_palette(theme.DARK)
    assert d.instant.c is theme.DARK and d.c is theme.DARK
    assert d.nav.item(0).icon().pixmap(16, 16).toImage() != before
    d.close()
