"""self.cache is not updated after a trash/rename done in the same pass: the next quick pass
re-walks a folder that no longer exists and, as the listing still names it, re-creates it on the PC."""
from __future__ import annotations
import shutil
from pathlib import Path
import pytest
from tests.test_drive import FakeDrive, write
from skyfold import drive_engine as de


@pytest.fixture()
def env25(tmp_path, monkeypatch):
    trashed = []
    def fake_trash(self, p: Path):
        trashed.append(p.relative_to(self.root).as_posix())
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    monkeypatch.setattr(de.DriveSyncer, "_trash_local", fake_trash)
    d = FakeDrive(); root = tmp_path / "Drive"
    return de.DriveSyncer(de.DriveState(tmp_path / "d.db"), d, root, 25), d, root, trashed


def test_folder_trashed_in_quick_pass_does_not_come_back_as_empty_folder(env25):
    s, d, root, trashed = env25
    d.put("F/a.txt", b"a"); d.put("G/b.txt", b"b")
    s.run()
    shutil.rmtree(root / "F")
    s.run(changed={"F"})
    assert d.trashed == ["F"] and not (root / "F").exists()
    write(root, "G/c.txt", b"c", age=30)
    s.run(changed={"F", "G/c.txt"})  # e.g. the watcher re-reported F, or the worker re-queued `changed`
    assert not (root / "F").exists(), sorted(p.name for p in root.iterdir())
    assert "F" not in s.cache
