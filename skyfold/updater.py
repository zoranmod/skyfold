"""Updates from Skyfold's own GitHub releases.

Checking asks GitHub's public API for the latest release (nothing about the user is sent: only
"Skyfold/<version>" as the client name). Updating downloads that release's zip, checks it against the
SHA-256 published next to it, unpacks it beside the app and hands over to a small script that runs
after Skyfold has closed: it swaps only Skyfold.exe and _internal (settings in "data" are never
touched), puts the old version back if the swap fails, and starts Skyfold again.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import APP_DIR, VERSION
from .i18n import t

log = logging.getLogger("skyfold.update")

REPO = "zoranmod/skyfold"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOADS = f"https://github.com/{REPO}/releases/download/"
PAGE = f"https://github.com/{REPO}/releases/latest"
ZIP_NAME = "Skyfold-windows.zip"
STAGE = ".skyfold-update"  # beside Skyfold.exe: same drive, so the swap is a rename


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    notes: str  # Markdown, as written on GitHub
    page: str
    zip_url: str
    sha_url: str


def parse_version(text: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+(?:\.\d+){1,3})", (text or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(candidate: str, current: str = VERSION) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


def _get(url: str, *, stream: bool = False, timeout: float = 20):
    import requests

    r = requests.get(url, timeout=timeout, stream=stream, allow_redirects=True,
                     headers={"User-Agent": f"Skyfold/{VERSION}", "Accept": "application/vnd.github+json"})
    r.raise_for_status()
    return r


def latest_release(get=_get) -> Release | None:
    """The latest published release, or None when it has no Windows zip with its checksum."""
    data = get(API).json()
    version = str(data.get("tag_name") or "").lstrip("v")
    if parse_version(version) is None or data.get("draft") or data.get("prerelease"):
        return None
    assets = {a.get("name"): a.get("browser_download_url") or "" for a in data.get("assets") or []}
    zip_url, sha_url = assets.get(ZIP_NAME, ""), assets.get(ZIP_NAME + ".sha256", "")
    if not (zip_url.startswith(DOWNLOADS) and sha_url.startswith(DOWNLOADS)):
        return None  # only files published with Skyfold's own releases
    page = data.get("html_url") or PAGE
    if not str(page).startswith(f"https://github.com/{REPO}/"):
        page = PAGE
    return Release(version, str(data.get("body") or ""), page, zip_url, sha_url)


def whats_new(notes: str) -> str:
    """The release notes without their download instructions (the app does that part itself)."""
    return re.sub(r"(?ms)^#{2,4}[ \t]*Download[^\n]*\n.*?(?=^---|^#{1,4}[ \t]|\Z)", "", notes or "").strip()


def can_install(app_dir: Path = APP_DIR) -> bool:
    """A packaged Skyfold in a folder it may write to (else: the release page is opened instead)."""
    if not getattr(sys, "frozen", False) or not (app_dir / "Skyfold.exe").is_file():
        return False
    try:
        fd, probe = tempfile.mkstemp(prefix=".skyfold-write-", dir=app_dir)
        os.close(fd)
        os.unlink(probe)
        return True
    except OSError:
        return False


def download(rel: Release, app_dir: Path = APP_DIR, progress: Callable[[int, int], None] | None = None,
             get=_get, cancelled: Callable[[], bool] = lambda: False) -> Path:
    """The release unpacked beside the app, after its checksum matched. Returns the new Skyfold folder."""
    words = get(rel.sha_url).text.split()
    expected = words[0].strip().lower() if words else ""
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise UpdateError(t("the published checksum can't be read"))
    stage = app_dir / STAGE
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    archive = stage / ZIP_NAME
    h = hashlib.sha256()
    r = get(rel.zip_url, stream=True, timeout=60)
    total = int(r.headers.get("Content-Length") or 0)
    done = 0
    with open(archive, "wb") as fh:
        for chunk in r.iter_content(1 << 16):
            if cancelled():
                raise UpdateError(t("cancelled"))
            fh.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if h.hexdigest() != expected:
        raise UpdateError(t("the download doesn't match its published checksum"))
    out = stage / "unpacked"
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in name.split("/"):
                raise UpdateError(t("the download contains an unsafe path"))
        z.extractall(out)
    archive.unlink()
    new = out / "Skyfold"
    if not (new / "Skyfold.exe").is_file() or not (new / "_internal").is_dir():
        raise UpdateError(t("the download isn't a Skyfold build"))
    return new


# Runs after Skyfold has quit. Only Skyfold.exe and _internal are swapped; on any failure the old ones
# are put back. Then Skyfold starts again, and the old version and the download are removed.
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$app = $args[0]; $new = $args[1]; $pid0 = [int]$args[2]; $tag = $args[3]; $log = $args[4]
function Note($m) { try { Add-Content -LiteralPath $log -Value ("{0} INFO skyfold.update: {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss,fff'), $m) } catch {} }
try { Wait-Process -Id $pid0 -Timeout 120 -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Milliseconds 500
$exe = Join-Path $app 'Skyfold.exe'; $int = Join-Path $app '_internal'
$oldExe = "$exe.old-$tag"; $oldInt = "$int.old-$tag"
$moved = @()
try {
  for ($i = 0; $i -lt 20; $i++) { try { Rename-Item -LiteralPath $exe -NewName (Split-Path $oldExe -Leaf); break } catch { Start-Sleep -Milliseconds 500; if ($i -eq 19) { throw } } }
  $moved += 'exe'
  Rename-Item -LiteralPath $int -NewName (Split-Path $oldInt -Leaf); $moved += 'int'
  Move-Item -LiteralPath (Join-Path $new 'Skyfold.exe') -Destination $exe
  Move-Item -LiteralPath (Join-Path $new '_internal') -Destination $int
  Note 'Update installed'
} catch {
  Note ("Update failed, the previous version is kept: " + $_.Exception.Message)
  if (Test-Path -LiteralPath $exe) { if ($moved -contains 'exe') { Remove-Item -LiteralPath $exe -Force -ErrorAction SilentlyContinue } }
  if (Test-Path -LiteralPath $int) { if ($moved -contains 'int') { Remove-Item -LiteralPath $int -Recurse -Force -ErrorAction SilentlyContinue } }
  if ($moved -contains 'exe') { Rename-Item -LiteralPath $oldExe -NewName 'Skyfold.exe' }
  if ($moved -contains 'int') { Rename-Item -LiteralPath $oldInt -NewName '_internal' }
}
try { Start-Process -FilePath $exe -ArgumentList '--tray' } catch { Note ('Could not start Skyfold again: ' + $_.Exception.Message) }
Start-Sleep -Seconds 3
Remove-Item -LiteralPath $oldExe -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $oldInt -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $app '.skyfold-update') -Recurse -Force -ErrorAction SilentlyContinue
"""


def start_install(new: Path, app_dir: Path = APP_DIR, pid: int | None = None, popen=subprocess.Popen) -> None:
    """Hand over to the swap script; the caller then quits Skyfold."""
    from .config import LOG_FILE

    script = Path(tempfile.gettempdir()) / f"skyfold-update-{uuid.uuid4().hex[:8]}.ps1"
    script.write_text(_SCRIPT, encoding="utf-8-sig")
    shell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    popen([str(shell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script),
           str(app_dir), str(new), str(pid or os.getpid()), uuid.uuid4().hex[:8], str(LOG_FILE)],
          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
          close_fds=True)
    log.info("Update: handed over to the installer; Skyfold closes now")
