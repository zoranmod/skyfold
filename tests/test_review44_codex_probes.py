"""Release 1.0.17, offline expectations for everyday Drive, Notes and desktop use."""
import cProfile
import gc
import json
import shutil
import tracemalloc
from types import SimpleNamespace as NS

import pytest

from skyfold import drive_engine as de, notes_engine as ne
from skyfold.drive_remote import ICloudDrive, ROOT_ID
from skyfold.remote import RemoteUnavailable
from tests.test_drive import env as drive_env, write
from tests.test_notes import env as notes_env
from tests.test_notes_quality_codex import OfflineCloudKit, asset, field, note_record, png, rec, token
from tests.test_review27_codex_ui_probes import qapp


def test_retry_of_replacement_preserves_new_cloud_edit(drive_env, monkeypatch):
    sync, remote, root, _ = drive_env
    remote.put("budget.txt", b"baseline")
    sync.run()
    write(root, "budget.txt", b"PC edit")
    upload = remote.upload

    def offline(*args):
        raise RemoteUnavailable("offline")

    monkeypatch.setattr(remote, "upload", offline)
    with pytest.raises(RemoteUnavailable):
        sync.run()
    assert "budget.txt" not in remote.items
    # Another device saves a new version while the PC is offline.
    remote.put("budget.txt", b"new version from Mac")
    monkeypatch.setattr(remote, "upload", upload)
    sync.run()
    active = {p.read_bytes() for p in root.glob("*.txt")}
    active.update(i["data"] for i in remote.items.values())
    assert {b"PC edit", b"new version from Mac"} <= active


def test_hidden_cloud_child_survives_deleting_visible_pc_folder(drive_env):
    sync, fake, root, _ = drive_env
    fake.put("Project/draft.txt", b"visible")
    fake.put("Project/hidden.txt", b"cloud only")
    # Real adapter traversal, including the wire-format hidden flag; the fake
    # stores the tree and provides its ordinary cascading folder trash.
    adapter = ICloudDrive(NS(drive=object()))

    def folders(ids):
        listings = []
        for ident in ids:
            parent = "" if ident == ROOT_ID else next(k for k,v in fake.items.items() if v["id"] == ident)
            children = []
            for path, item in fake.items.items():
                if path.rpartition("/")[0] == parent:
                    children.append(dict(name=path.rpartition("/")[2], drivewsid=item["id"], docwsid=item["id"],
                                         etag=item["etag"], type="FOLDER" if item["dir"] else "FILE",
                                         size=len(item["data"] or b""), hidden=path.endswith("hidden.txt")))
            listings.append(dict(drivewsid=ident, items=children, numberOfItems=len(children)))
        return listings

    adapter._folders = folders
    adapter.download = fake.download
    adapter.trash = fake.trash
    sync.remote = adapter
    sync.run()
    assert (root / "Project/draft.txt").is_file()
    assert not (root / "Project/hidden.txt").exists()
    shutil.rmtree(root / "Project")
    sync.run()
    assert "Project/hidden.txt" in fake.items


def test_refused_drive_trash_does_not_report_a_success(drive_env):
    sync, fake, root, _ = drive_env
    fake.put("doc.txt", b"document")
    sync.run()
    (root / "doc.txt").unlink()
    refusal = {"items": [{"status": "CONFLICT", "errorCode": "CONFLICT"}]}
    adapter = ICloudDrive(NS(drive=NS(move_items_to_trash=lambda *args: refusal)))
    fake.trash = adapter.trash
    result = sync.run()
    assert result.deleted_cloud == 0 and result.errors and "doc.txt" in sync.state.all()


def test_only_new_download_needs_room_for_one_copy(drive_env, monkeypatch):
    sync, remote, root, _ = drive_env
    from skyfold.engine import DISK_RESERVE
    size = 1 << 20
    remote.put("recording.mov", bytes(size))
    actual = shutil.disk_usage(root.parent)
    monkeypatch.setattr(shutil, "disk_usage", lambda _: actual._replace(free=DISK_RESERVE + size + size // 2))
    sync.run()
    assert (root / "recording.mov").stat().st_size == size


def test_large_drive_upload_does_not_allocate_a_file_sized_buffer(drive_env, monkeypatch):
    sync, remote, root, _ = drive_env
    sync.run()
    size = 12 << 20
    write(root, "clip.mov", bytes(size))
    # Discard the transport to measure just Skyfold, without FakeDrive's own read_bytes.
    monkeypatch.setattr(remote, "upload", lambda *args: None)
    gc.collect()
    tracemalloc.start()
    try:
        assert sync.run().uploaded == 1
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < size, peak


def photo_note(notes_env, tmp_path):
    sync, remote, root, _ = notes_env
    payload = png(tmp_path / "camera.jpg", 64, 48, "JPEG").read_bytes()
    attachment = rec(Media=field("REFERENCE", {"recordName": "media"}))
    media = rec(name="media", Image=asset("original", len(payload)))
    raw = OfflineCloudKit([attachment, media], {token("original")["downloadURL"]: payload})
    remote.export = ne.ICloudNotes(NS(notes=NS(_raw=raw, _lookup_note_record=lambda nid: note_record()))).export
    remote.put("n1", "Travel receipt")
    assert sync.run().exported == 1
    assets = root / ne.ASSETS / "n1"
    assert any(p.is_file() for p in assets.iterdir())
    return sync, remote, root, assets


def test_locking_note_removes_its_previously_exported_private_images(notes_env, tmp_path):
    sync, remote, root, assets = photo_note(notes_env, tmp_path)
    remote.put("n1", "Travel receipt", locked=True)
    sync.run()
    assert not (root / "Notes/Travel receipt.html").exists()
    assert not list(assets.glob("*"))


def test_text_edits_do_not_accumulate_identical_note_attachments(notes_env, tmp_path):
    sync, remote, root, assets = photo_note(notes_env, tmp_path)
    count = len(list(assets.iterdir()))
    for _ in range(3):
        remote.put("n1", "Travel receipt")  # new modification date, exact same image
        assert sync.run().exported == 1
    assert len(list(assets.iterdir())) == count


@pytest.mark.xfail(reason='planned after 1.0.18: a folder-only rename is reconciled by the next full Notes read', strict=True)
def test_folder_only_note_rename_updates_unchanged_children(notes_env):
    sync, remote, root, _ = notes_env
    remote.put("a", "Shopping")
    sync.run()
    remote.dirs["F1"] = ("Home", None)
    remote.v += 1  # folder-only change advances the zone cursor; no changed Note record
    sync.run()
    assert (root / "Home/Shopping.html").is_file()


def test_failed_note_export_reaches_worker_status(notes_env):
    from skyfold.config import Settings
    from skyfold.worker import SyncWorker

    sync, remote, root, _ = notes_env
    remote.put("a", "Receipt", body='<img src="https://assets.icloud-content.com/missing">')
    worker = SyncWorker(Settings())
    worker.notes = sync
    problems = []
    worker._run_notes(False, problems)
    assert sync.state.get("failed:a") and not (root / "Notes/Receipt.html").exists()
    assert problems, "the cycle uses this list to choose between error and Everything is in sync"


def test_majority_of_small_drive_gets_documented_confirmation(drive_env):
    from skyfold.config import Settings

    sync, remote, root, _ = drive_env
    sync.delete_threshold = Settings().delete_threshold
    # Documented rule (README): more than half of the Drive, from 5 items up, always asks.
    # Smaller deletions below the limit go to Recently Deleted without asking (by design).
    names = "abcdefgh"
    for name in names:
        remote.put(name, name.encode())
    sync.run()
    for name in names[:5]:
        (root / name).unlink()
    result = sync.run()
    assert set(result.pending_to_cloud) == set(names[:5]) and not result.deleted_cloud


def test_many_individual_deletions_do_not_require_pairwise_path_checks(drive_env):
    sync, remote, root, _ = drive_env
    n = 160
    for i in range(n):
        remote.put(f"{i}.txt", str(i).encode())
    sync.run()
    for p in root.glob("*.txt"):
        p.unlink()
    with cProfile.Profile() as profile:
        result = sync.run()
    assert len(result.pending_to_cloud) == n
    checks = sum(s.callcount for s in profile.getstats()
                 if getattr(s.code, "co_name", "") in ("under_any", "tops_of"))
    assert checks < 10 * n, f"{checks} ancestry checks for {n} files at the root"


def test_unchanged_quick_pass_does_not_rewrite_entire_drive_cache(drive_env, tmp_path, monkeypatch):
    sync, remote, root, _ = drive_env
    cache = tmp_path / "drive-cache.json"
    sync.cache_file = cache
    for i in range(40):
        remote.put(f"Archive/{i}.txt", str(i).encode())
    remote.put("Docs/today.txt", b"today")
    sync.run()
    before = json.loads(cache.read_text())
    written = []
    save = de.save_text

    def record(path, text):
        if path == cache:
            written.append(len(json.loads(text)["items"]))
        save(path, text)

    monkeypatch.setattr(de, "save_text", record)
    assert not sync.run(changed={"Docs/today.txt"}).full
    assert json.loads(cache.read_text()) == before
    assert not written, f"rewrote {written} entries for an unchanged folder"


def test_settings_theme_has_a_screen_reader_name(qapp):
    from PySide6.QtGui import QAccessible
    from skyfold.config import Settings
    from skyfold.ui import dialogs, theme

    dialog = dialogs.SettingsDialog(Settings(), theme.LIGHT)
    try:
        interface = QAccessible.queryAccessibleInterface(dialog.theme)
        assert interface.text(QAccessible.Text.Name).strip(), "Theme selector has no accessible name"
    finally:
        dialog.close()


def test_bosnian_note_index_preserves_counts_ending_in_one(qapp, monkeypatch):
    from PySide6.QtQml import QJSEngine
    from skyfold import i18n

    monkeypatch.setattr(i18n, "_lang", "bs")
    page = ne.render_index([], "")
    script = "\n".join(line for line in page.splitlines()
                       if line.startswith(("const S=", "function plural(")))
    js = QJSEngine()
    assert not js.evaluate(script).isError()
    for n in (1, 11, 21, 31, 101):
        for group in ("notes", "folderCount"):
            # (a top-level const lives only in its own evaluate call: the page script goes along)
            got = js.evaluate(script + f"\nplural({n}, S.{group})").toString()
            assert got.startswith(f"{n} "), got


def test_incomplete_newer_release_does_not_tell_user_they_are_current(qapp, monkeypatch):
    from skyfold import app, updater
    from skyfold.ui import dialogs
    from tests.test_updater import release_json

    messages = []
    get = lambda *args, **kwargs: NS(json=lambda: release_json(assets=[]))
    latest = updater.latest_release
    monkeypatch.setattr(updater, "latest_release", lambda: latest(get))

    def inline(job, done, failed):
        try:
            result = job()
        except Exception as exc:
            failed(str(exc))
        else:
            done(result)

    monkeypatch.setattr(dialogs, "run_async", inline)
    monkeypatch.setattr(app, "inform", lambda parent, title, message: messages.append(message))
    monkeypatch.setattr(app, "warn", lambda parent, title, message: messages.append(message))
    controller = NS(flyout=NS(set_update=lambda version: None))
    app.Controller.check_updates(controller, manual=True)
    assert messages and all("latest version" not in str(m).lower() for m in messages), messages


def test_refused_folder_creation_retries_after_write_access_returns(drive_env, monkeypatch):
    sync, remote, root, _ = drive_env
    sync.run()
    folder = root / "Invoices"
    folder.mkdir()
    refusal = {"folders": [{"status": "PERMISSION_DENIED", "errorCode": "PERMISSION_DENIED"}]}
    adapter = ICloudDrive(NS(drive=NS(create_folders=lambda *args: refusal)))
    with monkeypatch.context() as patch:
        patch.setattr(remote, "mkdir", adapter.mkdir)
        first = sync.run()
    # With the refusal lifted, two ordinary full passes must converge.
    sync.run()
    sync.run()
    assert folder.is_dir() and "Invoices" in remote.items, (first.errors, folder.exists(), remote.items)


def test_drive_restart_after_interrupted_upload_preserves_both_versions(drive_env, monkeypatch, tmp_path):
    sync, remote, root, _ = drive_env
    remote.put("report.txt", b"first version")
    sync.run()
    write(root, "report.txt", b"PC revision")
    def disconnected(*args):
        raise RemoteUnavailable("connection lost")
    with monkeypatch.context() as patch:
        patch.setattr(remote, "upload", disconnected)
        with pytest.raises(RemoteUnavailable):
            sync.run()
    sync.state._db.close()
    remote.put("report.txt", b"Mac revision while the PC was off")
    restarted = de.DriveSyncer(de.DriveState(tmp_path / "d.db"), remote, root, sync.delete_threshold)
    restarted.run()
    active = {p.read_bytes() for p in root.glob("*.txt")}
    active.update(item["data"] for item in remote.items.values() if not item["dir"])
    assert {b"PC revision", b"Mac revision while the PC was off"} <= active


def test_note_asset_cleanup_after_delete_keeps_personal_files(notes_env, tmp_path):
    sync, remote, root, assets = photo_note(notes_env, tmp_path)
    private_images = list(assets.iterdir())
    personal = assets / "my-copy.txt"
    personal.write_bytes(b"keep this")
    remote.delete("n1")
    sync.run()
    assert not (root / "Notes/Travel receipt.html").exists()
    assert personal.read_bytes() == b"keep this"
    assert all(not p.exists() for p in private_images)
