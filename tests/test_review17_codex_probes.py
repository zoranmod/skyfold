"""Reproduction probes from the seventeenth independent (Codex) review, kept as regression tests."""
import io
import logging
import os
import shutil
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest
import requests
from skyfold import drive_engine as de
from tests.test_drive import env, write
from tests.test_review10_fixes import _with_mtimes
from tests.test_review11_codex_probes import strict_ids, fresh


@pytest.mark.parametrize('quick', [False, True])
@pytest.mark.parametrize('action', ['unchanged', 'edit', 'delete'])
def test_cloud_edit_between_upload_and_learning_etag_is_preserved(env, monkeypatch, quick, action):
    s, d, root, _ = env
    _with_mtimes(d, monkeypatch)
    for n in range(10): d.put(f'keep{n}.txt', b'keep')
    s.run()
    write(root, 'new.txt', b'local0')
    s.run()
    assert s.state.all()['new.txt'].etag is None
    oldtime = (root / 'new.txt').stat().st_mtime
    d.put('new.txt', b'cloud!'); d.items['new.txt']['mtime'] = oldtime + 3600
    if action == 'delete': (root / 'new.txt').unlink()
    elif action == 'edit': write(root, 'new.txt', b'subsequent PC edit')
    for _ in range(4): s.run(changed={'new.txt'} if quick else None)
    if action == 'unchanged': assert (root / 'new.txt').read_bytes() == b'cloud!'
    else: assert any(it['data'] == b'cloud!' for it in d.items.values()), (d.trashed, d.uploads)


@pytest.mark.parametrize('delta', [0., .5, 1.5])
@pytest.mark.parametrize('action', ['unchanged', 'delete-file', 'delete-folder'])
def test_metadata_collision_is_not_proof_of_unchanged_content(env, monkeypatch, delta, action):
    s, d, root, _ = env
    _with_mtimes(d, monkeypatch)
    for n in range(10): d.put(f'keep{n}.txt', b'keep')
    d.put('D/file.txt', b'local0'); s.run()
    if delta:
        # Realistic precision mismatch: a PC upload keeps fractional mtime in the baseline,
        # while the adapter parses iCloud dateModified at whole-second precision.
        p = root / 'D/file.txt'; os.utime(p, (1600000000.5, 1600000000.5))
        s.run(); s.run()
    oldtime = s.state.all()['D/file.txt'].lmtime
    d.put('D/file.txt', b'cloud!'); d.items['D/file.txt']['mtime'] = oldtime + delta
    if action == 'delete-file': (root / 'D/file.txt').unlink()
    elif action == 'delete-folder': shutil.rmtree(root / 'D')
    for _ in range(4):
        res = s.run()
        s.approved |= set(res.pending_to_cloud)
    if action == 'unchanged': assert (root / 'D/file.txt').read_bytes() == b'cloud!'
    else: assert any(it['data'] == b'cloud!' for it in d.items.values()), d.trashed


@pytest.mark.parametrize('origin', ['apple-field', 'builtin-response', 'wrapped-response', 'own-wrapper'])
def test_brief_does_not_trust_external_text_by_exception_class(monkeypatch, origin):
    from skyfold.safelog import brief
    from skyfold.drive_remote import _check_item
    from skyfold.remote import RemoteUnavailable
    from skyfold.app import _RedactEmails
    secret = '<input name="firstName" value="SyntheticPrivatePerson"><input name="scnt" value="synthetic-secret-nonce">'
    if origin == 'apple-field':
        try: _check_item({'status': 'ERROR', 'error': secret}, 'move')
        except RuntimeError as exc: error = exc
    elif origin == 'builtin-response':
        error = RuntimeError(secret); error.response = requests.Response(); error.response.status_code = 500
    elif origin == 'wrapped-response':
        from pyicloud.exceptions import PyiCloudAPIResponseException
        error = RuntimeError(secret); error.__cause__ = PyiCloudAPIResponseException(secret, 500)
    else: error = RemoteUnavailable(secret)
    logger = logging.getLogger('skyfold.review17'); out = io.StringIO()
    h = logging.StreamHandler(out); h.addFilter(_RedactEmails()); logger.addHandler(h)
    before = logger.level; logger.setLevel(logging.INFO)
    try:
        logger.info('failure: %s', error)
        logger.info('activity: %s', brief(error))
    finally: logger.removeHandler(h); logger.setLevel(before)
    assert 'SyntheticPrivatePerson' not in out.getvalue() and 'synthetic-secret-nonce' not in out.getvalue(), out.getvalue()


def test_quit_while_drive_pass_is_entering_preserves_cancellation(env, monkeypatch):
    from skyfold.worker import SyncWorker
    from skyfold.config import Settings
    from skyfold.app import Controller
    s, d, root, _ = env
    for n in range(10): d.put(f'keep{n}.txt', b'keep')
    s.run(); write(root, 'new.txt', b'new local work')
    wk = SyncWorker(Settings(photos_dir_confirmed=True, notifications=False))
    wk.drive = s
    entered, release = threading.Event(), threading.Event()
    # _run_drive reads last-sync state after the loop's closing check, before engine.run().
    class State:
        def get(self, key): entered.set(); assert release.wait(5); return 'already synced'
        def set(self, *args): pass
    wk.state = State(); wk._emit_stats = lambda: None
    wk._kick_drive(full=True); assert entered.wait(5)
    def finish(_timeout): release.set(); wk._drive_thread.join(5); return not wk._drive_thread.is_alive()
    ctl = SimpleNamespace(worker=wk, tray=SimpleNamespace(hide=lambda: None),
                          thread=SimpleNamespace(quit=lambda: None, wait=finish),
                          app=SimpleNamespace(quit=lambda: None))
    Controller.quit(ctl)
    assert 'new.txt' not in d.items, (d.uploads, wk._closing, s.cancel_flag.is_set())


@pytest.mark.parametrize('direction', ['icloud-to-pc', 'pc-to-icloud'])
@pytest.mark.parametrize('directory', [False, True])
@pytest.mark.parametrize('delta', [0., 3600.])
def test_move_recovery_does_not_hide_cloud_content(env, monkeypatch, direction, directory, delta):
    from tests.test_review14_codex_probes import crash_after_pc_rename
    from tests.test_review11_codex_probes import lose_answer
    s, d, root, _ = env
    _with_mtimes(d, monkeypatch)
    if direction == 'icloud-to-pc':
        old, new = crash_after_pc_rename(s, d, root, monkeypatch, directory)
    else:
        s.cache_file = root.parent / 'cache.json'
        old, new = ('A', 'B') if directory else ('A.txt', 'B.txt')
        for n in range(40 if directory else 1):
            d.put(f'A/f{n:02}.txt' if directory else old, f'base-{n}'.encode())
        d.put('keep.txt', b'keep'); s.run(); strict_ids(d, monkeypatch)
        os.rename(root / old, root / new)
        lose_answer(d, monkeypatch, 'rename')
        s.run(); assert s._journal()
    path = f'{new}/f00.txt' if directory else new
    original_time = (root / path).stat().st_mtime
    d.put(path, b'cloud!'); d.items[path]['mtime'] = original_time + delta
    s = fresh(s, d, root)
    for _ in range(4): s.run()
    assert (root / path).read_bytes() == b'cloud!', (direction, directory, s.state.all().get(path))


def test_both_changed_with_matching_new_metadata_keep_both(env, monkeypatch):
    s, d, root, _ = env
    _with_mtimes(d, monkeypatch)
    d.put('file.txt', b'base-0'); s.run()
    p = write(root, 'file.txt', b'local!'); os.utime(p, (1700000000., 1700000000.))
    d.put('file.txt', b'cloud!'); d.items['file.txt']['mtime'] = 1700000000.
    assert not de._tag_only(s.state.all()['file.txt'], d._item('file.txt'))
    for _ in range(4): s.run()
    pc_contents = [p.read_bytes() for p in root.rglob('*.txt')]
    assert b'cloud!' in pc_contents and b'local!' in pc_contents, pc_contents


def test_upload_payload_preserves_client_mtime_without_network(tmp_path):
    from skyfold.drive_remote import ICloudDrive, DriveItem
    from pyicloud.services.drive import DriveService
    payloads = []
    class Session:
        def post(self, url, **kwargs):
            payloads.append(kwargs['json'])
            response = requests.Response(); response.status_code = 200; response._content = b'{}'
            return response
    service = DriveService('https://example.invalid/drive', 'https://example.invalid/doc', Session(), {})
    def send(folder_id, fh, **kwargs):
        info = {'fileChecksum': 'fake', 'wrappingKey': 'fake', 'referenceChecksum': 'fake', 'size': 6}
        return service._update_contentws(folder_id, info, 'fake-document', fh, **kwargs)
    adapter = ICloudDrive(SimpleNamespace(drive=SimpleNamespace(send_file=send)))
    p = tmp_path / 'file.txt'; p.write_bytes(b'cloud!')
    os.utime(p, (1600000000.123, 1600000000.123))
    parent = DriveItem('', 'root', 'root', 'z', None, True)
    adapter.upload(parent, p)
    assert payloads[0]['mtime'] == int(p.stat().st_mtime * 1000)
