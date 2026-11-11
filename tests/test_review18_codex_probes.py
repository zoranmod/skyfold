"""Reproduction probes from the eighteenth independent (Codex) review, kept as regression tests."""
import io
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from skyfold import drive_engine as de
from tests.test_drive import env, write
from tests.test_notes import env as notes_env
from tests.test_engine import env as photos_env


@pytest.mark.parametrize('cloud_action', ['unchanged', 'edit', 'delete'])
def test_metadata_preserving_pc_edit_is_not_forgotten(env, cloud_action):
    s, d, root, trashed = env
    for n in range(10): d.put(f'keep{n}.txt', b'keep')
    d.put('a.txt', b'base-0'); s.run()
    p = root / 'a.txt'; old = p.stat()
    p.write_bytes(b'local!'); os.utime(p, ns=(old.st_atime_ns, old.st_mtime_ns))
    if cloud_action == 'edit': d.put('a.txt', b'cloud!')
    elif cloud_action == 'delete': d.trash(d._item('a.txt'))
    for _ in range(4): s.run()
    pc_bytes = [p.read_bytes() for p in root.glob('*.txt')]
    assert b'local!' in pc_bytes and any(it['data'] == b'local!' for it in d.items.values()), (pc_bytes, d.items, trashed)


def test_comparing_download_detects_atomic_replacement_after_hash(env, monkeypatch):
    s, d, root, _ = env
    d.put('a.txt', b'base-0'); s.run()
    d.items['a.txt']['etag'] = d._etag()
    p = root / 'a.txt'; st = p.stat(); real = de.file_hash; changed = []
    def hash_then_save(path):
        got = real(path)
        if Path(path) == p and not changed:
            tmp = root / 'editor-save.tmp'; tmp.write_bytes(b'local!')
            os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns)); os.replace(tmp, p)
            changed.append(True)
        return got
    monkeypatch.setattr(de, 'file_hash', hash_then_save)
    for _ in range(4): s.run()
    assert changed and d.items['a.txt']['data'] == b'local!', (d.items['a.txt'], s.state.all()['a.txt'])


@pytest.mark.parametrize('partial_reply', [False, True])
def test_truncated_content_check_does_not_authorize_cloud_delete(env, monkeypatch, partial_reply):
    import requests
    from skyfold.drive_remote import ICloudDrive
    s, d, root, _ = env
    for n in range(10): d.put(f'keep{n}.txt', b'keep')
    d.put('a.txt', b'base-0'); s.run()
    d.put('a.txt', b'base-0 plus unseen new cloud work')
    (root / 'a.txt').unlink()
    original = d.download
    class Session:
        def get(self, url, **kwargs):
            response = requests.Response(); response.url = url; response.status_code = 200
            if url.endswith('/download/by_id'):
                response._content = b'{"data_token":{"url":"https://example.invalid/content"}}'
            else:
                response._content = b'base-0' if partial_reply else d.items['a.txt']['data']
                if partial_reply:
                    response.status_code = 206
                    response.headers['Content-Range'] = f"bytes 0-5/{len(d.items['a.txt']['data'])}"
                response.headers['Content-Length'] = str(len(response._content))
            response._content_consumed = True
            return response
    remote = SimpleNamespace(session=Session(), params={}, _document_root='https://example.invalid',
                             _raise_if_error=lambda response: response.raise_for_status())
    adapter = ICloudDrive(SimpleNamespace(drive=remote))
    def partial(item, dest, on_bytes):
        if item.path == 'a.txt': adapter.download(item, dest, on_bytes)
        else: original(item, dest, on_bytes)
    monkeypatch.setattr(d, 'download', partial)
    s.run()
    assert 'a.txt' in d.items, d.trashed


@pytest.mark.parametrize('has_hash', [False, True])
def test_legacy_note_edit_is_recoverable_on_cloud_update(notes_env, monkeypatch, has_hash):
    s, remote, root, _ = notes_env
    remote.put('n1', 'A', body='original'); s.run()
    page = root / 'Notes/A.html'; st = page.stat()
    page.write_text(page.read_text(encoding='utf-8') + '\nUNIQUE PERSONAL ADDITION', encoding='utf-8')
    os.utime(page, ns=(st.st_atime_ns, st.st_mtime_ns))
    if not has_hash: s.state.set('page:n1', None)
    recycled = []
    def trash(p): recycled.append(p.read_bytes()); p.unlink()
    monkeypatch.setattr(s, '_trash', trash)
    remote.put('n1', 'A', body='updated in cloud'); s.run()
    remaining = [p.read_bytes() for p in root.rglob('*.html')]
    assert any(b'UNIQUE PERSONAL ADDITION' in b for b in remaining + recycled), (remaining, recycled)


@pytest.mark.parametrize('cloud_deleted', [False, True])
def test_photos_move_detection_does_not_adopt_unrelated_same_name_size(photos_env, cloud_deleted):
    s, remote, root, trashed = photos_env
    remote.add('A1', 'photo.jpg', b'original'); s.run()
    (root / '2025/11/photo.jpg').unlink()
    unrelated = root / 'Other/photo.jpg'; unrelated.parent.mkdir(); unrelated.write_bytes(b'personal')
    s.run()
    if cloud_deleted:
        remote.remove('A1'); s.run()
        assert unrelated.exists() and unrelated.read_bytes() == b'personal', trashed
    else: assert not any(row.rel_path == 'Other/photo.jpg' for row in s.state.files()), s.state.files()


def test_quit_during_upload_read_starts_no_cloud_write(env, monkeypatch):
    from skyfold.worker import SyncWorker
    from skyfold.config import Settings
    from skyfold.app import Controller
    s, d, root, _ = env
    d.put('a.txt', b'old'); s.run(); write(root, 'a.txt', b'new local work')
    wk = SyncWorker(Settings()); wk.drive = s
    ctl = SimpleNamespace(worker=wk, tray=SimpleNamespace(hide=lambda: None),
                          thread=SimpleNamespace(quit=lambda: None, wait=lambda _: True), app=SimpleNamespace(quit=lambda: None))
    original = Path.read_bytes
    def read_then_quit(path):
        data = original(path)
        if path == root / 'a.txt': Controller.quit(ctl)
        return data
    monkeypatch.setattr(Path, 'read_bytes', read_then_quit)
    try: s.run()
    except de.Cancelled: pass
    assert not d.trashed and not d.uploads, (d.trashed, d.uploads, s.closing.is_set())


def test_identical_batch_needs_only_bounded_staging_space(env, monkeypatch):
    import shutil
    from skyfold import engine
    s, d, root, _ = env
    for n in range(20): d.put(f'f{n:02}.txt', b'0123456789')
    s.run()
    for it in d.items.values(): it['etag'] = d._etag()
    # 100 bytes above reserve fits 6 concurrent 10-byte comparisons, but not all 200 bytes.
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=engine.DISK_RESERVE + 100))
    failures = []
    for _ in range(3):
        try: s.run()
        except de.SyncError as exc: failures.append(str(exc))
    assert not failures, failures


def test_comparison_cost_is_once_per_tag_then_converges(env, monkeypatch):
    s, d, root, _ = env
    for n in range(5): d.put(f'D/f{n}.txt', b'1234567890')
    s.run(); calls = []; original = d.download
    def count(item, dest, on_bytes): calls.append(item.path); original(item, dest, on_bytes)
    monkeypatch.setattr(d, 'download', count)
    for it in d.items.values(): it['etag'] = d._etag()
    first = s.run(); assert len(calls) == 5 and first.downloaded == 0
    for _ in range(3): s.run()
    assert len(calls) == 5


@pytest.mark.parametrize('cancel', [False, True])
def test_cloud_hash_failure_and_cancellation_leave_no_temp_files(env, monkeypatch, cancel):
    s, d, root, _ = env
    d.put('a.txt', b'base'); s.run()
    def fail(item, dest, on_bytes):
        dest.write_bytes(b'partial')
        if cancel:
            s.closing.set(); on_bytes(7)
        raise OSError('synthetic failure')
    monkeypatch.setattr(d, 'download', fail)
    if cancel:
        with pytest.raises(de.Cancelled): s._cloud_hash(d._item('a.txt'))
    else: assert s._cloud_hash(d._item('a.txt')) is None
    assert not list(root.glob(de.TMP_PREFIX + '*'))


def test_text_works_with_qt_json_sqlite_and_comparisons(tmp_path):
    import json
    import sqlite3
    from PySide6.QtCore import QObject
    from skyfold.i18n import t
    from skyfold.safelog import Text, brief
    text = t('Synthetic {value}', value='word')
    assert isinstance(text, Text) and text == 'Synthetic word'
    assert json.loads(json.dumps({'text': text}))['text'] == text
    db = sqlite3.connect(tmp_path / 'text.db')
    assert db.execute('SELECT ?', (text,)).fetchone()[0] == text
    obj = QObject(); obj.setObjectName(text); assert obj.objectName() == text
    assert brief(RuntimeError(text)) == text


def test_readonly_identical_file_is_left_alone(env):
    import stat
    s, d, root, trashed = env
    d.put('a.txt', b'same'); s.run()
    p = root / 'a.txt'; before = p.stat()
    p.chmod(stat.S_IREAD)
    try:
        d.items['a.txt']['etag'] = d._etag()
        result = s.run()
        assert result.errors == 0 and result.downloaded == 0 and not trashed
        assert p.read_bytes() == b'same' and p.stat().st_ino == before.st_ino
    finally: p.chmod(stat.S_IWRITE | stat.S_IREAD)


def test_locked_comparison_is_retried_without_overwrite(env, monkeypatch):
    s, d, root, trashed = env
    d.put('a.txt', b'same'); s.run()
    p = root / 'a.txt'; original = de.file_hash
    def locked(path):
        if Path(path) == p: raise PermissionError('synthetic sharing violation')
        return original(path)
    monkeypatch.setattr(de, 'file_hash', locked)
    d.items['a.txt']['etag'] = d._etag()
    result = s.run()
    assert result.errors == 1 and p.read_bytes() == b'same' and not trashed
    monkeypatch.setattr(de, 'file_hash', original)
    result = s.run()
    assert result.errors == 0 and s.state.all()['a.txt'].etag == d.items['a.txt']['etag']
