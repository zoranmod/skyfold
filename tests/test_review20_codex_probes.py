"""Reproduction probes from the twentieth independent (Codex) review, kept as regression tests."""
import os
import sys
import threading
from types import SimpleNamespace
from pathlib import Path
from datetime import datetime, timezone
import pytest
from skyfold import drive_engine as de
from tests.test_drive import env, write
from tests.test_engine import env as photos_env
from tests.test_review11_codex_probes import strict_ids, fresh


@pytest.mark.parametrize('restored_content', [b'original', b'new-work'])
def test_photo_restored_during_icloud_read_is_not_deleted(photos_env, monkeypatch, restored_content):
    s, r, root, trashed = photos_env
    r.add('A1', 'photo.jpg', b'original'); s.run()
    p = root / '2025/11/photo.jpg'; p.unlink()
    cursor = r.cursor
    def restore_while_waiting_for_icloud():
        p.write_bytes(restored_content)  # Explorer Undo / editor save while the request was in flight
        return cursor()
    monkeypatch.setattr(r, 'cursor', restore_while_waiting_for_icloud)
    s.run()
    assert not r.deleted and p.exists(), (r.deleted, trashed)
    assert p.read_bytes() == restored_content


def test_drive_folder_addition_during_other_cloud_delete_stays(env, monkeypatch):
    s, d, root, trashed = env
    d.put('Folder/old.txt', b'old'); d.put('remove.txt', b'delete-me'); s.run()
    d.trash(d._item('Folder'))
    (root / 'remove.txt').unlink()
    original = d.trash
    def cloud_delete(item):
        write(root, 'Folder/new.txt', b'new local work')
        original(item)
    monkeypatch.setattr(d, 'trash', cloud_delete)
    s.run()
    assert (root / 'Folder/new.txt').exists(), trashed
    assert (root / 'Folder/new.txt').read_bytes() == b'new local work'


@pytest.mark.parametrize('first_step', ['move', 'rename'])
@pytest.mark.parametrize('stop', ['pause', 'quit'])
def test_interrupted_move_resumes_without_duplicates(env, monkeypatch, first_step, stop):
    s, d, root, _ = env
    d.put('Source/a.txt', b'contents'); d.put('Target', None)
    if first_step == 'rename': d.put('Target/a.txt', b'unrelated')
    s.run(); strict_ids(d, monkeypatch)
    (root / 'Source/a.txt').rename(root / 'Target/b.txt')
    original = getattr(d, first_step)
    def operation(*args):
        result = original(*args)
        s.cancel_flag.set()
        if stop == 'quit': s.closing.set()
        return result
    monkeypatch.setattr(d, first_step, operation)
    with pytest.raises(de.Cancelled): s.run()
    assert s._journal()
    monkeypatch.setattr(d, first_step, original)
    s = fresh(s, d, root)
    for _ in range(4): s.run()
    assert d.items['Target/b.txt']['data'] == b'contents'
    assert not s._journal()
    assert len([it for it in d.items.values() if it['data'] == b'contents']) == 1


def test_hundred_moved_live_pairs_compare_once_per_resource(photos_env, monkeypatch):
    s, r, root, trashed = photos_env
    for i in range(100):
        r.add(f'A{i}', 'photo.jpg', f'{i:04}'.encode(), live=f'v{i:04}'.encode(),
              date=datetime(2010 + i // 12, i % 12 + 1, 2, tzinfo=timezone.utc))
    s.run()
    rows = s.state.files()
    for row in rows:
        target = root / 'Sorted' / row.asset_id / Path(row.rel_path).name
        target.parent.mkdir(parents=True, exist_ok=True)
        (root / row.rel_path).rename(target)
    download = r.download; calls = []
    def record(resource, dest, cb):
        calls.append(resource.url); return download(resource, dest, cb)
    monkeypatch.setattr(r, 'download', record)
    s.run(force_full=True)
    assert len(calls) == 200 and len(set(calls)) == 200
    assert len(s.state.files()) == 200 and not r.deleted and not trashed
    assert all(row.rel_path.startswith('Sorted/') for row in s.state.files())


def test_untracked_live_pair_stays_untracked_through_cloud_changes(photos_env):
    s, r, root, trashed = photos_env
    r.add('A1', 'photo.jpg', b'original', live=b'video'); s.run()
    (root / 'Sorted').mkdir()
    (root / '2025/11/photo.jpg').rename(root / 'Sorted/photo.jpg')
    (root / 'Sorted/photo.jpg').write_bytes(b'modified')
    s.run()
    assert not s.state.files() and 'A1' in s.state.excluded()
    r.add('A1', 'photo.jpg', b'cloud-edit', live=b'new-video')
    for _ in range(3): s.run(force_full=True)
    assert not s.state.files() and not trashed and not r.deleted
    assert (root / 'Sorted/photo.jpg').read_bytes() == b'modified'
    assert (root / '2025/11/photo.MOV').read_bytes() == b'video'


@pytest.mark.parametrize('comparison_fails_once', [False, True])
def test_shared_candidate_does_not_become_cloud_delete_after_unknown(photos_env, monkeypatch, comparison_fails_once):
    s, r, root, trashed = photos_env
    r.add('A1', 'photo.jpg', b'identical', date=datetime(2025, 1, 2, tzinfo=timezone.utc))
    r.add('A2', 'photo.jpg', b'identical', date=datetime(2025, 2, 2, tzinfo=timezone.utc))
    s.run()
    first, second = s.state.files()
    (root / 'Sorted').mkdir()
    (root / first.rel_path).rename(root / 'Sorted/photo.jpg')
    (root / second.rel_path).unlink()
    original = r.download
    failed = []
    def download(resource, dest, cb):
        if comparison_fails_once and resource.url == f'u/{first.asset_id}' and not failed:
            failed.append(True)
            raise OSError('synthetic transient comparison failure')
        return original(resource, dest, cb)
    monkeypatch.setattr(r, 'download', download)
    for _ in range(3): s.run(force_full=True)
    assert first.asset_id in r.assets, (first.asset_id, r.deleted, s.state.files())
    assert not r.deleted, r.deleted  # ambiguity should untrack, as it does without the transient failure


def test_space_reservation_released_after_download_errors(env, monkeypatch):
    s, d, root, _ = env
    for i in range(12): d.put(f'f{i:02}.txt', b'content')
    checks = []; download = d.download
    def check(root, needed): checks.append(needed)
    def fail(item, dest, cb): raise OSError('synthetic download failure')
    monkeypatch.setattr(de, 'check_free_space', check)
    monkeypatch.setattr(d, 'download', fail)
    result = s.run()
    assert result.errors == 12 and max(checks[1:]) <= de.WORKERS * 7
    monkeypatch.setattr(d, 'download', download)
    result = s.run()
    assert result.downloaded == 12 and result.errors == 0
    assert all((root / f'f{i:02}.txt').read_bytes() == b'content' for i in range(12))


def test_pause_during_storage_refresh_does_not_start_new_drive_mutation(env, tmp_path, monkeypatch):
    from skyfold.worker import SyncWorker
    from skyfold.config import Settings
    from skyfold.state import State
    from skyfold.app import Controller
    s, d, root, _ = env
    s.run(); (root / 'NewFolder').mkdir()
    settings = Settings(photos_dir_confirmed=True, drive_enabled=True, drive_dir=str(root), notifications=False)
    worker = SyncWorker(settings)
    worker.drive = s
    worker.state = State(tmp_path / 'worker.db')
    worker._run_photos = lambda *args: True
    worker._emit_stats = lambda: None
    worker._ensure_watchers = lambda: None
    queued_settings = []
    monkeypatch.setattr(Settings, 'save', lambda self: None)
    controller = SimpleNamespace(settings=settings, worker=worker,
        flyout=SimpleNamespace(set_paused=lambda paused: None), _sync_pause_label=lambda: None,
        req=SimpleNamespace(applySettings=SimpleNamespace(emit=lambda *args: queued_settings.append(args))))
    worker._refresh_storage = lambda **kw: Controller.toggle_pause(controller)
    calls = []; original = d.mkdir
    def mkdir(*args):
        calls.append(settings.paused)
        return original(*args)
    monkeypatch.setattr(d, 'mkdir', mkdir)
    worker._cycle(False, False)
    if worker._drive_thread is not None:
        worker._drive_thread.join(5)
        assert not worker._drive_thread.is_alive()
    assert queued_settings and settings.paused
    assert not calls, calls
