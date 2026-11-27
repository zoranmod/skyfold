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


ALLOWED_HOSTS = {"api.github.com", "github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com",  # where GitHub serves releases from
                 "tmaproduction.blob.core.windows.net",  # the build attestations
                 "raw.githubusercontent.com"}  # release notes, when the API is limited


class _NoAuth:
    """An explicit "no credentials": Requests then never adds a .netrc login to update requests."""

    def __call__(self, r):
        return r


class RateLimited(UpdateError):
    """GitHub limits how often one internet address may ask (60 an hour without signing in)."""


def _rate_limited(r) -> RateLimited | None:
    """A 403/429 that is GitHub's limit (not a real refusal), with how long until it lifts."""
    import math
    import time as _time

    if getattr(r, "status_code", 200) not in (403, 429):
        return None
    h = getattr(r, "headers", None) or {}
    wait = None
    try:
        if h.get("Retry-After"):
            wait = float(h["Retry-After"])
        elif str(h.get("X-RateLimit-Remaining", "")) == "0" and h.get("X-RateLimit-Reset"):
            wait = float(h["X-RateLimit-Reset"]) - _time.time()
    except (TypeError, ValueError):
        wait = None
    if wait is None and getattr(r, "status_code", None) != 429             and "rate limit" not in str(getattr(r, "reason", "") or "").lower()             and "rate limit" not in _short_body(r).lower():  # (GitHub's secondary limit says so only there)
        return None
    minutes = max(1, math.ceil((wait or 60) / 60))
    return RateLimited(t("GitHub is limiting update checks from your internet connection right now; try again "
                         "in {n} min.", n=minutes))


def _short_body(r) -> str:
    """The start of an error reply's text (never of a download: its body is left unread)."""
    try:
        if getattr(r, "_content_consumed", False) or not getattr(r, "raw", None):
            return str(getattr(r, "text", "") or "")[:2000]
        return r.raw.read(2000, decode_content=True).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def _get(url: str, *, stream: bool = False, timeout: float = 20, follow: bool = True):
    """GET over HTTPS from GitHub only. Redirects are followed one by one (at most 5), each one
    checked; nothing of the user's (no stored logins) is sent - only "Skyfold/<version>".
    follow=False: a redirect is returned as it is (to read where it points)."""
    from urllib.parse import urljoin, urlsplit

    import requests

    from . import netpolicy

    netpolicy.install()
    session = requests.Session()
    for _ in range(6):
        parts = urlsplit(url)
        if parts.scheme != "https" or (parts.hostname or "").lower() not in ALLOWED_HOSTS:
            raise UpdateError(t("the update address isn't one of GitHub's"))
        r = session.get(url, timeout=timeout, stream=stream, allow_redirects=False, auth=_NoAuth(),
                        headers={"User-Agent": f"Skyfold/{VERSION}", "Accept": "application/vnd.github+json"})
        if getattr(r, "status_code", 200) in (301, 302, 303, 307, 308):
            if not follow:
                return r
            target = (getattr(r, "headers", None) or {}).get("Location", "")
            _close(r)
            if not target:
                raise UpdateError(t("the update address isn't one of GitHub's"))
            url = urljoin(url, target)
            continue
        limited = _rate_limited(r)
        if limited is not None:
            _close(r)
            raise limited
        r.raise_for_status()
        return r
    raise UpdateError(t("the update address isn't one of GitHub's"))


def _close(r) -> None:
    close = getattr(r, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001
            pass


class IncompleteRelease(RuntimeError):
    """A newer release is published without its own download files (yet)."""


def latest_release(get=_get) -> Release | None:
    """The latest published release, or None when it isn't a complete Skyfold release: its zip and
    checksum must be exactly this release's own files (same tag), anything else is refused. When
    GitHub's API is limiting this connection, the releases page tells the latest version instead."""
    try:
        return _latest_from_api(get)
    except RateLimited as limited:
        try:
            rel = _latest_from_page(get)
        except Exception:  # noqa: BLE001 - the page didn't help either: say why
            rel = None
        if rel is None:
            raise limited from None
        return rel


def _latest_from_page(get) -> Release | None:
    """github.com/<repo>/releases/latest points to the latest release's tag (not rate limited like
    the API); its files have fixed names, and its notes are in docs/release-notes-<version>.md."""
    r = get(PAGE, follow=False)
    try:
        target = (getattr(r, "headers", None) or {}).get("Location", "")
    finally:
        _close(r)
    m = re.fullmatch(rf"https://github\.com/{re.escape(REPO)}/releases/tag/(v(\d+(?:\.\d+){{1,3}}))", target or "")
    if not m:
        return None
    tag, version = m.group(1), m.group(2)
    base = f"{DOWNLOADS}{tag}/"
    notes = ""
    try:
        n = get(f"https://raw.githubusercontent.com/{REPO}/{tag}/docs/release-notes-{version}.md")
        try:
            notes = str(n.text)
        finally:
            _close(n)
    except Exception:  # noqa: BLE001 - notes are nice to have
        notes = ""
    return Release(version, notes, f"https://github.com/{REPO}/releases/tag/{tag}", base + ZIP_NAME,
                   base + ZIP_NAME + ".sha256")


def _latest_from_api(get) -> Release | None:
    try:
        data = get(API).json()
        if not isinstance(data, dict):
            return None
        tag = str(data.get("tag_name") or "")
        version = tag[1:] if tag.startswith("v") else ""
        if parse_version(version) is None or data.get("draft") or data.get("prerelease"):
            return None
        assets = {}
        for item in data.get("assets") or []:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                return None
            assets[item["name"]] = item.get("browser_download_url")
        base = f"{DOWNLOADS}{tag}/"
        zip_url, sha_url = assets.get(ZIP_NAME), assets.get(ZIP_NAME + ".sha256")
        if zip_url != base + ZIP_NAME or sha_url != base + ZIP_NAME + ".sha256":
            if is_newer(version):  # newer, but not complete (still uploading, or broken): never "latest"
                raise IncompleteRelease(t("Skyfold {version} is published, but its download isn't complete yet. "
                                          "Try again later.", version=version))
            return None  # only this release's own files
        page = data.get("html_url")
        if not isinstance(page, str) or not page.startswith(f"https://github.com/{REPO}/releases/"):
            page = PAGE
        body = data.get("body")
        return Release(version, body if isinstance(body, str) else "", page, zip_url, sha_url)
    except (AttributeError, TypeError, ValueError):
        return None


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


MAX_ZIP = 600 * 1024 * 1024  # a Skyfold zip is ~50 MB; anything far bigger isn't one
MAX_UNPACKED = 2 * 1024 * 1024 * 1024


def download(rel: Release, app_dir: Path = APP_DIR, progress: Callable[[int, int], None] | None = None,
             get=_get, cancelled: Callable[[], bool] = lambda: False,
             verify: Callable[[str, str, Callable], None] | None = None) -> Path:
    if verify is None and getattr(sys, "frozen", False):
        # the installed app never unpacks a release whose origin wasn't checked (build attestation)
        raise UpdateError(t("This update can't be checked, so it wasn't installed."))
    """The release unpacked beside the app, after its checksum matched. Returns the new Skyfold folder.
    Each attempt gets a folder of its own (nothing already there is ever touched); on any failure or
    cancel it is removed again."""
    stage: Path | None = None
    try:
        r = get(rel.sha_url)
        try:
            words = str(r.text).split()
        finally:
            _close(r)
        expected = words[0].strip().lower() if words else ""
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise UpdateError(t("the published checksum can't be read"))
        if verify is not None:  # built by Skyfold's own release workflow? (before anything is fetched)
            verify(expected, rel.version, get)
        stage = Path(tempfile.mkdtemp(prefix=STAGE + "-", dir=app_dir))
        archive = stage / ZIP_NAME
        h = hashlib.sha256()
        r = get(rel.zip_url, stream=True, timeout=60)
        try:
            total = int((getattr(r, "headers", None) or {}).get("Content-Length") or 0)
            if total > MAX_ZIP:
                raise UpdateError(t("the download isn't a Skyfold build"))
            if total and shutil.disk_usage(app_dir).free < total * 4:
                raise UpdateError(t("there isn't enough free disk space for the update"))
            done = 0
            with open(archive, "xb") as fh:
                for chunk in r.iter_content(1 << 16):
                    if cancelled():
                        raise UpdateError(t("cancelled"))
                    fh.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if done > MAX_ZIP:
                        raise UpdateError(t("the download isn't a Skyfold build"))
                    if progress:
                        progress(done, total)
        finally:
            _close(r)
        if cancelled():
            raise UpdateError(t("cancelled"))
        if h.hexdigest() != expected:
            raise UpdateError(t("the download doesn't match its published checksum"))
        out = stage / "unpacked"
        with zipfile.ZipFile(archive) as z:
            seen, size = set(), 0
            for info in z.infolist():
                name = info.filename.replace("\\", "/")
                low = name.lower()
                mode = (info.external_attr >> 16) & 0o170000
                if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in name.split("/") \
                        or low in seen or mode == 0o120000:
                    raise UpdateError(t("the download contains an unsafe path"))
                seen.add(low)
                size += info.file_size
            if size > MAX_UNPACKED:
                raise UpdateError(t("the download isn't a Skyfold build"))
            for info in z.infolist():
                if cancelled():
                    raise UpdateError(t("cancelled"))
                z.extract(info, out)
        archive.unlink()
        new = out / "Skyfold"
        if not (new / "Skyfold.exe").is_file() or not (new / "_internal").is_dir():
            raise UpdateError(t("the download isn't a Skyfold build"))
        return new
    except BaseException:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)  # this attempt's own folder only
        raise


# Runs after Skyfold has quit. Only Skyfold.exe and _internal are swapped; on any failure the old ones
# are put back. Then Skyfold starts again, and the old version and the download are removed.
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$app = $args[0]; $new = $args[1]; $pid0 = [int]$args[2]; $tag = $args[3]; $log = $args[4]
$stage = Split-Path (Split-Path $new -Parent) -Parent
function Note($m) { try { Add-Content -LiteralPath $log -Encoding UTF8 -Value ("{0} INFO skyfold.update: {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss,fff'), $m) } catch {} }
# 1) Skyfold must really have closed; if it hasn't, nothing is changed.
try { Wait-Process -Id $pid0 -Timeout 120 -ErrorAction SilentlyContinue } catch {}
if (Get-Process -Id $pid0 -ErrorAction SilentlyContinue) { Note 'Update not installed: Skyfold did not close'; exit 0 }
Start-Sleep -Milliseconds 500
$exe = Join-Path $app 'Skyfold.exe'; $int = Join-Path $app '_internal'
$oldExe = "$exe.old-$tag"; $oldInt = "$int.old-$tag"
$moved = @()
function Restore() {
  # Put the previous version back. Whatever can't be put back is kept as .old-*, never deleted.
  try { if ($moved -contains 'exe') { if (Test-Path -LiteralPath $exe) { Remove-Item -LiteralPath $exe -Force }; Rename-Item -LiteralPath $oldExe -NewName 'Skyfold.exe' } } catch { Note ('Could not put the previous Skyfold.exe back: ' + $_.Exception.Message) }
  try { if ($moved -contains 'int') { if (Test-Path -LiteralPath $int) { Remove-Item -LiteralPath $int -Recurse -Force }; Rename-Item -LiteralPath $oldInt -NewName '_internal' } } catch { Note ('Could not put the previous _internal back: ' + $_.Exception.Message) }
}
$ok = $false
try {
  for ($i = 0; $i -lt 20; $i++) { try { Rename-Item -LiteralPath $exe -NewName (Split-Path $oldExe -Leaf); break } catch { Start-Sleep -Milliseconds 500; if ($i -eq 19) { throw } } }
  $moved += 'exe'
  Rename-Item -LiteralPath $int -NewName (Split-Path $oldInt -Leaf); $moved += 'int'
  Move-Item -LiteralPath (Join-Path $new 'Skyfold.exe') -Destination $exe
  Move-Item -LiteralPath (Join-Path $new '_internal') -Destination $int
  $ok = $true
  Note 'Update installed'
} catch {
  Note ("Update failed, the previous version is kept: " + $_.Exception.Message)
  Restore
}
# 2) Start Skyfold; a new version that doesn't stay running is replaced by the previous one again.
$started = $false
try {
  $p = Start-Process -FilePath $exe -ArgumentList '--tray' -WorkingDirectory $app -PassThru
  Start-Sleep -Seconds 5
  $started = -not $p.HasExited
  if (-not $started) { throw ('it closed right away (exit code ' + $p.ExitCode + ')') }
} catch { Note ('Could not start Skyfold again: ' + $_.Exception.Message) }
if ($ok -and -not $started) {
  Note 'The previous version is put back'
  Restore
  try { Start-Process -FilePath $exe -ArgumentList '--tray' -WorkingDirectory $app } catch { Note ('Could not start the previous version either: ' + $_.Exception.Message) }
} elseif ($ok) {
  Remove-Item -LiteralPath $oldExe -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $oldInt -Recurse -Force -ErrorAction SilentlyContinue
}
if ((Split-Path $stage -Leaf) -like '.skyfold-update-*') { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
exit 0
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
