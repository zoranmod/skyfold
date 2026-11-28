"""Skyfold is English only. The t() layer stays (it fills in values and picks singulars); a language
stored by an older version ("bs") is read without complaint and changes nothing."""

from __future__ import annotations

import ast
import json
import string
from pathlib import Path

import pytest

from skyfold import config, i18n

ROOT = Path(__file__).resolve().parent.parent / "skyfold"


def _used_keys() -> set[str]:
    keys: set[str] = set()
    for p in ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "t"
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                keys.add(node.args[0].value)
    return keys


def test_every_text_is_a_valid_format_string():
    """t() calls str.format on a text that is given values: a stray brace would fail only when shown."""
    for text in _used_keys():
        list(string.Formatter().parse(text))  # raises ValueError on "{" without "}"


def test_every_singular_belongs_to_a_text_the_app_uses():
    unused = sorted(k for k in i18n._FORMS_EN if k not in _used_keys())
    assert not unused, "\n".join(unused)


def test_english_is_the_only_language():
    assert i18n.LANGUAGES == {"en": "English"}
    assert not hasattr(i18n, "BS") and not hasattr(i18n, "_FORMS_BS")


@pytest.mark.parametrize("code", ["bs", "en", "de", "", None])
def test_any_language_asked_for_gives_english(code):
    i18n.set_language(code)
    assert i18n.language() == "en"
    assert i18n.t("Downloading {i} of {n}", i=2, n=5) == "Downloading 2 of 5"
    assert i18n.t("Settings") == "Settings" and i18n.t("{n} items", n=3) == "3 items"


def test_settings_saved_in_bosnian_load_and_show_english(tmp_path, monkeypatch):
    """A settings.json from the time the app had Bosnian: every other choice is kept, the language is
    ignored, and the file is not rewritten just to drop it."""
    file = tmp_path / "settings.json"
    stored = json.dumps({"language": "bs", "theme": "oled", "poll_minutes": 7})
    file.write_text(stored, encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "SETTINGS_FILE", file)
    s = config.Settings.load()
    assert (s.theme, s.poll_minutes) == ("oled", 7)
    assert not hasattr(s, "language")
    assert i18n.language() == "en" and i18n.t("Settings…") == "Settings…"
    assert file.read_text(encoding="utf-8") == stored
    s.save()  # the next ordinary save simply leaves the old choice out
    assert "language" not in json.loads(file.read_text(encoding="utf-8"))


def test_the_settings_window_has_no_language_choice(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel

    from skyfold.ui import dialogs, theme

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    d = dialogs.SettingsDialog(config.Settings(), theme.LIGHT)
    try:
        assert not hasattr(d, "lang")
        assert "Language" not in [w.text() for w in d.findChildren(QLabel)]
        items = [c.itemText(i) for c in d.findChildren(QComboBox) for i in range(c.count())]
        assert "Bosanski" not in items and "English" not in items
        assert d.theme.count() >= 4  # (the choice above it is still there)
    finally:
        d.close()
        app.processEvents()
