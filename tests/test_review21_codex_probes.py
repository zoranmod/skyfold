"""Reproduction probes from the twenty-first independent (Codex) review, kept as regression tests."""
import io
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from tests.test_engine import env as photos_env
from tests.test_drive import env, write


@pytest.mark.parametrize('changed_length', [False, True])
def test_edited_photo_is_kept_when_cloud_original_is_deleted(photos_env, changed_length):
    from PySide6.QtGui import QImage
    from PySide6.QtCore import QBuffer, QIODevice
    def jpeg(caption):
        image = QImage(8, 8, QImage.Format.Format_RGB32)
        image.fill(0xff336699)
        image.setText('Description', caption)
        buffer = QBuffer(); assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        assert image.save(buffer, 'JPEG')
        return bytes(buffer.data())
    old = jpeg('Original caption')
    edited = jpeg('My edited caption, longer' if changed_length else 'Modified caption')
    assert old != edited
    assert (len(old) != len(edited)) == changed_length
    s, r, root, trashed = photos_env
    r.add('A1', 'photo.jpg', old); s.run()
    p = root / '2025/11/photo.jpg'
    p.write_bytes(edited)  # normal local metadata edit; no timestamp preservation or timing hooks
    assert QImage(str(p)).text('Description').startswith('My edited' if changed_length else 'Modified')
    r.remove('A1')
    s.run(force_full=True)
    assert p.exists(), trashed
    assert p.read_bytes() == edited


@pytest.mark.parametrize('path', ['notes_change_reason', 'pcs_message', 'exception_as_message'])
def test_dependency_response_text_is_not_written_to_skyfold_log(tmp_path, monkeypatch, path):
    from skyfold.app import _RedactEmails
    from skyfold.notes_engine import ICloudNotes
    from pyicloud import PyiCloudService
    from pyicloud.exceptions import PyiCloudException
    secret = '<input name="scnt" value="synthetic-private-nonce"><p>Private response content</p>'
    logpath = tmp_path / 'skyfold.log'
    handler = logging.FileHandler(logpath, encoding='utf-8')
    handler.addFilter(_RedactEmails())
    root = logging.getLogger()
    root.addHandler(handler)
    logger = logging.getLogger('pyicloud')
    old_level = logger.level; logger.setLevel(logging.WARNING)
    try:
        if path == 'notes_change_reason':
            from pyicloud.services.notes.service import NotesService
            from pyicloud.common.cloudkit import CKErrorItem
            svc = NotesService.__new__(NotesService)
            svc._matches_current_sync_cursor = lambda since: False
            error = CKErrorItem(serverErrorCode='INTERNAL_ERROR', reason=secret, recordName='test-note')
            svc._raw = SimpleNamespace(changes=lambda **kw: [SimpleNamespace(records=[error])])
            remote = ICloudNotes(SimpleNamespace(notes=svc))
            with pytest.raises(Exception): remote.changes('synthetic-cursor')
        elif path == 'pcs_message':
            api = PyiCloudService.__new__(PyiCloudService)
            api._check_pcs_consent = lambda: {'isICDRSDisabled': True, 'isDeviceConsentedForPCS': True}
            api._send_pcs_request = lambda *a, **kw: {'status': 'failure', 'message': secret}
            with pytest.raises(PyiCloudException): api._request_pcs_for_service('photos')
        else:
            import requests
            from pyicloud.services.drive import DriveService
            response = requests.Response(); response.status_code = 500; response.reason = secret
            with pytest.raises(PyiCloudException): DriveService._raise_if_error(response)
    finally:
        root.removeHandler(handler); handler.close(); logger.setLevel(old_level)
    output = logpath.read_text(encoding='utf-8')
    assert 'synthetic-private-nonce' not in output and 'Private response content' not in output, output


def test_drive_content_edit_with_normal_timestamp_is_preserved(env):
    s, d, root, _ = env
    d.put('a.txt', b'original'); s.run()
    write(root, 'a.txt', b'modified')
    d.trash(d._item('a.txt'))
    for _ in range(3): s.run()
    assert (root / 'a.txt').read_bytes() == b'modified'
    assert d.items['a.txt']['data'] == b'modified'
