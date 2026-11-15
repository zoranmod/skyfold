"""Twenty-eighth follow-up review (Codex review 30): own regression tests next to the kept probes."""
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from skyfold import auth, config, drive_engine as de, engine as pe, notes_engine as ne
from tests.test_drive import env as drive_env, write  # noqa: F401 - fixtures
from tests.test_engine import env as photos_env  # noqa: F401
from tests.test_notes import env as notes_env  # noqa: F401
from tests.test_review28_codex_probes import deny_lookup


# A30-1 -----------------------------------------------------------------------------------------
def _pc_shell(drive_env):
    s, cloud, root, recycled = drive_env
    cloud.put("Trip/a.txt", b"A")
    s.run()
    write(root, "Trip/desktop.ini", b"LOCAL ONLY")
    cloud.trash(cloud._item("Trip"))
    s.run()
    s.run()
    assert "Trip" not in cloud.items and not (root / "Trip/a.txt").exists()
    return s, cloud, root


def test_a_synced_file_added_to_a_kept_folder_reopens_it(drive_env):
    s, cloud, root = _pc_shell(drive_env)
    write(root, "Trip/new.txt", b"NEW", age=60)
    s.run()
    s.run()
    assert "Trip" in cloud.items and cloud.items["Trip/new.txt"]["data"] == b"NEW"
    assert "Trip/desktop.ini" not in cloud.items  # (still never synced)
    assert not s.state.meta_items("shell:")


def test_a_kept_folder_removed_on_this_pc_is_forgotten(drive_env):
    s, cloud, root = _pc_shell(drive_env)
    shutil.rmtree(root / "Trip")
    s.run()
    assert not s.state.meta_items("shell:") and "Trip" not in cloud.items


def test_a_kept_folder_made_again_in_icloud_syncs_normally(drive_env):
    s, cloud, root = _pc_shell(drive_env)
    cloud.put("Trip/b.txt", b"B")
    s.run()
    assert (root / "Trip/b.txt").read_bytes() == b"B" and not s.state.meta_items("shell:")


def test_a_new_file_in_icloud_reopens_a_folder_kept_there(drive_env):
    s, cloud, root, _ = drive_env
    cloud.put("Trip/a.txt", b"A")
    s.run()
    cloud.put("Trip/desktop.ini", b"CLOUD ONLY")
    shutil.rmtree(root / "Trip")
    s.run()
    s.run()
    assert not (root / "Trip").exists()
    cloud.put("Trip/new.txt", b"NEW")
    s.run()
    assert (root / "Trip/new.txt").read_bytes() == b"NEW"


def test_an_edited_file_still_brings_the_folder_back_to_icloud(drive_env):
    s, cloud, root, _ = drive_env
    cloud.put("Trip/a.txt", b"A")
    s.run()
    write(root, "Trip/desktop.ini", b"LOCAL ONLY")
    write(root, "Trip/a.txt", b"EDITED HERE", age=60)
    cloud.trash(cloud._item("Trip"))
    s.run()
    s.run()
    assert cloud.items["Trip/a.txt"]["data"] == b"EDITED HERE" and not s.state.meta_items("shell:")


# B30-3 -----------------------------------------------------------------------------------------
def test_a_folder_that_cant_be_read_completely_forgets_nothing(drive_env, monkeypatch):
    s, cloud, root, recycled = drive_env
    cloud.put("Trip/a.txt", b"A")
    cloud.put("Trip/b.txt", b"B")
    s.run()
    write(root, "Trip/a.txt", b"EDITED", age=60)
    with monkeypatch.context() as m:
        deny_lookup(m, root / "Trip/b.txt", winerror=5)
        assert s._keep_unsynced_local(["Trip"], s.state.all()) == []
    assert s.state.all().get("Trip/a.txt") is not None and not recycled  # decided as a whole, later


# A30-2 / B30-5 ---------------------------------------------------------------------------------
def _private(monkeypatch, tmp_path):
    portable, private = tmp_path / "portable", tmp_path / "private"
    portable.mkdir()
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(config, "DATA_DIR", portable)
    monkeypatch.setattr(config, "SETTINGS_FILE", portable / "settings.json")
    monkeypatch.setattr(config, "PRIVATE_DIR", private)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    return portable, private


def test_the_earlier_database_is_copied_and_left_as_it_is(tmp_path, monkeypatch):
    portable, private = _private(monkeypatch, tmp_path)
    with closing(sqlite3.connect(portable / "state.sqlite3")) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('x')")
        conn.commit()
    before = (portable / "state.sqlite3").read_bytes()
    config.migrate_private_files()
    assert (portable / "state.sqlite3").read_bytes() == before and not config.MIGRATION_PROBLEMS
    with closing(sqlite3.connect(private / "state.sqlite3")) as conn:
        assert conn.execute("SELECT v FROM t").fetchone() == ("x",)
    config.migrate_private_files()  # the next start: nothing is taken over again
    assert not config.MIGRATION_PROBLEMS


def test_a_database_that_cant_be_taken_over_is_reported(tmp_path, monkeypatch):
    portable, private = _private(monkeypatch, tmp_path)
    (portable / "state.sqlite3").write_bytes(b"not a database at all")
    config.migrate_private_files()
    assert len(config.MIGRATION_PROBLEMS) == 1 and "state.sqlite3" in config.MIGRATION_PROBLEMS[0]
    assert (portable / "state.sqlite3").read_bytes() == b"not a database at all"


def test_a_busy_earlier_database_gives_up_in_time(tmp_path, monkeypatch):
    portable, private = _private(monkeypatch, tmp_path)
    with closing(sqlite3.connect(portable / "state.sqlite3")) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.commit()
    monkeypatch.setattr(config, "BACKUP_SECONDS", -1.0)
    config.migrate_private_files()
    assert config.MIGRATION_PROBLEMS and not (private / "state.sqlite3").exists()
    assert not [p for p in private.iterdir() if p.name.endswith(".new")]


# A30-3 -----------------------------------------------------------------------------------------
def _sketch_page(root):
    from PySide6.QtGui import QColor, QImage

    assets = root / "_assets"
    assets.mkdir(parents=True)
    preview = assets / "preview.png"
    img = QImage(4, 4, QImage.Format_RGB32)
    img.fill(QColor("red"))
    img.save(str(preview), "PNG")
    page = root / "export.html"
    page.write_text('<img data-uti="com.apple.paper" data-id="D1" src="_assets/preview.png">', encoding="utf-8")

    def download(_url, folder):
        out = Path(folder) / "download"
        big = QImage(32, 32, QImage.Format_RGB32)
        big.fill(QColor("blue"))
        big.save(str(out), "PNG")
        return out
    return assets, preview, page, download


def test_a_preview_this_export_downloaded_is_replaced(notes_env):
    _, _, root, _ = notes_env
    assets, preview, page, download = _sketch_page(root)
    assert ne.upgrade_sketches(page, {"D1": "u"}, download, made={ne._file_identity(preview)},
                               assets_dir=assets) == 1
    assert not preview.exists()


def test_a_downloaded_preview_outside_the_assets_folder_stays(notes_env):
    _, _, root, _ = notes_env
    assets, preview, page, download = _sketch_page(root)
    assert ne.upgrade_sketches(page, {"D1": "u"}, download, made={ne._file_identity(preview)},
                               assets_dir=assets / "other") == 1
    assert preview.exists()


def test_export_records_only_its_own_downloads(notes_env, monkeypatch):
    """The real export wiring: what download_asset_to saved during this export is what may go."""
    seen = {}

    class Raw:
        def download_asset_to(self, url, directory):
            p = Path(directory) / f"asset-{url}"
            p.write_bytes(b"x")
            return str(p)

    raw = Raw()

    def fake_upgrade(page, fallbacks, download, made=None, assets_dir=None):
        seen["made"], seen["assets"] = set(made), assets_dir
        return 0

    _, _, root, _ = notes_env
    root.mkdir(parents=True, exist_ok=True)
    before = root / "before.png"
    before.write_bytes(b"older")

    class Exporter:
        def __init__(self, *_a, **_k):
            pass

        def export(self, record, output_dir, filename):
            raw.download_asset_to("one", str(root))
            (Path(output_dir) / (filename + ".html")).write_text("<p>", encoding="utf-8")
            ne._SKETCH_FALLBACKS["D1"] = "u"
            return True

    import pyicloud.services.notes.rendering.exporter as ex

    monkeypatch.setattr(ex, "NoteExporter", Exporter)
    monkeypatch.setattr(ne, "upgrade_sketches", fake_upgrade)
    notes = ne.ICloudNotes(api=None)
    notes._svc = type("Svc", (), {"_raw": raw, "_lookup_note_record": lambda self, _id: object()})()
    monkeypatch.setattr(ne, "_prefer_sharpest_previews", lambda: None)
    notes.export("n1", root, "page", root)
    assert seen["made"] == {ne._file_identity(root / "asset-one")} and seen["assets"] == root
    assert ne._file_identity(before) not in seen["made"]
    assert "download_asset_to" not in vars(raw) or vars(raw)["download_asset_to"].__name__ != "recording"


# B30-6 -----------------------------------------------------------------------------------------
def test_a_first_index_claim_from_a_crash_needs_its_exact_bytes(notes_env):
    s, r, root, _ = notes_env
    r.put("n1", "A")
    s.run()
    index = root / ne.INDEX
    mine = index.read_bytes()
    s.state.set("index_digest", None)
    s.state.set("index_owned", "pending:" + "0" * 64)  # stopped before its index got there
    index.write_bytes(mine.replace(b"</html>", b"<!-- edited by the user --></html>"))
    s.write_index()
    assert b"edited by the user" in index.read_bytes() and not s.state.get("index_owned")


def test_a_first_index_that_did_get_there_is_ours(notes_env):
    s, r, root, _ = notes_env
    r.put("n1", "A")
    s.run()
    index = root / ne.INDEX
    digest = s.state.get("index_digest")
    s.state.set("index_digest", None)
    s.state.set("index_owned", "pending:" + digest)
    s.write_index()
    assert s.state.get("index_owned") == "1" and s.state.get("index_digest")


def test_a_sign_out_mark_that_cant_be_looked_at_blocks_restore(tmp_path, monkeypatch):
    session = tmp_path / "session"
    session.mkdir()
    monkeypatch.setattr(auth, "SESSION_DIR", session)
    monkeypatch.setattr(auth, "SIGNED_OUT", tmp_path / "signed-out")
    with monkeypatch.context() as m:
        deny_lookup(m, tmp_path / "signed-out", winerror=5)
        assert auth.restore("someone@example.invalid") is None


# B30-4 -----------------------------------------------------------------------------------------
def test_an_unreadable_photos_folder_is_named_once(photos_env, monkeypatch):
    s, cloud, root, _ = photos_env
    told = []
    real_activity = s.cb.activity
    s.cb.activity = lambda kind, text, target="": (told.append(text), real_activity(kind, text, target))
    cloud.add("A1", "a.jpg", b"A")
    s.run(force_full=True)
    (root / s.state.files()[0].rel_path).unlink()
    locked = root / "Locked"
    locked.mkdir()
    real = os.scandir

    def scandir(p="."):
        if Path(p) == locked:
            raise PermissionError(13, "denied", str(p))
        return real(p)

    monkeypatch.setattr(os, "scandir", scandir)
    s.run(force_full=True)
    s.run(force_full=True)
    named = [x for x in told if "Locked" in x]
    assert len(named) == 1 and not cloud.deleted
