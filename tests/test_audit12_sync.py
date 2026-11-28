"""Offline safety regressions for the 1.2 audit."""

import os
from pathlib import Path
from types import SimpleNamespace

from skyfold.worker import SyncWorker
from tests.test_drive import env as drive_env, write


def test_deselection_preserves_folder_when_a_child_cannot_be_enumerated(drive_env, monkeypatch):
    drive, cloud, root, _ = drive_env
    cloud.put("Garden/plan.txt", b"synced")
    drive.run()
    draft = write(root, "Garden/Unfiled/draft.txt", b"only on this PC")
    recycled = []
    monkeypatch.setattr(drive, "_trash_local", recycled.append)
    scan = os.scandir

    def inaccessible(folder):
        if Path(folder) == draft.parent:
            raise PermissionError(13, "temporarily unreadable", str(folder))
        return scan(folder)

    monkeypatch.setattr(os, "scandir", inaccessible)
    worker = SimpleNamespace(drive=drive, settings=SimpleNamespace(drive_folders=[]),
                             log=lambda *args: None)
    SyncWorker.trash_drive_paths(worker, ["Garden"])
    assert not any(item == draft or item in draft.parents for item in recycled)
