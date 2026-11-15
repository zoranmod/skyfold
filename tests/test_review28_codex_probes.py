"""Codex's probes from review 28 (of 505e380), kept as tests; existing fakes and disposable files only.

The tests Codex named characterize_* asserted a defect; here the same scenarios assert the fixed
behaviour (fixed_*). Since the fixes look at files with os.lstat, a simulated lookup failure is
injected into both os.stat and os.lstat, so the tests reach the new code. The three blocker tests are
kept as Codex wrote them except for that, and for clearing the marker's Hidden attribute before the
test writes into it (Windows refuses to rewrite a hidden file).
"""
import os
import shutil
from pathlib import Path

import pytest
from skyfold import engine as pe, notes_engine as ne
from skyfold import drive_engine as de
from tests.test_drive import env as drive_env
from tests.test_engine import env as photos_env
from tests.test_notes import env as notes_env
from tests.test_review27_codex_probes import interrupted_previous


@pytest.mark.parametrize("tagged", [True, False], ids=["known-tag-control", "missing-tag"])
def test_drive_folder_delete_preserves_cloud_edit(drive_env, tagged):
    s, cloud, root, _ = drive_env
    path = "Trip/a.txt"
    cloud.put(path, b"BEFORE")
    if not tagged:
        cloud.items[path]["etag"] = None
    s.run()
    assert s.state.all()[path].hash
    assert (root / path).read_bytes() == b"BEFORE"

    cloud.put(path, b"EDITED")
    if not tagged:
        cloud.items[path]["etag"] = None
    shutil.rmtree(root / "Trip")
    s.run()

    assert cloud.items.get(path, {}).get("data") == b"EDITED", cloud.trashed


def deny_lookup(monkeypatch, path, *, lookup="both", winerror=21):
    for name in (("stat", "lstat") if lookup == "both" else (lookup,)):
        real = getattr(os, name)
        def wrapped(candidate, *args, _real=real, **kwargs):
            if not isinstance(candidate, int) and Path(candidate) == path:
                raise OSError(0, "synthetic lookup failure", str(path), winerror)
            return _real(candidate, *args, **kwargs)
        monkeypatch.setattr(os, name, wrapped)


def test_fixed_albumdir_keeps_record_after_failed_identity_read(photos_env, monkeypatch):
    s, cloud, root, _ = photos_env
    s.settings.albums_as_folders = True
    cloud.add("A1", "a.jpg", b"IMAGE")
    cloud.album_map = {"Trip": ["A1"]}
    s.run(force_full=True)
    folder = root / "Albums/Trip"
    key = "albumdir:Albums/Trip"
    assert s.state.get(key)
    s.settings.albums_as_folders = False
    with monkeypatch.context() as m:
        deny_lookup(m, folder)
        s._prune_empty()
    assert folder.is_dir() and s.state.get(key)  # unknown identity: kept, looked at again
    s.run(force_full=True)
    assert not folder.exists() and s.state.get(key) is None


@pytest.mark.parametrize("artifact", ["stage", "obsolete", "deleted-note", "renamed-note"])
def test_fixed_notes_failed_lookup_keeps_live_path_on_record(notes_env, monkeypatch, artifact):
    s, cloud, root, trashed = notes_env
    cloud.put("n1", "A")
    s.run(full=True)
    old_page = root / "Notes/A.html"
    if artifact == "stage":
        path = root / "Notes/.skyfold-tmp-note-left.html"
        path.write_bytes(b"STAGED")
        s._own(path, "stage")
        key = "tmp:Notes/.skyfold-tmp-note-left.html"
        with monkeypatch.context() as m:
            deny_lookup(m, path)
            s._tidy_leftovers()
        assert path.exists() and s.state.get(key) == "stage"
        s._tidy_leftovers()
        assert not path.exists() and s.state.get(key) is None
    elif artifact == "obsolete":
        path = root / "Notes/Obsolete.html"
        path.write_bytes(old_page.read_bytes())
        key = "obsolete:Notes/Obsolete.html"
        s.state.set(key, ne._digest(path))
        with monkeypatch.context() as m:
            deny_lookup(m, path)
            s._remove_leftover_pages(ne.NotesResult())
        assert path.exists() and s.state.get(key)
        s._remove_leftover_pages(ne.NotesResult())
        assert not path.exists() and s.state.get(key) is None and trashed == ["Obsolete.html"]
        return
    elif artifact == "deleted-note":
        cloud.delete("n1")
        with monkeypatch.context() as m:
            deny_lookup(m, old_page)
            s.run()
        assert old_page.exists() and "n1" in s.state.all()  # remembered: removed next time
    else:
        cloud.put("n1", "B")
        with monkeypatch.context() as m:
            deny_lookup(m, old_page)
            s.run(full=True)
        assert old_page.exists() and (root / "Notes/B.html").exists()
        assert s.state.get("obsolete:Notes/A.html")  # written down: removed once it can be looked at
    assert not trashed


def test_fixed_photo_move_stat_error_deletes_nothing(photos_env, monkeypatch):
    s, cloud, root, _ = photos_env
    cloud.add("A1", "a.jpg", b"IMAGE")
    s.run(force_full=True)
    old = root / s.state.files()[0].rel_path
    dest = root / "Moved/a.jpg"
    dest.parent.mkdir()
    old.rename(dest)
    with monkeypatch.context() as m:
        deny_lookup(m, dest, winerror=5)
        s.run(force_full=True)
    assert dest.read_bytes() == b"IMAGE" and cloud.deleted == []
    s.run(force_full=True)  # readable again: recognised as moved
    assert cloud.deleted == [] and s.state.files()[0].rel_path == "Moved/a.jpg"


def test_fixed_photo_original_recheck_holds_on_lstat_error(photos_env, monkeypatch):
    s, cloud, root, _ = photos_env
    cloud.add("A1", "a.jpg", b"IMAGE")
    s.run(force_full=True)
    file = root / s.state.files()[0].rel_path
    scan = s.scan_local
    def restored_after_scan():
        result = scan()
        file.write_bytes(b"RESTORED")
        return result
    file.unlink()
    monkeypatch.setattr(s, "scan_local", restored_after_scan)
    with monkeypatch.context() as m:
        deny_lookup(m, file, lookup="lstat", winerror=5)
        s.run(force_full=True)
    assert file.read_bytes() == b"RESTORED" and cloud.deleted == []


def test_fixed_album_leaf_keeps_record_after_failed_lookup(photos_env, monkeypatch):
    s, cloud, root, _ = photos_env
    s.settings.albums_as_folders = True
    cloud.add("A1", "a.jpg", b"IMAGE")
    cloud.album_map = {"Trip": ["A1"]}
    s.run(force_full=True)
    leaf = root / "Albums/Trip/a.jpg"
    assert s.state.get("albumid:Albums/Trip/a.jpg")
    s.settings.albums_as_folders = False
    with monkeypatch.context() as m:
        deny_lookup(m, leaf)
        s.run(force_full=True)
    # The lookup of the album path failing doesn't stop the removal: the entry is moved to a private
    # name (no lookup needed) and proven there to be the photo's second name, so records and link go
    # together - never the records alone while the entry stays.
    assert not leaf.exists() and "Albums/Trip/a.jpg" not in s.state.album_links()
    assert s.state.get("albumid:Albums/Trip/a.jpg") is None
    assert (root / s.state.files()[0].rel_path).read_bytes() == b"IMAGE"  # the photo itself stays


def test_previous_name_exclusion_is_local_to_the_path(drive_env, tmp_path, monkeypatch):
    s, cloud, root, _, previous = interrupted_previous(drive_env, tmp_path)
    cloud.put(previous.name, b"UNRELATED CLOUD FILE")
    cloud.put("neighbor.txt", b"OTHER")
    res = s.run(changed={"a.txt"})
    assert not res.full
    assert previous.read_bytes() == b"OLD"
    assert (root / "neighbor.txt").read_bytes() == b"OTHER"
    assert cloud.items[previous.name]["data"] == b"UNRELATED CLOUD FILE"
    s.run()
    assert previous.read_bytes() == b"UNRELATED CLOUD FILE"


def test_fixed_quick_scan_holds_record_for_unlisted_path(drive_env, tmp_path, monkeypatch):
    s, cloud, root, _, previous = interrupted_previous(drive_env, tmp_path)
    cloud.put(previous.name, b"UNRELATED CLOUD FILE")
    actual = os.scandir
    def filtered(path):
        with actual(path) as entries:
            return iter(e for e in entries if e.name != previous.name)
    with monkeypatch.context() as m:
        m.setattr(os, "scandir", lambda p: filtered(p) if Path(p) == root else actual(p))
        res = s.run(changed={"a.txt"})
    assert not res.full and res.conflicts == 0
    assert previous.read_bytes() == b"OLD"  # held: nothing downloaded onto it
    assert not list(root.glob("a (previous version) (PC conflict*"))
    assert s.state.get_meta("prev:" + previous.name)
    assert previous.name in s.unreadable


def test_fixed_previous_record_released_when_path_becomes_directory(drive_env, tmp_path):
    s, cloud, root, _, previous = interrupted_previous(drive_env, tmp_path)
    previous.rename(tmp_path / "saved-old")
    previous.mkdir()
    (previous / "new.txt").write_bytes(b"PERSONAL")
    s.run()
    assert not s.state.get_meta("prev:" + previous.name)  # a folder there: provably not that file
    s.run(changed={"a.txt"})
    assert previous.name not in s.unreadable
    assert (previous / "new.txt").read_bytes() == b"PERSONAL"


def test_fixed_next_replacement_keeps_another_versions_record(drive_env, tmp_path):
    s, cloud, root, _, previous = interrupted_previous(drive_env, tmp_path)
    cloud.put("a.txt", b"THIRD VERSION")
    res = s.run(changed={"a.txt"})
    assert not res.full and res.downloaded == 1
    assert previous.read_bytes() == b"OLD"
    assert s.state.get_meta("prev:" + previous.name)  # still OLD's record
    s.run()
    assert previous.name not in cloud.items and previous.name not in cloud.uploads
    assert not previous.exists() and (root / "a.txt").read_bytes() == b"THIRD VERSION"


@pytest.mark.parametrize("tagged", [True, False], ids=["known-tag-control", "missing-tag"])
def test_late_folder_approval_rechecks_child_version(drive_env, tagged):
    s, cloud, root, _ = drive_env
    path = "Trip/a.txt"
    s.delete_threshold = 1
    cloud.put(path, b"BEFORE")
    cloud.put("Trip/b.txt", b"B")
    cloud.put("anchor.txt", b"BEFORE")
    if not tagged:
        cloud.items[path]["etag"] = None
    s.run()
    shutil.rmtree(root / "Trip")
    cloud.put("anchor.txt", b"DRIVE DOWNLOAD IN SAME PASS")

    def approve(to_cloud, to_pc):
        assert to_cloud == ["Trip"] and not to_pc
        cloud.put(path, b"EDITED")
        if not tagged:
            cloud.items[path]["etag"] = None
        s.approved.update(to_cloud)

    s.on_pending = approve
    s.run()
    assert cloud.items.get(path, {}).get("data") == b"EDITED", cloud.trashed


@pytest.mark.parametrize("service", ["photos", "drive", "notes"])
def test_marker_creation_must_not_truncate_existing_user_file(
    service, request, monkeypatch
):
    s, cloud, root, _ = request.getfixturevalue(f"{service}_env")
    if service == "photos":
        # A tracked root after upgrade legitimately has both marker names.
        cloud.add("A1", "a.jpg", b"IMAGE")
        s.run(force_full=True)
        marker = root / pe.MARKER
        (root / pe.LEGACY_MARKERS[0]).write_bytes(b"OLD MARKER")
    else:
        root.mkdir()
        marker = root / (ne.MARKER if service == "notes" else de.MARKER)
    if marker.exists() and os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(marker), 0x80)
    marker.write_bytes(b"UNIQUE PERSONAL DATA AT A RESERVED NAME")
    fails = []

    def transient(real):
        def wrapped(path, *args, **kwargs):
            if not isinstance(path, int) and Path(path) == marker and not fails:
                fails.append(path)
                raise OSError(0, "synthetic Windows drive not ready", str(path), 21)
            return real(path, *args, **kwargs)
        return wrapped

    with monkeypatch.context() as m:
        m.setattr(os, "stat", transient(os.stat))
        m.setattr(os, "lstat", transient(os.lstat))
        try:
            s.run()
        except (pe.SyncError, FileExistsError):
            pass  # refusing to sync is also safe
    assert fails
    assert marker.read_bytes() == b"UNIQUE PERSONAL DATA AT A RESERVED NAME"


def test_fixed_drive_late_pc_deletion_holds_unreadable_file(drive_env, monkeypatch):
    s, cloud, root, _ = drive_env
    s.delete_threshold = 1
    for name in ("a.txt", "b.txt", "anchor.txt"):
        cloud.put(name, b"ORIGINAL")
    s.run()
    for name in ("a.txt", "b.txt"):
        cloud.trash(cloud._item(name))
    cloud.put("anchor.txt", b"ANCHOR CHANGED")
    path = root / "a.txt"
    with monkeypatch.context() as m:
        def approve(to_cloud, to_pc):
            assert not to_cloud and sorted(to_pc) == ["a.txt", "b.txt"]
            deny_lookup(m, path)
            s.approved.update(to_pc)
        s.on_pending = approve
        s.run()
    assert path.read_bytes() == b"ORIGINAL" and "a.txt" in s.state.all()  # nothing decided
    s.run()
    assert "a.txt" not in cloud.uploads and "a.txt" not in cloud.items


@pytest.mark.parametrize("artifact", ["settings", "state"])
def test_private_migration_must_not_replace_existing_destination(
    notes_env, monkeypatch, artifact
):
    from skyfold import config

    syncer, cloud, _, _ = notes_env
    cloud.put("n1", "Existing installation")
    syncer.run(full=True)
    sandbox = syncer.root.parent
    app = sandbox / "Skyfold"
    data = app / "data"
    data.mkdir(parents=True)
    local = sandbox / "Local"
    private = local / "Skyfold"
    private.mkdir(parents=True)
    monkeypatch.setattr(config, "APP_DIR", app)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "PRIVATE_DIR", private)
    monkeypatch.setattr(config, "SETTINGS_FILE", data / "settings.json")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    if artifact == "settings":
        current = data / "settings.json"
        legacy = sandbox / "iSync/data/settings.json"
    else:
        current = private / "state.sqlite3"
        legacy = data / "state.sqlite3"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    if artifact == "settings":
        current.write_text('{"language": "bs"}', encoding="utf8")
        legacy.write_text('{"language": "en"}', encoding="utf8")
    else:
        import sqlite3
        from contextlib import closing

        for path, owner in ((current, "CURRENT INSTALLATION"), (legacy, "OLDER INSTALLATION")):
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
                db.execute("INSERT INTO meta VALUES ('owner', ?)", (owner,))
                db.commit()
    current_bytes = current.read_bytes()
    config.migrate_private_files()
    assert current.read_bytes() == current_bytes
    with monkeypatch.context() as m:
        deny_lookup(m, current)
        try:
            config.migrate_private_files()
        except FileExistsError:
            pass
    assert current.read_bytes() == current_bytes


def test_drive_marker_lookup_failure_must_not_adopt_other_account(drive_env, monkeypatch, tmp_path):
    from tests.test_drive import FakeDrive

    old, old_cloud, root, _ = drive_env
    old.account = "account-one"
    old_cloud.put("personal.txt", b"ACCOUNT ONE DATA")
    old.run()
    marker = root / de.MARKER
    assert "account-one" in marker.read_text(encoding="utf8")
    # A copied/restored Drive root can have the file's bytes without its DOS
    # Hidden attribute. A still-hidden marker may fail open('w') on Windows.
    import ctypes
    assert ctypes.windll.kernel32.SetFileAttributesW(str(marker), 0x80)
    other_cloud = FakeDrive()
    other = de.DriveSyncer(de.DriveState(tmp_path / "different-account.db"), other_cloud, root, 3)
    other.account = "account-two"
    with pytest.raises(pe.SyncError):
        other.run()  # healthy lookup detects the old owner's marker

    with monkeypatch.context() as m:
        deny_lookup(m, marker)
        try:
            other.run()
        except (pe.SyncError, FileExistsError):
            pass
    assert not other_cloud.uploads, other_cloud.uploads
