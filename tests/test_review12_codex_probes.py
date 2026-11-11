"""Reproduction probes from the twelfth independent (Codex) review, kept as regression tests."""
import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from skyfold import drive_engine as de
from skyfold.engine import Cancelled
from skyfold.remote import AuthRequired
from tests.test_drive import env, write
from tests.test_review11_codex_probes import strict_ids, lose_answer, fresh


def test_destination_parent_deleted_during_pending_move_can_settle(env, monkeypatch):
    s, d, root, trashed = env
    d.put('G/a.txt', b'moving')
    d.put('F/a.txt', b'unrelated cloud deletion')  # Forces rename-first ordering.
    s.run(); strict_ids(d, monkeypatch)
    os.rename(root / 'G/a.txt', root / 'F/d.txt')
    lose_answer(d, monkeypatch, 'rename')
    s.run()
    assert 'G/d.txt' in d.items and s.state.get_meta(de.PENDING_MOVE)
    d.trash(d._item('F'))
    results = [s.run(changed={'F/d.txt'}) for _ in range(8)]
    assert not (root / 'F/a.txt').exists(), (s._journal(), trashed, [(r.full, r.errors) for r in results])
    assert s.state.get_meta(de.PENDING_MOVE) is None
    assert (root / 'F/d.txt').read_bytes() == b'moving'


@pytest.mark.parametrize('boundary', ['after-local-rename', 'after-parent-row'])
def test_cloud_originated_folder_move_crash_preserves_later_deletion(env, monkeypatch, boundary):
    s, d, root, _ = env
    s.cache_file = root.parent / 'cache.json'
    for n in range(40):
        d.put(f'A/f{n:02}.txt', f'data-{n}'.encode())
    d.put('keep.txt', b'keep')
    s.run(); strict_ids(d, monkeypatch)
    d.rename(d._item('A'), 'B')
    if boundary == 'after-local-rename':
        move0 = s._local_move
        def move(*a):
            result = move0(*a)
            assert result
            raise SystemExit('power loss after PC rename')
        monkeypatch.setattr(s, '_local_move', move)
    else:
        put0 = s.state.put
        def put(row):
            put0(row)
            if row.path == 'B':
                raise SystemExit('power loss after new parent row')
        monkeypatch.setattr(s.state, 'put', put)
    with pytest.raises(SystemExit):
        s.run()
    d.trash(d._item('B'))
    s = fresh(s, d, root)
    results = [s.run() for _ in range(5)]
    assert not any(p.startswith('B/') for p in d.items), (sorted(d.items), [(r.uploaded, r.pending_to_pc) for r in results])
    assert any(r.pending_to_pc for r in results)


def test_pause_during_rename_does_not_start_another_queued_pass(env, monkeypatch):
    from skyfold.worker import SyncWorker
    from skyfold.config import Settings
    s, d, root, _ = env
    d.put('Docs/a.txt', b'base'); s.run(); strict_ids(d, monkeypatch)
    os.rename(root / 'Docs/a.txt', root / 'Docs/b.txt')
    wk = SyncWorker(Settings(drive_enabled=True, drive_dir=str(root), notifications=False))
    wk.drive = s
    data = {}
    wk.state = SimpleNamespace(get=lambda k: data.get(k), set=lambda k, v: data.update({k: v}))
    wk._emit_stats = lambda: None
    wk._drive_want_full = True
    rename0 = d.rename
    def rename(*a):
        result = rename0(*a)
        # A file was saved while the pass ran, then the user pressed Pause.
        write(root, 'Docs/new.txt', b'created before pause')
        with wk._drive_lock:
            wk._drive_changes.add('Docs/new.txt')
        wk.settings.paused = True
        wk.cancel()
        return result
    monkeypatch.setattr(d, 'rename', rename)
    walks = d.full_walks + d.quick_walks
    wk._drive_loop()
    assert 'Docs/new.txt' not in d.items, (d.uploads, d.full_walks + d.quick_walks - walks)
    assert s.cancel_flag.is_set()


@pytest.mark.parametrize('edited', [b'LOCAL', b'longer local work'])
def test_edit_after_download_publication_is_not_adopted_as_synced(env, monkeypatch, edited):
    s, d, root, _ = env
    d.put('a.txt', b'cloud')
    hide0 = de.hide_like_mac
    edited_once = []
    def hide(path, *args):
        if path == root / 'a.txt' and not edited_once:
            edited_once.append(True)
            path.write_bytes(edited)
            os.utime(path, (1700000000., 1700000000.))
        return hide0(path, *args)
    # This callback runs after the downloaded file is published, before the future returns.
    monkeypatch.setattr(de, 'hide_like_mac', hide)
    s.run()
    assert edited_once and (root / 'a.txt').read_bytes() == edited
    results = [s.run() for _ in range(4)]
    assert d.items['a.txt']['data'] == edited, (s.state.all(), [r.uploaded for r in results])


@pytest.mark.parametrize('collisions', [1, 20])
def test_conflict_race_retry_and_exhaustion_never_lose_any_copy(env, monkeypatch, collisions):
    s, d, root, _ = env
    d.put('a.txt', b'base'); s.run()
    write(root, 'a.txt', b'PC work')
    d.put('a.txt', b'cloud work')
    original = s._conflict_name
    created = []
    def choose(rel):
        path = original(rel)
        if len(created) < collisions:
            write(root, path, f'other-{len(created)}'.encode())
            created.append(path)
        return path
    monkeypatch.setattr(s, '_conflict_name', choose)
    first = s.run()
    for n, path in enumerate(created):
        assert (root / path).read_bytes() == f'other-{n}'.encode()
    assert b'PC work' in [p.read_bytes() for p in root.glob('*.txt')]
    if collisions == 20:
        assert first.errors and (root / 'a.txt').read_bytes() == b'PC work'
    monkeypatch.setattr(s, '_conflict_name', original)
    for _ in range(4): s.run()
    assert (root / 'a.txt').read_bytes() == b'cloud work'
    assert any(it['data'] == b'PC work' for it in d.items.values())


@pytest.mark.parametrize('same_account', [True, False])
def test_root_and_account_switch_cannot_replay_old_pending_snapshots(env, monkeypatch, tmp_path, same_account):
    s, d, root, _ = env
    s.account = 'account-A'
    d.put('a.txt', b'base'); s.run(); strict_ids(d, monkeypatch)
    os.rename(root / 'a.txt', root / 'b.txt')
    lose_answer(d, monkeypatch, 'rename')
    s.run()
    assert s._journal()
    new_root = tmp_path / 'new-root'
    new_file = write(new_root, 'b.txt', b'MINE')
    os.utime(new_file, (1600000000., 1600000000.))
    from tests.test_drive import FakeDrive
    remote = d if same_account else FakeDrive()
    if same_account:
        d.trash(d._item('b.txt'))
    replacement = de.DriveSyncer(s.state, remote, new_root, delete_threshold=3)
    replacement.account = 'account-A' if same_account else 'account-B'
    replacement.run()
    assert new_file.read_bytes() == b'MINE'
    assert not replacement._journal()


@pytest.mark.parametrize('failure', ['auth', 'database', 'user-cancel'])
def test_download_abort_stops_sibling_at_next_chunk_and_keeps_original_failure(env, monkeypatch, failure):
    import sqlite3
    s, d, root, _ = env
    d.put('a.txt', b'a'); d.put('b.txt', b'b')
    sibling_started = threading.Event()
    sibling_stopped = threading.Event()
    signal_abort = threading.Event()
    pool_shutdown = de.ThreadPoolExecutor.shutdown
    def shutdown(pool, *a, **kw):
        signal_abort.set()  # Called only after the original failure has set the internal abort event.
        return pool_shutdown(pool, *a, **kw)
    monkeypatch.setattr(de.ThreadPoolExecutor, 'shutdown', shutdown)
    def download(item, path, on_bytes):
        if item.path == 'b.txt':
            sibling_started.set()
            assert signal_abort.wait(5)
            try:
                on_bytes(1)
            except Cancelled:
                sibling_stopped.set()
                raise
            path.write_bytes(b'b')
        else:
            assert sibling_started.wait(5)
            if failure == 'auth':
                raise AuthRequired('expired')
            if failure == 'user-cancel':
                s.cancel_flag.set()
            path.write_bytes(b'a')
            on_bytes(1)
    monkeypatch.setattr(d, 'download', download)
    if failure == 'database':
        put0 = s.state.put
        def put(row):
            if row.path == 'a.txt':
                raise sqlite3.OperationalError('disk full')
            return put0(row)
        monkeypatch.setattr(s.state, 'put', put)
    expected = {'auth': AuthRequired, 'database': sqlite3.OperationalError, 'user-cancel': Cancelled}[failure]
    with pytest.raises(expected):
        s.run()
    assert sibling_stopped.is_set()
    assert not (root / 'b.txt').exists()
    assert s.cancel_flag.is_set() == (failure == 'user-cancel')


@pytest.mark.parametrize('failure', ['http-503-json', 'http-503-html', 'connection', 'timeout'])
def test_real_pyicloud_restore_outage_keeps_session_and_shows_offline(tmp_path, monkeypatch, failure):
    from pyicloud import PyiCloudService
    from skyfold import auth
    from skyfold.worker import SyncWorker
    from skyfold.config import Settings
    session = tmp_path / 'session'
    session.mkdir()
    monkeypatch.setattr(auth, 'SESSION_DIR', session)
    monkeypatch.setattr(auth, 'SIGNED_OUT', tmp_path / 'signed-out')
    seed = PyiCloudService('review@example.invalid', None, cookie_directory=str(session),
                          authenticate=False, refresh_interval=None)
    seed.session.data['session_token'] = 'synthetic-saved-token'
    seed.session._save_session_data()
    # Use the real pyicloud request/normalization/authentication stack over a fake HTTP transport.
    calls = []
    def request(_session, method, url, **kwargs):
        calls.append((method, url))
        if failure == 'connection':
            raise requests.exceptions.ConnectionError('synthetic outage')
        if failure == 'timeout':
            raise requests.exceptions.Timeout('synthetic timeout')
        response = requests.Response()
        response.status_code = 503
        response.reason = 'Service Unavailable'
        response.url = url
        response.headers['Content-Type'] = 'application/json' if failure.endswith('json') else 'text/html'
        response._content = b'{"error":"Service unavailable"}' if failure.endswith('json') else b'<h1>Service unavailable</h1>'
        return response
    monkeypatch.setattr(requests.Session, 'request', request)
    wk = SyncWorker(Settings(apple_id='review@example.invalid', photos_dir_confirmed=True))
    states, notifications = [], []
    wk.stateChanged.connect(lambda *a: states.append(a))
    wk.notify.connect(lambda *a: notifications.append(a))
    wk.restore_session()
    assert calls and states[-1][0] == 'offline', (calls, states, notifications)
    assert not notifications and not auth.SIGNED_OUT.exists()
    assert any('synthetic-saved-token' in p.read_text(errors='replace') for p in session.iterdir() if p.is_file())
