"""Connection diagnostics: an opt-in record of Skyfold's requests, for a bug report.

Off at every start. Turned on in Settings, it records for 30 minutes into `connections.log` (two
files of at most 512 KB) and then stops by itself. Each line has only fixed labels: the time, the
method, which kind of server (Apple sign-in, iCloud service, iCloud content, GitHub update), the
service's name, the known words of the request path, Apple's status code, how long it took and the
kind of failure. Never the address itself, a name, an id, a file name, a cookie, a token, a
password, an Apple ID, a body or an error text.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

MINUTES = 30
MAX_BYTES = 512 * 1024  # each of the two files

_lock = threading.Lock()
_until = 0.0  # wall-clock time it stops (so a PC that slept doesn't go on recording)
_file: Path | None = None

# iCloud service names (the host's first label, without the account's pNN- partition)
_SERVICES = frozenset({
    "idmsa", "setup", "www", "gateway", "gatewayws", "ckdatabasews", "docws", "drivews", "photosupload",
    "uploadimagews", "sharedstreams", "findme", "fmipweb", "account", "contentws", "cvws", "keyvalue",
})
# words of a request path that say what kind of request it is (anything else becomes "…")
_WORDS = frozenset({
    "appleauth", "auth", "signin", "init", "complete", "verify", "trusteddevice", "securitycode", "phone",
    "accountlogin", "validate", "logout", "setup", "ws", "database", "1", "production", "private", "shared",
    "public", "records", "query", "lookup", "modify", "changes", "zone", "zones", "list",
    "com.apple.photos.cloud", "com.apple.notes", "com.apple.clouddocs", "com.apple.safari.bookmarks",
    "com.apple.cloudkit", "retrieveitemdetailsinfolders", "retrieveitemdetails", "createfolders",
    "renameitems", "moveitems", "deleteitems", "upload", "download", "by_id", "web", "putasset",
    "fmipservice", "client", "initclient", "refreshclient", "playsound", "storageusageinfo",
    "repos", "zoranmod", "skyfold", "releases", "latest", "tags", "attestations",
})
_PARTITION = re.compile(r"^p\d+-")


def file_path() -> Path:
    from .config import PRIVATE_DIR

    return _file or (PRIVATE_DIR / "connections.log")


def files() -> list[Path]:
    p = file_path()
    return [q for q in (p, p.with_name(p.name + ".1")) if q.exists()]


def start(minutes: int = MINUTES) -> None:
    global _until
    with _lock:
        _until = time.time() + minutes * 60
    _write(f"--- recording started, {minutes} min ---")


def stop() -> None:
    global _until
    with _lock:
        was = _until > time.time()
        _until = 0.0
    if was:
        _write_now("--- recording stopped ---")


def on() -> bool:
    return _until > time.time()


def until() -> float:
    """When it stops (time.time()), 0 when it is off."""
    return _until if on() else 0.0


def delete() -> None:
    stop()
    with _lock:
        for p in files():
            try:
                p.unlink()
            except OSError:
                pass


def describe(method: str, url: str) -> tuple[str, str, str, str]:
    """(method, server kind, service, path words) of a request: fixed labels only."""
    verb = method if method in ("GET", "POST", "HEAD", "PUT", "DELETE", "PATCH") else "OTHER"
    from .netpolicy import destination

    kind = destination(url)
    try:
        p = urlsplit(url)
        host = (p.hostname or "").rstrip(".").lower()
        segments = [s.lower() for s in p.path.split("/") if s]
    except (ValueError, TypeError, AttributeError):
        return verb, "unknown", "-", "-"
    if kind == "github":
        group, service = "github-update", host if host in ("api.github.com", "github.com") else "github-files"
    elif kind == "apple":
        if host.endswith(("icloud-content.com", "icloud-content.com.cn", "cdn-apple.com")):
            group = "apple-content"
        elif host == "idmsa.apple.com":
            group = "apple-auth"
        else:
            group = "icloud"
        first = _PARTITION.sub("", host.split(".", 1)[0])
        service = first if first in _SERVICES else "other"
    else:
        return verb, "unknown", "-", "-"
    if group == "apple-content":
        return verb, group, service, "content"
    words: list[str] = []
    for s in segments[:12]:
        w = s if s in _WORDS else "…"
        if not (w == "…" and words and words[-1] == "…"):
            words.append(w)
    return verb, group, service, "/" + "/".join(words)


def _failure(exc: BaseException | None) -> str:
    if exc is None:
        return "none"
    from .netpolicy import NetworkPolicyBlocked

    if isinstance(exc, NetworkPolicyBlocked):
        return "policy-blocked"
    try:
        from requests import exceptions as rx

        for cls, label in ((rx.ConnectTimeout, "connect-timeout"), (rx.ReadTimeout, "read-timeout"),
                           (rx.Timeout, "timeout"), (rx.SSLError, "tls"), (rx.ProxyError, "proxy"),
                           (rx.ConnectionError, "connection")):
            if isinstance(exc, cls):
                return label
    except ImportError:
        pass
    return "other"


def timed(send, session, request, kwargs):
    """Session.send with a diagnostic line when recording is on (one line per request, and per
    step of a redirect). The reply itself is never read here."""
    if not on():
        return send(session, request, **kwargs)
    t0 = time.monotonic()
    try:
        r = send(session, request, **kwargs)
    except BaseException as exc:
        _record(request, None, t0, exc, kwargs)
        raise
    _record(request, r, t0, None, kwargs)
    return r


def _record(request, response, t0: float, exc: BaseException | None, kwargs) -> None:
    try:
        ms = int((time.monotonic() - t0) * 1000)
        verb, group, service, words = describe(str(getattr(request, "method", "") or ""),
                                               str(getattr(request, "url", "") or ""))
        status = getattr(response, "status_code", None)
        status = str(status) if isinstance(status, int) and 100 <= status <= 599 else "-"
        timing = "ms-to-headers" if kwargs.get("stream") else "ms"
        _write(f"{verb} {group} {service} {words} status={status} {timing}={ms} failure={_failure(exc)}")
    except Exception:  # noqa: BLE001 - a diagnostic never breaks a request
        pass


def blocked() -> None:
    """A request the network rules refused (written by netpolicy, before anything is sent)."""
    if on():
        _write("BLOCKED unknown - - status=- ms=0 failure=policy-blocked")


def _write(line: str) -> None:
    if not on():
        return
    _write_now(line)


def _write_now(line: str) -> None:
    global _until
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    data = f"{stamp} {line}\n".encode("utf-8")
    with _lock:
        p = file_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            if size + len(data) > MAX_BYTES:
                os.replace(p, p.with_name(p.name + ".1"))  # the older half; the one before it goes
            with open(p, "ab") as fh:
                fh.write(data)
        except OSError:
            _until = 0.0  # e.g. a full disk: recording stops, syncing goes on
