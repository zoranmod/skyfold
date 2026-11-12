"""Independent preservation probes; failed assertions mean observed ownership violations.

Only disposable paths, repository fakes, and normal sequential sync calls.
utime represents files left overnight, not a race or injected sync failure.
The fixtures replace recycling with a recorded unlink; native checks are separate.
"""
import os
import shutil
import time
from pathlib import Path

import pytest

from skyfold import notes_engine as ne, engine as pe, drive_engine as de
from tests.test_notes import env as notes_env, FakeNotes
from tests.test_drive import env as drive_env, write
from tests.test_engine import env as photos_env


@pytest.mark.parametrize('name', ['.skyfold-tmp-note-personal.html', '.skyfold-index-personal.tmp'])
def test_notes_old_untracked_private_name_is_preserved(notes_env, name):
    s, r, root, trashed = notes_env
    p = write(root, name, b'ONLY LOCAL PERSONAL CONTENT', age=7200)
    s.run(full=True)
    assert p.exists() and p.read_bytes() == b'ONLY LOCAL PERSONAL CONTENT', {'name':name, 'recycled':trashed}


@pytest.mark.parametrize('name', ['.skyfold-tmp-note-old-personal.html', '.skyfold-tmp-note-index-personal.html'])
def test_notes_old_untracked_aside_name_is_preserved(notes_env, name):
    s, r, root, trashed = notes_env
    s.run(full=True)
    p = write(root, name, b'ONLY LOCAL PERSONAL CONTENT', age=7200)
    s.run(full=True)
    assert p.exists(), {'name':name, 'recycled':trashed}


@pytest.mark.parametrize('name', ['.skyfold-tmp-personal.txt', '.skyfold-tmp-backup.zip'])
def test_drive_old_untracked_private_name_is_preserved(drive_env, name):
    s, r, root, trashed = drive_env
    p = write(root, name, b'ONLY LOCAL PERSONAL CONTENT', age=90000)
    s.run()
    assert p.exists() and p.read_bytes() == b'ONLY LOCAL PERSONAL CONTENT', {'name':name, 'recycled':trashed}


@pytest.mark.parametrize('name', ['.skyfold-tmp-old-personal.txt', '.skyfold-tmp-old-1234abcd-personal.txt'])
def test_drive_unrecorded_aside_is_not_renamed_or_uploaded(drive_env, name):
    s, r, root, trashed = drive_env
    p = write(root, name, b'LOCAL BACKUP NOT FOR CLOUD', age=120)
    s.run()
    s.run()
    assert p.exists() and not r.uploads, {'remaining':[q.name for q in root.iterdir()], 'uploads':r.uploads, 'recycled':trashed}


def test_drive_unrecorded_aside_metadata_is_not_ownership(drive_env):
    s, r, root, trashed = drive_env
    r.put('plan.txt', b'CLOUD'); s.run()
    base = s.state.all()['plan.txt']
    p = write(root, '.skyfold-tmp-old-1234abcd-plan.txt', b'LOCAL')
    os.utime(p, (base.lmtime, base.lmtime))  # a copied backup can retain a file's timestamp
    s.run()
    assert p.exists() and p.read_bytes() == b'LOCAL', {'recycled':trashed}


def test_notes_recent_untracked_private_name_is_preserved(notes_env):
    s, r, root, trashed = notes_env
    p = write(root, '.skyfold-tmp-note-personal.html', b'PERSONAL', age=60)
    s.run()
    assert p.read_bytes() == b'PERSONAL' and not trashed


def test_drive_recent_untracked_private_name_is_preserved(drive_env):
    s, r, root, trashed = drive_env
    p = write(root, '.skyfold-tmp-personal.txt', b'PERSONAL', age=60)
    s.run()
    assert p.read_bytes() == b'PERSONAL' and not trashed and not r.uploads


def test_notes_copied_index_in_fresh_root_is_preserved(notes_env, tmp_path):
    s, r, root, trashed = notes_env
    r.put('original', 'Original'); s.run()
    copied = (root / 'Notes.html').read_bytes() + b'\n<p>PERSONAL ANNOTATION, NOT SYNCED</p>'
    root2 = tmp_path / 'second-export'; root2.mkdir()
    p = root2 / 'Notes.html'; p.write_bytes(copied)
    r2 = FakeNotes(); r2.put('different', 'Different note')
    s2 = ne.NotesSyncer(ne.NotesState(tmp_path / 'second.db'), r2, root2)
    s2.run()
    assert p.read_bytes() == copied, {'recycled':trashed}


def test_notes_plain_personal_index_is_preserved(notes_env):
    s, r, root, trashed = notes_env
    p = write(root, 'Notes.html', b'<html>PERSONAL INDEX</html>')
    with pytest.raises(Exception, match="isn't from Skyfold"):
        s.run()
    assert p.read_bytes() == b'<html>PERSONAL INDEX</html>' and not trashed


def test_photos_user_empty_album_folder_is_preserved(photos_env):
    s, r, root, trashed = photos_env
    p = root / pe.ALBUMS_DIR / 'My planned trip' / 'Day 1'
    p.mkdir(parents=True)
    s.run(force_full=True)
    assert p.is_dir(), {'recycled':trashed}


def test_photos_user_replacement_album_file_is_preserved(photos_env):
    s, r, root, trashed = photos_env
    s.settings.albums_as_folders = True
    r.add('A1', 'photo.jpg', b'CLOUD ORIGINAL'); r.album_map = {'Holiday':['A1']}
    s.run(force_full=True)
    p = root / pe.ALBUMS_DIR / 'Holiday/photo.jpg'
    p.unlink()  # user removes the hard link, then saves an independent picture here
    p.write_bytes(b'MY UNRELATED PERSONAL PICTURE')
    s.settings.albums_as_folders = False  # ordinary Settings action
    s.run(force_full=True)
    assert p.exists() and p.read_bytes() == b'MY UNRELATED PERSONAL PICTURE', {'recycled':trashed}


def test_photos_untracked_album_file_is_preserved(photos_env):
    s, r, root, trashed = photos_env
    s.settings.albums_as_folders = True
    r.add('A1', 'photo.jpg', b'CLOUD ORIGINAL'); r.album_map = {'Holiday':['A1']}
    p = write(root, f'{pe.ALBUMS_DIR}/Holiday/photo.jpg', b'PERSONAL PICTURE')
    s.run(force_full=True)
    assert p.read_bytes() == b'PERSONAL PICTURE' and not trashed


def test_photos_untracked_temp_prefix_is_preserved(photos_env):
    s, r, root, trashed = photos_env
    p = write(root, '.skyfold-tmp-personal.jpg', b'PERSONAL PICTURE', age=90000)
    s.run(force_full=True)
    assert p.read_bytes() == b'PERSONAL PICTURE' and not trashed


def test_photos_changed_live_part_is_preserved(photos_env):
    s, r, root, trashed = photos_env
    r.add('A1', 'photo.jpg', b'PHOTO', live=b'VIDEO'); s.run()
    p = root / '2025/11/photo.MOV'; p.write_bytes(b'LOCAL')  # same size, different hash
    (root / '2025/11/photo.jpg').unlink()
    s.run(force_full=True)
    assert p.read_bytes() == b'LOCAL' and p.name not in trashed


def test_notes_assets_user_content_is_not_swept(notes_env):
    s, r, root, trashed = notes_env
    p = write(root, '_assets/own/personal.txt', b'PERSONAL', age=90000)
    q = write(root, '_assets/own/.skyfold-tmp-note-personal.html', b'PERSONAL2', age=90000)
    r.put('n1', 'Budget'); s.run(); r.delete('n1'); s.run()
    assert p.read_bytes() == b'PERSONAL' and q.read_bytes() == b'PERSONAL2'


def test_notes_recorded_obsolete_different_content_is_preserved(notes_env):
    s, r, root, trashed = notes_env
    r.put('n1', 'Budget'); s.run()
    p = root / 'Notes/old-name.html'; shutil.copy2(root / 'Notes/Budget.html', p)
    s.state.set('obsolete:Notes/old-name.html', ne._digest(p))
    p.write_bytes(b'USER REPLACEMENT')
    s.run(full=True)
    assert p.read_bytes() == b'USER REPLACEMENT' and not s.state.get('obsolete:Notes/old-name.html')


def test_notes_recorded_obsolete_exact_content_is_recycled(notes_env):
    s, r, root, trashed = notes_env
    r.put('n1', 'Budget'); s.run()
    p = root / 'Notes/old-name.html'; shutil.copy2(root / 'Notes/Budget.html', p)
    s.state.set('obsolete:Notes/old-name.html', ne._digest(p))
    s.run(full=True)
    assert not p.exists() and 'old-name.html' in trashed


def test_drive_previous_version_collision_preserves_existing_file(drive_env):
    s, r, root, trashed = drive_env
    r.put('plan.txt', b'ORIGINAL'); s.run()
    p = write(root, 'plan (previous version).txt', b'MY BACKUP')
    r.put('plan.txt', b'NEW CLOUD VERSION'); s.run()
    assert p.read_bytes() == b'MY BACKUP'


def test_drive_conflict_name_collision_preserves_existing_file(drive_env):
    s, r, root, trashed = drive_env
    r.put('plan.txt', b'ORIGINAL'); s.run()
    write(root, 'plan.txt', b'LOCAL EDIT')
    candidate = s._conflict_name('plan.txt')
    p = write(root, candidate, b'MY OTHER FILE')
    r.put('plan.txt', b'CLOUD EDIT'); s.run()
    assert p.read_bytes() == b'MY OTHER FILE'


def test_drive_marker_other_account_refuses_root(drive_env):
    s, r, root, trashed = drive_env
    marker = write(root, de.MARKER, b'account: another-account\n')
    p = write(root, 'personal.txt', b'PERSONAL')
    s.account = 'current-account'
    with pytest.raises(Exception, match='another Apple ID'):
        s.run()
    assert marker.read_bytes() == b'account: another-account\n' and p.read_bytes() == b'PERSONAL'
    assert not r.uploads and not trashed


def test_notes_dependency_downloads_use_new_asset_names(tmp_path):
    from types import SimpleNamespace
    from pyicloud.services.notes.client import CloudKitNotesClient
    assets = tmp_path / '_assets' / 'note-id'
    client = SimpleNamespace(download_asset_stream=lambda url: iter([b'SYNTHETIC ASSET']))
    first = Path(CloudKitNotesClient.download_asset_to(client, 'https://example.invalid/asset', str(assets)))
    first.write_bytes(b'PERSONAL MODIFICATION')
    second = Path(CloudKitNotesClient.download_asset_to(client, 'https://example.invalid/asset', str(assets)))
    assert second != first and first.read_bytes() == b'PERSONAL MODIFICATION'
    assert second.read_bytes() == b'SYNTHETIC ASSET'


def test_notes_previous_version_name_is_not_overwritten(notes_env):
    s, r, root, trashed = notes_env
    r.put('n1', 'Budget'); s.run()
    previous = write(root, 'Notes/Budget (previous version).html', b'USER BACKUP')
    p = root / 'Notes/Budget.html'
    p.write_bytes(p.read_bytes() + b'LOCAL EDIT')
    r.put('n1', 'Budget', body='CLOUD CHANGE'); s.run()
    assert previous.read_bytes() == b'USER BACKUP'


def test_config_write_probe_does_not_delete_preexisting_file(tmp_path, monkeypatch):
    from skyfold import config
    monkeypatch.setattr(config, 'APP_DIR', tmp_path)
    p = write(tmp_path, 'data/.write-test', b'ONLY USER CONTENT')
    config._data_dir()
    assert p.exists() and p.read_bytes() == b'ONLY USER CONTENT'

