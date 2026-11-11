"""Reproduction probes from the twenty-second independent (Codex) review, kept as regression tests."""
import hashlib
import io
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from tests.test_engine import env as photos_env
from tests.test_notes import env as notes_env


def test_sketch_diagnostics_do_not_log_server_field_text(tmp_path, monkeypatch):
    from skyfold import notes_engine as ne
    from skyfold.app import _RedactEmails
    from pyicloud.common.cloudkit import CKRecord
    from pyicloud.services.notes.rendering.ck_datasource import CloudKitNoteDataSource
    secret = 'private-note-response'
    record = CKRecord.model_validate({
        'recordName': 'test-attachment', 'recordType': 'Attachment',
        'fields': {'UTI': {'type': 'STRING', 'value': 'com.apple.paper'},
                   'PreviewAppearances': {'type': 'STRING_LIST', 'value': [secret]}}})
    # Restore the library hook after the test; use the real original implementation.
    original = CloudKitNoteDataSource.add_attachment_record
    monkeypatch.setattr(CloudKitNoteDataSource, 'add_attachment_record', original)
    monkeypatch.setattr(ne, '_SKETCH_INFO', {})
    monkeypatch.setattr(ne, '_SKETCH_FALLBACKS', {})
    monkeypatch.setattr(ne, '_CURRENT_NOTE', ['Notes/Test.html'])
    ne._prefer_vector_sketches()
    logpath = tmp_path / 'skyfold.log'
    handler = logging.FileHandler(logpath, encoding='utf-8'); handler.addFilter(_RedactEmails())
    previous = ne.log.level; ne.log.setLevel(logging.INFO); ne.log.addHandler(handler)
    try: CloudKitNoteDataSource().add_attachment_record(record)
    finally:
        ne.log.removeHandler(handler); handler.close(); ne.log.setLevel(previous)
    output = logpath.read_text(encoding='utf-8')
    assert secret not in output, output


def test_signin_diagnostics_do_not_log_unexpected_server_status_text(tmp_path, monkeypatch):
    import pyicloud
    from pyicloud import PyiCloudService
    from skyfold import auth
    from skyfold.app import _RedactEmails
    secret = 'private-auth-response'
    api = PyiCloudService.__new__(PyiCloudService)
    api.data = {'hsaTrustedBrowser': secret, 'dsInfo': {'hsaVersion': 2}}
    api._requires_mfa = False; api._auth_data = {}
    api._session = SimpleNamespace(data={'session_token': 'saved'})
    api.authenticate = lambda **kw: None  # accepted fake authentication; no account is contacted
    assert api.is_trusted_session == secret  # installed pyicloud uses typing.cast, not runtime validation
    monkeypatch.setattr(pyicloud, 'PyiCloudService', lambda *a, **kw: api)
    monkeypatch.setattr(auth, 'SESSION_DIR', tmp_path / 'session')
    logpath = tmp_path / 'skyfold.log'
    handler = logging.FileHandler(logpath, encoding='utf-8'); handler.addFilter(_RedactEmails())
    previous = auth.log.level; auth.log.setLevel(logging.INFO); auth.log.addHandler(handler)
    try: auth.start_login('review@example.invalid', 'fake-password')
    finally:
        auth.log.removeHandler(handler); handler.close(); auth.log.setLevel(previous)
    output = logpath.read_text(encoding='utf8')
    assert secret not in output, output


@pytest.mark.parametrize('cloud_delete', [True, False])
def test_edited_live_video_survives_original_removal(photos_env, cloud_delete):
    s, r, root, trashed = photos_env
    s.settings.delete_pc_to_cloud = cloud_delete
    r.add('A1', 'photo.jpg', b'photo', live=b'video'); s.run()
    (root / '2025/11/photo.jpg').unlink()
    video = root / '2025/11/photo.MOV'; video.write_bytes(b'EDIT!')
    s.run()
    assert video.read_bytes() == b'EDIT!' and 'photo.MOV' not in trashed
    assert ('A1' in r.assets) == (not cloud_delete)
    assert not s.state.files()


def test_adopted_photo_gets_hash_and_changed_content_is_kept(photos_env):
    s, r, root, trashed = photos_env
    root.mkdir(parents=True); (root / '2025/11').mkdir(parents=True)
    p = root / '2025/11/photo.jpg'; p.write_bytes(b'original')
    r.add('A1', 'photo.jpg', b'original')
    ts = r.assets['A1'].date.timestamp(); os.utime(p, (ts, ts))
    s.run()
    assert s.state.files()[0].rel_path == '2025/11/photo.jpg'
    assert s.state.files()[0].hash == hashlib.sha256(b'original').hexdigest()
    p.write_bytes(b'modified'); r.remove('A1'); s.run(force_full=True)
    assert p.read_bytes() == b'modified' and not trashed


def test_moved_photo_retains_hash_and_edited_copy_is_kept(photos_env):
    s, r, root, trashed = photos_env
    r.add('A1', 'photo.jpg', b'original'); s.run()
    before = s.state.files()[0].hash
    (root / 'Sorted').mkdir()
    p = root / 'Sorted/photo.jpg'; (root / '2025/11/photo.jpg').rename(p)
    s.run(); assert s.state.files()[0].hash == before
    p.write_bytes(b'modified'); r.remove('A1'); s.run(force_full=True)
    assert p.read_bytes() == b'modified' and not trashed


def test_own_exception_log_and_activity_hide_remote_response(tmp_path):
    from skyfold.app import _RedactEmails
    from skyfold.worker import SyncWorker
    from skyfold.config import Settings
    from skyfold.state import State
    from skyfold.remote import RemoteUnavailable
    worker = SyncWorker(Settings(notifications=False))
    worker.state = State(tmp_path / 'state.db')
    secret = '<input name="scnt" value="synthetic-private-token">'
    output = io.StringIO(); handler = logging.StreamHandler(output); handler.addFilter(_RedactEmails())
    logger = logging.getLogger('skyfold'); previous = logger.level
    logger.setLevel(logging.INFO); logger.addHandler(handler)
    problems = []
    try:
        worker._handle(RemoteUnavailable(secret), problems, 'Photos')
        try: raise ValueError(secret)
        except ValueError:
            logger.error('Unexpected error', exc_info=sys.exc_info())  # app's excepthook path
    finally:
        logger.removeHandler(handler); logger.setLevel(previous)
    assert 'synthetic-private-token' not in output.getvalue()
    assert 'synthetic-private-token' not in repr(worker.state.recent_activity())
    assert 'synthetic-private-token' not in repr(problems)


def test_edited_note_survives_normal_cloud_update(notes_env):
    s, r, root, trashed = notes_env
    r.put('N1', 'Note', body='original'); s.run()
    p = root / 'Notes/Note.html'
    p.write_text(p.read_text(encoding='utf8').replace('original', 'my work'), encoding='utf8')
    r.put('N1', 'Note', body='new cloud content'); s.run()
    assert 'new cloud content' in p.read_text(encoding='utf8')
    assert trashed  # edited prior page is recoverable, never permanently unlinked
