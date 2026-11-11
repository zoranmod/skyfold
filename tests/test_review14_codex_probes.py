"""Reproduction probes from the fourteenth independent (Codex) review, kept as regression tests."""
import io
import json
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


def crash_after_pc_rename(s, d, root, monkeypatch, directory):
    s.cache_file = root.parent / 'cache.json'
    for n in range(40 if directory else 1):
        d.put(f'A/f{n:02}.txt' if directory else 'A.txt', f'base-{n}'.encode())
    d.put('keep.txt', b'keep'); s.run(); strict_ids(d, monkeypatch)
    old, new = ('A', 'B') if directory else ('A.txt', 'B.txt')
    d.rename(d._item(old), new)
    original = s._local_move
    def crash(*args):
        assert original(*args)
        raise SystemExit('after local rename')
    monkeypatch.setattr(s, '_local_move', crash)
    with pytest.raises(SystemExit): s.run()
    assert s._journal()
    return old, new


@pytest.mark.parametrize('directory', [False, True])
def test_local_deletion_after_move_crash_remains_a_local_deletion(env, monkeypatch, directory):
    s, d, root, _ = env
    old, new = crash_after_pc_rename(s, d, root, monkeypatch, directory)
    if directory:
        for p in (root / new).iterdir(): p.unlink()
        (root / new).rmdir()
    else: (root / new).unlink()
    assert not (root / old).exists() and not (root / new).exists()
    s = fresh(s, d, root)
    results = [s.run() for _ in range(4)]
    assert not (root / new).exists(), [(r.downloaded, r.pending_to_cloud) for r in results]
    if directory: assert any(r.pending_to_cloud for r in results)
    else: assert new not in d.items


@pytest.mark.parametrize('save', ['atomic-replace', 'in-place'])
def test_edit_after_crash_can_finish_without_permanent_hold(env, monkeypatch, save):
    s, d, root, _ = env
    old, new = crash_after_pc_rename(s, d, root, monkeypatch, False)
    before = os.lstat(root / new).st_ino
    if save == 'atomic-replace':
        replacement = write(root, 'editor-save.tmp', b'new local work')
        os.replace(replacement, root / new)
        assert os.lstat(root / new).st_ino != before
    else: write(root, new, b'new local work')
    s = fresh(s, d, root)
    results = [s.run() for _ in range(5)]
    assert any(it['data'] == b'new local work' for it in d.items.values()), (s._journal(), [(r.errors, r.uploaded) for r in results])
    assert not s._journal()


def test_unreadable_pc_move_identity_is_visible_to_user(env, monkeypatch):
    s, d, root, _ = env
    old, new = crash_after_pc_rename(s, d, root, monkeypatch, False)
    s = fresh(s, d, root)
    lstat0 = os.lstat
    def unreadable(path, *args, **kwargs):
        if Path(path) == root / new: raise PermissionError('file identity access denied')
        return lstat0(path, *args, **kwargs)
    monkeypatch.setattr(os, 'lstat', unreadable)
    messages = []
    s.cb.activity = lambda *args: messages.append(args)
    results = [s.run() for _ in range(4)]
    assert s._journal()
    assert any(r.errors for r in results) or any(m[0] == 'error' for m in messages), (messages, [(r.full, r.errors) for r in results])


@pytest.mark.parametrize('identity', [0, 2**100 + 123])
def test_zero_and_128_bit_ids_can_recover_simple_rename(env, monkeypatch, identity):
    s, d, root, _ = env
    lstat0 = os.lstat
    class Stat:
        def __init__(self, original): self.original = original
        @property
        def st_ino(self): return identity
        def __getattr__(self, key): return getattr(self.original, key)
    def stat(path, *args, **kwargs):
        value = lstat0(path, *args, **kwargs)
        return Stat(value) if Path(path).name in ('A.txt', 'B.txt') else value
    monkeypatch.setattr(os, 'lstat', stat)
    old, new = crash_after_pc_rename(s, d, root, monkeypatch, False)
    d.trash(d._item(new))
    s = fresh(s, d, root)
    for _ in range(3): s.run()
    assert new not in d.items and not (root / new).exists()
    assert not s._journal()


@pytest.mark.parametrize('second', ['expired', 'valid'])
def test_server_trouble_does_not_poison_next_restore(tmp_path, monkeypatch, second):
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
    phase = [0]
    def request(_session, method, url, **kwargs):
        r = requests.Response(); r.url = url; r.headers['Content-Type'] = 'application/json'
        if phase[0] == 0:
            r.status_code = 503 if url.endswith('/validate') else 401
            r._content = b'{}'
        elif second == 'expired': r.status_code = 401; r._content = b'{}'
        else: r.status_code = 200; r._content = b'{"hsaTrustedBrowser":true,"dsInfo":{"hsaVersion":2,"dsid":"fake"},"webservices":{}}'
        r.reason = 'Service Unavailable' if r.status_code == 503 else 'Unauthorized' if r.status_code == 401 else 'OK'
        return r
    monkeypatch.setattr(requests.Session, 'request', request)
    wk = SyncWorker(Settings(apple_id='review@example.invalid', photos_dir_confirmed=True))
    states, apis = [], []
    wk.stateChanged.connect(lambda *a: states.append(a))
    wk.set_api = apis.append
    wk.restore_session(); assert states[-1][0] == 'offline'
    phase[0] = 1
    wk.restore_session()
    if second == 'expired': assert states[-1][0] == 'auth' and not apis
    else: assert len(apis) == 1 and not auth._had_trouble(apis[0])


@pytest.mark.parametrize('status', [409, 500])
def test_html_auth_error_body_never_enters_redacted_log(tmp_path, monkeypatch, status):
    from pyicloud import PyiCloudService
    from skyfold import auth
    from skyfold.app import _RedactEmails
    session = tmp_path / 'session'; session.mkdir()
    monkeypatch.setattr(auth, 'SESSION_DIR', session)
    monkeypatch.setattr(auth, '_CURRENT', None)
    body = '<html><input name="firstName" value="Synthetic Private Person"><input name="scnt" value="synthetic-secret-nonce"><span>review&#64;example.invalid</span></html>'
    def request(_session, method, url, **kwargs):
        r = requests.Response(); r.url = url; r.status_code = status; r.reason = 'Conflict' if status == 409 else 'Server Error'
        r.headers['Content-Type'] = 'text/html'; r._content = body.encode()
        return r
    monkeypatch.setattr(requests.Session, 'request', request)
    # Start at the real MFA-page parser; no real password exchange or network takes place.
    monkeypatch.setattr(PyiCloudService, 'authenticate', lambda api, **kw: api._get_mfa_auth_options())
    stream = io.StringIO(); handler = logging.StreamHandler(stream)
    handler.addFilter(_RedactEmails()); handler.setFormatter(logging.Formatter('%(message)s'))
    auth.log.addHandler(handler)
    try:
        with pytest.raises(auth.LoginError): auth.start_login('review@example.invalid', 'synthetic-password')
    finally: auth.log.removeHandler(handler)
    output = stream.getvalue()
    assert 'Synthetic Private Person' not in output and 'synthetic-secret-nonce' not in output and 'review&#64;' not in output, output


@pytest.mark.parametrize('trigger', ['manual', 'watcher', 'full-check', 'shutdown'])
def test_stop_can_resume_but_shutdown_cannot_schedule_new_work(env, monkeypatch, trigger):
    from skyfold.worker import SyncWorker
    from skyfold.config import Settings
    s, d, root, _ = env
    d.put('a.txt', b'base'); s.run(); write(root, 'new.txt', b'new work')
    wk = SyncWorker(Settings(drive_enabled=True, drive_dir=str(root), notifications=False, photos_dir_confirmed=True))
    wk.drive = s
    data = {}
    wk.state = SimpleNamespace(get=lambda k: data.get(k), set=lambda k, v: data.update({k: v}))
    wk._emit_stats = lambda: None
    wk._run_photos = lambda *args: True
    wk._refresh_storage = lambda **kwargs: None
    wk._ensure_watchers = lambda: None
    wk.syncer = SimpleNamespace(cancel_flag=s.cancel_flag)
    wk._drive_changes.add('new.txt')
    if trigger == 'shutdown': wk.shutdown()
    else: wk.cancel()
    if trigger in ('manual', 'shutdown'): wk.sync_now(True)
    elif trigger == 'watcher': wk._drive_changed()
    else: wk.full_check()
    if wk._drive_thread: wk._drive_thread.join(5)
    assert not wk.drive_running()
    if trigger == 'shutdown': assert 'new.txt' not in d.items
    else: assert d.items['new.txt']['data'] == b'new work'


@pytest.mark.parametrize('entry', ['controller', 'worker'])
def test_real_quit_entry_stops_cycle_finishing_storage_refresh(env, monkeypatch, entry):
    from skyfold.worker import SyncWorker
    from skyfold.config import Settings
    from skyfold.app import Controller
    s, d, root, _ = env
    d.put('a.txt', b'base'); s.run(); write(root, 'new.txt', b'queued during refresh')
    wk = SyncWorker(Settings(drive_enabled=True, drive_dir=str(root), notifications=False, photos_dir_confirmed=True))
    wk.drive = s
    data = {}
    wk.state = SimpleNamespace(get=lambda k: data.get(k), set=lambda k, v: data.update({k: v}))
    wk._emit_stats = lambda: None
    wk._run_photos = lambda *args: True
    wk._ensure_watchers = lambda: None
    started, release = threading.Event(), threading.Event()
    def refresh(**kwargs):
        started.set()
        assert release.wait(5)
    wk._refresh_storage = refresh
    cycle = threading.Thread(target=lambda: wk._cycle(False, False))
    cycle.start(); assert started.wait(5)
    def finish(_timeout=0):
        release.set(); cycle.join(5)
        if wk._drive_thread: wk._drive_thread.join(5)
        assert not cycle.is_alive() and not wk.drive_running()
        return True
    if entry == 'controller':
        # QThread.quit does not interrupt its currently executing _cycle slot.
        controller = SimpleNamespace(worker=wk, tray=SimpleNamespace(hide=lambda: None),
                                     thread=SimpleNamespace(quit=lambda: None, wait=finish),
                                     app=SimpleNamespace(quit=lambda: None))
        Controller.quit(controller)
    else:
        wk.shutdown(); finish()
    assert 'new.txt' not in d.items, (d.uploads, getattr(wk, '_closing', False), getattr(wk, '_drive_stopped', False))


def test_409_html_boot_args_parse_through_real_mfa_code(tmp_path, monkeypatch):
    from pyicloud import PyiCloudService
    from skyfold import auth
    monkeypatch.setattr(auth, '_CURRENT', None)
    auth._serialize_session_saves()
    def request(_session, method, url, **kwargs):
        r = requests.Response(); r.url = url
        if kwargs.get('headers', {}).get('Accept') == 'text/html':
            r.status_code = 409; r.reason = 'Conflict'; r.headers['Content-Type'] = 'text/html'
            r._content = b'<html><script class="boot_args" type="application/json">{"direct":{"twoSV":{}}}</script></html>'
        else:
            r.status_code = 200; r.reason = 'OK'; r.headers['Content-Type'] = 'application/json'; r._content = b'{}'
        return r
    monkeypatch.setattr(requests.Session, 'request', request)
    api = PyiCloudService('review@example.invalid', None, cookie_directory=str(tmp_path), authenticate=False, refresh_interval=None)
    assert isinstance(api._get_mfa_auth_options(), dict)
    assert api._hsa2_boot_context is not None
