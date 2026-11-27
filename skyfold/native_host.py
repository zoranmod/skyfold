"""The browser side of Safari bookmarks: Skyfold as a "native messaging host".

The Skyfold Safari Bookmarks extension (Firefox, Chrome, Edge) asks for the bookmarks; the browser
starts Skyfold.exe with its own arguments, and Skyfold answers on stdin/stdout with the bookmarks it
last read from iCloud (the cache written next to "Safari Bookmarks.html"). This process never signs in
and never touches iCloud: it only hands over what the running Skyfold already saved.

Only the extensions named here may start it: the browsers check that against the host manifest, which
Skyfold registers for the current Windows user when Safari bookmarks are turned on.
"""
from __future__ import annotations

import json
import os
import struct
import sys
from pathlib import Path

HOST = "com.skyfold.bookmarks"
FIREFOX_ID = "safari-bookmarks@skyfold"
CHROME_ID = "jnjmklnljkebbfbhkcjjjfcmmppfpdel"  # from the public key in extension/manifest.chrome.json
# ids the stores gave the extension (Edge Add-ons, Chrome Web Store): added once known
STORE_IDS: tuple[str, ...] = ("ggcdepiimncfgfelhikkbjpdfikmfhdj",)  # Edge Add-ons (Store ID 0RDCK9PBJ2DZ)
CHROMIUM_IDS = (CHROME_ID, *STORE_IDS)
MAX_MESSAGE = 1024 * 1024  # browsers accept at most 1 MB from a host
BROWSERS = {  # registry places where each browser looks for hosts (current user)
    "firefox": r"Software\Mozilla\NativeMessagingHosts",
    "chrome": r"Software\Google\Chrome\NativeMessagingHosts",
    "edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
}


def is_native_call(argv: list[str]) -> bool:
    """Started by a browser for the extension? (Chrome/Edge pass the extension's origin; Firefox the
    host manifest's path and the extension id.)"""
    args = argv[1:]
    if any(a.startswith(f"chrome-extension://{i}/") for i in CHROMIUM_IDS for a in args):
        return True
    return len(args) >= 2 and args[0].lower().endswith(".json") and args[1] == FIREFOX_ID


def cache_file() -> Path:
    from .config import PRIVATE_DIR

    return PRIVATE_DIR / "safari-bookmarks.json"


def _std_binary():
    """The real stdin/stdout pipes the browser gave this (windowed) process, as binary streams."""
    if sys.platform == "win32":
        import ctypes
        import msvcrt

        k = ctypes.windll.kernel32
        k.GetStdHandle.restype = ctypes.c_void_p
        fin = msvcrt.open_osfhandle(k.GetStdHandle(-10), os.O_RDONLY | os.O_BINARY)
        fout = msvcrt.open_osfhandle(k.GetStdHandle(-11), os.O_WRONLY | os.O_BINARY)
        return os.fdopen(fin, "rb", 0), os.fdopen(fout, "wb", 0)
    return sys.stdin.buffer, sys.stdout.buffer


def read_message(stream) -> dict | None:
    head = stream.read(4)
    if len(head) < 4:
        return None
    n = struct.unpack("<I", head)[0]
    if n > MAX_MESSAGE:
        return None
    data = stream.read(n)
    try:
        msg = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return msg if isinstance(msg, dict) else None


def write_message(stream, obj: dict) -> None:
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_MESSAGE:
        data = json.dumps({"ok": False, "error": "too-many"}).encode("utf-8")
    stream.write(struct.pack("<I", len(data)) + data)
    stream.flush()


def answer(msg: dict) -> dict:
    cmd = msg.get("cmd")
    if cmd == "ping":
        return {"ok": True}
    if cmd != "bookmarks":
        return {"ok": False, "error": "unknown"}
    try:
        from . import auth

        if auth.signed_out():
            return {"ok": False, "error": "no-bookmarks"}  # signed out: nobody's bookmarks are handed out
    except Exception:  # noqa: BLE001 - (only a check; the copy is removed at sign-out too)
        pass
    try:
        data = json.loads(cache_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"ok": False, "error": "no-bookmarks"}  # Skyfold hasn't read them yet (or it's turned off)
    except (OSError, ValueError):
        return {"ok": False, "error": "unreadable"}
    return {"ok": True, "updated": data.get("updated"), "tree": data.get("tree", [])}


def serve(inp=None, out=None) -> int:
    if inp is None or out is None:
        inp, out = _std_binary()
    while True:
        msg = read_message(inp)
        if msg is None:
            return 0
        write_message(out, answer(msg))


# registration -------------------------------------------------------------------------
def _manifest(path: str, browser: str) -> dict:
    m = {"name": HOST, "description": "Skyfold: Safari bookmarks from iCloud", "path": path, "type": "stdio"}
    if browser == "firefox":
        m["allowed_extensions"] = [FIREFOX_ID]
    else:
        m["allowed_origins"] = [f"chrome-extension://{i}/" for i in CHROMIUM_IDS]
    return m


def register(enable: bool, exe: str | None = None, winreg=None) -> bool:
    """Make the browsers find (or no longer find) Skyfold for the extension. Only for the current
    Windows user, only the keys named HOST. Returns whether anything changed."""
    from .config import PRIVATE_DIR, save_text

    if winreg is None:
        import winreg  # noqa: PLW0621
    exe = exe or sys.executable
    folder = PRIVATE_DIR / "native-host"
    changed = False
    for browser, base in BROWSERS.items():
        key_path = base + "\\" + HOST
        manifest = folder / f"{HOST}.{browser}.json"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as k:
                current = winreg.QueryValueEx(k, "")[0]
        except OSError:
            current = None
        if not enable:
            if current is not None and Path(current) == manifest:  # only our own registration
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
                    changed = True
                except OSError:
                    pass
            continue
        text = json.dumps(_manifest(exe, browser), indent=2)
        try:
            old = manifest.read_text(encoding="utf-8")
        except OSError:
            old = None
        if old != text:
            folder.mkdir(parents=True, exist_ok=True)
            save_text(manifest, text)
            changed = True
        if current is None or Path(current) != manifest:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(manifest))
            changed = True
    return changed
