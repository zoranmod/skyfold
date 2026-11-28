"""Taking over pre-account-marker Photos folders requires a complete inventory."""

import os
from pathlib import Path

import pytest

from skyfold import engine
from tests.test_drive import write
from tests.test_engine import env as photos_env


@pytest.mark.parametrize("failure", ["list", "file stat", "directory stat"])
def test_legacy_photo_adoption_retries_before_any_unknown_media_can_upload(photos_env, monkeypatch, failure):
    sync, remote, root, _ = photos_env
    sync.settings.apple_id = ""  # make a marker-only folder as earlier releases did
    remote.add("known", "garden.jpg", b"known synced bytes")
    sync.run()
    older = write(root, "Archive/before-upgrade.jpg", b"unattributed local bytes", age=3600)
    sync.settings.apple_id = "reader@example.test"
    sync.settings.photos_upload = True
    errors = []

    with monkeypatch.context() as patch:
        if failure == "list":
            scan = os.scandir

            def inaccessible(folder):
                if Path(folder) == older.parent:
                    raise PermissionError(13, "temporarily unreadable", str(folder))
                return scan(folder)

            patch.setattr(os, "scandir", inaccessible)
        else:
            inspect = Path.lstat

            def unreadable(path):
                if path == (older if failure == "file stat" else older.parent):
                    raise PermissionError(13, "temporarily unreadable", str(path))
                return inspect(path)

            patch.setattr(Path, "lstat", unreadable)
            if failure == "directory stat":
                symlink = Path.is_symlink

                def unclassified(path):
                    if path == older.parent:
                        raise PermissionError(13, "cannot classify directory", str(path))
                    return symlink(path)

                patch.setattr(Path, "is_symlink", unclassified)
        try:
            sync.run()
        except (OSError, engine.SyncError) as exc:
            errors.append(exc)
    adopted_too_early = (root / engine.OWNER_FILE).exists()
    sync.run()
    sync.run()
    assert older.name not in getattr(remote, "uploads", [])
    assert errors and not adopted_too_early
    assert (root / engine.OWNER_FILE).is_file()
