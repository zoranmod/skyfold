"""Codex's probes from review 27 (of 7fc9baa), kept as tests. Repository fakes and disposable paths only.

The tests Codex named characterize_* asserted a defect (B1, B2, B3, B4); here the same scenarios
assert the fixed behaviour and are named fixed_*. The A1 blocker test is kept as Codex wrote it.
"""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from skyfold import drive_engine as de, engine as pe, notes_engine as ne
from tests.test_drive import env as drive_env, write
from tests.test_engine import env as photos_env
from tests.test_notes import env as notes_env
from tests.test_review26_codex_probes import launch_child


def interrupted_previous(drive_env, tmp_path):
    s, r, root, recycled = drive_env
    r.put('a.txt', b'OLD'); s.run(); r.put('a.txt', b'NEW')
    launch_child('drive-before-recycle', root, tmp_path / 'd.db')
    p = root / 'a (previous version).txt'
    assert p.read_bytes() == b'OLD'
    assert s.state.get_meta('prev:' + p.name) == s._file_id(p)
    return s, r, root, recycled, p


def test_fixed_quick_scan_never_uploads_recorded_previous(drive_env, tmp_path, monkeypatch):
    s, r, root, recycled, p = interrupted_previous(drive_env, tmp_path)
    recycler = s._trash_local
    def refused(path):
        raise PermissionError('synthetic Bin refusal')
    monkeypatch.setattr(s, '_trash_local', refused)
    assert s.run().uploaded == 0  # full recovery correctly quarantines the owned file
    assert s.state.get_meta('prev:' + p.name)
    monkeypatch.setattr(s, '_trash_local', recycler)
    res = s.run(changed={'a.txt'})  # normal watcher follow-up, after recovery could not recycle
    assert not res.full and r.quick_walks
    assert p.name not in r.uploads and p.name not in r.items  # left alone by the quick pass
    assert s.state.get_meta('prev:' + p.name) and p.read_bytes() == b'OLD'
    assert (root / 'a.txt').read_bytes() == b'NEW'
    s.run()  # the next full pass finishes the move to the Recycle Bin
    assert not p.exists() and p.name in recycled and not s.state.meta_items('prev:')
    assert p.name not in r.uploads


def test_fixed_unknown_previous_id_keeps_record_and_never_uploads(drive_env, tmp_path, monkeypatch):
    s, r, root, recycled, p = interrupted_previous(drive_env, tmp_path)
    real_id = s._file_id
    monkeypatch.setattr(s, '_file_id', lambda path: None)
    s.run()
    assert s.state.get_meta('prev:' + p.name)  # unknown is not "another file"
    assert p.name not in r.uploads and p.name not in recycled
    assert p.read_bytes() == b'OLD' and (root / 'a.txt').read_bytes() == b'NEW'
    monkeypatch.setattr(s, '_file_id', real_id)  # identities readable again: the move is finished
    s.run()
    assert not p.exists() and p.name in recycled and p.name not in r.uploads


def test_fixed_failed_lookup_keeps_previous_record(drive_env, tmp_path, monkeypatch):
    s, r, root, recycled, p = interrupted_previous(drive_env, tmp_path)
    actual_lstat = os.lstat
    def denied(q, *a, **k):
        if Path(q) == p:
            raise PermissionError(13, 'synthetic lookup failure')
        return actual_lstat(q, *a, **k)
    monkeypatch.setattr(os, 'lstat', denied)  # a lookup error is not absence
    monkeypatch.setattr(s, '_trash_local', lambda q: (_ for _ in ()).throw(PermissionError('busy')))
    observed = s.scan_local()
    assert p.name not in observed and s.state.get_meta('prev:' + p.name)
    monkeypatch.setattr(os, 'lstat', actual_lstat)  # looked at normally again
    monkeypatch.delattr(s, '_trash_local')  # the test's Recycle Bin again
    s.run()
    assert p.name not in r.uploads and not p.exists() and p.name in recycled


def test_no_file_ids_from_start_uses_registered_private_aside(drive_env, monkeypatch):
    s, r, root, recycled = drive_env
    r.put('a.txt', b'OLD'); s.run()
    monkeypatch.setattr(s, '_file_id', lambda path: None)
    r.put('a.txt', b'NEW'); s.run(); s.run()
    assert (root / 'a.txt').read_bytes() == b'NEW'
    assert not r.uploads and not s.state.meta_items('prev:')
    assert any(Path(p).name.startswith(de.ASIDE_PREFIX) for p in recycled)


def test_settle_applies_to_different_file_at_previous_name(drive_env, tmp_path):
    s, r, root, recycled, p = interrupted_previous(drive_env, tmp_path)
    p.rename(tmp_path / 'retained-old.txt')
    write(root, p.name, b'PERSONAL NEW', age=0)
    assert s.run().uploaded == 0
    assert p.name not in recycled and not s.state.meta_items('prev:')
    write(root, p.name, b'PERSONAL NEW', age=de.SETTLE_SECONDS + 2)
    assert s.run().uploaded == 1
    assert r.items[p.name]['data'] == b'PERSONAL NEW'
    assert (tmp_path / 'retained-old.txt').read_bytes() == b'OLD'


@pytest.mark.parametrize('kind', ['tmp', 'aside', 'prev'])
def test_absent_record_is_removed_without_claiming_unknown_temp(drive_env, kind):
    s, r, root, recycled = drive_env
    s.run()
    s.state.set_meta(kind + ':absent', '1')
    p = write(root, '.skyfold-tmp-personal-file', b'PERSONAL', age=90000)
    s.run()
    assert s.state.get_meta(kind + ':absent') is None
    assert p.read_bytes() == b'PERSONAL' and p.name not in r.uploads


def test_album_stage_recovery_preserves_new_occupant(photos_env, tmp_path):
    s, r, root, recycled = photos_env
    s.settings.albums_as_folders = True
    r.add('A1', 'photo.jpg', b'CLOUD'); r.album_map = {'Holiday':['A1']}
    s.run(force_full=True)
    p = root / 'Albums/Holiday/photo.jpg'
    p.unlink(); p.write_bytes(b'PERSONAL ORIGINAL')
    launch_child('photos-after-album-aside', root, tmp_path / 's.db')
    assert not p.exists() and s.state.items('albumstage:')
    p.write_bytes(b'NEW OCCUPANT')
    s.settings.albums_as_folders = False
    s.run(force_full=True)
    assert p.read_bytes() == b'NEW OCCUPANT'
    assert p.with_name('photo (2).jpg').read_bytes() == b'PERSONAL ORIGINAL'
    assert not s.state.items('albumstage:')


def test_unknown_album_folder_identity_preserves_folder(photos_env, monkeypatch):
    s, r, root, recycled = photos_env
    s.settings.albums_as_folders = True
    r.add('A1', 'photo.jpg', b'CLOUD'); r.album_map = {'Holiday':['A1']}
    monkeypatch.setattr(s, '_file_id', lambda p: None)
    s.run(force_full=True)
    s.settings.albums_as_folders = False; s.run(force_full=True)
    assert (root / 'Albums/Holiday').is_dir()
    assert not list((root / 'Albums/Holiday').iterdir())


def test_notes_changed_index_refused_recycle_is_retried(notes_env, monkeypatch):
    s, r, root, recycled = notes_env
    r.put('n1', 'A', body='OLD'); s.run(full=True)
    p = root / 'Notes.html'
    p.write_bytes(p.read_bytes() + b'<!-- PERSONAL EDIT -->')
    recycler = s._trash
    def refused(path): raise PermissionError('synthetic Bin refusal')
    monkeypatch.setattr(s, '_trash', refused)
    r.put('n1', 'A', body='NEW'); s.run(full=True)
    records = s.state.items('tmp:')
    assert records and any(v == 'index' for _, v in records)
    assert any(b'PERSONAL EDIT' in (root / k[4:]).read_bytes() for k, v in records if v == 'index')
    monkeypatch.setattr(s, '_trash', recycler)
    s.run(full=True)
    assert not s.state.items('tmp:') and recycled


@pytest.mark.parametrize('legacy', pe.LEGACY_MARKERS)
def test_fixed_tracked_root_never_claims_unrelated_legacy_marker(photos_env, legacy):
    s, r, root, recycled = photos_env
    r.add('A1', 'photo.jpg', b'CLOUD'); s.run(force_full=True)
    (root / pe.MARKER).unlink()
    unknown = root / legacy; unknown.write_bytes(b'PERSONAL MARKER-NAMED FILE')
    s.run(force_full=True)
    assert unknown.read_bytes() == b'PERSONAL MARKER-NAMED FILE'  # never renamed or changed
    assert (root / pe.MARKER).is_file() and (root / pe.MARKER).read_bytes() != unknown.read_bytes()


@pytest.mark.parametrize('ids_available', [True, False], ids=['native-ids-control', 'zero-ids-blocker'])
def test_unknown_stat_file_ids_must_not_unlink_personal_album_replacement(photos_env, monkeypatch, ids_available):
    """Safety assertion. FAILS: samefile accepts dev:0 == dev:0 before the B2 guard.

    Model stat on a filesystem with unavailable file IDs, consistently from album creation.
    No crash, timing race, database corruption or mocked samefile/unlink is involved.
    """
    s, r, root, recycled = photos_env
    actual_stat = os.stat
    def without_file_id(path, *args, **kwargs):
        st = actual_stat(path, *args, **kwargs)
        try:
            Path(path).relative_to(root)
        except (ValueError, TypeError):
            return st
        values = {name:getattr(st, name) for name in dir(st) if name.startswith('st_')}
        values['st_ino'] = 0
        return SimpleNamespace(**values)
    if not ids_available:
        monkeypatch.setattr(os, 'stat', without_file_id)
    s.settings.albums_as_folders = True
    r.add('A1', 'photo.jpg', b'CLOUD'); r.album_map = {'Holiday':['A1']}
    s.run(force_full=True)
    p = root / 'Albums/Holiday/photo.jpg'
    assert bool(s.state.get('albumid:Albums/Holiday/photo.jpg')) is ids_available
    p.unlink(); p.write_bytes(b'PERSONAL UNIQUE CONTENT')
    s.settings.albums_as_folders = False
    s.run(force_full=True)
    assert (root / '2025/11/photo.jpg').read_bytes() == b'CLOUD'
    preserved = p.exists() and p.read_bytes() == b'PERSONAL UNIQUE CONTENT'
    assert preserved or p.name in recycled, 'Unique personal bytes were unlinked, never recycled'


