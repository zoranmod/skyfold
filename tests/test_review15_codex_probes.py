"""Reproduction probes from the fifteenth independent (Codex) review, kept as regression tests."""
import io
import logging
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest
import requests
from skyfold import drive_engine as de
from tests.test_drive import env, write
from tests.test_review11_codex_probes import strict_ids, fresh
from tests.test_review10_fixes import _with_mtimes
from tests.test_review14_codex_probes import crash_after_pc_rename


@pytest.mark.parametrize('directory', [False, True])
@pytest.mark.parametrize('local_action', ['unchanged', 'edit', 'delete'])
def test_same_size_cloud_edit_during_pc_recovery_is_not_adopted_unseen(env, monkeypatch, directory, local_action):
    s, d, root, _ = env
    old, new = crash_after_pc_rename(s, d, root, monkeypatch, directory)
    path = new + '/f00.txt' if directory else new
    original = (root / path).read_bytes()
    cloud = b'cloud!'
    assert len(cloud) == len(original) == 6
    d.put(path, cloud); d.items[path]['mtime'] = 1700000000.
    _with_mtimes(d, monkeypatch)
    if local_action == 'edit': write(root, path, b'local changed work')
    elif local_action == 'delete': (root / path).unlink()
    s = fresh(s, d, root)
    results = [s.run() for _ in range(4)]
    if local_action == 'unchanged':
        assert (root / path).read_bytes() == cloud, (s.state.all(), [(r.downloaded, r.uploaded) for r in results])
    else:
        assert any(it['data'] == cloud for it in d.items.values()), (d.trashed, d.uploads, [(r.downloaded, r.uploaded) for r in results])


def test_different_size_cloud_edit_is_preserved_control(env, monkeypatch):
    s, d, root, _ = env
    old, new = crash_after_pc_rename(s, d, root, monkeypatch, False)
    d.put(new, b'longer cloud work'); d.items[new]['mtime'] = 1700000000.
    _with_mtimes(d, monkeypatch)
    s = fresh(s, d, root)
    for _ in range(3): s.run()
    assert (root / new).read_bytes() == b'longer cloud work'


@pytest.mark.parametrize('replacement', [b'cloud!', b'longer replacement'])
def test_failed_pc_rename_never_deletes_unrelated_cloud_replacement(env, monkeypatch, replacement):
    s, d, root, _ = env
    s.cache_file = root.parent / 'cache.json'
    d.put('A.txt', b'base-0'); s.run(); strict_ids(d, monkeypatch)
    old_id = d.items['A.txt']['id']
    d.rename(d._item('A.txt'), 'B.txt')
    def crash(*args): raise SystemExit('after journal, BEFORE filesystem rename')
    monkeypatch.setattr(s, '_local_move', crash)
    with pytest.raises(SystemExit): s.run()
    # A different local item now occupies the old name; the destination never existed locally.
    temp = write(root, 'editor.tmp', b'new independent PC work')
    os.replace(temp, root / 'A.txt')
    d.trash(d._item('B.txt'))
    d.put('B.txt', replacement); d.items['B.txt']['mtime'] = 1700000000.
    assert d.items['B.txt']['id'] != old_id
    _with_mtimes(d, monkeypatch)
    d.trashed.clear()
    s = fresh(s, d, root)
    for _ in range(4): s.run()
    assert any(it['data'] == replacement for it in d.items.values()), (d.trashed, d.items)
    assert any(it['data'] == b'new independent PC work' for it in d.items.values())


@pytest.mark.parametrize('quit_entry', ['controller', 'shutdown'])
def test_no_notes_export_starts_after_quit_during_storage_refresh(tmp_path, monkeypatch, quit_entry):
    from skyfold import notes_engine as ne
    from skyfold.worker import SyncWorker
    from skyfold.config import Settings
    from skyfold.app import Controller
    from tests.test_notes import FakeNotes
    remote = FakeNotes(); remote.put('n1', 'New note', body='private synthetic note')
    root = tmp_path / 'Notes'
    notes = ne.NotesSyncer(ne.NotesState(tmp_path / 'notes.db'), remote, root)
    wk = SyncWorker(Settings(notes_enabled=True, notes_dir=str(root), photos_dir_confirmed=True, notifications=False))
    wk.notes = notes
    wk._run_photos = lambda *args: True
    wk._emit_stats = lambda: None
    watcher_restarts = []
    wk._ensure_watchers = lambda: watcher_restarts.append(True)
    started, release = threading.Event(), threading.Event()
    def refresh(**kw):
        started.set(); assert release.wait(5)
    wk._refresh_storage = refresh
    cycle = threading.Thread(target=lambda: wk._cycle(False, False))
    cycle.start(); assert started.wait(5)
    def finish(_timeout=0):
        release.set(); cycle.join(5)
        assert not cycle.is_alive()
        return True
    if quit_entry == 'controller':
        ctl = SimpleNamespace(worker=wk, tray=SimpleNamespace(hide=lambda: None),
                              thread=SimpleNamespace(quit=lambda: None, wait=finish),
                              app=SimpleNamespace(quit=lambda: None))
        Controller.quit(ctl)
    else: wk.shutdown(); finish()
    assert remote.exports == 0, (remote.exports, sorted(str(p.relative_to(root)) for p in root.rglob('*.html')), watcher_restarts)
    assert not watcher_restarts


def test_findmy_refresh_does_not_log_raw_auth_html(tmp_path, monkeypatch):
    from pyicloud import PyiCloudService
    from skyfold import auth, extras
    from skyfold.app import _RedactEmails
    monkeypatch.setattr(auth, '_CURRENT', None)
    auth._serialize_session_saves()
    api = PyiCloudService('review@example.invalid', None, cookie_directory=str(tmp_path), authenticate=False, refresh_interval=None)
    body = '<html><input name="firstName" value="Synthetic Private Person"><input name="scnt" value="synthetic-secret-nonce"><span>review&#64;example.invalid</span></html>'
    def request(_session, method, url, **kw):
        r = requests.Response(); r.url = url; r.status_code = 500; r.reason = 'Server Error'
        r.headers['Content-Type'] = 'text/html'; r._content = body.encode(); return r
    monkeypatch.setattr(requests.Session, 'request', request)
    def refresh(**kw): api.session.get('https://fmipweb.icloud.com/fmipservice/client/web/refreshClient')
    holder = SimpleNamespace(devices=SimpleNamespace(refresh=refresh, devices={}))
    stream = io.StringIO(); handler = logging.StreamHandler(stream); handler.addFilter(_RedactEmails())
    original_level = extras.log.level; extras.log.setLevel(logging.INFO); extras.log.addHandler(handler)
    try: extras.devices(holder, refresh=True)
    finally: extras.log.removeHandler(handler); extras.log.setLevel(original_level)
    output = stream.getvalue()
    assert 'Synthetic Private Person' not in output and 'synthetic-secret-nonce' not in output and 'review&#64;' not in output, output


@pytest.mark.parametrize('code', [503, '503', None, 'synthetic-secret-code'])
def test_describe_keeps_diagnostics_without_messages_or_bodies(code):
    from skyfold.auth import _describe
    from pyicloud.exceptions import PyiCloudAPIResponseException
    response = requests.Response(); response.status_code = 429; response._content = b'private synthetic HTML'
    try:
        try: raise ValueError('private synthetic inner error')
        except ValueError as inner: raise PyiCloudAPIResponseException('private synthetic outer error', code, response) from inner
    except PyiCloudAPIResponseException as error:
        result = _describe(error)
    assert 'private synthetic' not in result and 'synthetic-secret-code' not in result
    assert 'PyiCloudAPIResponseException' in result and 'ValueError' in result
    assert f'{Path(__file__).name}:' in result
    if code in (503, '503'): assert '(503)' in result
    if code is None: assert '(429)' in result


def test_crash_before_filesystem_rename_keeps_original_item_safe(env, monkeypatch):
    s, d, root, _ = env
    s.cache_file = root.parent / 'cache.json'
    d.put('A.txt', b'original'); s.run(); strict_ids(d, monkeypatch)
    d.rename(d._item('A.txt'), 'B.txt')
    def crash(*args): raise SystemExit('before filesystem rename')
    monkeypatch.setattr(s, '_local_move', crash)
    with pytest.raises(SystemExit): s.run()
    s = fresh(s, d, root)
    for _ in range(3): s.run()
    assert (root / 'B.txt').read_bytes() == b'original'
    assert d.items['B.txt']['data'] == b'original'
    assert not d.trashed and not s._journal()
