"""Reproduction probes from the sixteenth independent (Codex) review, kept as regression tests."""
import io
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from skyfold import drive_engine as de
from tests.test_drive import env, write
from tests.test_review11_codex_probes import strict_ids, fresh
from tests.test_review10_fixes import _with_mtimes


@pytest.mark.parametrize('crash', [False, True])
@pytest.mark.parametrize('local_action', ['unchanged', 'edit', 'delete'])
def test_cloud_edit_before_rename_journal_is_preserved(env, monkeypatch, crash, local_action):
    s, d, root, _ = env
    s.cache_file = root.parent / 'cache.json'
    for n in range(40): d.put(f'A/f{n:02}.txt', f'base-{n}'.encode())
    d.put('keep.txt', b'keep'); s.run(); strict_ids(d, monkeypatch)
    d.rename(d._item('A'), 'B')
    # The edit is already in the listing from which Skyfold decides the folder rename.
    d.put('B/f00.txt', b'cloud!'); d.items['B/f00.txt']['mtime'] = 1700000000.
    _with_mtimes(d, monkeypatch)
    original = s._local_move
    def interrupt(*args):
        assert original(*args)
        raise SystemExit('after filesystem rename, before database transaction')
    if crash:
        monkeypatch.setattr(s, '_local_move', interrupt)
        with pytest.raises(SystemExit): s.run()
        s = fresh(s, d, root)
    else: s.run()
    if local_action == 'edit': write(root, 'B/f00.txt', b'new PC work')
    elif local_action == 'delete': (root / 'B/f00.txt').unlink()
    results = [s.run() for _ in range(4)]
    if local_action == 'unchanged':
        assert (root / 'B/f00.txt').read_bytes() == b'cloud!', (s.state.all()['B/f00.txt'], results)
    else:
        assert any(it['data'] == b'cloud!' for it in d.items.values()), (d.trashed, d.uploads, results)


@pytest.mark.parametrize('route', ['storage', 'drive-retry', 'worker-error'])
def test_other_http_failures_do_not_log_response_bodies(tmp_path, monkeypatch, route):
    from pyicloud import PyiCloudService
    from skyfold import auth, worker, drive_remote
    from skyfold.config import Settings
    from skyfold.app import _RedactEmails
    monkeypatch.setattr(auth, '_CURRENT', None)
    auth._serialize_session_saves()
    api = PyiCloudService('review@example.invalid', None, cookie_directory=str(tmp_path), authenticate=False, refresh_interval=None)
    secret = 'SyntheticPrivatePerson'
    body = f'<html><input name="firstName" value="{secret}"><input name="scnt" value="synthetic-secret-nonce"></html>'
    def request(_session, method, url, **kw):
        r = requests.Response(); r.url = url; r.status_code = 500; r.reason = 'Server Error'
        r.headers['Content-Type'] = 'text/html'; r._content = body.encode(); return r
    monkeypatch.setattr(requests.Session, 'request', request)
    def fail(): return api.session.get('https://setup.icloud.com/setup/ws/1/storageUsageInfo')
    wk = worker.SyncWorker(Settings())
    class Account:
        @property
        def storage(self): return fail()
    wk.api = SimpleNamespace(account=Account())
    logger = drive_remote.log if route == 'drive-retry' else worker.log
    stream = io.StringIO(); handler = logging.StreamHandler(stream); handler.addFilter(_RedactEmails())
    before = logger.level; logger.setLevel(logging.INFO); logger.addHandler(handler)
    try:
        if route == 'storage': wk._refresh_storage(force=True)
        elif route == 'drive-retry':
            with pytest.raises(Exception): drive_remote.with_retries(fail, attempts=2, pause=0)
        else:
            try: fail()
            except Exception as exc: wk._handle(exc, [], 'Photos')
    finally: logger.removeHandler(handler); logger.setLevel(before)
    output = stream.getvalue()
    assert secret not in output and 'synthetic-secret-nonce' not in output, output


def test_note_preview_diagnostics_do_not_log_file_contents(tmp_path):
    from skyfold import notes_engine as ne
    from skyfold.app import _RedactEmails
    assets = tmp_path / ne.ASSETS; assets.mkdir()
    preview = assets / 'preview.png'; preview.write_bytes(b'not-an-image')
    page = tmp_path / 'Synthetic Private Note.html'
    page.write_text(f'<img data-id="drawing1" data-type="com.apple.paper" src="{ne.ASSETS}/preview.png">', encoding='utf-8')
    incoming = assets / 'incoming.dat'; incoming.write_bytes(b'PrivateNoteText!!more private note text')
    stream = io.StringIO(); handler = logging.StreamHandler(stream); handler.addFilter(_RedactEmails())
    before = ne.log.level; ne.log.setLevel(logging.INFO); ne.log.addHandler(handler)
    try: ne.upgrade_sketches(page, {'drawing1': 'https://example.invalid/drawing'}, lambda *_: incoming)
    finally: ne.log.removeHandler(handler); ne.log.setLevel(before)
    assert 'Drawing in' in stream.getvalue(), stream.getvalue()
    assert 'PrivateNoteText' not in stream.getvalue(), stream.getvalue()


@pytest.mark.parametrize('replace_old', [False, True])
def test_not_done_recovery_converges_without_repeating_uploads(env, monkeypatch, replace_old):
    s, d, root, _ = env
    s.cache_file = root.parent / 'cache.json'
    d.put('A.txt', b'original'); s.run(); strict_ids(d, monkeypatch)
    d.rename(d._item('A.txt'), 'B.txt')
    def crash(*args): raise SystemExit('before filesystem rename')
    monkeypatch.setattr(s, '_local_move', crash)
    with pytest.raises(SystemExit): s.run()
    if replace_old:
        os.replace(write(root, 'editor.tmp', b'new PC work'), root / 'A.txt')
    s = fresh(s, d, root)
    for _ in range(5): s.run()
    uploaded = len(d.uploads)
    final = [s.run() for _ in range(3)]
    assert not s._journal() and len(d.uploads) == uploaded
    assert all(r.downloaded == r.uploaded == r.errors == 0 for r in final)
    assert d.items['B.txt']['data'] == b'original'
    if replace_old: assert d.items['A.txt']['data'] == b'new PC work'
    else: assert 'A.txt' not in d.items


def test_held_pc_entry_recovers_when_access_returns(env, monkeypatch):
    from tests.test_review14_codex_probes import crash_after_pc_rename
    s, d, root, _ = env
    old, new = crash_after_pc_rename(s, d, root, monkeypatch, False)
    s = fresh(s, d, root)
    original = s._exact_id
    monkeypatch.setattr(s, '_exact_id', lambda rel: False if rel == new else original(rel))
    for _ in range(3): assert s.run().errors > 0 and s._journal()
    monkeypatch.setattr(s, '_exact_id', original)
    for _ in range(3): s.run()
    assert not s._journal() and (root / new).read_bytes() == b'base-0'


def test_pc_move_recovery_when_old_parent_becomes_a_file(env, monkeypatch):
    s, d, root, _ = env
    s.cache_file = root.parent / 'cache.json'
    d.put('P/A.txt', b'original'); d.put('Q', None); d.put('keep.txt', b'keep')
    s.run(); strict_ids(d, monkeypatch)
    d.move(d._item('P/A.txt'), d._item('Q'))
    original = s._local_move
    def crash(*args):
        assert original(*args)
        raise SystemExit('after local move')
    monkeypatch.setattr(s, '_local_move', crash)
    with pytest.raises(SystemExit): s.run()
    (root / 'P').rmdir(); write(root, 'P', b'a new file instead of the empty folder')
    write(root, 'Q/A.txt', b'edited after the move')
    s = fresh(s, d, root)
    results = [s.run() for _ in range(5)]
    assert not s._journal(), (s._journal(), [r.errors for r in results])
    assert d.items['Q/A.txt']['data'] == b'edited after the move'


def test_quit_during_worker_start_does_not_start_restore_or_watchers(tmp_path, monkeypatch):
    import threading
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
    from skyfold import worker, auth
    from skyfold.app import Controller
    from skyfold.config import Settings
    app = QApplication.instance() or QApplication([])
    root = tmp_path / 'Photos'; root.mkdir()
    monkeypatch.setattr(worker, 'STATE_DB', tmp_path / 'state.db')
    wk = worker.SyncWorker(Settings(apple_id='review@example.invalid', photos_dir_confirmed=True, photos_dir=str(root)))
    entered, release = threading.Event(), threading.Event()
    original = worker.State
    def slow_open(*args):
        entered.set(); assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(worker, 'State', slow_open)
    started = []
    def restore(*args): started.append(('restore', wk._closing_now())); return None
    monkeypatch.setattr(auth, 'restore', restore)
    class Watcher:
        def __init__(self, root, hit):
            self.root = root; started.append(('watcher', wk._closing_now()))
        def alive(self): return True
        def stop(self): pass
    monkeypatch.setattr(worker, '_Watcher', Watcher)
    thread = QThread(); wk.moveToThread(thread); thread.started.connect(wk.start)
    thread.start(); assert entered.wait(5)
    ctl = SimpleNamespace(worker=wk, tray=SimpleNamespace(hide=lambda: None), thread=thread, app=SimpleNamespace(quit=lambda: None))
    timer = threading.Timer(.1, release.set); timer.start()
    Controller.quit(ctl); timer.join(); assert thread.wait(5000)
    wk._stop_watchers()
    assert not started, started


def test_queued_findmy_request_does_not_start_after_quit(monkeypatch):
    import threading
    from PySide6.QtCore import QRunnable, QThreadPool
    from PySide6.QtWidgets import QApplication
    from skyfold import extras
    from skyfold.worker import SyncWorker
    from skyfold.config import Settings
    from skyfold.app import Controller
    from skyfold.ui.dialogs import FindMyDialog
    from skyfold.ui.theme import palette
    app = QApplication.instance() or QApplication([])
    wk = SyncWorker(Settings())
    pool = QThreadPool.globalInstance(); original_count = pool.maxThreadCount()
    pool.setMaxThreadCount(1)
    entered, release = threading.Event(), threading.Event()
    class Occupied(QRunnable):
        def run(self): entered.set(); release.wait(5)
    occupied = Occupied(); pool.start(occupied); assert entered.wait(5)
    started = []
    def devices(*args, **kwargs): started.append(wk._closing_now()); return []
    monkeypatch.setattr(extras, 'devices', devices)
    dlg = FindMyDialog(palette('light'), lambda: object())
    ctl = SimpleNamespace(worker=wk, tray=SimpleNamespace(hide=lambda: None),
                          thread=SimpleNamespace(quit=lambda: None, wait=lambda _: True),
                          app=SimpleNamespace(quit=lambda: None))
    try:
        Controller.quit(ctl)
        release.set(); assert pool.waitForDone(5000)
    finally:
        release.set(); pool.waitForDone(5000); pool.setMaxThreadCount(original_count)
    assert not started, started


def test_safe_diagnostics_retain_http_status_and_errno():
    from skyfold.auth import _describe
    from pyicloud.exceptions import PyiCloudAPIResponseException
    response = requests.Response(); response.status_code = 429
    outer = PyiCloudAPIResponseException('private response', 503, response)
    outer.__cause__ = PermissionError(13, 'private path')
    text = _describe(outer)
    assert '(503)' in text and 'HTTP 429' in text and 'errno 13' in text
    assert 'private' not in text
