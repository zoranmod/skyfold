"""Reproduction probes from the twenty-fourth independent (Codex) review, kept as regression tests."""
import shutil
from pathlib import Path
from types import SimpleNamespace
import pytest

from tests.test_notes import env as notes_env
from tests.test_drive import env as drive_env, write
from tests.test_engine import env as photos_env

@pytest.mark.parametrize('suffix',[' (1)',' (2)',' (2026)'])
def test_user_numbered_copy_is_not_a_recorded_cleanup_target(notes_env,suffix):
    s,r,root,trashed=notes_env
    r.put('n1','Budget',body='cloud original');s.run()
    original=root/'Notes/Budget.html'
    backup=original.with_stem('Budget'+suffix)
    shutil.copy2(original,backup)
    s.run(full=True)
    assert backup.exists(),{'wrongly_recycled':trashed,'source_intact':original.exists()}

@pytest.mark.parametrize('name,modified',[
 ('Budget - Copy',False),('Budget (2)',True),('Budget - annotations',True),
])
def test_user_note_copy_controls(notes_env,name,modified):
    s,r,root,trashed=notes_env
    r.put('n1','Budget',body='cloud original');s.run()
    backup=root/f'Notes/{name}.html'
    shutil.copy2(root/'Notes/Budget.html',backup)
    if modified:backup.write_text(backup.read_text(encoding='utf8')+'<p>Personal notes</p>',encoding='utf8')
    before=backup.read_bytes();s.run(full=True)
    assert backup.read_bytes()==before

def test_numbered_user_copy_is_kept_when_original_note_changes(notes_env):
    s,r,root,trashed=notes_env
    r.put('n1','Budget',body='original');s.run()
    backup=root/'Notes/Budget (2).html';shutil.copy2(root/'Notes/Budget.html',backup)
    before=backup.read_bytes();r.put('n1','Budget',body='updated');s.run(full=True)
    assert backup.read_bytes()==before

@pytest.mark.parametrize('copy_title,new_title',[
 ('Budget (2)','Budget'),('Budget - my annotated copy','Budget - my annotated copy'),
])
def test_new_cloud_note_does_not_claim_untracked_annotated_export(notes_env,copy_title,new_title):
    s,r,root,trashed=notes_env
    r.put('n1','Budget',body='original cloud note');s.run()
    backup=root/f'Notes/{copy_title}.html'
    shutil.copy2(root/'Notes/Budget.html',backup)
    backup.write_text(backup.read_text(encoding='utf8')+'<p>my unique annotations</p>',encoding='utf8')
    before=backup.read_bytes()
    r.put('n2',new_title,body='a separate new cloud note');s.run()
    assert backup.read_bytes()==before,{'wrongly_recycled':trashed,'now_contains':backup.read_text(encoding='utf8')[-150:]}

@pytest.mark.parametrize('remote_change',['rename','move','delete','update'])
def test_drive_ordinary_cloud_changes_preserve_local_edit(drive_env,remote_change):
    s,d,root,trashed=drive_env
    d.put('Docs/a.txt',b'original');s.run()
    write(root,'Docs/a.txt',b'local private change')
    if remote_change=='rename':d._relocate('Docs/a.txt','Docs/b.txt')
    elif remote_change=='move':d.put('Other',None);d._relocate('Docs/a.txt','Other/a.txt')
    elif remote_change=='delete':d.items.pop('Docs/a.txt')
    else:d.put('Docs/a.txt',b'remote independent change')
    for _ in range(3):s.run()
    contents=[p.read_bytes() for p in root.rglob('*') if p.is_file() and not p.name.startswith('.')]
    assert b'local private change' in contents

@pytest.mark.parametrize('side',['pc','cloud'])
def test_drive_case_only_rename_converges(drive_env,side):
    s,d,root,trashed=drive_env;d.put('Report.txt',b'report');s.run()
    if side=='pc':(root/'Report.txt').rename(root/'REPORT.txt')
    else:d._relocate('Report.txt','REPORT.txt')
    for _ in range(3):s.run()
    assert (root/'REPORT.txt').read_bytes()==b'report'
    assert len([p for p in d.items if p.lower()=='report.txt'])==1

def test_photos_local_annotated_album_copy_preserved(photos_env):
    s,r,root,trashed=photos_env
    r.add('A','IMG.JPG',b'original');r.album_map={'Trip':['A']};s.run()
    copy=next((root/'Albums').rglob('*.JPG'))
    # A normal editor that replaces its output breaks the hard link.
    temp=copy.with_suffix('.edited');temp.write_bytes(b'personal edit');temp.replace(copy)
    r.album_map={};s.run(force_full=True)
    assert any(p.is_file() and p.read_bytes()==b'personal edit' for p in root.rglob('*')) or copy.name in trashed

@pytest.mark.parametrize('field',['errorCode','status'])
@pytest.mark.parametrize('value',['unknown-opaque-secret','ERROR-opaque-secret','token: a7Q9opaque'])
def test_drive_server_rejection_allowlist(field,value):
    from skyfold.drive_remote import _check_item
    from skyfold.safelog import brief
    try:_check_item({'status':'ERROR',field:value},'rename')
    except RuntimeError as exc:assert value.lower() not in brief(exc).lower()

