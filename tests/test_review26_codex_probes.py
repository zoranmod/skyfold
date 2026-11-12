"""Codex's probes from review 26 (of a0d6767), kept as tests. Existing fakes and disposable paths only.

The six tests Codex named characterize_* asserted the defect (B1-B6); here the same scenarios
assert the fixed behaviour and are named fixed_*.
The child mode exits the process at an actual filesystem operation, leaving
SQLite WAL and generated files as the application left them, without finally.
"""
import os
import subprocess
import sys
from pathlib import Path

if __name__ == '__main__':
    sys.path.insert(0, sys.argv[1])

import pytest
from skyfold import drive_engine as de, notes_engine as ne, engine as pe
from tests.test_drive import env as drive_env, FakeDrive, write
from tests.test_notes import env as notes_env, FakeNotes
from tests.test_engine import env as photos_env


def launch_child(mode, root, db):
    repo = Path(de.__file__).resolve().parent.parent
    done = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(repo), mode,
                           str(root), str(db)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 86, (mode, done.returncode, done.stdout, done.stderr)


def child():
    _, repo, mode, root_s, db_s = sys.argv
    root, db = Path(root_s), Path(db_s)
    def audit(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo'}:
            raise RuntimeError('QA: network disabled')
    sys.addaudithook(audit)
    if mode.startswith('drive-'):
        r = FakeDrive(); r.put('a.txt', b'OLD'); r.put('a.txt', b'NEW')
        s = de.DriveSyncer(de.DriveState(db), r, root, delete_threshold=3)
        if mode == 'drive-before-recycle':
            s._trash_local = lambda p: os._exit(86)
        elif mode == 'drive-during-download':
            real_download = r.download
            def download(item, dest, on_bytes):
                real_download(item, dest, on_bytes)
                os._exit(86)
            r.download = download
        else:
            rename = os.rename
            def interrupted(src, dst):
                rename(src, dst)
                src, dst = Path(src), Path(dst)
                if mode == 'drive-after-aside' and dst.name.startswith(de.ASIDE_PREFIX):
                    os._exit(86)
                if mode == 'drive-after-publish' and src.name.startswith(de.TMP_PREFIX) and dst.name == 'a.txt':
                    os._exit(86)
            os.rename = interrupted
        s.run()
    elif mode == 'photos-after-album-aside':
        from tests.test_engine import FakeRemote
        from skyfold.config import Settings
        from skyfold.state import State
        r = FakeRemote(); r.add('A1', 'photo.jpg', b'CLOUD'); r.album_map = {'Holiday':['A1']}
        s = pe.Syncer(State(db), r, Settings(photos_dir=str(root), albums_as_folders=False,
                                           folder_structure='year_month', delete_threshold=3))
        rename = os.rename
        def interrupted(src, dst):
            rename(src, dst)
            if Path(dst).name.startswith(pe.TMP_PREFIX + 'album-'):
                os._exit(86)
        os.rename = interrupted
        s.run(force_full=True)
    elif mode.startswith('notes-'):
        r = FakeNotes(); r.put('n1', 'A', body='OLD'); r.put('n1', 'A', body='NEW')
        s = ne.NotesSyncer(ne.NotesState(db), r, root)
        if mode == 'notes-during-export':
            export = r.export
            def interrupted(*args, **kwargs):
                export(*args, **kwargs)
                os._exit(86)
            r.export = interrupted
        else:
            rename = os.rename
            def interrupted(src, dst):
                rename(src, dst)
                if Path(dst).name.startswith(ne.STAGE_PREFIX + 'index-'):
                    os._exit(86)
            os.rename = interrupted
        s.run(full=True)
    raise SystemExit('Requested crash point was not reached')


@pytest.mark.parametrize('mode', ['drive-during-download', 'drive-after-aside', 'drive-after-publish'])
def test_real_drive_crash_has_durable_ownership_record(drive_env, tmp_path, mode):
    s, r, root, recycled = drive_env
    r.put('a.txt', b'OLD'); s.run(); r.put('a.txt', b'NEW')
    unknown = write(root, '.skyfold-tmp-user-backup', b'PERSONAL', age=90000)
    launch_child(mode, root, tmp_path / 'd.db')
    temporary = s.state.meta_items('tmp:')
    asides = s.state.meta_items('aside:')
    assert temporary
    if mode != 'drive-during-download':
        assert asides
        assert any((root / key[6:]).read_bytes() == b'OLD' for key in asides)
    s.run(); s.run()
    assert (root / 'a.txt').read_bytes() == b'NEW'
    assert unknown.read_bytes() == b'PERSONAL'
    assert not s.state.meta_items('aside:')
    # A vanished temp path can leave a stale metadata key; no unrelated path is touched.


@pytest.mark.parametrize('mode', ['notes-during-export', 'notes-after-index-aside'])
def test_real_notes_crash_has_durable_ownership_record(notes_env, tmp_path, mode):
    s, r, root, recycled = notes_env
    r.put('n1', 'A', body='OLD'); s.run(); r.put('n1', 'A', body='NEW')
    unknown = write(root, '.skyfold-tmp-note-user.html', b'PERSONAL', age=90000)
    launch_child(mode, root, tmp_path / 'n.db')
    records = dict(s.state.items('tmp:'))
    kind = 'stage' if mode == 'notes-during-export' else 'index'
    assert kind in records.values()
    assert any((root / key[4:]).exists() for key, value in records.items() if value == kind)
    s.run(full=True)
    assert b'NEW' in (root / 'Notes/A.html').read_bytes()
    assert (root / 'Notes.html').exists() and unknown.read_bytes() == b'PERSONAL'
    assert not s.state.items('tmp:')


def test_fixed_drive_previous_version_is_not_uploaded_after_crash(drive_env, tmp_path):
    s, r, root, recycled = drive_env
    r.put('a.txt', b'OLD'); s.run(); r.put('a.txt', b'NEW')
    launch_child('drive-before-recycle', root, tmp_path / 'd.db')
    named = root / 'a (previous version).txt'
    assert named.read_bytes() == b'OLD' and (root / 'a.txt').read_bytes() == b'NEW'
    # The record moved with the file: the friendly name is written down, the private one is not.
    assert s.state.meta_items('prev:') and not s.state.meta_items('aside:')
    s.run(); s.run()
    assert named.name not in r.uploads and named.name not in r.items
    assert not named.exists() and named.name in recycled  # finished: in the Recycle Bin
    assert (root / 'a.txt').read_bytes() == b'NEW' and not s.state.meta_items('prev:')


def test_fixed_unknown_album_identity_is_never_a_match(photos_env, monkeypatch):
    s, r, root, recycled = photos_env
    s.settings.albums_as_folders = True
    r.add('A1', 'photo.jpg', b'CLOUD'); r.album_map = {'Holiday':['A1']}
    s.run(force_full=True)
    rel = 'Albums/Holiday/photo.jpg'; p = root / rel
    p.unlink(); p.write_bytes(b'PERSONAL REPLACEMENT')
    s.state.set('albumid:' + rel, None)  # an older album entry with no identity
    monkeypatch.setattr(s, '_file_id', lambda p: None)  # stat failure / unavailable file identity
    s.settings.albums_as_folders = False
    s.run(force_full=True)
    assert p.read_bytes() == b'PERSONAL REPLACEMENT' and not recycled


def test_fixed_failed_notes_recycle_keeps_the_aside_on_record(notes_env, monkeypatch):
    s, r, root, recycled = notes_env
    r.put('n1', 'A', body='OLD'); s.run()
    p = root / 'Notes/A.html'; p.write_bytes(p.read_bytes() + b'UNIQUE LOCAL EDIT')
    prior = write(root, 'Notes/A (previous version).html', b'EXISTING BACKUP')
    def denied(p):
        raise PermissionError('synthetic Recycle Bin failure')
    monkeypatch.setattr(s, '_trash', denied)
    r.put('n1', 'A', body='NEW'); s.run()
    leftovers = list((root / 'Notes').glob(ne.STAGE_PREFIX + 'old-*'))
    assert len(leftovers) == 1 and b'UNIQUE LOCAL EDIT' in leftovers[0].read_bytes()
    assert s.state.get('tmp:' + leftovers[0].relative_to(root).as_posix()) == 'old'
    assert prior.read_bytes() == b'EXISTING BACKUP'
    # the Recycle Bin works again: the next sync finishes the job
    monkeypatch.setattr(s, '_trash', lambda q: (recycled.append(q.name), q.unlink()))
    s.run(full=True)
    assert not leftovers[0].exists() and leftovers[0].name in recycled and not s.state.items('tmp:')


def test_fixed_album_aside_is_put_back_after_crash(photos_env, tmp_path):
    s, r, root, recycled = photos_env
    s.settings.albums_as_folders = True
    r.add('A1', 'photo.jpg', b'CLOUD'); r.album_map = {'Holiday':['A1']}
    s.run(force_full=True)
    p = root / 'Albums/Holiday/photo.jpg'
    p.unlink(); p.write_bytes(b'PERSONAL REPLACEMENT')
    launch_child('photos-after-album-aside', root, tmp_path / 's.db')
    s.settings.albums_as_folders = False; s.run(force_full=True)
    assert not list(p.parent.glob(pe.TMP_PREFIX + 'album-*'))
    assert p.read_bytes() == b'PERSONAL REPLACEMENT' and not recycled
    assert not s.state.items('albumstage:')


def test_fixed_recreated_empty_album_directory_is_kept(photos_env):
    s, r, root, recycled = photos_env
    s.settings.albums_as_folders = True
    r.add('A1', 'photo.jpg', b'CLOUD'); r.album_map = {'Holiday':['A1']}
    s.run(force_full=True)
    original = root / 'Albums/Holiday'; saved = root / 'Albums/Archive'
    original.rename(saved)
    original.mkdir()  # manual replacement of a previously managed folder
    s.settings.albums_as_folders = False; s.run(force_full=True)
    assert original.is_dir() and (saved / 'photo.jpg').read_bytes() == b'CLOUD'
    assert 'albumdir:Albums/Holiday' not in dict(s.state.items('albumdir:'))


@pytest.mark.parametrize('name', pe.LEGACY_MARKERS)
def test_fixed_legacy_marker_lookalike_is_left_alone(photos_env, name):
    s, r, root, recycled = photos_env
    p = write(root, name, b'UNRELATED MARKER-LOOKALIKE CONTENT')
    s.run(force_full=True)
    assert p.read_bytes() == b'UNRELATED MARKER-LOOKALIKE CONTENT'
    assert (root / pe.MARKER).is_file() and (root / pe.MARKER).read_bytes() != p.read_bytes()
    assert not recycled


def test_unknown_index_is_info_not_error_and_does_not_block_pages(notes_env, tmp_path):
    from skyfold.engine import Callbacks
    s, r, root, recycled = notes_env
    r.put('n1', 'A'); s.run()
    contents = (root / 'Notes.html').read_bytes() + b'PERSONAL'
    second = tmp_path / 'other'; second.mkdir(); (second / 'Notes.html').write_bytes(contents)
    events = []
    class Capture(Callbacks):
        def activity(self, kind, text, target=''):
            events.append((kind, text))
    r2 = FakeNotes(); r2.put('n2', 'B')
    s2 = ne.NotesSyncer(ne.NotesState(tmp_path / 'other.db'), r2, second, Capture())
    result = s2.run(full=True); s2.run(full=True)
    assert result.errors == 0 and result.exported == 1
    assert (second / 'Notes/B.html').exists()
    assert (second / 'Notes.html').read_bytes() == contents
    assert sum(k == 'info' for k, _ in events) == 1
    assert not s2.state.get('index_owned') and not s2.state.get('index_digest')


def test_registry_prefixes_are_literal_not_sql_wildcards(notes_env, drive_env):
    ns, _, _, _ = notes_env; ds, _, _, _ = drive_env
    ns.state.set('tmp:a_b%file', 'stage'); ns.state.set('other:a_b%file', 'stage')
    ds.state.set_meta('tmp:a_b%file', '1'); ds.state.set_meta('other:a_b%file', '1')
    assert dict(ns.state.items('tmp:')) == {'tmp:a_b%file':'stage'}
    assert ds.state.meta_items('tmp:') == {'tmp:a_b%file':'1'}


def test_existing_nested_album_directories_are_never_claimed(photos_env):
    s, r, root, recycled = photos_env
    s.settings.albums_as_folders = True
    parent = root / 'Albums/Personal'; parent.mkdir(parents=True)
    r.add('A1', 'photo.jpg', b'CLOUD'); r.album_map = {'Personal/Generated':['A1']}
    s.run(force_full=True)
    keys = dict(s.state.items('albumdir:'))
    assert 'albumdir:Albums/Personal/Generated' in keys
    assert 'albumdir:Albums/Personal' not in keys and 'albumdir:Albums' not in keys
    s.settings.albums_as_folders = False; s.run(force_full=True)
    assert parent.is_dir() and not (parent / 'Generated').exists()


def test_new_note_stage_is_recorded_before_export(notes_env, monkeypatch):
    s, r, root, recycled = notes_env
    export = r.export; seen = []
    def observing(nid, out_dir, filename, assets):
        key = 'tmp:' + (out_dir / filename).relative_to(root).as_posix()
        seen.append((s.state.get(key), (out_dir / filename).exists()))
        return export(nid, out_dir, filename, assets)
    monkeypatch.setattr(r, 'export', observing)
    r.put('n1', 'A'); s.run()
    assert seen == [('stage', False)]


if __name__ == '__main__':
    child()
