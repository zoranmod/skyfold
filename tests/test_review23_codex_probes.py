"""Reproduction probes from the twenty-third independent (Codex) review, kept as regression tests.

One probe is adapted (marked in place): the wrong-account pass is refused with SyncError.
"""
import io
import logging
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_drive import env as drive_env, write
from tests.test_engine import env as photos_env
from tests.test_notes import env as notes_env


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import requests
    def forbidden(*args, **kwargs):
        raise AssertionError('Real network forbidden in review probes')
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)


@pytest.mark.parametrize('operation', ['rename', 'move'])
@pytest.mark.parametrize('field', ['errorCode', 'status'])
def test_drive_rejection_does_not_disclose_unallowlisted_response(tmp_path, operation, field):
    from skyfold.drive_remote import ICloudDrive, DriveItem
    from skyfold.drive_engine import DriveSyncer, DriveState, DriveResult
    from skyfold.app import _RedactEmails
    from skyfold.safelog import brief
    from skyfold.worker import SyncWorker
    from skyfold.config import Settings
    from skyfold.state import State
    from skyfold.ui.dialogs import _Task
    secret = 'private-response-opaque-nonce' if field == 'errorCode' else 'ERROR-private-response-opaque-nonce'
    payload = {'items': [{'status': 'ERROR', field: secret}]}
    response = SimpleNamespace(json=lambda: payload)
    drive = SimpleNamespace(rename_items=lambda *a: payload,
                            session=SimpleNamespace(post=lambda *a, **kw: response),
                            service_root='https://offline.invalid', params={},
                            _raise_if_error=lambda response: None)
    remote = ICloudDrive(SimpleNamespace(drive=drive))
    item = DriveItem('a.txt', 'id', 'doc', 'zone', 'e1', False, 4, 1600000000)
    parent = DriveItem('Folder', 'folder', 'folder', 'zone', 'e2', True)
    # Exercise the actual adapter and ordinary move failure's production log sink.
    syncer = DriveSyncer(DriveState(tmp_path / 'drive.db'), remote, tmp_path / 'Drive', 5)
    worker = SyncWorker(Settings(notifications=False))
    worker.state = State(tmp_path / 'state.db')
    shown = []
    def perform():
        return remote.rename(item, 'b.txt') if operation == 'rename' else remote.move(item, parent)
    task = _Task(perform)
    task.signals.failed.connect(shown.append)
    logpath = tmp_path / 'skyfold.log'
    handler = logging.FileHandler(logpath, encoding='utf8')
    handler.addFilter(_RedactEmails())
    logger = logging.getLogger('skyfold'); level = logger.level
    logger.setLevel(logging.INFO); logger.addHandler(handler)
    try:
        new = 'b.txt' if operation == 'rename' else 'Folder/a.txt'
        syncer._remote_move({'a.txt': item, '': DriveItem('', 'root', 'root', 'zone', None, True),
                             'Folder': parent}, 'a.txt', new, DriveResult())
        task.run()
        try:
            perform()
        except RuntimeError as exc:
            worker._handle(exc, [], 'iCloud Drive')
    finally:
        logger.removeHandler(handler); logger.setLevel(level); handler.close()
    observed = {'log': logpath.read_text(encoding='utf8'),
                'activity': repr(worker.state.recent_activity()), 'dialog': repr(shown)}
    leaked = [sink for sink, text in observed.items() if secret.upper() in text.upper()]
    assert not leaked, (field, operation, leaked, observed)


def test_user_copy_of_exported_note_is_not_deleted(notes_env):
    s, r, root, trashed = notes_env
    r.put('n1', 'Budget', body='cloud original'); s.run()
    backup = root / 'Notes/Budget - my annotated copy.html'
    original = root / 'Notes/Budget.html'
    shutil.copy2(original, backup)
    backup.write_text(backup.read_text(encoding='utf8').replace('cloud original', 'my private annotations'), encoding='utf8')
    s.run(full=True)
    assert backup.exists(), trashed


def test_old_settings_dialog_cannot_restore_previous_account(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from skyfold.app import Controller, Requests
    from skyfold.config import Settings
    from skyfold.worker import SyncWorker
    from skyfold.state import State
    from skyfold.drive_engine import DriveState
    from skyfold.notes_engine import NotesState
    from skyfold.ui import theme
    from skyfold import auth
    app = QApplication.instance() or QApplication([])
    initial = Settings(apple_id='alice@example.invalid', photos_dir=str(tmp_path / 'Photos'),
                       drive_dir=str(tmp_path / 'Drive'), notes_dir=str(tmp_path / 'Notes'),
                       paused=True, notifications=False, start_with_windows=False)
    worker = SyncWorker(initial)
    worker.state = State(tmp_path / 'state.db')
    worker.drive_state = DriveState(tmp_path / 'drive.db')
    worker.notes_state = NotesState(tmp_path / 'notes.db')
    # Disable scheduling only; exercise real settings, account checks and engine construction.
    monkeypatch.setattr(worker, '_apply_timers', lambda: None)
    monkeypatch.setattr(worker, '_ensure_watchers', lambda: None)
    monkeypatch.setattr(Settings, 'save', lambda self: None)
    monkeypatch.setattr(auth, 'commit_login', lambda api: None)
    monkeypatch.setattr(auth, 'make_current', lambda api: None)
    req = Requests(); req.applySettings.connect(worker.apply_settings); req.setApi.connect(worker.set_api)
    c = SimpleNamespace(settings=initial, worker=worker, req=req, c=theme.palette('light'),
                        _dialogs={}, tray=SimpleNamespace(geometry=lambda: None),
                        flyout=SimpleNamespace(hide=lambda: None, set_account=lambda _: None, show_at=lambda _: None))
    c._show_dialog = lambda key, dlg: Controller._show_dialog(c, key, dlg)
    c.sign_out = lambda: None
    c._on_theme_changed = lambda: None
    c.restart = lambda: None
    Controller.open_settings(c)
    stale = c._dialogs['settings']
    Controller.open_login(c)
    login = c._dialogs['login']
    api_b = SimpleNamespace(account_name='bob@example.invalid')
    login.loggedIn.emit(api_b, 'bob@example.invalid')
    assert c.settings.apple_id == worker.settings.apple_id == 'bob@example.invalid'
    # The user returns to the still-open Settings window and presses Save, minutes later if desired.
    stale._save()
    observed = (c.settings.apple_id, worker.settings.apple_id, worker.api.account_name)
    for dlg in c._dialogs.values():
        dlg.close()
    assert observed == ('bob@example.invalid',) * 3, observed


def test_stale_settings_cannot_upload_alices_drive_into_bobs_account(tmp_path, monkeypatch):
    import hashlib
    from PySide6.QtWidgets import QApplication
    from skyfold.app import Controller, Requests
    from skyfold.config import Settings
    from skyfold import worker as wk, auth
    from skyfold.state import State
    from skyfold.drive_engine import DriveState, DriveSyncer
    from skyfold.notes_engine import NotesState
    from skyfold.ui import theme
    from tests.test_drive import FakeDrive
    app = QApplication.instance() or QApplication([])
    initial = Settings(apple_id='alice@example.invalid', photos_dir=str(tmp_path / 'Photos'),
                       drive_dir=str(tmp_path / 'Drive'), notes_dir=str(tmp_path / 'Notes'),
                       paused=True, notifications=False, start_with_windows=False)
    remote_a, remote_b = FakeDrive(), FakeDrive()
    remote_a.put('Private-Alice.txt', b'Alice private content')
    drive_state = DriveState(tmp_path / 'drive.db')
    old = DriveSyncer(drive_state, remote_a, Path(initial.drive_dir), 5)
    old.account = hashlib.sha256(initial.apple_id.encode()).hexdigest()[:16]
    old.run()
    worker = wk.SyncWorker(initial)
    worker.state = State(tmp_path / 'state.db'); worker.drive_state = drive_state
    worker.notes_state = NotesState(tmp_path / 'notes.db')
    monkeypatch.setattr(worker, '_apply_timers', lambda: None)
    monkeypatch.setattr(worker, '_ensure_watchers', lambda: None)
    monkeypatch.setattr(Settings, 'save', lambda self: None)
    monkeypatch.setattr(auth, 'commit_login', lambda api: None)
    monkeypatch.setattr(auth, 'make_current', lambda api: None)
    api_b = SimpleNamespace(account_name='bob@example.invalid')
    def adapter(api):
        assert api is api_b
        remote_b._api = api
        return remote_b
    monkeypatch.setattr(wk, 'ICloudDrive', adapter)
    # Keep cache work in the disposable fixture too.
    monkeypatch.setattr(wk, 'STATE_DB', tmp_path / 'state.db')
    req = Requests(); req.applySettings.connect(worker.apply_settings); req.setApi.connect(worker.set_api)
    c = SimpleNamespace(settings=initial, worker=worker, req=req, c=theme.palette('light'),
                        _dialogs={}, tray=SimpleNamespace(geometry=lambda: None),
                        flyout=SimpleNamespace(hide=lambda: None, set_account=lambda _: None, show_at=lambda _: None))
    c._show_dialog = lambda key, dlg: Controller._show_dialog(c, key, dlg)
    c.sign_out = lambda: None; c._on_theme_changed = lambda: None; c.restart = lambda: None
    Controller.open_settings(c); stale = c._dialogs['settings']
    Controller.open_login(c)
    c._dialogs['login'].loggedIn.emit(api_b, 'bob@example.invalid')
    stale._save()
    # Later, turn Drive on in a newly opened Settings window, accepting the standard prompts.
    Controller.open_settings(c); settings = c._dialogs['settings']
    settings.drive_on.setChecked(True)
    monkeypatch.setattr(settings, '_ask_what_to_sync', lambda: True)
    monkeypatch.setattr(settings, '_confirm_drive_size', lambda _: True)
    settings._save()
    assert worker.drive is not None
    # Skyfold's change to this probe: the pass is REFUSED (the folder's marker names Alice's account,
    # the session in use is Bob's), which is the designed outcome; the worker shows the message.
    from skyfold.engine import SyncError
    with pytest.raises(SyncError):
        worker.drive.run()  # the normal next pass against the fake account B
    observed = (c.settings.apple_id, worker.api.account_name, remote_b.uploads)
    for dlg in c._dialogs.values(): dlg.close()
    assert not remote_b.uploads, observed


@pytest.mark.parametrize('quick', [False, True])
def test_drive_case_only_local_rename_has_one_cloud_copy(drive_env, quick):
    s, d, root, _ = drive_env
    d.put('Report.txt', b'original'); s.run()
    (root / 'Report.txt').rename(root / 'report.txt')
    for _ in range(3): s.run(changed={'Report.txt', 'report.txt'} if quick else None)
    assert list(d.items) == ['report.txt'], d.items


@pytest.mark.parametrize('cloud_change', ['edit', 'delete'])
def test_drive_changed_local_content_remains_available(drive_env, cloud_change):
    s, d, root, _ = drive_env
    d.put('a.txt', b'original'); s.run()
    write(root, 'a.txt', b'my important work')
    if cloud_change == 'edit': d.put('a.txt', b'new cloud version')
    else: d.trash(d._item('a.txt'))
    for _ in range(3): s.run()
    assert any(p.read_bytes() == b'my important work' for p in root.glob('*.txt'))
    assert any(it['data'] == b'my important work' for it in d.items.values())


def test_photo_local_edit_survives_cloud_deletion(photos_env):
    s, r, root, trashed = photos_env
    r.add('p1', 'photo.jpg', b'original'); s.run()
    p = root / '2025/11/photo.jpg'; p.write_bytes(b'modified')
    r.remove('p1'); s.run(force_full=True)
    assert p.read_bytes() == b'modified' and not trashed


@pytest.mark.parametrize('field', ['requires_2fa', 'requires_2sa', 'is_trusted_session', 'sent', 'two_factor_delivery_method', 'accepted', 'restore_trusted'])
def test_all_changed_auth_diagnostics_reject_unexpected_text(tmp_path, monkeypatch, field):
    import pyicloud
    from skyfold import auth
    from skyfold.app import _RedactEmails
    secret = 'private-auth-payload'
    api = SimpleNamespace(requires_2fa=False, requires_2sa=False, is_trusted_session=True,
                          session=SimpleNamespace(data={}, _save_session_data=lambda: None),
                          authenticate=lambda **kw: None, security_key_names=[],
                          request_2fa_code=lambda: True, use_existing_trusted_device_code=lambda: None,
                          two_factor_delivery_method='sms', validate_2fa_code=lambda code: True,
                          trust_session=lambda: True)
    if field in ('requires_2fa', 'requires_2sa', 'is_trusted_session', 'two_factor_delivery_method'):
        setattr(api, field, secret)
    if field in ('sent', 'two_factor_delivery_method', 'restore_trusted'):
        api.requires_2fa = True
    if field == 'sent': api.request_2fa_code = lambda: secret
    if field == 'accepted': api.validate_2fa_code = lambda code: secret
    if field == 'restore_trusted': api.is_trusted_session = secret
    monkeypatch.setattr(pyicloud, 'PyiCloudService', lambda *a, **kw: api)
    monkeypatch.setattr(auth, 'SESSION_DIR', tmp_path / 'session')
    monkeypatch.setattr(auth, 'SIGNED_OUT', tmp_path / 'signed-out')
    auth.SESSION_DIR.mkdir()
    output = io.StringIO(); handler = logging.StreamHandler(output); handler.addFilter(_RedactEmails())
    logger = auth.log; level = logger.level; logger.setLevel(logging.INFO); logger.addHandler(handler)
    try:
        try:
            if field == 'accepted': auth.submit_code(api, '123456')
            elif field == 'restore_trusted': auth.restore('test@example.invalid')
            else: auth.start_login('test@example.invalid', 'synthetic-password')
        except auth.LoginError:
            pass
    finally:
        logger.removeHandler(handler); logger.setLevel(level)
    assert secret not in output.getvalue(), output.getvalue()


@pytest.mark.parametrize('payload', [
    {'PreviewPrivatePayload': {'type': 'STRING', 'value': 'private-note-payload'}},
    {'PreviewAppearances': {'type': 'STRING_LIST', 'value': ['private-note-payload']}},
    {'Orientation': {'type': 'STRING', 'value': 'private-note-payload'}},
])
def test_changed_sketch_field_diagnostics_hide_names_and_values(tmp_path, monkeypatch, payload):
    from pyicloud.common.cloudkit import CKRecord
    from pyicloud.services.notes.rendering.ck_datasource import CloudKitNoteDataSource
    from skyfold import notes_engine as ne
    from skyfold.app import _RedactEmails
    original = CloudKitNoteDataSource.add_attachment_record
    monkeypatch.setattr(CloudKitNoteDataSource, 'add_attachment_record', original)
    monkeypatch.setattr(ne, '_SKETCH_INFO', {}); monkeypatch.setattr(ne, '_SKETCH_FALLBACKS', {})
    monkeypatch.setattr(ne, '_CURRENT_NOTE', ['Test.html'])
    rec = CKRecord.model_validate({'recordName': 'attachment', 'recordType': 'Attachment',
        'fields': {'UTI': {'type': 'STRING', 'value': 'com.apple.paper'}, **payload}})
    ne._prefer_vector_sketches()
    output = io.StringIO(); handler = logging.StreamHandler(output); handler.addFilter(_RedactEmails())
    logger = ne.log; level = logger.level; logger.setLevel(logging.INFO); logger.addHandler(handler)
    try: CloudKitNoteDataSource().add_attachment_record(rec)
    finally: logger.removeHandler(handler); logger.setLevel(level)
    assert 'private-note-payload' not in output.getvalue()
    assert 'PreviewPrivatePayload' not in output.getvalue()


@pytest.mark.parametrize('route', ['foreign_argument', 'foreign_finished_text', 'foreign_exception', 'own_exception', 'excepthook', 'warning'])
def test_remaining_error_log_routes_hide_response_bodies(route):
    import warnings
    from skyfold.app import _RedactEmails
    secret = '<input name="scnt" value="synthetic-private-response">'
    output = io.StringIO(); handler = logging.StreamHandler(output); handler.addFilter(_RedactEmails())
    root = logging.getLogger(); root.addHandler(handler)
    try:
        foreign = logging.getLogger('pyicloud.review23')
        own = logging.getLogger('skyfold.review23')
        if route == 'foreign_argument': foreign.warning('Server answered %s', secret)
        elif route == 'foreign_finished_text': foreign.warning('Server answered ' + secret)
        elif route == 'foreign_exception': foreign.warning(ValueError(secret))
        elif route == 'own_exception': own.warning('Failed: %s', ValueError(secret))
        elif route == 'warning':
            logging.captureWarnings(True)
            try: warnings.warn(secret, UserWarning)
            finally: logging.captureWarnings(False)
        else:
            try: raise ValueError(secret)
            except ValueError: own.error('Unexpected error', exc_info=sys.exc_info())
    finally: root.removeHandler(handler)
    assert 'synthetic-private-response' not in output.getvalue(), output.getvalue()
