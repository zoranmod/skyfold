"""Every t("...") string in the code must have a Bosnian translation with the same placeholders."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from skyfold import i18n

ROOT = Path(__file__).resolve().parent.parent / "skyfold"


def _used_keys() -> set[str]:
    keys: set[str] = set()
    for p in ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "t"
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                keys.add(node.args[0].value)
    return keys


def test_every_string_has_a_bosnian_translation():
    missing = sorted(k for k in _used_keys() if k not in i18n.BS)
    assert not missing, "\n".join(missing)


def test_placeholders_match():
    for en, bs in i18n.BS.items():
        assert set(re.findall(r"{(\w+)}", en)) == set(re.findall(r"{(\w+)}", bs)), en


def test_switching_language():
    try:
        i18n.set_language("bs")
        assert i18n.t("Downloading {i} of {n}", i=2, n=5) == "Preuzimam 2 od 5"
        i18n.set_language("en")
        assert i18n.t("Downloading {i} of {n}", i=2, n=5) == "Downloading 2 of 5"
    finally:
        i18n.set_language("en")
