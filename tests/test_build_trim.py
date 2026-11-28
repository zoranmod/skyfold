"""build.ps1 leaves modules and DLLs out of the packaged app to keep it small. These tests make sure
nothing the app uses is among them: a module left out by mistake would only fail in the packaged
app, in front of the user."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILD = (ROOT / "build.ps1").read_text(encoding="utf-8")


def _list(name: str) -> list[str]:
    """The strings of build.ps1's `$name = @( ... )` list (comments left out)."""
    body = re.search(r"^\$" + name + r" = @\((.*?)^\s*\)|^\$" + name + r" = @\((.*?)\)$", BUILD, re.S | re.M)
    assert body, f"${name} not found in build.ps1"
    text = re.sub(r"#[^\n]*", "", body.group(1) or body.group(2))
    return re.findall(r'"([^"]+)"', text)


EXCLUDED = _list("exclude")
DROPPED = [d.lower() for d in _list("drop")]


def _imports(path: Path) -> set[str]:
    """Every module a source file imports by absolute name (also inside functions)."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{a.name}" for a in node.names)
    return found


APP_IMPORTS = set().union(*(_imports(p) for p in [ROOT / "run.py", *(ROOT / "skyfold").rglob("*.py")]))


def test_lists_were_read():
    assert "setuptools" in EXCLUDED and "PIL" in EXCLUDED
    assert "ucrtbase.dll" in DROPPED and any("opengl32sw" in d for d in DROPPED)


def test_the_app_imports_nothing_that_is_left_out():
    for module in APP_IMPORTS:
        for gone in EXCLUDED:
            assert module != gone and not module.startswith(gone + "."), f"{module} is excluded in build.ps1"


def test_qt_modules_the_app_imports_keep_their_dll():
    used = {m.split(".")[1] for m in APP_IMPORTS if m.startswith("PySide6.Qt")}
    assert {"QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtSvg", "QtPdf"} <= used
    for module in used:  # PySide6.QtPdf needs PySide6\Qt6Pdf.dll
        assert f"pyside6\\qt6{module[2:].lower()}.dll" not in DROPPED, module


def test_plugins_the_app_needs_are_kept():
    """The Windows platform plug-in, the picture formats (photos, note attachments), SVG icons and the
    style plug-in Qt loads at start; and the DLLs that are loaded first among the runtime copies."""
    kept = ["pyside6\\plugins\\platforms\\qwindows.dll", "pyside6\\plugins\\platforms\\qoffscreen.dll",
            "pyside6\\plugins\\imageformats", "pyside6\\plugins\\iconengines", "pyside6\\plugins\\styles",
            "vcruntime140.dll", "vcruntime140_1.dll", "shiboken6\\msvcp140.dll", "pyside6\\msvcp140_1.dll",
            "pyside6\\msvcp140_2.dll", "libcrypto-3.dll", "libssl-3.dll", "python312.dll", "sqlite3.dll"]
    for path in kept:
        for gone in DROPPED:
            pattern = "^" + re.escape(gone).replace("\\*", ".*") + "$"
            assert not re.match(pattern, path) and not path.startswith(gone + "\\"), f"{path} is dropped by {gone}"


def test_nothing_is_packed():
    """No UPX or other packer: packed DLLs make antivirus false alarms much more likely."""
    assert "--noupx" in BUILD
    assert "--upx-dir" not in BUILD


@pytest.mark.skipif(sys.platform != "win32", reason="the packaged app is Windows only")
def test_everything_loads_with_the_excluded_modules_blocked():
    """A fresh Python in which the excluded modules can't be imported (as in the packaged app) loads
    every Skyfold module and all that --selftest loads, the Windows keyring backend included (it
    reaches cffi, whose compiler helpers are what pull setuptools into the build)."""
    code = r"""
import importlib, importlib.abc, pkgutil, sys
EXCLUDED = %r

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if any(name == gone or name.startswith(gone + ".") for gone in EXCLUDED):
            raise ModuleNotFoundError("left out of the packaged app: " + name, name=name)
        return None

for name in list(sys.modules):  # (what the environment's own start-up loaded, e.g. setuptools' hook)
    if any(name == gone or name.startswith(gone + ".") for gone in EXCLUDED):
        del sys.modules[name]
sys.meta_path.insert(0, Block())
import skyfold
names = [m.name for m in pkgutil.walk_packages(skyfold.__path__, "skyfold.")]
names += ["pyicloud", "pyicloud.utils", "pyicloud.base", "pyicloud.services.photos",
          "pyicloud.services.photos_cloudkit.service", "pyicloud.srp_password", "pyicloud.hsa2_bridge",
          "pyicloud.services.drive", "pyicloud.services.findmyiphone",
          "pyicloud.services.notes.rendering.exporter", "fido2.rpid", "srp",
          "cryptography.hazmat.primitives.hashes", "requests", "certifi", "keyring", "keyring.backends.Windows",
          "send2trash", "watchdog.observers", "PySide6.QtPdf", "PySide6.QtSvg", "PySide6.QtNetwork",
          "decimal", "datetime", "zoneinfo", "tzlocal", "win32com.shell.shell", "win32com.server.policy"]
for name in names:
    importlib.import_module(name)
import decimal, datetime
assert decimal.__name__ == "decimal" and "_decimal" in sys.modules  # the compiled ones
assert "_datetime" in sys.modules
print("LOADED", len(names))
""" % (EXCLUDED,)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=180)
    assert done.returncode == 0 and "LOADED" in done.stdout, done.stderr[-3000:]
