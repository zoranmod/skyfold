"""Independent QA reproductions using the existing Skyfold Photos and Drive fakes."""
import shutil

import pytest

from skyfold.config import Settings
from skyfold.drive_engine import DriveSyncer
from skyfold.state import State
from skyfold.worker import SyncWorker
from tests.test_drive import env as drive_env, write
from tests.test_engine import env as photos_env
from tests.test_review34_codex_probes import click, qt_application, reviewer_controls, seed


@pytest.mark.parametrize("rel", ["2025/11/Holiday.jpg", "Family/Holiday.jpg"])
def test_blocker_ordinary_pc_photo_rename_does_not_delete_in_icloud(photos_env, rel):
    syncer, cloud, root, _ = photos_env
    content = b"ORIGINAL PHOTO"
    cloud.add("A1", "IMG_0001.jpg", content)
    syncer.run(force_full=True)
    original = root / "2025/11/IMG_0001.jpg"
    identifier = original.stat().st_ino
    renamed = root / rel
    renamed.parent.mkdir(parents=True, exist_ok=True)
    original.rename(renamed)
    assert renamed.stat().st_ino == identifier and renamed.read_bytes() == content
    syncer.run()
    assert "A1" in cloud.assets and not cloud.deleted, cloud.deleted


def test_blocker_renaming_a_live_photo_still_does_not_recycle_its_video(photos_env):
    syncer, cloud, root, recycled = photos_env
    cloud.add("A1", "IMG_0001.jpg", b"ORIGINAL PHOTO", live=b"ORIGINAL MOTION")
    syncer.run(force_full=True)
    parent = root / "2025/11"
    video = parent / "IMG_0001.MOV"
    assert video.read_bytes() == b"ORIGINAL MOTION"
    (parent / "IMG_0001.jpg").rename(parent / "Holiday.jpg")
    syncer.run()
    assert video.is_file() and not recycled, (cloud.deleted, recycled)


def test_photo_move_with_the_same_basename_keeps_the_cloud_original(photos_env):
    syncer, cloud, root, _ = photos_env
    cloud.add("A1", "IMG_0001.jpg", b"ORIGINAL PHOTO")
    syncer.run(force_full=True)
    moved = root / "Family/IMG_0001.jpg"
    moved.parent.mkdir()
    (root / "2025/11/IMG_0001.jpg").rename(moved)
    syncer.run()
    assert "A1" in cloud.assets and not cloud.deleted
    assert syncer.state.files()[0].rel_path == "Family/IMG_0001.jpg"


def drive_worker(syncer, root, qt_application):
    # Resume an already-synced profile. Recent self-generated watcher events
    # from the setup downloads do not belong to the user's later operations.
    steady = DriveSyncer(syncer.state, syncer.remote, root, syncer.delete_threshold)
    worker = SyncWorker(Settings(drive_enabled=True, drive_dir=str(root), notifications=False))
    worker.state = State(root.parent / "worker.db")
    worker.drive, worker.drive_state = steady, steady.state
    worker._drivePendingSeen.connect(worker._on_drive_pending)

    def finish():
        thread = worker._drive_thread
        assert thread is not None
        thread.join(timeout=15)
        assert not thread.is_alive()
        qt_application.processEvents()
        assert worker._drive_ui[0] == "idle"

    def full():
        worker._kick_drive(full=True)
        finish()

    def notify(paths, kind):
        for rel in paths:
            worker._drive_hit(str(root / rel), kind)
        worker._drive_changed()
        finish()

    return worker, full, notify


@pytest.mark.parametrize("count", [1, 3])
def test_one_worker_batch_escalates_once_for_local_deletions(drive_env, qt_application, count):
    s, cloud, root, _ = drive_env
    names = [f"Docs/{n}.txt" for n in range(3)]
    seed(s, cloud, [*names, "Other/keep.txt"])
    s.delete_threshold = 8
    worker, full, notify = drive_worker(s, root, qt_application)
    full()
    before_full, before_quick = cloud.full_walks, cloud.quick_walks
    for p in names[:count]:
        (root / p).unlink()
    notify(names[:count], "deleted")
    assert cloud.full_walks == before_full + 1 and cloud.quick_walks == before_quick + 1
    assert set(cloud.trashed) == set(names[:count])
    assert "Docs" in cloud.items and "Other/keep.txt" in cloud.items
    assert worker._drive_pending is None and not worker._drive_changes


def test_completed_editor_delete_and_recreate_save_stays_quick(drive_env, qt_application):
    s, cloud, root, recycled = drive_env
    name = "Docs/edit.txt"
    seed(s, cloud, [name, "Other/keep.txt"])
    worker, full, notify = drive_worker(s, root, qt_application)
    full()
    before = cloud.full_walks
    (root / name).unlink()
    write(root, name, b"COMPLETE NEW SAVE")
    notify([name], "modified")
    assert cloud.full_walks == before
    assert cloud.items[name]["data"] == b"COMPLETE NEW SAVE"
    assert worker._drive_pending is None and not recycled


def test_save_while_confirmation_waits_stays_quick(drive_env, qt_application):
    # B36-1 (fixed in 1.0.10): deletions already waiting for an answer don't force full reads
    s, cloud, root, _ = drive_env
    paths = ["Docs/a.txt", "Docs/b.txt"]
    seed(s, cloud, [*paths, "Other/keep.txt"])
    worker, full, notify = drive_worker(s, root, qt_application)
    full()
    for path in paths:
        (root / path).unlink()
    notify(paths, "deleted")
    assert set(worker._drive_pending.to_cloud) == set(paths)
    pending_gen = worker._drive_pending.gen
    for suffix in ("first", "second"):
        before_full, before_quick = cloud.full_walks, cloud.quick_walks
        new = f"Docs/{suffix}-unrelated-save.txt"
        write(root, new, b"UNRELATED SAVE")
        notify([new], "created")
        assert cloud.full_walks == before_full and cloud.quick_walks > before_quick
        assert cloud.items[new]["data"] == b"UNRELATED SAVE"
        assert set(worker._drive_pending.to_cloud) == set(paths)
        assert worker._drive_pending.gen == pending_gen
    assert not cloud.trashed and not worker._drive_changes


@pytest.mark.parametrize("side", ["pc", "cloud"])
def test_regrouped_question_stays_stable_and_can_be_approved(drive_env, qt_application, side):
    s, cloud, root, recycled = drive_env
    a, b, draft = "Trip/a.txt", "Trip/b.txt", "Trip/draft.tmp"
    seed(s, cloud, [a, b])
    scan, open_dialog, controller = reviewer_controls(s)
    if side == "pc":
        write(root, draft, b"scratch")
        cloud.trash(cloud._item("Trip"))
        assert set(scan().pending_to_pc) == {a, b}
        old_gen = controller._pending.gen
        (root / draft).unlink()
        (root / b).unlink()
        assert scan().pending_to_pc == ["Trip"]
    else:
        cloud.put(draft, b"scratch")
        shutil.rmtree(root / "Trip")
        assert set(scan().pending_to_cloud) == {a, b}
        old_gen = controller._pending.gen
        cloud.trash(cloud._item(draft))
        cloud.trash(cloud._item(b))
        assert scan().pending_to_cloud == ["Trip"]
    gen = controller._pending.gen
    assert gen > old_gen
    for _ in range(3):
        scan()
        assert controller._pending.gen == gen
        assert getattr(controller._pending, f"to_{side}") == ["Trip"]
    click(open_dialog(), "Delete on this PC too" if side == "pc" else "Delete in iCloud too")
    if side == "pc":
        assert recycled == ["Trip"] and not (root / a).exists()
    else:
        assert "Trip" not in cloud.items and "Trip" in cloud.trashed
    scan()
    assert controller._pending is None
