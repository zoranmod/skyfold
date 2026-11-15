"""Codex review 34 probes (of 0afdf64), kept as regression tests. One case is planned for 1.0.7
(xfail, strict: it shows up as soon as it is fixed); characterize_* cases document accepted behaviour."""
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from skyfold import app, config
from skyfold.i18n import t
from skyfold.worker import SyncWorker
from tests.test_drive import env as drive_env, write
from tests.test_engine import env as photos_env
from tests.test_notes import env as notes_env
from tests.test_review31_codex_probes import private_paths


@pytest.fixture(scope="module")
def qt_application():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def reviewer_controls(s):
    """Connect the real dialog and worker handlers without starting live services."""
    worker = SimpleNamespace(
        _pending=None, _drive_pending=None, drive=s, drive_state=s.state,
        notify=SimpleNamespace(emit=lambda *_args: None),
    )
    dialogs = []
    controller = SimpleNamespace(
        _pending=None, c={"warn_text": "#a76010"},
        _show_dialog=lambda _name, dialog: dialogs.append(dialog),
        req=SimpleNamespace(resolvePending=SimpleNamespace(
            emit=lambda action, ids: SyncWorker.resolve_pending(worker, action, ids))),
    )
    worker.pendingChanged = SimpleNamespace(emit=lambda pending: setattr(controller, "_pending", pending))

    def scan(changed=None):
        result = s.run(changed)
        if result.full or result.pending_to_cloud or result.pending_to_pc:
            SyncWorker._on_drive_pending(worker, result.pending_to_cloud, result.pending_to_pc)
        return result

    def open_dialog():
        app.Controller.open_pending(controller)
        return dialogs[-1]

    worker._kick_drive = lambda **_kwargs: scan()
    return scan, open_dialog, controller


def click(dialog, label):
    from PySide6.QtWidgets import QPushButton

    button = next(b for b in dialog.findChildren(QPushButton) if b.text() == t(label))
    assert button.isEnabled()
    button.click()


def seed(s, cloud, paths):
    s.delete_threshold = 1
    for p in paths:
        cloud.put(p, b"ORIGINAL")
    s.run()


def test_blocker_old_cloud_dialog_cannot_approve_replacement_seen_by_new_pass(drive_env, qt_application):
    s, cloud, root, _ = drive_env
    paths = ["a.txt", "b.txt"]
    seed(s, cloud, paths)
    scan, open_dialog, _ = reviewer_controls(s)
    for p in paths:
        (root / p).unlink()
    assert set(scan().pending_to_cloud) == set(paths)
    old_dialog = open_dialog()

    old_id = cloud._item(paths[0]).docwsid
    cloud.trash(cloud._item(paths[0]))
    cloud.put(paths[0], b"ORIGINAL")
    new_id = cloud._item(paths[0]).docwsid
    assert old_id != new_id
    assert set(scan().pending_to_cloud) == set(paths)
    assert cloud._item(paths[0]).docwsid == new_id
    cloud.trashed.clear()
    click(old_dialog, "Delete in iCloud too")
    assert paths[0] in cloud.items, cloud.trashed


@pytest.mark.parametrize("side", ["cloud", "pc"])
def test_blocker_pending_folder_cannot_delete_unanswered_children(drive_env, side):
    s, cloud, root, local_trash = drive_env
    paths = ["Trip/a.txt", "Trip/b.txt"]
    seed(s, cloud, paths)
    if side == "cloud":
        shutil.rmtree(root / "Trip")
        assert s.run().pending_to_cloud == ["Trip"]
        cloud.put(paths[1], b"newly edited cloud document")
    else:
        cloud.trash(cloud._item("Trip"))
        assert s.run().pending_to_pc == ["Trip"]
        write(root, paths[1], b"newly edited PC document")
    cloud.trashed.clear()
    s.run()
    if side == "cloud":
        assert paths[0] in cloud.items, cloud.trashed
    else:
        assert (root / paths[0]).is_file(), local_trash


@pytest.mark.xfail(strict=True, reason="planned for 1.0.7: needs the file id of the version shown (goes to the Recycle Bin)")
def test_blocker_pc_approval_does_not_recycle_a_new_copy_at_the_same_path(drive_env, qt_application):
    s, cloud, root, local_trash = drive_env
    paths = ["a.txt", "b.txt"]
    seed(s, cloud, paths)
    scan, open_dialog, _ = reviewer_controls(s)
    for p in paths:
        cloud.trash(cloud._item(p))
    assert set(scan().pending_to_pc) == set(paths)
    dlg = open_dialog()
    original = root / paths[0]
    saved = root.parent / "saved original.txt"
    original.rename(saved)
    shutil.copy2(saved, original)
    assert original.stat().st_ino != saved.stat().st_ino
    click(dlg, "Delete on this PC too")
    assert original.is_file(), local_trash


def test_blocker_folder_approval_cannot_recycle_new_folder_with_old_file_name(drive_env, qt_application):
    s, cloud, root, local_trash = drive_env
    seed(s, cloud, ["Trip/archive", "Trip/notes.txt"])
    scan, open_dialog, _ = reviewer_controls(s)
    cloud.trash(cloud._item("Trip"))
    assert scan().pending_to_pc == ["Trip"]
    dlg = open_dialog()
    replacement = root / "Trip/archive"
    replacement.unlink()
    replacement.mkdir()
    click(dlg, "Delete on this PC too")
    assert replacement.is_dir(), local_trash


def test_blocker_stale_restore_pc_button_does_not_reupload_to_cloud(drive_env, qt_application):
    s, cloud, root, _ = drive_env
    paths = ["a.txt", "b.txt"]
    seed(s, cloud, paths)
    scan, open_dialog, _ = reviewer_controls(s)
    for p in paths:
        (root / p).rename(root.parent / p)
    assert set(scan().pending_to_cloud) == set(paths)
    old_dialog = open_dialog()
    for p in paths:
        cloud.trash(cloud._item(p))
        (root.parent / p).rename(root / p)
    assert set(scan().pending_to_pc) == set(paths)
    click(old_dialog, "Bring them back to this PC")
    assert not set(paths).intersection(cloud.items), cloud.uploads


@pytest.mark.parametrize("side", ["cloud", "pc"])
def test_blocker_file_and_empty_folder_both_count_toward_limit(drive_env, side):
    s, cloud, root, local_trash = drive_env
    seed(s, cloud, ["a.txt"])
    cloud.put("Empty", None)
    s.run()
    if side == "cloud":
        (root / "a.txt").unlink()
        (root / "Empty").rmdir()
    else:
        cloud.trash(cloud._item("a.txt"))
        cloud.trash(cloud._item("Empty"))
    cloud.trashed.clear()
    result = s.run()
    pending = result.pending_to_cloud if side == "cloud" else result.pending_to_pc
    assert set(pending) == {"a.txt", "Empty"}, (cloud.trashed, local_trash)
    assert not local_trash and not cloud.trashed


def test_characterize_case_only_basename_stays_held_until_renamed_to_new_name(drive_env):
    s, cloud, root, local_trash = drive_env
    seed(s, cloud, ["report.txt"])
    (root / "report.txt").rename(root / "intermediate")
    (root / "intermediate").rename(root / "REPORT.TXT")
    cloud.trash(cloud._item("report.txt"))
    s.run()
    write(root, "REPORT.TXT", b"saved a new edit")
    for _ in range(4):
        result = s.run()
        assert not result.pending_to_pc
    assert not local_trash and not cloud.items and not cloud.uploads
    (root / "REPORT.TXT").rename(root / "new report.txt")
    s.run()
    assert cloud.items["new report.txt"]["data"] == b"saved a new edit"


@pytest.mark.parametrize("same_pass", [False, True])
def test_untagged_unchanged_cloud_items_can_be_approved(drive_env, same_pass):
    s, cloud, root, _ = drive_env
    paths = ["Trip/a.txt", "Trip/b.txt"]
    seed(s, cloud, paths)
    for p in paths:
        cloud.items[p]["etag"] = None
    shutil.rmtree(root / "Trip")
    if same_pass:
        s.on_pending = lambda cloud_paths, _pc_paths: s.approve(cloud_paths, "cloud")
        s.run()
    else:
        question = s.run()
        assert question.pending_to_cloud == ["Trip"]
        assert s.approve(question.pending_to_cloud, "cloud") == ["Trip"]
        s.run()
    assert not cloud.items and cloud.trashed == ["Trip"]


def test_fixed_quick_listing_keeps_the_displayed_batch(drive_env, qt_application):
    s, cloud, root, _ = drive_env
    paths = ["Alpha/a.txt", "Beta/b.txt"]
    seed(s, cloud, paths)
    scan, open_dialog, controller = reviewer_controls(s)
    for p in paths:
        (root / p).unlink()
    assert set(scan().pending_to_cloud) == set(paths)
    assert set(controller._pending.to_cloud) == set(paths)
    dialog = open_dialog()
    change = "Alpha/new independent edit.txt"
    write(root, change, b"new independent document")
    # (review 35) the quick pass hands the deletions to a full read: the question stays the whole batch
    assert set(scan(changed={change}).pending_to_cloud) == set(paths)
    assert set(controller._pending.to_cloud) == set(paths) and not cloud.trashed
    click(dialog, "Delete in iCloud too")
    assert set(cloud.trashed) == set(paths)


def test_fixed_quick_pass_never_carries_an_older_answer_to_a_replacement(drive_env):
    s, cloud, root, _ = drive_env
    paths = ["Alpha/a.txt", "Alpha/b.txt"]
    seed(s, cloud, paths)
    for p in paths:
        (root / p).unlink()
    old = s.run().pending_to_cloud
    assert set(old) == set(paths) and s.approve(old, "cloud") == old
    old_id = cloud._item(paths[0]).docwsid
    cloud.trash(cloud._item(paths[0]))
    cloud.put(paths[0], b"ORIGINAL")
    assert cloud._item(paths[0]).docwsid != old_id
    cloud.trashed.clear()
    result = s.run(changed={paths[0]})
    assert result.full and paths[0] not in cloud.trashed and paths[0] in cloud.items  # asked again


@pytest.mark.parametrize("side", ["cloud", "pc"])
def test_blocker_photos_unanswered_batch_cannot_shrink_into_automatic_delete(photos_env, side):
    s, phone, root, local_trash = photos_env
    s.settings.delete_threshold = 1
    phone.add("A1", "a.jpg", b"first image")
    phone.add("A2", "b.jpg", b"second image")
    s.run(force_full=True)
    photo = root / "2025/11/a.jpg"
    second = root / "2025/11/b.jpg"
    if side == "cloud":
        photo.rename(root.parent / "a.jpg")
        second.rename(root.parent / "b.jpg")
        pending = s.run(force_full=True).pending
        assert set(pending.to_cloud) == {"A1", "A2"}
        (root.parent / "b.jpg").rename(second)
    else:
        phone.remove("A1")
        phone.remove("A2")
        pending = s.run(force_full=True).pending
        assert set(pending.to_pc) == {"A1", "A2"}
        phone.add("A2", "b.jpg", b"second image")
    s.run(force_full=True)
    if side == "cloud":
        assert "A1" in phone.assets, phone.deleted
    else:
        assert photo.is_file(), local_trash


def test_blocker_photos_cloud_approval_cannot_recycle_restored_pc_copies(photos_env, qt_application):
    s, phone, root, local_trash = photos_env
    s.settings.delete_threshold = 1
    phone.add("A1", "a.jpg", b"first image")
    phone.add("A2", "b.jpg", b"second image")
    s.run(force_full=True)
    paths = [root / "2025/11/a.jpg", root / "2025/11/b.jpg"]
    for path in paths:
        path.rename(root.parent / path.name)
    question = s.run(force_full=True).pending
    assert set(question.to_cloud) == {"A1", "A2"}
    worker = SimpleNamespace(
        _pending=question, _drive_pending=None, syncer=s,
        pendingChanged=SimpleNamespace(emit=lambda _pending: None),
        full_check=lambda: s.run(force_full=True),
    )
    dialogs = []
    controller = SimpleNamespace(
        _pending=question, c={"warn_text": "#a76010"},
        _show_dialog=lambda _key, dialog: dialogs.append(dialog),
        req=SimpleNamespace(resolvePending=SimpleNamespace(
            emit=lambda action, ids: SyncWorker.resolve_pending(worker, action, ids))),
    )
    app.Controller.open_pending(controller)
    for asset, path in zip(("A1", "A2"), paths):
        (root.parent / path.name).rename(path)
        phone.remove(asset)
    click(dialogs[-1], "Delete in iCloud too")
    assert not local_trash and all(p.is_file() for p in paths)


def test_ordinary_folder_rename_moves_without_creating_a_duplicate(drive_env):
    s, cloud, root, _ = drive_env
    seed(s, cloud, ["Trip/a.txt", "Trip/Sub/b.txt"])
    old_id = cloud._item("Trip").docwsid
    (root / "Trip").rename(root / "Holiday")
    for _ in range(3):
        result = s.run()
    assert cloud.renamed == [("Trip", "Holiday")]
    assert cloud._item("Holiday").docwsid == old_id
    assert not result.pending_to_cloud and not cloud.trashed and not cloud.uploads
    assert set(cloud.items) == {"Holiday", "Holiday/a.txt", "Holiday/Sub", "Holiday/Sub/b.txt"}


def test_characterize_fallback_orphan_copy_can_mix_with_a_migrated_database(notes_env, tmp_path, monkeypatch):
    source, private = private_paths(monkeypatch, tmp_path)
    prior = source / config.DATABASE
    with closing(sqlite3.connect(prior)) as old:
        old.execute("PRAGMA journal_mode=DELETE")
        old.execute("CREATE TABLE evidence (value BLOB)")
        old.executemany("INSERT INTO evidence VALUES (?)", [(b"L" * 9000,)] * 16)
        old.commit()
    unrelated = tmp_path / "unrelated.sqlite3"
    script = """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA journal_mode=DELETE')
db.execute('PRAGMA cache_size=4')
db.execute('CREATE TABLE evidence (value BLOB)')
db.executemany('INSERT INTO evidence VALUES (?)', [(b'U'*9000,)] * 16)
db.commit()
db.execute('UPDATE evidence SET value=?', (b'X'*9000,))
os._exit(91)
"""
    process = subprocess.run([sys.executable, "-c", script, str(unrelated)], timeout=15, check=False)
    assert process.returncode == 91
    orphan = private / (config.DATABASE + "-journal")
    Path(str(unrelated) + "-journal").rename(orphan)
    journal = orphan.read_bytes()
    assert b"U" * 1024 in journal
    original_rename = config.os.rename

    def deny_rename(src, dst, *args, **kwargs):
        if Path(src) == orphan:
            raise PermissionError("simulated orphan rename refusal")
        return original_rename(src, dst, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(config.os, "rename", deny_rename)
        config.migrate_private_files()
    assert not config.JOURNAL_PROBLEMS and not config.MIGRATION_PROBLEMS
    assert orphan.exists()
    assert any(p.read_bytes() == journal for p in private.glob(orphan.name + ".orphan-*"))
    with closing(sqlite3.connect(private / config.DATABASE)) as published:
        assert published.execute("SELECT count(*) FROM evidence WHERE substr(value, 1, 1)=?",
                                 (b"U",)).fetchone()[0] > 0
    with closing(sqlite3.connect(prior)) as old:
        assert old.execute("SELECT count(*) FROM evidence WHERE substr(value, 1, 1)=?",
                           (b"L",)).fetchone() == (16,)
