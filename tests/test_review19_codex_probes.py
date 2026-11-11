"""Reproduction probes from the nineteenth independent (Codex) review, kept as regression tests."""
import os
import sys
import threading
from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path
import pytest
from skyfold import drive_engine as de
from tests.test_drive import env, write
from tests.test_engine import env as photos_env


@pytest.mark.parametrize('same_content', [True, False])
def test_two_moved_photos_with_same_name_and_size_are_both_preserved(photos_env, same_content):
    s, r, root, trashed = photos_env
    r.add('A1', 'photo.jpg', b'first!', date=datetime(2025, 1, 2, tzinfo=timezone.utc))
    r.add('A2', 'photo.jpg', b'first!' if same_content else b'other!', date=datetime(2025, 2, 2, tzinfo=timezone.utc))
    s.run()
    for month, folder in [('01', 'One'), ('02', 'Two')]:
        (root / folder).mkdir()
        (root / f'2025/{month}/photo.jpg').rename(root / folder / 'photo.jpg')
    s.run()
    assert not r.deleted and set(r.assets) == {'A1', 'A2'}, (r.deleted, s.state.files(), trashed)
    assert {row.rel_path for row in s.state.files()} == {'One/photo.jpg', 'Two/photo.jpg'}


def test_live_photo_with_false_candidate_before_true_candidate_is_followed(photos_env, monkeypatch):
    s, r, root, trashed = photos_env
    r.add('A1', 'photo.jpg', b'photo!', live=b'video!')
    s.run()
    (root / 'First').mkdir(); (root / 'Second').mkdir()
    (root / 'First/photo.jpg').write_bytes(b'other!')
    (root / '2025/11/photo.jpg').rename(root / 'Second/photo.jpg')
    import skyfold.engine as pe
    original_walk = os.walk
    def ordered_walk(*args, **kwargs):
        for base, dirs, files in original_walk(*args, **kwargs):
            dirs.sort()
            yield base, dirs, files
    monkeypatch.setattr(pe.os, 'walk', ordered_walk)
    s.run()
    assert (root / '2025/11/photo.MOV').exists(), (trashed, s.state.files())
    assert any(row.rel_path == 'Second/photo.jpg' for row in s.state.files())


@pytest.mark.parametrize('partial_http', [False, True])
def test_incomplete_photo_comparison_does_not_forget_live_pair(photos_env, monkeypatch, partial_http):
    s, r, root, trashed = photos_env
    r.add('A1', 'photo.jpg', b'photo!', live=b'video!'); s.run()
    (root / 'Sorted').mkdir()
    (root / '2025/11/photo.jpg').rename(root / 'Sorted/photo.jpg')
    if partial_http:
        import requests
        from pyicloud.common.cloudkit.client import _CloudKitHTTP
        from skyfold.remote import ICloudRemote
        class Session:
            def get(self, url, **kwargs):
                resp = requests.Response(); resp.status_code = 206; resp.url = url
                resp._content = b'ph'; resp._content_consumed = True
                resp.headers.update({'Content-Length': '2', 'Content-Range': 'bytes 0-1/6'})
                return resp
        http = _CloudKitHTTP('https://example.invalid', Session(), {})
        adapter = ICloudRemote(SimpleNamespace(photos=SimpleNamespace(
            private_client=SimpleNamespace(_client=SimpleNamespace(download_asset_stream=http.get_stream)))))
        monkeypatch.setattr(r, 'download', adapter.download)
    else:
        monkeypatch.setattr(r, 'download', lambda resource, dest, cb: dest.write_bytes(b'ph'))
    s.run()
    assert (root / '2025/11/photo.MOV').exists() and s.state.files(), (trashed, s.state.files())


def test_approval_rechecks_file_content_after_question(env):
    s, d, root, trashed = env
    for i in range(4): d.put(f'f{i}.txt', b'base!!')
    s.run()
    for i in range(4): d.trash(d._item(f'f{i}.txt'))
    def approve(cloud, pc):
        p = root / 'f0.txt'; st = p.stat()
        p.write_bytes(b'edited'); os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
        s.approved.update(pc)
    s.on_pending = approve
    s.run()
    assert (root / 'f0.txt').exists(), trashed
    assert (root / 'f0.txt').read_bytes() == b'edited'


def test_no_next_remote_mkdir_after_quit_during_previous_mkdir(env, monkeypatch):
    s, d, root, _ = env
    s.run()
    (root / 'First').mkdir(); (root / 'Second').mkdir()
    real = d.mkdir
    calls = []
    def mkdir(parent, name):
        calls.append((name, s.closing.is_set()))
        real(parent, name)
        s.closing.set()  # Quit while the first in-flight request completes
        s.cancel_flag.set()
    monkeypatch.setattr(d, 'mkdir', mkdir)
    try: s.run()
    except de.Cancelled: pass
    assert len(calls) == 1, calls


def test_no_remote_rename_after_quit_during_move(env, monkeypatch):
    from tests.test_review11_codex_probes import strict_ids
    s, d, root, _ = env
    d.put('Source/a.txt', b'contents'); d.put('Target', None); s.run()
    strict_ids(d, monkeypatch)
    (root / 'Source/a.txt').rename(root / 'Target/b.txt')
    move, rename = d.move, d.rename
    calls = []
    def move_then_quit(*args):
        result = move(*args)
        s.closing.set(); s.cancel_flag.set()
        return result
    def record_rename(*args):
        calls.append(s.closing.is_set())
        return rename(*args)
    monkeypatch.setattr(d, 'move', move_then_quit)
    monkeypatch.setattr(d, 'rename', record_rename)
    try: s.run()
    except de.Cancelled: pass
    assert not calls, (calls, d.renamed)


def test_parallel_downloads_reserve_space_retained_by_previous_versions(env, monkeypatch):
    s, d, root, _ = env
    for i in range(12): d.put(f'f{i:02}.txt', b'old-content')
    s.run()
    for i in range(12): d.put(f'f{i:02}.txt', b'new-content')
    unit = len(b'new-content'); reserve = 2 * unit
    accounting = {'free': reserve + 7 * unit, 'minimum': reserve + 7 * unit}
    lock = threading.Lock(); batch = threading.Barrier(de.WORKERS)
    def check(root, needed):
        with lock:
            if accounting['free'] < reserve + needed:
                raise de.SyncError('modelled insufficient free space')
    original_download = d.download
    def download(item, dest, cb):
        batch.wait(timeout=10)  # all concurrent fetches passed their separate space checks
        with lock:
            accounting['free'] -= item.size
            accounting['minimum'] = min(accounting['minimum'], accounting['free'])
        original_download(item, dest, cb)
        # Old files retained in the same-volume Recycle Bin do not release disk space.
    monkeypatch.setattr(de, 'check_free_space', check)
    monkeypatch.setattr(d, 'download', download)
    result = s.run()
    assert accounting['minimum'] >= reserve, (accounting, result)


def test_scrub_metadata_edit_uploads_once_and_then_converges(env):
    s, d, root, _ = env
    d.put('a.txt', b'base!!'); s.run()
    p = root / 'a.txt'; st = p.stat()
    p.write_bytes(b'edited'); os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    results = [s.run() for _ in range(4)]
    assert d.items['a.txt']['data'] == b'edited'
    assert d.uploads.count('a.txt') == 1
    assert all(r.uploaded == r.downloaded == 0 for r in results[-2:])


def test_negative_lmtime_marker_uploads_once_and_converges(env):
    s, d, root, _ = env
    d.put('a.txt', b'base!!'); s.run()
    p = root / 'a.txt'; st = p.stat()
    p.write_bytes(b'edited'); os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    d.put('a.txt', b'base!!')  # changed tag, unchanged cloud content
    s.run()
    assert s.state.all()['a.txt'].lmtime == -1
    results = [s.run() for _ in range(4)]
    assert d.items['a.txt']['data'] == b'edited' and p.read_bytes() == b'edited'
    assert d.uploads.count('a.txt') == 1
    assert all(r.uploaded == r.downloaded == 0 for r in results[-2:])


def test_unavailable_photo_comparison_keeps_pair_for_retry(photos_env, monkeypatch):
    s, r, root, trashed = photos_env
    r.add('A1', 'photo.jpg', b'photo!', live=b'video!'); s.run()
    (root / 'Sorted').mkdir()
    (root / '2025/11/photo.jpg').rename(root / 'Sorted/photo.jpg')
    def failure(*args): raise RuntimeError('fake unavailable comparison')
    monkeypatch.setattr(r, 'download', failure)
    s.run()
    assert (root / '2025/11/photo.MOV').exists() and len(s.state.files()) == 2
    assert not trashed and not r.deleted
