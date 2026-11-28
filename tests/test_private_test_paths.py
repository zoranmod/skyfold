"""Account cleanup in tests must never reach an interactive user's profile."""
import subprocess
import sys
from pathlib import Path

from skyfold import auth, avatar, config, netdiag, worker


def test_config_import_in_a_child_uses_the_disposable_portable_settings(_account_files_in_tmp):
    root = _account_files_in_tmp
    config.SETTINGS_FILE.write_text('{"language":"bs"}', encoding="utf-8")
    code = """
import sys
from pathlib import Path
from skyfold import config, i18n
root = Path(sys.argv[1])
assert config.APP_DIR == Path(config.__file__).resolve().parents[1]
assert config.DATA_DIR == root / "app" / "data"
assert config.PRIVATE_DIR.is_relative_to(root)
assert i18n.language() == "bs"
"""
    subprocess.run([sys.executable, "-c", code, str(root)], check=True, timeout=20,
                   cwd=Path(__file__).resolve().parents[1])


def test_packaged_settings_ignore_the_test_override(tmp_path, monkeypatch):
    executable_folder = tmp_path / "packaged"
    monkeypatch.setattr(config, "APP_DIR", executable_folder)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert config._data_dir() == executable_folder / "data"


def test_default_account_cleanup_and_sync_paths_are_disposable(_account_files_in_tmp):
    root = _account_files_in_tmp
    picture = avatar.path_for("garden@example.test")
    paths = (picture, auth.SIGNED_OUT, auth.SESSION_DIR, worker.STATE_DB, netdiag.file_path(),
             config.SETTINGS_FILE, config.LOG_FILE)
    assert all(path.is_relative_to(root) for path in paths)
    settings = config.Settings()
    assert all(Path(path).is_relative_to(root) for path in
               (settings.photos_dir, settings.drive_dir, settings.notes_dir, settings.bookmarks_dir))
    picture.write_bytes(b"synthetic")
    avatar.forget_all()
    assert not picture.exists()
