"""One-shot, read-only probe: can Skyfold's saved iCloud session read Safari bookmarks?

Run ONLY while Skyfold is closed. It works on a temporary COPY of the saved session (the real one is
never changed), sends at most a few READ requests to Apple's CloudKit service for the container
com.apple.SafariShared.WBSCloudBookmarksStore, and prints only counts, record type names, field names
and error codes - never a title, URL, identifier or token. Nothing is written to iCloud.

    python tools_bookmarks_probe.py
"""
import json
import logging
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

logging.disable(logging.CRITICAL)  # no library debug output (it could contain data)


def main() -> int:
    import os

    from skyfold.config import SESSION_DIR

    installed = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Skyfold" / "data" / "settings.json"
    try:  # the installed Skyfold's settings (the Apple ID it signed in with; never printed)
        apple_id = json.loads(installed.read_text(encoding="utf-8")).get("apple_id") or ""
    except (OSError, ValueError):
        apple_id = ""
    if not apple_id or not SESSION_DIR.is_dir():
        print("RESULT: no saved session (sign in with Skyfold first)")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="skyfold-probe-"))
    try:
        shutil.copytree(SESSION_DIR, tmp / "session")
        from pyicloud import PyiCloudService

        api = PyiCloudService(apple_id, None, cookie_directory=str(tmp / "session"), authenticate=False,
                              refresh_interval=None)
        try:
            api.authenticate()
        except Exception as exc:  # noqa: BLE001
            print(f"RESULT: the saved session isn't valid ({type(exc).__name__}); nothing else was tried")
            return 3
        if api.requires_2fa or api.requires_2sa:
            print("RESULT: Apple asks for a new sign-in; nothing else was tried")
            return 3
        base = api.get_webservice_url("ckdatabasews")
        url = f"{base}/database/1/com.apple.SafariShared.WBSCloudBookmarksStore/production/private"
        types, fields, missing = Counter(), Counter(), Counter()
        deleted = pages = 0
        body = {"zones": [{"zoneID": {"zoneName": "Bookmarks"}}]}
        while pages < 3:  # a small page budget
            pages += 1
            r = api.session.post(url + "/changes/zone", params=api.params, json=body, timeout=30,
                                 allow_redirects=False)
            print(f"HTTP {r.status_code} for changes/zone (page {pages})")
            if r.status_code != 200:
                try:
                    err = r.json()
                    print("  error:", err.get("serverErrorCode") or err.get("reason", "")[:80] or "?")
                except ValueError:
                    pass
                return 4
            data = r.json()
            zones = data.get("zones") or []
            if not zones:
                print("RESULT: empty answer (inconclusive)")
                return 5
            z = zones[0]
            if z.get("serverErrorCode"):
                print("RESULT: CloudKit refused:", z.get("serverErrorCode"))
                return 4
            for rec in z.get("records") or []:
                if rec.get("deleted"):
                    deleted += 1
                    continue
                t = rec.get("recordType") or "?"
                types[t] += 1
                f = rec.get("fields") or {}
                for name in f:
                    fields[f"{t}.{name}"] += 1
                if t == "BookmarkLeaf":
                    for need in ("Title", "URL", "ParentFolder"):
                        if not (f.get(need) or {}).get("value"):
                            missing[need] += 1
            if not z.get("moreComing"):
                break
            body = {"zones": [{"zoneID": {"zoneName": "Bookmarks"}, "syncToken": z.get("syncToken")}]}
        print("record types:", dict(types))
        print("deleted markers:", deleted)
        print("fields seen:", sorted(fields))
        print("bookmarks missing a usable value:", dict(missing) or "none")
        ok = types.get("BookmarkLeaf", 0) > 0 and not missing
        print("RESULT:", "bookmarks are readable" if ok else "read, but bookmarks not usable as they are")
        return 0 if ok else 6
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # the session copy is thrown away


if __name__ == "__main__":
    sys.exit(main())
