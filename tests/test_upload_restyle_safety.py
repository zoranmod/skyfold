"""Offline regression cases for Photos uploads and upgrading an existing Notes page."""
import os
import io
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from skyfold import notes_engine as ne
from skyfold import remote as photos
from tests.test_engine import _old, env as photos_env
from tests.test_notes import env as notes_env


def put(root, name, data=b"photo"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    _old(path)
    return path


def test_live_sidecar_and_album_subtree_are_private(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    for name in ("žuto.HEIC", "ŽUTO.MOV", "Albums/Trip/private.jpg", "solo.mov"):
        put(root, name, name.encode())
    s.run()
    assert sorted(remote.uploads) == ["solo.mov", "žuto.HEIC"]


@pytest.mark.skipif(os.name != "nt", reason="Windows file attributes")
@pytest.mark.parametrize("attribute", [2, 4, 0x1000])
def test_skip_hidden_system_and_offline_files(photos_env, attribute):
    import win32api

    s, remote, root, _ = photos_env
    s.run()
    path = put(root, "private.jpg")
    win32api.SetFileAttributes(str(path), attribute)
    try:
        s.upload_new()
        assert not getattr(remote, "uploads", [])
    finally:
        win32api.SetFileAttributes(str(path), 128)


def test_unresolved_move_never_becomes_upload(photos_env, monkeypatch):
    s, remote, root, _ = photos_env
    remote.add("A", "pic.jpg", b"same")
    s.run()
    s.settings.photos_upload = True
    out = root / "album/pic.jpg"
    out.parent.mkdir()
    (root / s.state.files()[0].rel_path).rename(out)
    monkeypatch.setattr(remote, "lookup", lambda ids: {})
    s.run()
    assert not getattr(remote, "uploads", [])


def test_kept_local_file_does_not_resurrect_cloud_deletion(photos_env):
    s, remote, _, _ = photos_env
    s.settings.photos_upload = True
    s.settings.delete_cloud_to_pc = False
    remote.add("A", "keep.jpg", b"keep")
    s.run()
    remote.remove("A")
    s.run()
    s.run()
    assert not remote.assets


def test_temporarily_unreadable_kept_photo_allows_later_replacement(photos_env, monkeypatch):
    from skyfold import engine

    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.settings.delete_cloud_to_pc = False
    remote.add("A", "stay.jpg", b"old")
    s.run()
    path = root / s.state.files()[0].rel_path
    remote.remove("A")
    def busy(p):
        raise PermissionError("busy")
    with monkeypatch.context() as m:
        m.setattr(engine, "_file_hash", busy)
        s.run()
    s.run()
    assert not remote.assets
    path.write_bytes(b"new version")
    _old(path)
    s.run()
    assert b"new version" in remote.blobs.values()


def test_unlisted_registration_does_not_recycle_source(photos_env, monkeypatch):
    s, remote, root, trashed = photos_env
    s.settings.photos_upload = True
    s.run()
    path = put(root, "source.jpg")
    with monkeypatch.context() as m:
        m.setattr(remote, "list_assets", lambda cb: iter(()))
        s.run(force_full=True)
        assert path.exists() and not trashed
    s.run(force_full=True)
    assert [row.rel_path for row in s.state.files()] == [path.name]
    assert remote.uploads == [path.name]


def test_change_feed_can_precede_visibility_in_all_photos(photos_env, monkeypatch):
    s, remote, root, _ = photos_env
    s.run()
    s.settings.photos_upload = True
    path = put(root, "new.jpg")
    s.run()  # the change feed already has the new asset
    with monkeypatch.context() as m:
        m.setattr(remote, "list_assets", lambda cb: iter(()))
        s.run(force_full=True)
    assert path.exists()
    s.run(force_full=True)  # the full listing catches up
    remote.remove(next(iter(remote.assets)))
    s.run(force_full=True)
    assert not path.exists()


def test_ambiguous_post_is_not_replayed_and_does_not_starve_next_file(photos_env, monkeypatch):
    s, remote, root, _ = photos_env
    s.run()
    s.settings.photos_upload = True
    monkeypatch.setattr(s, "UPLOAD_MAX", 1)
    put(root, "a.jpg")
    put(root, "b.jpg", b"other")
    upload = remote.upload
    calls = []

    def reply(path):
        calls.append(path.name)
        receipt = upload(path)
        if path.name == "a.jpg":
            raise TimeoutError("lost registration reply")
        return receipt

    monkeypatch.setattr(remote, "upload", reply)
    s.run()
    s.run()
    assert calls == ["a.jpg", "b.jpg"]
    assert len(list(root.rglob("a.jpg"))) == 1


def test_duplicate_receipt_is_not_a_new_asset(photos_env, monkeypatch):
    s, _, root, _ = photos_env
    s.run()
    path = put(root, "copy.jpg")
    receipt = SimpleNamespace(cplAsset="existing", is_duplicate=True)
    client = SimpleNamespace(upload_file=lambda *a, **kw: receipt)
    lib = SimpleNamespace(_client=client, zone_id={"zoneName": "PrimarySync"},
                          upload_file=lambda path: None)
    remote = photos.ICloudRemote(SimpleNamespace(photos=SimpleNamespace(libraries={"root": lib})))
    result = remote.upload(path)
    assert result is not None and result.is_duplicate and result.asset_id == "existing"


def test_typed_registration_auth_rejection_stops_for_sign_in(photos_env):
    from pyicloud.common.cloudkit.client import CloudKitApiError

    s, _, root, _ = photos_env
    s.run()
    path = put(root, "signin.jpg")
    def denied(*a, **kwargs):
        raise CloudKitApiError("Photos putAsset rejected signin.jpg with status 421",
                               payload={"response": {"status": 421}})
    lib = SimpleNamespace(_client=SimpleNamespace(upload_file=denied), zone_id={"zoneName": "PrimarySync"},
                          upload_file=denied)
    remote = photos.ICloudRemote(SimpleNamespace(photos=SimpleNamespace(libraries={"root": lib})))
    with pytest.raises(photos.AuthRequired):
        remote.upload(path)


def test_same_timestamp_replacement_after_duplicate_is_uploaded(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    remote.add("A", "old.jpg", b"aaaa")
    s.run()
    path = put(root, "copy.jpg", b"aaaa")
    before = path.stat()
    s.run()
    replacement = put(root, "temp.bin", b"bbbb")
    os.replace(replacement, path)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    s.run()
    assert b"bbbb" in remote.blobs.values()


def test_deleting_unindexed_duplicate_does_not_delete_preexisting_cloud_asset(photos_env):
    s, remote, root, _ = photos_env
    s.run()
    remote.add("on-phone", "phone.jpg", b"same")
    path = put(root, "my-copy.jpg", b"same")
    s.settings.photos_upload = True
    s.run()
    path.unlink()
    s.run(force_full=True)
    assert "on-phone" in remote.assets and not remote.deleted
    assert (root / "2025/11/phone.jpg").read_bytes() == b"same"


def test_symbolic_link_never_uploads_target(photos_env):
    s, remote, root, _ = photos_env
    s.run()
    outside = put(root.parent, "outside.jpg")
    try:
        (root / "linked.jpg").symlink_to(outside)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows did not grant file symlink creation")
        raise
    s.upload_new()
    assert not getattr(remote, "uploads", [])


@pytest.mark.parametrize("attribute", [0x400, 0x40000, 0x400000])
def test_reparse_and_provider_recall_attributes_prevent_upload(photos_env, monkeypatch, attribute):
    s, remote, root, _ = photos_env
    s.run()
    file = put(root, "cloud.jpg")
    real_lstat = Path.lstat
    def metadata(path):
        raw = real_lstat(path)
        if path == file:
            return SimpleNamespace(st_file_attributes=attribute, st_mode=raw.st_mode)
        return raw
    monkeypatch.setattr(Path, "lstat", metadata)
    s.upload_new()
    assert not getattr(remote, "uploads", [])


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing modes")
def test_open_writer_with_old_timestamp_is_deferred(photos_env):
    s, remote, root, _ = photos_env
    s.run()
    path = put(root, "copy.jpg")
    with path.open("ab"):
        s.upload_new()
    assert not getattr(remote, "uploads", [])
    s.upload_new()
    assert remote.uploads == ["copy.jpg"]


def old_note(env):
    s, remote, root, _ = env
    remote.put("A", "Sample")
    s.run()
    p = root / s.state.all()["A"][1]
    prefix = b'<html><head><style id="skyfold">old</style></head><body>'
    p.write_bytes(prefix + b'body\xff</body></html>')
    s.state.set("page:A", ne._digest(p))
    s.state.set("note_style", "1")
    return s, p


def test_restyle_preserves_arbitrary_body_bytes(notes_env):
    s, p = old_note(notes_env)
    assert s._restyle_pages() == 1
    assert b"body\xff" in p.read_bytes() and ne._is_our_page(p)


def test_restyle_skips_body_style_and_handles_comments():
    decoy = '<style id="skyfold">example</style>'
    assert ne.restyle_note_html("<body>" + decoy + "</body>") is None
    head = "<head><!--" + decoy + '--><style id="skyfold">old</style></head>'
    result = ne.restyle_note_html(head)
    assert "<!--" + decoy + "-->" in result and ">old</style>" not in result


def test_restyle_never_overwrites_edit_during_publication(notes_env, monkeypatch):
    s, page = old_note(notes_env)
    def intercept(name):
        original = getattr(os, name)
        def update(src, dst, *args, **kwargs):
            if Path(src) == page or Path(dst) == page:
                if not edited:
                    page.write_bytes(b"saved by editor")
                    edited.append(True)
            return original(src, dst, *args, **kwargs)
        monkeypatch.setattr(os, name, update)
    edited = []
    intercept("rename")
    intercept("replace")
    s._restyle_pages()
    assert edited and page.read_bytes() == b"saved by editor"
    assert s.state.get("page:A") != ne._digest(page)


def test_restyle_retries_transient_io_error(notes_env, monkeypatch):
    s, page = old_note(notes_env)
    with monkeypatch.context() as m:
        def busy(*args, **kwargs):
            raise PermissionError("busy")
        m.setattr(ne, "_digest", busy)
        s._restyle_pages()
        assert s.state.get("note_style") != ne.NOTE_STYLE
    s.run()
    assert ne.NOTE_CSS.strip().encode() in page.read_bytes()


def test_large_restyle_does_not_read_entire_page_into_memory(notes_env, monkeypatch):
    s, page = old_note(notes_env)
    with page.open("ab") as stream:
        stream.write(b"x" * (2 << 20))
    s.state.set("page:A", ne._digest(page))
    original = Path.read_bytes
    def bounded(path):
        assert path != page, "whole public page read into memory"
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", bounded)
    assert s._restyle_pages() == 1


@pytest.mark.parametrize("shape", ["transcoded", "companion-only"])
def test_different_original_cannot_authorize_cloud_delete(photos_env, monkeypatch, shape):
    s, remote, root, _ = photos_env
    s.run()
    s.settings.photos_upload = True
    source = put(root, "pic.heic", b"sent")
    upload = remote.upload

    def response(path):
        receipt = upload(path)
        asset = next(iter(remote.assets.values()))
        if shape == "companion-only":
            asset.resources[0].key = "original_video"
        else:
            remote.blobs[asset.resources[0].url] = b"JPEG"
        return receipt

    monkeypatch.setattr(remote, "upload", response)
    s.run()
    assert all(row.rel_path != source.name for row in s.state.files())
    source.unlink()
    s.run()
    assert not remote.deleted


def test_registration_failure_preserves_reason_and_retries_once_due(photos_env, monkeypatch):
    from pyicloud.services.photos_cloudkit.upload import PhotosUploader
    from skyfold import engine

    s, _, root, _ = photos_env
    s.run()
    put(root, "retry.jpg")
    calls = []
    def reject(**kw):
        calls.append(1)
        raise ConnectionError("synthetic connection failure")
    uploader = PhotosUploader(session=SimpleNamespace(post=reject), base_url="https://invalid.example",
                              base_params={}, timeout=(1, 1))
    client = SimpleNamespace(upload_file=uploader.upload)
    lib = SimpleNamespace(_client=client, zone_id={"zoneName": "PrimarySync"},
                          upload_file=lambda p: uploader.upload(p, zone_name="PrimarySync"))
    s.remote = photos.ICloudRemote(SimpleNamespace(photos=SimpleNamespace(libraries={"root": lib})))
    events = []
    monkeypatch.setattr(s.cb, "activity", lambda kind, text, *rest: events.append(kind))
    s.upload_new()
    s.upload_new()
    assert calls == [1] and events == ["error"]
    later = engine.time.time() + 7200
    monkeypatch.setattr(engine.time, "time", lambda: later)
    s.upload_new()
    assert calls == [1, 1] and events == ["error"]


def test_restyle_crash_after_publication_recovers_ownership(notes_env):
    s, page = old_note(notes_env)
    child = """
import os, sys
from pathlib import Path
from skyfold import notes_engine as ne
from tests.test_notes import FakeNotes
page = Path(sys.argv[1])
root = page.parents[1]
s = ne.NotesSyncer(ne.NotesState(root.parent / 'n.db'), FakeNotes(), root)
def crash_after(fn):
    def move(src, dst):
        result = fn(src, dst)
        if Path(dst) == page:
            os._exit(74)
        return result
    return move
os.rename = crash_after(os.rename)
os.replace = crash_after(os.replace)
s._restyle_pages()
"""
    result = subprocess.run([sys.executable, "-c", child, str(page)], timeout=30)
    assert result.returncode == 74
    s.run()
    assert ne.NOTE_CSS.strip().encode() in page.read_bytes()
    assert s.state.get("page:A") == ne._digest(page)
    assert not list(page.parent.glob(ne.STAGE_PREFIX + "*"))


def test_pinned_upload_streams_a_logical_four_gib_file(monkeypatch):
    from pyicloud.services.photos_cloudkit.upload import PhotosUploader
    from requests import Request

    size = 4 << 30
    class Movie(io.RawIOBase):
        pos = 0

        def tell(self):
            return self.pos

        def seek(self, offset, whence=os.SEEK_SET):
            self.pos = {os.SEEK_SET: 0, os.SEEK_CUR: self.pos, os.SEEK_END: size}[whence] + offset
            return self.pos

        def read(self, n=-1):
            assert 0 <= n <= 65536, "unbounded read of a video"
            chunk = bytes(min(n, size - self.pos))
            self.pos += len(chunk)
            return chunk

    handle = Movie()
    def post(url, data, timeout):
        prepared = Request("POST", url, data=data).prepare()
        assert prepared.body is handle and int(prepared.headers["Content-Length"]) == size
        assert len(data.read(65536)) == 65536
        data.seek(-1, os.SEEK_END)
        assert len(data.read(65536)) == 1
        return SimpleNamespace(status_code=200, json=lambda: {"singleFile": {"size": size}})
    uploader = PhotosUploader(session=SimpleNamespace(post=post), base_url="https://invalid.example",
                              base_params={}, timeout=(1, 1))
    monkeypatch.setattr(Path, "open", lambda *a, **kw: handle)
    assert uploader.send_bytes("https://invalid.example/upload", Path("movie.mov")).size == size
    assert handle.closed


@pytest.mark.parametrize("error", [photos.AuthRequired, photos.RemoteUnavailable])
def test_auth_and_unavailable_stop_pass_and_allow_retry(photos_env, monkeypatch, error):
    s, remote, root, _ = photos_env
    s.run()
    s.settings.photos_upload = True
    path = put(root, "auth.jpg")
    def fail(path):
        raise error("synthetic")
    with monkeypatch.context() as m:
        m.setattr(remote, "upload", fail)
        with pytest.raises(error):
            s.run()
    s.run()
    assert remote.uploads == [path.name]


@pytest.mark.skipif(os.name != "nt", reason="NTFS junction")
def test_upload_ignores_directory_junction(photos_env):
    from _winapi import CreateJunction

    s, remote, root, _ = photos_env
    s.run()
    other = root.parent / "outside"
    put(other, "private.jpg")
    CreateJunction(str(other), str(root / "shortcut"))
    s.upload_new()
    assert not getattr(remote, "uploads", [])


def test_content_still_present_after_case_only_rename(photos_env):
    s, remote, root, _ = photos_env
    s.settings.photos_upload = True
    s.run()
    path = put(root, "juli.jpg")
    s.run()
    path.rename(root / "JULI.JPG")
    s.run()
    assert remote.uploads == ["juli.jpg"] and not remote.deleted
    assert (root / "JULI.JPG").read_bytes() == b"photo"
