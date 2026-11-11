"""Regression tests for the twelfth independent (Codex) review, of commit 99d4e32."""

import pytest

from skyfold import drive_engine as de
from tests.test_drive import env  # noqa: F401


def test_a_crash_right_after_a_file_rename_from_icloud_never_uploads_it_again(env, monkeypatch, tmp_path):  # noqa: F811
    s, d, root, _ = env
    d.put("Docs/a.txt", b"renamed in iCloud")
    d.put("Docs/keep.txt", b"keep")
    s.run()
    d._relocate("Docs/a.txt", "Docs/b.txt")
    real = s._local_move

    def move(*a):
        assert real(*a)
        raise SystemExit("power loss after the rename on this PC")

    monkeypatch.setattr(s, "_local_move", move)
    with pytest.raises(SystemExit):
        s.run()
    del d.items["Docs/b.txt"]  # then deleted in iCloud
    fresh = de.DriveSyncer(de.DriveState(tmp_path / "d.db"), d, root, delete_threshold=3)
    for _ in range(3):
        fresh.run()
    assert not any(it["data"] == b"renamed in iCloud" for it in d.items.values())
    assert fresh.state.get_meta(de.PENDING_MOVE) is None
