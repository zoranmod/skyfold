"""The picture of the signed-in Apple ID for Settings: the photo on the user's own card in Contacts
("My Card"), the same one iCloud.com shows. Kept only on this PC, next to the sign-in, one small
square PNG per Apple ID, and removed when the user signs out."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from .config import PRIVATE_DIR

log = logging.getLogger(__name__)

SIDE = 160  # pixels: sharp at 40 px on a 300 % display


def path_for(apple_id: str, folder: Path | None = None) -> Path:
    key = hashlib.sha256((apple_id or "").strip().lower().encode()).hexdigest()[:16]
    return (folder or PRIVATE_DIR) / f"account-{key}.png"


def save(apple_id: str, data: bytes, folder: Path | None = None) -> bool:
    """The middle square of the picture, made small, written in one step. False if it isn't a picture."""
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QImage

    from . import auth

    if auth.signed_out():
        return False  # (signed out meanwhile: nothing of the account is written)
    img = QImage.fromData(data)
    if img.isNull() or img.width() < 8 or img.height() < 8:
        return False
    side = min(img.width(), img.height())
    img = img.copy(QRect((img.width() - side) // 2, (img.height() - side) // 2, side, side))
    img = img.scaled(SIDE, SIDE, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    dest = path_for(apple_id, folder)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    if not img.save(str(tmp), "PNG"):
        return False
    os.replace(tmp, dest)
    return True


def forget(apple_id: str, folder: Path | None = None) -> None:
    try:
        path_for(apple_id, folder).unlink(missing_ok=True)
    except OSError as exc:
        log.info("Account picture not removed: %s", exc.__class__.__name__)


def forget_all(folder: Path | None = None) -> None:
    """Signing out: no account's picture stays behind."""
    for p in (folder or PRIVATE_DIR).glob("account-*.png"):
        try:
            p.unlink()
        except OSError as exc:
            log.info("Account picture not removed: %s", exc.__class__.__name__)
