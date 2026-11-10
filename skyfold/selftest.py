"""Hidden diagnostic: `Skyfold.exe --selftest report.txt`.

Exercises, inside the packaged exe, everything that is only loaded lazily at sign-in or sync
time, so packaging mistakes show up here instead of in front of the user. The sign-in check
uses a made-up account that does not exist; no real account is touched.
"""

from __future__ import annotations

import importlib
import sys
import tempfile
import traceback
from pathlib import Path

DUMMY_ID = "skyfold-selftest-nonexistent@example.invalid"


def main(report: str) -> int:
    lines: list[str] = []
    ok = True

    def check(name: str, fn) -> None:
        nonlocal ok
        try:
            detail = fn()
            lines.append(f"OK    {name}" + (f": {detail}" if detail else ""))
        except Exception:  # noqa: BLE001
            ok = False
            lines.append(f"FAIL  {name}\n{traceback.format_exc()}")

    for mod in ("pyicloud", "pyicloud.utils", "pyicloud.base", "pyicloud.services.photos",
                "pyicloud.services.photos_cloudkit.service", "pyicloud.srp_password",
                "pyicloud.hsa2_bridge", "fido2.rpid", "srp", "cryptography.hazmat.primitives.hashes",
                "requests", "certifi", "keyring", "send2trash", "watchdog.observers",
                "pyicloud.services.drive", "skyfold.drive_engine", "skyfold.drive_remote", "skyfold.notes_engine", "skyfold.extras", "skyfold.ui_text", "PySide6.QtPdf", "pyicloud.services.findmyiphone",
                "pyicloud.services.notes.rendering.exporter",
                "skyfold.app", "skyfold.worker", "skyfold.ui.dialogs"):
        check(f"import {mod}", lambda m=mod: importlib.import_module(m) and None)

    def certs() -> str:
        import certifi

        p = Path(certifi.where())
        assert p.exists(), p
        return p.name

    check("certifi bundle", certs)

    def psl() -> str:
        from fido2 import rpid

        return "public suffix list loaded" if rpid.verify_rp_id("apple.com", "https://idmsa.apple.com") else "?"

    check("fido2 data", psl)

    def keyring_lookup() -> str:
        from pyicloud.utils import get_password_from_keyring

        get_password_from_keyring(DUMMY_ID)
        return "no crash"

    check("keyring lookup", keyring_lookup)

    def trash_backend() -> str:
        import pythoncom  # noqa: F401
        from win32com.server.exception import COMException  # noqa: F401
        from win32com.server.policy import DesignatedWrapPolicy  # noqa: F401
        from win32com.shell import shell

        assert shell.CLSID_FileOperation
        return "IFileOperation with a fail-closed delete check"

    check("Recycle Bin backend", trash_backend)

    def trash() -> str:
        from skyfold.engine import recycle

        f = Path(tempfile.mkdtemp()) / "skyfold-selftest.txt"
        f.write_text("x")
        recycle(f)
        assert not f.exists()
        return "file sent to Recycle Bin"

    if "--online" in sys.argv:  # sends a small file to the real Recycle Bin
        check("send2trash", trash)
    else:
        lines.append("SKIP  send2trash (run with --online to include it)")

    def login() -> str:
        from skyfold import auth

        # Never touch the real session folder: the made-up account gets a throwaway one.
        auth.SESSION_DIR = Path(tempfile.mkdtemp(prefix="skyfold-selftest-"))

        try:
            auth.start_login(DUMMY_ID, "not-a-real-password")
        except auth.LoginError as exc:
            msg = str(exc)
            # Apple rejecting the made-up account is the expected, healthy outcome; after a few
            # runs Apple "locks" the made-up account instead - that also proves the round trip.
            if msg in (auth.t("Wrong Apple ID or password."),
                       auth.t("This Apple account is locked. Unlock it at iforgot.apple.com.")):
                return f"Apple answered: {msg}"
            raise AssertionError(f"unexpected sign-in error: {msg!r} (cause: {exc.__cause__!r})") from exc
        return "unexpected success"

    # Only on request: repeated failed sign-ins from the user's network are best avoided.
    if "--online" in sys.argv:
        check("sign-in round trip (dummy account)", login)
    else:
        lines.append("SKIP  sign-in round trip (run with --online to include it)")

    lines.append("RESULT: " + ("ALL OK" if ok else "FAILURES"))
    Path(report).write_text("\n".join(lines), encoding="utf-8")
    return 0 if ok else 1
