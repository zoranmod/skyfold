"""Independent QA reproductions for Skyfold a9f1e67 using its existing fakes."""
import shutil
from types import SimpleNamespace

import pytest

from skyfold import app
from skyfold.config import Settings
from skyfold.state import State
from skyfold.worker import SyncWorker
from tests.test_drive import env as drive_env, write
from tests.test_engine import env as photos_env
from tests.test_review34_codex_probes import click, qt_application, reviewer_controls, seed


@pytest.mark.parametrize("surviving_side", ["pc", "cloud"])
def test_blocker_unanswered_children_cannot_be_deleted_as_a_parent(drive_env, surviving_side):
    s, cloud, root, recycled = drive_env
    a, b, scratch = "Trip/a.txt", "Trip/b.txt", "Trip/draft.tmp"
    seed(s, cloud, [a, b])
    if surviving_side == "pc":
        write(root, scratch, b"editor scratch")
        cloud.trash(cloud._item("Trip"))
        question = s.run()
        assert set(question.pending_to_pc) == {a, b}
        # No dialog is opened or answered. Clean up on the surviving side only.
        (root / scratch).unlink()
        (root / b).unlink()
        s.run()
        assert (root / a).is_file(), recycled
    else:
        cloud.put(scratch, b"editor scratch")
        shutil.rmtree(root / "Trip")
        question = s.run()
        assert set(question.pending_to_cloud) == {a, b}
        cloud.trash(cloud._item(scratch))
        cloud.trash(cloud._item(b))
        cloud.trashed.clear()
        s.run()
        assert a in cloud.items, cloud.trashed


def test_blocker_drive_quick_scope_cannot_split_a_new_cloud_deletion_batch(drive_env, qt_application):
    s, cloud, root, recycled = drive_env
    paths = ["Alpha/a.txt", "Beta/b.txt"]
    seed(s, cloud, paths)
    worker = SyncWorker(Settings(drive_enabled=True, drive_dir=str(root), notifications=False))
    worker.state = State(root.parent / "worker.db")
    worker.drive, worker.drive_state = s, s.state
    worker._drivePendingSeen.connect(worker._on_drive_pending)

    def finish_pass():
        thread = worker._drive_thread
        assert thread is not None
        thread.join(timeout=10)
        assert not thread.is_alive()
        qt_application.processEvents()

    worker._kick_drive(full=True)
    finish_pass()
    assert worker._drive_pending is None and worker.state.get("drive_last_sync")
    # Both cloud deletions are already complete when an unrelated local edit triggers a quick check.
    for p in paths:
        cloud.trash(cloud._item(p))
    changed = "Alpha/new independent edit.txt"
    write(root, changed, b"another document")
    worker._drive_hit(str(root / changed), "modified")
    assert worker._drive_changes == {changed}
    worker._drive_changed()  # the production debounce callback, without waiting for the timer
    finish_pass()
    after_quick = list(recycled)
    worker._kick_drive(full=True)
    finish_pass()
    assert not recycled and all((root / p).is_file() for p in paths), (after_quick, recycled)


def test_full_drive_scan_holds_the_same_cross_folder_batch(drive_env):
    s, cloud, root, recycled = drive_env
    paths = ["Alpha/a.txt", "Beta/b.txt"]
    seed(s, cloud, paths)
    for p in paths:
        cloud.trash(cloud._item(p))
    for _ in range(2):
        assert set(s.run().pending_to_pc) == set(paths)
    assert not recycled and all((root / p).is_file() for p in paths)


def photos_controls(syncer):
    worker = SimpleNamespace(
        _pending=None, _drive_pending=None, syncer=syncer, state=syncer.state,
        settings=SimpleNamespace(notifications=False),
        notify=SimpleNamespace(emit=lambda *_args: None),
        _set_state=lambda *_args: None,
    )
    shown = []
    actions = []
    schedules = []
    controller = SimpleNamespace(
        _pending=None, c={"warn_text": "#a76010"},
        _show_dialog=lambda _name, dialog: shown.append(dialog),
    )

    def resolve(action, ids):
        actions.append((action, tuple(ids)))
        SyncWorker.resolve_pending(worker, action, ids)

    controller.req = SimpleNamespace(resolvePending=SimpleNamespace(emit=resolve))
    worker.pendingChanged = SimpleNamespace(emit=lambda pending: setattr(controller, "_pending", pending))

    def scan(full=True):
        problems = []
        assert SyncWorker._run_photos(worker, full, problems)
        assert not problems
        return controller._pending

    def request(*, full, manual, force_drive):
        schedules.append((full, manual, force_drive))
        return scan(full)

    worker._request = request
    worker.full_check = lambda: SyncWorker.full_check(worker)

    def open_dialog():
        app.Controller.open_pending(controller)
        return shown[-1]

    return scan, open_dialog, actions, schedules


def photos_batch(syncer, remote, root, count):
    syncer.settings.delete_threshold = 1
    assets = [f"A{i}" for i in range(count)]
    for asset in assets:
        remote.add(asset, asset + ".jpg", asset.encode())
    syncer.run(force_full=True)
    return assets, [root / "2025" / "11" / (asset + ".jpg") for asset in assets]


@pytest.mark.parametrize("side", ["to_cloud", "to_pc"])
def test_photos_dialog_approves_the_next_full_pass(photos_env, qt_application, side):
    syncer, remote, root, recycled = photos_env
    assets, paths = photos_batch(syncer, remote, root, 2)
    for asset, path in zip(assets, paths):
        if side == "to_cloud":
            path.unlink()
        else:
            remote.remove(asset)
    scan, open_dialog, actions, schedules = photos_controls(syncer)
    pending = scan()
    assert set(getattr(pending, side)) == set(assets)
    for _ in range(3):
        assert scan(False).gen == pending.gen
    click(open_dialog(), "Delete in iCloud too" if side == "to_cloud" else "Delete on this PC too")
    assert actions[0][0] == f"approve:{side}:{pending.gen_side[side]}"  # that side's version
    assert schedules == [(True, True, True)]
    if side == "to_cloud":
        assert set(remote.deleted) == set(assets) and not recycled
    else:
        assert set(recycled) == {p.name for p in paths} and not remote.deleted
    assert scan() is None


def test_photos_dialog_keeps_its_remaining_unchanged_section_answerable(photos_env, qt_application):
    # B35-1 (fixed in 1.0.10): each side of the question has its own version
    syncer, remote, root, recycled = photos_env
    assets, paths = photos_batch(syncer, remote, root, 4)
    for path in paths[:2]:
        path.unlink()
    for asset in assets[2:]:
        remote.remove(asset)
    scan, open_dialog, actions, schedules = photos_controls(syncer)
    pending = scan()
    assert set(pending.to_cloud) == set(assets[:2]) and set(pending.to_pc) == set(assets[2:])
    dialog = open_dialog()
    click(dialog, "Delete in iCloud too")
    assert set(remote.deleted) == set(assets[:2])
    current = scan()
    assert not current.to_cloud and set(current.to_pc) == set(pending.to_pc)
    assert current.gen != pending.gen and current.gen_side["to_pc"] == pending.gen_side["to_pc"]
    click(dialog, "Delete on this PC too")  # the dialog still open from before
    assert actions[-1][0] == f"approve:to_pc:{pending.gen_side['to_pc']}"
    assert set(recycled) == {p.name for p in paths[2:]}


def test_drive_dialog_keeps_its_remaining_unchanged_section_answerable(drive_env, qt_application):
    # B35-1 (fixed in 1.0.10): each side of the question has its own version
    s, cloud, root, recycled = drive_env
    paths = ["a.txt", "b.txt", "c.txt", "d.txt"]
    seed(s, cloud, paths)
    for p in paths[:2]:
        (root / p).unlink()
    for p in paths[2:]:
        cloud.trash(cloud._item(p))
    cloud.trashed.clear()
    scan, open_dialog, controller = reviewer_controls(s)
    scan()
    old = controller._pending
    assert set(old.to_cloud) == set(paths[:2]) and set(old.to_pc) == set(paths[2:])
    dialog = open_dialog()
    click(dialog, "Delete in iCloud too")
    assert set(cloud.trashed) == set(paths[:2])
    current = controller._pending
    assert current.to_pc == old.to_pc and not current.to_cloud
    assert current.gen != old.gen and current.gen_side["to_pc"] == old.gen_side["to_pc"]
    click(dialog, "Delete on this PC too")  # the dialog still open from before
    assert set(recycled) == set(paths[2:])


def test_untagged_drive_folder_has_stable_version_until_dialog_approval(drive_env, qt_application):
    s, cloud, root, _ = drive_env
    paths = ["Trip/a.txt", "Trip/b.txt"]
    seed(s, cloud, paths)
    for p in paths:
        cloud.items[p]["etag"] = None
    shutil.rmtree(root / "Trip")
    scan, open_dialog, controller = reviewer_controls(s)
    assert scan().pending_to_cloud == ["Trip"]
    gen = controller._pending.gen
    dialog = open_dialog()
    for _ in range(4):
        assert scan().pending_to_cloud == ["Trip"]
        assert controller._pending.gen == gen
    assert not cloud.trashed
    click(dialog, "Delete in iCloud too")
    assert cloud.trashed == ["Trip"] and not cloud.items


def test_drive_question_converges_after_cloud_saves_stop(drive_env, qt_application):
    s, cloud, root, _ = drive_env
    paths = ["Trip/a.txt", "Trip/b.txt", "Trip/c.txt", "Trip/d.txt"]
    seed(s, cloud, paths)
    shutil.rmtree(root / "Trip")
    scan, open_dialog, controller = reviewer_controls(s)
    assert scan().pending_to_cloud == ["Trip"]
    first_gen = controller._pending.gen
    # Ordinary edits on the surviving side, with a complete scan between saves.
    cloud.put(paths[0], b"cloud edit one")
    assert set(scan().pending_to_cloud) == set(paths[1:])
    cloud.put(paths[1], b"cloud edit two")
    assert set(scan().pending_to_cloud) == set(paths[2:])
    stable = controller._pending.gen
    assert stable > first_gen and not cloud.trashed
    for _ in range(3):
        assert set(scan().pending_to_cloud) == set(paths[2:])
        assert controller._pending.gen == stable
    click(open_dialog(), "Delete in iCloud too")
    assert set(cloud.trashed) == set(paths[2:])
    assert cloud.items[paths[0]]["data"] == b"cloud edit one"
    assert cloud.items[paths[1]]["data"] == b"cloud edit two"
