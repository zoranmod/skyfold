"""Reproduction probes from the eleventh independent (Codex) review, kept as regression tests."""
import json
import os
import sys
from pathlib import Path, PurePosixPath

import pytest
import requests

from tests.test_drive import env, write
from tests.test_review10_fixes import _with_mtimes
from skyfold import drive_engine as de


def strict_ids(d, monkeypatch):
    rename0, move0, trash0 = d.rename, d.move, d.trash
    def current(item):
        return next(p for p, it in d.items.items() if it['id'] == item.docwsid)
    def check(item, target):
        p = current(item)
        if d.items[p]['etag'] != item.etag:
            raise RuntimeError('stale etag')
        if target != p and target.lower() != p.lower() and any(q.lower() == target.lower() for q in d.items):
            raise RuntimeError('iCloud refused: CONFLICT')
        return p
    def rename(item, name):
        p = current(item)
        target = str(PurePosixPath(p).parent / name) if '/' in p else name
        check(item, target)
        rename0(d._item(p), name)
        return d.items[current(item)]['etag']
    def move(item, parent):
        p = current(item)
        target = f'{parent.path}/{PurePosixPath(p).name}' if parent.path else PurePosixPath(p).name
        check(item, target)
        move0(d._item(p), parent)
        return d.items[current(item)]['etag']
    def trash(item):
        p = current(item)
        check(item, p)
        trash0(d._item(p))
    monkeypatch.setattr(d, 'rename', rename)
    monkeypatch.setattr(d, 'move', move)
    monkeypatch.setattr(d, 'trash', trash)


def lose_answer(d, monkeypatch, method):
    original = getattr(d, method)
    once = [True]
    def operation(*args):
        result = original(*args)
        if once:
            once.pop()
            raise requests.exceptions.Timeout('committed, response lost')
        return result
    monkeypatch.setattr(d, method, operation)
    return original


def fresh(s, d, root):
    cache = s.cache_file
    s.state._db.close()
    return de.DriveSyncer(de.DriveState(root.parent / 'd.db'), d, root,
                          delete_threshold=s.delete_threshold, cache_file=cache)


def test_successful_folder_move_crash_does_not_resurrect_cloud_deletion(env, monkeypatch):
    s, d, root, trashed = env
    s.cache_file = root.parent / 'cache.json'
    for n in range(40):
        d.put(f'G/A/f{n:02}.txt', f'content-{n}'.encode())
    d.put('F/keep.txt', b'keep')
    s.run(); strict_ids(d, monkeypatch)
    os.rename(root / 'G/A', root / 'F/D')
    put0 = s.state.put
    def put(row):
        put0(row)
        if row.path == 'F/D':
            raise SystemExit('crash after parent row, before child rows')
    monkeypatch.setattr(s.state, 'put', put)
    with pytest.raises(SystemExit):
        s.run()
    d.trash(d._item('F/D'))
    s = fresh(s, d, root)
    results = [s.run() for _ in range(4)]
    assert not any(p.startswith('F/D/') for p in d.items), sorted(d.items)
    assert any(r.pending_to_pc for r in results), (trashed, results)


def test_root_partial_never_finishes_pending_move(env, monkeypatch):
    s, d, root, _ = env
    d.put('a.txt', b'base'); d.put('other.txt', b'other')
    s.run(); strict_ids(d, monkeypatch)
    os.rename(root / 'a.txt', root / 'b.txt')
    rename0 = d.rename
    monkeypatch.setattr(d, 'rename', lambda *a: (_ for _ in ()).throw(requests.exceptions.Timeout('no response')))
    s.run()
    monkeypatch.setattr(d, 'rename', rename0)
    walk0 = d.walk
    def partial(*a, **kw):
        out = walk0(*a, **kw)
        d.incomplete = {''}
        return {p: r for p, r in out.items() if p != 'other.txt'}
    monkeypatch.setattr(d, 'walk', partial)
    before = list(d.renamed)
    s.run()
    assert d.renamed == before, d.renamed
    assert s.state.get_meta(de.PENDING_MOVE)


def test_retry_after_cloud_edit_keeps_edit(env, monkeypatch):
    s, d, root, _ = env
    _with_mtimes(d, monkeypatch)
    d.put('G/a.txt', b'base'); d.put('F/keep.txt', b'keep')
    s.run(); strict_ids(d, monkeypatch)
    os.rename(root / 'G/a.txt', root / 'F/d.txt')
    lose_answer(d, monkeypatch, 'move')
    s.run()
    d.items['F/a.txt'].update(data=b'EDIT', etag=d._etag(), mtime=1_700_000_000.)
    rename0 = d.rename
    once = [True]
    def rename(*a):
        if once:
            once.pop()
            raise requests.exceptions.Timeout('before rename committed')
        return rename0(*a)
    monkeypatch.setattr(d, 'rename', rename)
    for _ in range(5):
        s.run()
    assert (root / 'F/d.txt').read_bytes() == b'EDIT', (d.items, s.state.all())


def test_second_lost_answer_does_not_redefine_original_cloud_version(env, monkeypatch):
    s, d, root, _ = env
    _with_mtimes(d, monkeypatch)
    d.put('G/a.txt', b'base'); d.put('F/keep.txt', b'keep')
    s.run(); strict_ids(d, monkeypatch)
    os.rename(root / 'G/a.txt', root / 'F/d.txt')
    lose_answer(d, monkeypatch, 'move')
    s.run()
    d.items['F/a.txt'].update(data=b'EDIT', etag=d._etag(), mtime=1_700_000_000.)
    lose_answer(d, monkeypatch, 'rename')
    # Power loss after the recovery step, before the normal sync planner runs.
    monkeypatch.setattr(s, '_check_cancel', lambda: (_ for _ in ()).throw(SystemExit('power loss')))
    with pytest.raises(SystemExit):
        s.run()
    s = fresh(s, d, root)
    for _ in range(5):
        s.run()
    assert d.items['F/d.txt']['data'] == b'EDIT'
    assert (root / 'F/d.txt').read_bytes() == b'EDIT'


def test_pending_journal_is_not_applied_to_new_root(env, monkeypatch, tmp_path):
    s, d, root, _ = env
    d.put('Docs/a.txt', b'base'); d.put('Docs/other.txt', b'other')
    s.run(); strict_ids(d, monkeypatch)
    os.rename(root / 'Docs/a.txt', root / 'Docs/b.txt')
    lose_answer(d, monkeypatch, 'rename')
    s.run()
    d.trash(d._item('Docs/b.txt'))
    # User switches Drive folder to an existing, previously unsynced folder.
    new_root = tmp_path / 'NewDrive'
    new_file = write(new_root, 'Docs/b.txt', b'MINE')
    old_stat = (root / 'Docs/b.txt').stat()
    os.utime(new_file, (old_stat.st_mtime, old_stat.st_mtime))
    s.root = new_root
    s.run()
    assert new_file.exists() and new_file.read_bytes() == b'MINE'


def test_late_conflict_destination_is_not_overwritten(env, monkeypatch):
    s, d, root, _ = env
    d.put('a.txt', b'base'); s.run()
    write(root, 'a.txt', b'PC edit')
    d.put('a.txt', b'Cloud edit')
    name0 = s._conflict_name
    created = []
    def name(rel):
        result = name0(rel)
        target = root / result
        target.write_bytes(b'OTHER APPLICATION SAVE')
        created.append(target)
        return result
    monkeypatch.setattr(s, '_conflict_name', name)
    s.run()
    assert created[0].read_bytes() == b'OTHER APPLICATION SAVE'


@pytest.mark.parametrize('kind', ['auth', 'unavailable', 'database'])
def test_download_failure_is_not_user_cancellation(env, monkeypatch, kind):
    from types import SimpleNamespace
    import sqlite3
    from skyfold.worker import SyncWorker
    from skyfold.config import Settings
    from skyfold.remote import AuthRequired, RemoteUnavailable
    s, d, root, _ = env
    d.put('a.txt', b'cloud data')
    wk = SyncWorker(Settings())
    wk.drive = s
    wk.state = SimpleNamespace(get=lambda *a: None)
    wk.api = object()
    states, logs, auth = [], [], []
    wk._set_state = lambda *a: states.append(a)
    wk.log = lambda *a: logs.append(a)
    wk._authLost.connect(auth.append)
    def error(*a):
        if kind == 'auth':
            raise AuthRequired('session expired')
        if kind == 'unavailable':
            raise RemoteUnavailable('Approve web data access on your iPhone')
        raise sqlite3.OperationalError('database or disk is full')
    if kind == 'database':
        put0 = s.state.put
        def put(row):
            if row.path == 'a.txt': error()
            return put0(row)
        monkeypatch.setattr(s.state, 'put', put)
    else:
        monkeypatch.setattr(d, 'download', error)
    problems = []
    assert not s.cancel_flag.is_set()
    wk._run_drive(problems)
    assert auth if kind == 'auth' else problems, (states, logs, auth, problems, s.cancel_flag.is_set())


def test_resume_clears_old_flag_and_reports_listing_error(env, monkeypatch):
    from types import SimpleNamespace
    from skyfold.worker import SyncWorker
    from skyfold.config import Settings
    s, d, root, _ = env
    s.run()
    wk = SyncWorker(Settings())
    wk.drive = s
    wk.state = SimpleNamespace(get=lambda *a: None)
    wk._set_state = lambda *a: None
    wk.log = lambda *a: None
    wk.cancel()
    assert s.cancel_flag.is_set()
    monkeypatch.setattr(d, 'walk', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('real listing failure')))
    problems = []
    wk._run_drive(problems)
    assert problems and not s.cancel_flag.is_set()


@pytest.mark.parametrize('position', ['final', 'intermediate'])
def test_previous_build_journal_converges_without_duplicate(env, monkeypatch, position):
    s, d, root, _ = env
    d.put('G/a.txt', b'legacy data'); d.put('F/keep.txt', b'keep')
    s.run(); strict_ids(d, monkeypatch)
    ident = d.items['G/a.txt']['id']
    os.rename(root / 'G/a.txt', root / 'F/d.txt')
    d.move(d._item('G/a.txt'), d._item('F'))
    if position == 'final':
        d.rename(d._item('F/a.txt'), 'd.txt')
    # Exact old format: unknown answer before the old code updates its baseline.
    s.state.set_meta(de.PENDING_MOVE, json.dumps({'id': ident, 'old': 'G/a.txt',
                                                'new': 'F/d.txt', 'at': 'G/a.txt'}))
    for _ in range(5):
        s.run()
    copies = sorted(p for p, it in d.items.items() if it['data'] == b'legacy data')
    assert copies == ['F/d.txt'], copies


@pytest.mark.parametrize('edit_cloud', [False, True])
def test_local_child_edit_during_folder_recovery_is_preserved(env, monkeypatch, edit_cloud):
    s, d, root, _ = env
    _with_mtimes(d, monkeypatch)
    for n in range(40):
        d.put(f'G/A/nested/f{n:02}.txt', f'base-{n}'.encode())
    d.put('F/keep.txt', b'keep')
    s.run(); strict_ids(d, monkeypatch)
    os.rename(root / 'G/A', root / 'F/D')
    lose_answer(d, monkeypatch, 'move')
    s.run()
    write(root, 'F/D/nested/f00.txt', b'local work')
    if edit_cloud:
        d.items['F/A/nested/f00.txt'].update(data=b'cloud work', etag=d._etag(), mtime=1_700_000_000.)
    for _ in range(5):
        s.run()
    contents = [p.read_bytes() for p in (root / 'F/D').rglob('*.txt')]
    assert b'local work' in contents
    if edit_cloud:
        assert b'cloud work' in contents
    assert s.state.get_meta(de.PENDING_MOVE) is None


def test_never_settled_entry_holds_only_affected_paths_but_forces_full_pass(env, monkeypatch):
    s, d, root, _ = env
    d.put('G/a.txt', b'base'); d.put('F/keep.txt', b'keep'); d.put('Other/x.txt', b'other')
    s.run(); strict_ids(d, monkeypatch)
    os.rename(root / 'G/a.txt', root / 'F/d.txt')
    lose_answer(d, monkeypatch, 'move')
    s.run()
    walk0 = d.walk
    def partial(*a, **kw):
        out = walk0(*a, **kw)
        d.incomplete = {'F'}
        return out
    monkeypatch.setattr(d, 'walk', partial)
    walks = d.full_walks
    for n in range(10):
        changed = write(root, 'Other/x.txt', f'local-{n}'.encode())
        os.utime(changed, (1700000000 + n, 1700000000 + n))
        result = s.run(changed={'Other/x.txt'})
        assert result.full and s.state.get_meta(de.PENDING_MOVE)
        assert d.items['Other/x.txt']['data'] == f'local-{n}'.encode()
    assert d.full_walks - walks == 10
    assert [p for p, it in d.items.items() if it['data'] == b'base'] == ['F/a.txt']


@pytest.mark.parametrize('order', ['parent-first', 'child-first'])
def test_related_parent_child_pending_entries_converge(env, monkeypatch, order):
    s, d, root, _ = env
    d.put('P/a.txt', b'child'); d.put('P/sub/x.txt', b'other')
    s.run(); strict_ids(d, monkeypatch)
    before = s.state.all()
    def entry(old, new):
        item = d._item(old)
        return {'old': old, 'at': old, 'new': new, 'size': item.size, 'mtime': item.mtime,
                'rows': [[p[len(old):], b.is_dir, b.etag, b.size, b.lmtime, b.lsize]
                         for p, b in before.items() if p == old or p.startswith(old + '/')]}
    parent = (d.items['P']['id'], entry('P', 'Q'))
    child = (d.items['P/a.txt']['id'], entry('P/a.txt', 'P/b.txt'))
    # Both intent records still refer to their original paths; both remote operations completed.
    os.rename(root / 'P/a.txt', root / 'P/b.txt')
    d.rename(d._item('P/a.txt'), 'b.txt')
    os.rename(root / 'P', root / 'Q')
    d.rename(d._item('P'), 'Q')
    pairs = [parent, child] if order == 'parent-first' else [child, parent]
    s.state.set_meta(de.PENDING_MOVE, json.dumps(dict(pairs)))
    for _ in range(5):
        s.run()
    assert [p for p, it in d.items.items() if it['data'] == b'child'] == ['Q/b.txt'], d.items
    assert [p.relative_to(root).as_posix() for p in root.rglob('b.txt')] == ['Q/b.txt']
    assert s.state.get_meta(de.PENDING_MOVE) is None


def test_recovered_folder_deletion_waits_and_approval_keeps_late_edit(env, monkeypatch):
    s, d, root, trashed = env
    for n in range(40):
        d.put(f'A/f{n:02}.txt', f'base-{n}'.encode())
    d.put('keep.txt', b'keep')
    s.run(); strict_ids(d, monkeypatch)
    os.rename(root / 'A', root / 'B')
    lose_answer(d, monkeypatch, 'rename')
    s.run()
    d.trash(d._item('B'))
    result = s.run()
    assert result.pending_to_pc
    assert len(list((root / 'B').glob('*.txt'))) == 40 and not trashed
    write(root, 'B/f00.txt', b'late local work')
    s.approved.update(result.pending_to_pc)
    for _ in range(4):
        s.run()
    assert (root / 'B/f00.txt').read_bytes() == b'late local work'
    assert 'B/f00.txt' not in trashed


@pytest.mark.parametrize('boundary', ['drop-1', 'drop-50', 'drop-101', 'put-1', 'put-50', 'put-101', 'journal', 'committed'])
def test_process_death_inside_recovery_transaction_is_atomic(tmp_path, boundary):
    import subprocess
    db = tmp_path / 'atomic.db'
    state = de.DriveState(db)
    state.put(de.Base('A', True, 'folder-tag', None, None, None, False))
    for n in range(100):
        state.put(de.Base(f'A/f{n:03}.txt', False, f'tag-{n}', n, 1600000000., n, False))
    entry = {'old': 'A', 'new': 'B', 'at': 'A'}
    state.set_meta(de.PENDING_MOVE, json.dumps({'id-A': entry}))
    before = state.all()
    source = r'''
import os, sys
from pathlib import Path
from skyfold import drive_engine as de
state = de.DriveState(Path(sys.argv[1]))
boundary = sys.argv[2]
s = de.DriveSyncer(state, None, Path(sys.argv[1]).parent, 3)
count = {'drop': 0, 'put': 0}
for method, label in [('drop_one', 'drop'), ('put', 'put')]:
    original = getattr(state, method)
    def wrapped(*args, original=original, label=label):
        result = original(*args)
        count[label] += 1
        if boundary == f'{label}-{count[label]}': os._exit(77)
        return result
    setattr(state, method, wrapped)
meta0 = state.set_meta
def meta(k, v):
    meta0(k, v)
    if k == de.PENDING_MOVE and boundary == 'journal': os._exit(77)
state.set_meta = meta
s._move_rows('A', 'B', None, 'id-A', None)
os._exit(77)
'''
    result = subprocess.run([sys.executable, '-c', source, str(db), boundary], timeout=20,
                            capture_output=True, text=True)
    assert result.returncode == 77, result.stderr
    if boundary == 'committed':
        assert len(state.all()) == 101
        assert all(p == 'B' or p.startswith('B/') for p in state.all())
        assert state.get_meta(de.PENDING_MOVE) is None
    else:
        assert state.all() == before
        assert json.loads(state.get_meta(de.PENDING_MOVE)) == {'id-A': entry}
    state._db.close()
