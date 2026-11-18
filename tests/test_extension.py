"""The browser extension's link to Skyfold: message framing, answers, who may start it, registration,
and the extension's own sync logic (run with Node when it is installed)."""
import io
import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from skyfold import bookmarks as bm, native_host as nh

ROOT = Path(__file__).resolve().parent.parent


def frame(obj):
    data = json.dumps(obj).encode()
    return struct.pack("<I", len(data)) + data


def unframe(raw):
    out, pos = [], 0
    while pos < len(raw):
        n = struct.unpack("<I", raw[pos:pos + 4])[0]
        out.append(json.loads(raw[pos + 4:pos + 4 + n]))
        pos += 4 + n
    return out


@pytest.fixture
def cache(tmp_path, monkeypatch):
    path = tmp_path / "safari-bookmarks.json"
    monkeypatch.setattr(nh, "cache_file", lambda: path)
    return path


def test_the_host_answers_with_the_saved_bookmarks(cache):
    cache.write_text(json.dumps({"updated": "2026-11-18T16:00:00+00:00",
                                 "tree": [{"t": "Fav", "c": [{"t": "A", "u": "https://a.example/"}]}]}))
    out = io.BytesIO()
    assert nh.serve(io.BytesIO(frame({"cmd": "ping"}) + frame({"cmd": "bookmarks"})), out) == 0
    ping, answer = unframe(out.getvalue())
    assert ping == {"ok": True} and answer["ok"] and answer["tree"][0]["c"][0]["u"] == "https://a.example/"


def test_without_saved_bookmarks_it_says_so(cache):
    out = io.BytesIO()
    nh.serve(io.BytesIO(frame({"cmd": "bookmarks"})), out)
    assert unframe(out.getvalue()) == [{"ok": False, "error": "no-bookmarks"}]


def test_only_our_extensions_count_as_a_browser_call():
    assert nh.is_native_call(["Skyfold.exe", f"chrome-extension://{nh.CHROME_ID}/", "--parent-window=0"])
    assert nh.is_native_call(["Skyfold.exe", "chrome-extension://ggcdepiimncfgfelhikkbjpdfikmfhdj/"])
    assert nh.is_native_call(["Skyfold.exe", r"C:\x\com.skyfold.bookmarks.firefox.json", nh.FIREFOX_ID])
    assert not nh.is_native_call(["Skyfold.exe", "chrome-extension://someoneelse/"])
    assert not nh.is_native_call(["Skyfold.exe", "--tray"])


def test_export_saves_what_the_extension_gets(tmp_path, cache):
    from tests.test_bookmarks import RECORDS, FakeApi

    bm.export(FakeApi([{"records": RECORDS, "moreComing": False}]), tmp_path)
    tree = json.loads(cache.read_text(encoding="utf-8"))["tree"]
    assert tree[0]["t"] == "Favorites" and [c["t"] for c in tree[0]["c"]] == ["First", "Work", "Second"]


class FakeReg:
    HKEY_CURRENT_USER, REG_SZ = "HKCU", 1

    def __init__(self):
        self.keys = {}

    class _Key:
        def __init__(self, reg, path):
            self.reg, self.path = reg, path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, root, path):  # noqa: N802
        if path not in self.keys:
            raise OSError("missing")
        return self._Key(self, path)

    def CreateKey(self, root, path):  # noqa: N802
        self.keys.setdefault(path, None)
        return self._Key(self, path)

    def QueryValueEx(self, k, name):  # noqa: N802
        return (self.keys[k.path], 1)

    def SetValueEx(self, k, name, _r, _t, value):  # noqa: N802
        self.keys[k.path] = value

    def DeleteKey(self, root, path):  # noqa: N802
        del self.keys[path]


def test_registration_is_for_our_extensions_only_and_undone(tmp_path, monkeypatch):
    monkeypatch.setattr("skyfold.config.PRIVATE_DIR", tmp_path)
    reg = FakeReg()
    reg.keys[r"Software\Google\Chrome\NativeMessagingHosts\something.else"] = "x"
    assert nh.register(True, exe=r"C:\Apps\Skyfold\Skyfold.exe", winreg=reg)
    firefox = json.loads((tmp_path / "native-host" / f"{nh.HOST}.firefox.json").read_text())
    chrome = json.loads((tmp_path / "native-host" / f"{nh.HOST}.chrome.json").read_text())
    assert firefox["allowed_extensions"] == [nh.FIREFOX_ID] and firefox["path"].endswith("Skyfold.exe")
    assert chrome["allowed_origins"] == [f"chrome-extension://{i}/" for i in nh.CHROMIUM_IDS]
    assert "chrome-extension://ggcdepiimncfgfelhikkbjpdfikmfhdj/" in chrome["allowed_origins"]  # Edge Add-ons
    assert len([k for k in reg.keys if k.endswith(nh.HOST)]) == 3
    assert not nh.register(True, exe=r"C:\Apps\Skyfold\Skyfold.exe", winreg=reg)  # already so: nothing done
    assert nh.register(False, winreg=reg)
    assert list(reg.keys) == [r"Software\Google\Chrome\NativeMessagingHosts\something.else"]


def test_the_chrome_id_matches_the_manifest_key():
    import base64
    import hashlib

    key = json.loads((ROOT / "extension" / "manifest.chrome.json").read_text(encoding="utf-8"))["key"]
    h = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    assert "".join(chr(97 + int(c, 16)) for c in h) == nh.CHROME_ID
    ff = json.loads((ROOT / "extension" / "manifest.firefox.json").read_text(encoding="utf-8"))
    assert ff["browser_specific_settings"]["gecko"]["id"] == nh.FIREFOX_ID


@pytest.mark.skipif(shutil.which("node") is None, reason="Node isn't installed")
def test_the_extensions_sync_logic():
    r = subprocess.run(["node", str(ROOT / "tests" / "extension_sync.test.js")], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
