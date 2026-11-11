"""Reproduction probes from the thirteenth independent (Codex) review, kept as regression tests."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
import requests
from skyfold import drive_engine as de
from tests.test_drive import env, write
from tests.test_review11_codex_probes import strict_ids, fresh, lose_answer


@pytest.mark.parametrize('change', ['recreate-old', 'list-error'])
def test_pc_move_recovery_preserves_intent_when_path_evidence_is_ambiguous(env, monkeypatch, change):
    s, d, root, _ = env
    s.cache_file = root.parent / 'cache.json'
    for n in range(40): d.put(f'A/f{n:02}.txt', f'body-{n}'.encode())
    d.put('keep.txt', b'keep'); s.run(); strict_ids(d, monkeypatch)
    d.rename(d._item('A'), 'B')
    original = s._local_move
    def crash(*args):
        assert original(*args)
        raise SystemExit('after local rename')
    monkeypatch.setattr(s, '_local_move', crash)
    with pytest.raises(SystemExit): s.run()
    assert s._journal()
    if change == 'recreate-old':
        write(root, 'A/new.txt', b'new independent work')
    d.trash(d._item('B'))
    s = fresh(s, d, root)
    list0 = os.listdir
    if change == 'list-error':
        once = [True]
        def listing(p):
            if Path(p) == root and once:
                once.pop()
                raise PermissionError('transient read failure')
            return list0(p)
        monkeypatch.setattr(os, 'listdir', listing)
    results = [s.run() for _ in range(5)]
    assert not any(p.startswith('B/') for p in d.items), (sorted(d.items), [(r.uploaded, r.pending_to_pc) for r in results])
    if change == 'recreate-old': assert (root / 'A/new.txt').read_bytes() == b'new independent work'


@pytest.mark.parametrize('edited', [b'EDIT', b'longer new local work'])
def test_edit_immediately_after_cloud_file_rename_reaches_cloud(env, monkeypatch, edited):
    s, d, root, _ = env
    d.put('a.txt', b'base'); s.run(); strict_ids(d, monkeypatch)
    d.rename(d._item('a.txt'), 'b.txt')
    original = s._local_move
    def edit(*args):
        assert original(*args)
        write(root, 'b.txt', edited)
        return True
    monkeypatch.setattr(s, '_local_move', edit)
    s.run()
    results = [s.run() for _ in range(4)]
    assert d.items['b.txt']['data'] == edited, (s.state.all(), [r.uploaded for r in results])


@pytest.mark.parametrize('directory', [False, True])
def test_cloud_case_only_rename_crash_follows_later_delete(env, monkeypatch, directory):
    s, d, root, _ = env
    s.cache_file = root.parent / 'cache.json'
    old, new = ('Folder', 'folder') if directory else ('Name.txt', 'name.txt')
    for n in range(4 if directory else 1): d.put(f'{old}/f{n}.txt' if directory else old, b'original')
    d.put('keep.txt', b'keep'); s.run(); strict_ids(d, monkeypatch)
    d.rename(d._item(old), new)
    original = s._local_move
    def crash(*args):
        assert original(*args)
        raise SystemExit('after case-only rename')
    monkeypatch.setattr(s, '_local_move', crash)
    with pytest.raises(SystemExit): s.run()
    d.trash(d._item(new))
    s = fresh(s, d, root)
    results = [s.run() for _ in range(4)]
    assert not any(p == new or p.startswith(new + '/') for p in d.items)
    assert not s._journal()
    if directory: assert any(r.pending_to_pc for r in results)


@pytest.mark.parametrize('stop', ['shutdown', 'cancel', 'pause-resume'])
def test_queued_drive_work_obeys_stop_and_resume(env, monkeypatch, stop):
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
    def rename(*args):
        result = rename0(*args)
        write(root, 'Docs/new.txt', b'queued work')
        with wk._drive_lock: wk._drive_changes.add('Docs/new.txt')
        if stop == 'shutdown':
            wk.shutdown()
        elif stop == 'cancel':
            wk.cancel()
        else:
            wk.settings.paused = True
            wk.cancel()
        return result
    monkeypatch.setattr(d, 'rename', rename)
    wk._drive_loop()
    assert 'Docs/new.txt' not in d.items, d.uploads
    assert s.cancel_flag.is_set()
    if stop == 'pause-resume':
        assert 'Docs/new.txt' in wk._drive_changes
        wk.settings.paused = False
        wk._drive_loop()
        assert d.items['Docs/new.txt']['data'] == b'queued work'


@pytest.mark.parametrize('scenario,expected', [('503-401', 'offline'), ('503-200', 'offline'), ('503-503', 'offline'), ('401-401', 'auth'), ('string-503', 'offline')])
def test_restore_classifies_first_server_failure_without_losing_evidence(tmp_path, monkeypatch, scenario, expected):
    from pyicloud import PyiCloudService
    from skyfold import auth
    from skyfold.worker import SyncWorker
    from skyfold.config import Settings
    session = tmp_path / 'session'; session.mkdir()
    monkeypatch.setattr(auth, 'SESSION_DIR', session)
    monkeypatch.setattr(auth, 'SIGNED_OUT', tmp_path / 'signed-out')
    monkeypatch.setattr(auth, '_CURRENT', None)
    seed = PyiCloudService('review@example.invalid', None, cookie_directory=str(session), authenticate=False, refresh_interval=None)
    seed.session.data['session_token'] = 'synthetic-token'
    seed.session.cookies.set('X-APPLE-WEBAUTH-TOKEN', 'synthetic-cookie', domain='.icloud.com', path='/')
    seed.session._save_session_data()
    calls = []
    def request(_session, method, url, **kwargs):
        endpoint = url.rsplit('/', 1)[-1]; calls.append(endpoint)
        response = requests.Response(); response.url = url
        response.headers['Content-Type'] = 'application/json'
        if scenario == 'string-503':
            response.status_code = 200
            response._content = b'{"error":"Service Unavailable","errorCode":"503"}'
        else:
            first, later = scenario.split('-')
            response.status_code = int(first if endpoint == 'validate' else later)
            response._content = b'{}'
        response.reason = 'Service Unavailable' if response.status_code == 503 else 'Unauthorized' if response.status_code == 401 else 'OK'
        return response
    monkeypatch.setattr(requests.Session, 'request', request)
    wk = SyncWorker(Settings(apple_id='review@example.invalid', photos_dir_confirmed=True))
    states, notifications = [], []
    wk.stateChanged.connect(lambda *a: states.append(a))
    wk.notify.connect(lambda *a: notifications.append(a))
    wk.restore_session()
    assert states[-1][0] == expected, (calls, states, notifications)
    if expected == 'offline': assert not notifications
    assert not auth.SIGNED_OUT.exists()


def test_pc_and_cloud_journals_under_same_parent_settle_together(env, monkeypatch):
    s, d, root, _ = env
    s.cache_file = root.parent / 'cache.json'
    for n in range(2): d.put(f'Shared/Local/f{n}.txt', f'L-{n}'.encode())
    for n in range(4): d.put(f'Shared/Cloud/f{n}.txt', f'C-{n}'.encode())
    d.put('keep.txt', b'keep'); s.run(); strict_ids(d, monkeypatch)
    os.rename(root / 'Shared/Local', root / 'Shared/LocalNew')
    d.rename(d._item('Shared/Cloud'), 'CloudNew')
    lose_answer(d, monkeypatch, 'rename')
    original = s._local_move
    def crash(*args):
        assert original(*args)
        raise SystemExit('after PC move while cloud journal also pending')
    monkeypatch.setattr(s, '_local_move', crash)
    with pytest.raises(SystemExit): s.run()
    assert len(s._journal()) == 2, s._journal()
    assert any(e.get('kind') == 'pc' for e in s._journal().values())
    d.trash(d._item('Shared/LocalNew')); d.trash(d._item('Shared/CloudNew'))
    s = fresh(s, d, root)
    results = [s.run() for _ in range(4)]
    assert not d.uploads, d.uploads
    assert not s._journal()
    assert any(r.pending_to_pc for r in results)


def test_crash_while_dropping_refused_move_can_retry_without_losing_work(env, monkeypatch):
    s, d, root, trashed = env
    d.put('G/a.txt', b'moving'); d.put('F/a.txt', b'unrelated')
    s.run(); strict_ids(d, monkeypatch)
    os.rename(root / 'G/a.txt', root / 'F/d.txt')
    lose_answer(d, monkeypatch, 'rename'); s.run()
    d.trash(d._item('F'))
    journal0 = s._journal_put
    def crash(ident, entry):
        if entry is None: raise SystemExit('before clearing rejected intent')
        return journal0(ident, entry)
    monkeypatch.setattr(s, '_journal_put', crash)
    with pytest.raises(SystemExit): s.run()
    s = fresh(s, d, root)
    for _ in range(5): s.run()
    assert not s._journal()
    assert (root / 'F/d.txt').read_bytes() == b'moving'
    assert not (root / 'F/a.txt').exists()
    assert sum(it['data'] == b'moving' for it in d.items.values()) == 1, d.items
