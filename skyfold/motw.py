"""The Windows "came from the internet" mark (Mark of the Web) on files Skyfold downloads.

Windows keeps it in a small extra stream of the file, `Zone.Identifier`. With it, SmartScreen,
Office's Protected View and other programs treat a file from iCloud like an email attachment.
Only the zone is written: no address (an iCloud address can hold a file id or an access token).

Writing the stream changes the file's modified time, so it is written before Skyfold sets the
file's time and remembers its size and time. A disk without extra streams (FAT32, exFAT, some
network shares) or any other failure only means no mark: the download goes on as before.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

STREAM = ":Zone.Identifier"
INTERNET = 3  # Windows' Internet zone (4 is "restricted sites")
_failed = 0  # how many marks couldn't be written (no paths are logged)


def zone(path: Path | str) -> int | None:
    """The zone a file is marked with, or None (no mark, or no way to read it)."""
    if os.name != "nt":
        return None
    try:
        with open(str(path) + STREAM, encoding="utf-8", errors="replace") as fh:
            text = fh.read(4096)
    except OSError:
        return None
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip().lower() == "zoneid":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


def mark(path: Path | str, zone_id: int = INTERNET) -> bool:
    """Mark a file Skyfold just downloaded as coming from the internet. Never raises."""
    global _failed
    if os.name != "nt":
        return False
    try:
        with open(str(path) + STREAM, "w", encoding="ascii", newline="") as fh:
            fh.write(f"[ZoneTransfer]\r\nZoneId={int(zone_id)}\r\n")
        return True
    except (OSError, ValueError):
        _failed += 1
        if _failed in (1, 10, 100):
            log.info("Couldn't mark %d downloaded file(s) as coming from the internet "
                     "(the disk may not support it)", _failed)
        return False


def copy_mark(src: Path | str, dst: Path | str) -> None:
    """Give a copy the same mark as its original (a copy made instead of a hard link), keeping the
    copy's times. An unmarked original gives an unmarked copy."""
    z = zone(src)
    if z is None:
        return
    try:
        st = os.stat(dst)
    except OSError:
        return
    if mark(dst, z):
        try:
            os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
        except OSError:
            pass
