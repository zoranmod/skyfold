"""Reproduction probes from the tenth independent (Codex) review, kept as regression tests."""
import json
import os
import sys
from pathlib import Path
import pytest
import requests
from tests.test_drive import env
from skyfold import drive_engine as de

def strict_ids(d, monkeypatch):
    """Resolve operations by stable IDs and reject stale etags, like the real adapter."""
    rename0,move0,trash0=d.rename,d.move,d.trash
    def current(item):
        return next((p for p,it in d.items.items() if it['id']==item.docwsid),None)
    def rename(item,name):
        p=current(item)
        assert p is not None
        if d.items[p]['etag']!=item.etag:raise RuntimeError('stale etag')
        rename0(d._item(p),name)
        return d.items[current(item)]['etag']
    def move(item,parent):
        p=current(item)
        assert p is not None
        if d.items[p]['etag']!=item.etag:raise RuntimeError('stale etag')
        move0(d._item(p),parent)
        return d.items[current(item)]['etag']
    def trash(item):
        p=current(item)
        if p is not None:
            if d.items[p]['etag']!=item.etag:raise RuntimeError('stale etag')
            trash0(d._item(p))
    monkeypatch.setattr(d,'rename',rename)
    monkeypatch.setattr(d,'move',move)
    monkeypatch.setattr(d,'trash',trash)


def fresh(s,d,root):
    cache=s.cache_file
    s.state._db.close()
    return de.DriveSyncer(de.DriveState(root.parent/'d.db'),d,root,delete_threshold=3,cache_file=cache)


@pytest.mark.parametrize('edited',[b'EDIT',b'longer cloud edit'])
def test_cloud_edit_after_lost_rename_is_downloaded(env,monkeypatch,edited):
    s,d,root,_=env
    d.put('Docs/a.txt',b'base');s.run()
    strict_ids(d,monkeypatch)
    os.rename(root/'Docs/a.txt',root/'Docs/b.txt')
    rename0=d.rename;once=[True]
    def rename(item,name):
        got=rename0(item,name)
        if once:
            once.pop();raise requests.exceptions.Timeout('response lost after committed rename')
        return got
    monkeypatch.setattr(d,'rename',rename)
    s.run()
    assert s.state.get_meta(de.PENDING_MOVE)
    d.put('Docs/b.txt',edited)
    item0=d._item
    def item(p):
        it=item0(p)
        if p=='Docs/b.txt':it.mtime+=60
        return it
    monkeypatch.setattr(d,'_item',item)
    for _ in range(4):s.run()
    assert (root/'Docs/b.txt').read_bytes()==edited


def test_second_move_must_not_erase_first_unresolved_journal(env,monkeypatch):
    s,d,root,_=env
    d.put('G/a.txt',b'first moved content')
    d.put('F/keep.txt',b'keep')
    d.put('H/x.txt',b'second')
    s.run();strict_ids(d,monkeypatch)
    os.rename(root/'G/a.txt',root/'F/d.txt')
    os.rename(root/'H/x.txt',root/'H/y.txt')
    move0=d.move;once=[True]
    def move(item,parent):
        got=move0(item,parent)
        if once:
            once.pop();raise requests.exceptions.Timeout('first move committed, answer lost')
        return got
    monkeypatch.setattr(d,'move',move)
    s.run()
    observed_journal=s.state.get_meta(de.PENDING_MOVE)
    for _ in range(4):s.run()
    copies=[p for p,it in d.items.items() if it['data']==b'first moved content']
    assert copies==['F/d.txt'],(observed_journal,copies)


def test_crash_during_rebase_keeps_baseline_recoverable(env,monkeypatch):
    s,d,root,_=env
    s.cache_file=root.parent/'cache.json'
    d.put('G/a.txt',b'moved content');d.put('F/keep.txt',b'keep')
    s.run();strict_ids(d,monkeypatch)
    os.rename(root/'G/a.txt',root/'F/d.txt')
    rename0=d.rename
    monkeypatch.setattr(d,'rename',lambda *a: (_ for _ in ()).throw(RuntimeError('rename refused')))
    put0=s.state.put
    def put(row):
        if row.path=='F/a.txt':raise SystemExit('crash after drop, before replacement row')
        return put0(row)
    monkeypatch.setattr(s.state,'put',put)
    with pytest.raises(SystemExit):s.run()
    assert s.state.get_meta(de.PENDING_MOVE)
    monkeypatch.setattr(d,'rename',rename0)
    s=fresh(s,d,root)
    for _ in range(4):s.run()
    copies=[p for p,it in d.items.items() if it['data']==b'moved content']
    assert copies==['F/d.txt'],copies


@pytest.mark.parametrize('file_count',[2,40])
def test_pending_folder_with_partial_listing_does_not_duplicate_tree(env,monkeypatch,file_count):
    s,d,root,_=env
    d.put('G/A/one.txt',b'one');d.put('G/A/two.txt',b'two')
    for n in range(file_count-2):d.put(f'G/A/extra-{n}.txt',f'unique-file-{n}'.encode())
    d.put('F/keep.txt',b'keep')
    s.run();strict_ids(d,monkeypatch)
    os.rename(root/'G/A',root/'F/D')
    move0=d.move;once=[True]
    def move(item,parent):
        got=move0(item,parent)
        if once:
            once.pop();raise requests.exceptions.Timeout('folder move committed, answer lost')
        return got
    monkeypatch.setattr(d,'move',move)
    s.run()
    walk0=d.walk
    def partial(*a,**kw):
        out=walk0(*a,**kw)
        d.incomplete={'F/A'}
        return {p:r for p,r in out.items() if not p.startswith('F/A/')}
    monkeypatch.setattr(d,'walk',partial)
    s.run()
    monkeypatch.setattr(d,'walk',walk0)
    for _ in range(4):s.run()
    copies=[p for p,it in d.items.items() if it['data']==b'one']
    subtree_files=[p for p,it in d.items.items() if not it['dir'] and p.startswith(('F/A/','F/D/'))]
    assert copies==['F/D/one.txt'],(copies,len(subtree_files),file_count)


def test_case_only_pending_rename_settles(env,monkeypatch):
    s,d,root,_=env
    d.put('Docs/Readme.txt',b'content');s.run();strict_ids(d,monkeypatch)
    os.rename(root/'Docs/Readme.txt',root/'Docs/README.txt')
    rename0=d.rename;once=[True]
    def rename(item,name):
        got=rename0(item,name)
        if once:
            once.pop();raise requests.exceptions.Timeout('answer lost')
        return got
    monkeypatch.setattr(d,'rename',rename)
    for _ in range(4):s.run()
    assert [p for p,it in d.items.items() if it['data']==b'content']==['Docs/README.txt']
    assert (root/'Docs/README.txt').read_bytes()==b'content'
    assert s.state.get_meta(de.PENDING_MOVE) is None


def test_unlisted_pending_item_waits_then_settles(env,monkeypatch):
    s,d,root,_=env
    d.put('Docs/a.txt',b'content');s.run();strict_ids(d,monkeypatch)
    os.rename(root/'Docs/a.txt',root/'Docs/b.txt')
    rename0=d.rename;once=[True]
    def rename(item,name):
        got=rename0(item,name)
        if once:
            once.pop();raise requests.exceptions.Timeout('answer lost')
        return got
    monkeypatch.setattr(d,'rename',rename)
    s.run()
    walk0=d.walk
    def partial(*a,**kw):
        out=walk0(*a,**kw);d.incomplete={'Docs'}
        return {p:r for p,r in out.items() if not p.startswith('Docs/')}
    monkeypatch.setattr(d,'walk',partial)
    for _ in range(3):
        s.run(changed={'Docs'})
        assert s.state.get_meta(de.PENDING_MOVE)
        assert not d.uploads and not d.trashed
    monkeypatch.setattr(d,'walk',walk0)
    for _ in range(3):s.run()
    assert s.state.get_meta(de.PENDING_MOVE) is None
    assert [p for p,it in d.items.items() if it['data']==b'content']==['Docs/b.txt']


def test_deleted_pending_item_is_not_resurrected(env,monkeypatch):
    s,d,root,_=env
    d.put('Docs/a.txt',b'content');s.run();strict_ids(d,monkeypatch)
    os.rename(root/'Docs/a.txt',root/'Docs/b.txt')
    rename0=d.rename;once=[True]
    def rename(item,name):
        got=rename0(item,name)
        if once:
            once.pop();raise requests.exceptions.Timeout('rename committed, answer lost')
        return got
    monkeypatch.setattr(d,'rename',rename)
    s.run()
    d.trash(d._item('Docs/b.txt'))
    for _ in range(4):s.run()
    assert 'Docs/b.txt' not in d.items,d.items


@pytest.mark.parametrize('rename_first',[False,True])
@pytest.mark.parametrize('crash_at',[
    'journal-before','journal-after','op1-before','op1-after','op2-before','op2-after',
    'clear-before','clear-after','drop-before','drop-after','put-before','put-after',
])
def test_successful_move_crash_boundaries_recover(env,monkeypatch,rename_first,crash_at):
    s,d,root,_=env
    s.cache_file=root.parent/'cache.json'
    d.put('G/a.txt',b'moved content')
    d.put('F/a.txt' if rename_first else 'F/keep.txt',b'unrelated')
    s.run();strict_ids(d,monkeypatch)
    os.rename(root/'G/a.txt',root/'F/d.txt')
    hit=[]
    def boundary(label):
        if label==crash_at and not hit:
            hit.append(label);raise SystemExit(label)
    move0,rename0=d.move,d.rename
    def operation(fn,label):
        def call(*a):
            boundary(label+'-before');result=fn(*a);boundary(label+'-after');return result
        return call
    monkeypatch.setattr(d,'move',operation(move0,'op2' if rename_first else 'op1'))
    monkeypatch.setattr(d,'rename',operation(rename0,'op1' if rename_first else 'op2'))
    meta0,drop0,put0=s.state.set_meta,s.state.drop,s.state.put
    def meta(key,value):
        label=('journal' if value else 'clear') if key==de.PENDING_MOVE else None
        if label:boundary(label+'-before')
        meta0(key,value)
        if label:boundary(label+'-after')
    def drop(path):
        if path=='G/a.txt':boundary('drop-before')
        drop0(path)
        if path=='G/a.txt':boundary('drop-after')
    def put(row):
        if row.path=='F/d.txt':boundary('put-before')
        put0(row)
        if row.path=='F/d.txt':boundary('put-after')
    monkeypatch.setattr(s.state,'set_meta',meta)
    monkeypatch.setattr(s.state,'drop',drop)
    monkeypatch.setattr(s.state,'put',put)
    with pytest.raises(SystemExit):s.run()
    assert hit==[crash_at]
    monkeypatch.setattr(d,'move',move0);monkeypatch.setattr(d,'rename',rename0)
    s=fresh(s,d,root)
    for _ in range(4):s.run()
    assert [p for p,it in d.items.items() if it['data']==b'moved content']==['F/d.txt']
    assert (root/'F/d.txt').read_bytes()==b'moved content'
    assert s.state.get_meta(de.PENDING_MOVE) is None

