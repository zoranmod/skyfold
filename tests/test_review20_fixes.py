"""Tests for the fixes after the twentieth (Codex) review: a decision to delete is looked at again
right before it is carried out, and Pause takes effect at once."""

from skyfold.config import Settings
from skyfold.worker import SyncWorker
from tests.test_engine import env as photos_env  # noqa: F401


def test_a_photo_edited_on_the_pc_stays_when_it_is_deleted_in_icloud(photos_env):  # noqa: F811
    s, remote, root, trashed = photos_env
    remote.add("A1", "photo.jpg", b"original")
    remote.add("A2", "other.jpg", b"untouched")
    s.run()
    (root / "2025/11/photo.jpg").write_bytes(b"edited on this PC, longer")
    remote.remove("A1")
    remote.remove("A2")
    s.run(force_full=True)
    assert (root / "2025/11/photo.jpg").read_bytes() == b"edited on this PC, longer"
    assert not (root / "2025/11/other.jpg").exists()  # the unchanged one went to the Recycle Bin
    assert not s.state.files()


def test_pause_is_seen_by_the_worker_before_the_settings_arrive():
    wk = SyncWorker(Settings())
    assert not wk._paused()
    wk.set_paused(True)  # the UI thread, the moment Pause is pressed
    assert wk._paused() and not wk.settings.paused
    wk.drive = object()
    wk._kick_drive(full=True)
    assert wk._drive_thread is None  # nothing new starts
    wk.set_paused(False)
    assert not wk._paused()


def test_new_users_are_asked_about_more_than_five_deletions():
    assert Settings().delete_threshold == 5
