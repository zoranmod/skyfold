"""Updates: what counts as newer, which releases are used, the download check and the swap."""
import hashlib
import io
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from skyfold import updater as up

DL = up.DOWNLOADS + "v9.9.9/"


def release_json(**kw):
    data = {"tag_name": "v9.9.9", "body": "**New**", "html_url": "https://github.com/zoranmod/skyfold/releases/tag/v9.9.9",
            "assets": [{"name": up.ZIP_NAME, "browser_download_url": DL + up.ZIP_NAME},
                       {"name": up.ZIP_NAME + ".sha256", "browser_download_url": DL + up.ZIP_NAME + ".sha256"}]}
    data.update(kw)
    return data


def test_newer_compares_numbers_not_text():
    assert up.is_newer("1.0.10", "1.0.9") and up.is_newer("v2.0", "1.9.9")
    assert not up.is_newer("1.0.9", "1.0.9") and not up.is_newer("1.0.8", "1.0.9")
    assert not up.is_newer("nightly", "1.0.9")


def test_only_releases_with_skyfolds_own_files_are_used():
    get = lambda url, **_: SimpleNamespace(json=lambda: release_json())
    rel = up.latest_release(get)
    assert rel.version == "9.9.9" and rel.zip_url == DL + up.ZIP_NAME and rel.notes == "**New**"
    foreign = release_json(assets=[{"name": up.ZIP_NAME, "browser_download_url": "https://evil.example/x.zip"},
                                   {"name": up.ZIP_NAME + ".sha256", "browser_download_url": DL + "x"}])
    assert up.latest_release(lambda url, **_: SimpleNamespace(json=lambda: foreign)) is None
    assert up.latest_release(lambda url, **_: SimpleNamespace(json=lambda: release_json(prerelease=True))) is None


def build_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def fake_get(zip_bytes: bytes, digest: str):
    def get(url, **_):
        if url.endswith(".sha256"):
            return SimpleNamespace(text=f"{digest}  {up.ZIP_NAME}\n")
        return SimpleNamespace(headers={"Content-Length": str(len(zip_bytes))},
                               iter_content=lambda n: [zip_bytes[i:i + n] for i in range(0, len(zip_bytes), n)])
    return get


def rel():
    return up.Release("9.9.9", "", up.PAGE, DL + up.ZIP_NAME, DL + up.ZIP_NAME + ".sha256")


def test_download_is_checked_and_unpacked_beside_the_app(tmp_path):
    z = build_zip({"Skyfold/Skyfold.exe": b"NEW EXE", "Skyfold/_internal/lib.dll": b"NEW LIB"})
    seen = []
    new = up.download(rel(), tmp_path, lambda d, t: seen.append((d, t)), get=fake_get(z, hashlib.sha256(z).hexdigest()))
    assert (new / "Skyfold.exe").read_bytes() == b"NEW EXE" and seen[-1] == (len(z), len(z))
    assert new.parent.parent.name.startswith(up.STAGE + "-") and new.is_relative_to(tmp_path)


def test_a_download_that_doesnt_match_its_checksum_is_refused(tmp_path):
    z = build_zip({"Skyfold/Skyfold.exe": b"NEW", "Skyfold/_internal/a": b"x"})
    with pytest.raises(up.UpdateError):
        up.download(rel(), tmp_path, get=fake_get(z, "0" * 64))


def test_unsafe_paths_in_the_zip_are_refused(tmp_path):
    z = build_zip({"../evil.txt": b"x", "Skyfold/Skyfold.exe": b"NEW", "Skyfold/_internal/a": b"x"})
    with pytest.raises(up.UpdateError):
        up.download(rel(), tmp_path, get=fake_get(z, hashlib.sha256(z).hexdigest()))
    assert not (tmp_path.parent / "evil.txt").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell")
def test_the_swap_replaces_only_the_program_and_keeps_data(tmp_path, monkeypatch):
    app = tmp_path / "Skyfold"
    (app / "_internal").mkdir(parents=True)
    (app / "data").mkdir()
    (app / "data" / "settings.json").write_text("{}")
    (app / "Skyfold.exe").write_bytes(b"OLD")
    (app / "_internal" / "old.dll").write_bytes(b"OLD")
    new = app / (up.STAGE + "-test") / "unpacked" / "Skyfold"
    (new / "_internal").mkdir(parents=True)
    import shutil as _sh

    _sh.copy(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe"),
             new / "Skyfold.exe")  # a program that starts and stays running, like Skyfold
    (new / "_internal" / "new.dll").write_bytes(b"NEW")
    monkeypatch.setattr("skyfold.config.LOG_FILE", tmp_path / "log.txt")
    started = []
    up.start_install(new, app, pid=999999, popen=lambda *a, **k: started.append(subprocess.Popen(*a, **k)))
    try:
        started[0].wait(60)
    finally:  # close the stand-in that was started as "Skyfold"
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Get-Process | Where-Object {{ $_.Path -eq '{app / 'Skyfold.exe'}' }} | Stop-Process -Force"],
                       capture_output=True)
    assert (app / "Skyfold.exe").read_bytes()[:2] == b"MZ" and not list(app.glob("*.old-*"))
    assert (app / "_internal" / "new.dll").exists() and not (app / "_internal" / "old.dll").exists()
    assert (app / "data" / "settings.json").read_text() == "{}"
    assert not (app / (up.STAGE + "-test")).exists()  # its own download folder is removed
    assert "Update installed" in (tmp_path / "log.txt").read_text()


def test_not_packaged_means_no_install_here(tmp_path):
    assert not up.can_install(tmp_path)  # tests run from source: the release page is opened instead


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell")
def test_a_failed_swap_puts_the_old_version_back(tmp_path, monkeypatch):
    app = tmp_path / "Skyfold"
    (app / "_internal").mkdir(parents=True)
    (app / "Skyfold.exe").write_bytes(b"OLD")
    (app / "_internal" / "old.dll").write_bytes(b"OLD")
    new = tmp_path / "broken"
    new.mkdir()
    (new / "Skyfold.exe").write_bytes(b"NEW")  # no _internal: the swap can't finish
    monkeypatch.setattr("skyfold.config.LOG_FILE", tmp_path / "log.txt")
    started = []
    up.start_install(new, app, pid=999999, popen=lambda *a, **k: started.append(subprocess.Popen(*a, **k)))
    started[0].wait(60)
    assert (app / "Skyfold.exe").read_bytes() == b"OLD" and (app / "_internal" / "old.dll").exists()
    assert "previous version is kept" in (tmp_path / "log.txt").read_text()


def test_whats_new_leaves_out_the_download_instructions():
    notes = "**1.0.9**\n\n### What's new\n- a\n\n### Download\n`zip` below.\n\n---\n**Na bosanskom:** b"
    text = up.whats_new(notes)
    assert "What's new" in text and "- a" in text and "Na bosanskom" in text and "zip" not in text
