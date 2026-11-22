"""iCloud Drive adapter: lists the whole Drive tree and performs file operations.

iCloud Drive's web service has no change feed, so every sync walks the folder tree.
Folders are fetched in batches (one request per up to 50 folders) to keep that cheap.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .remote import _translate
from .safelog import Text, code

log = logging.getLogger(__name__)

ROOT_ID = "FOLDER::com.apple.CloudDocs::root"
BATCH = 50
WORKERS = 6  # requests to Apple at the same time (listing folders, downloading files)
UPLOAD_WORKERS = 3  # new files sent at the same time (Apple limits each upload connection)


class MacPackage(Exception):
    """A Mac "package" (e.g. an .xcodeproj or .app): a folder that Macs show as one file.
    iCloud only offers it as a zip that can't be synced back, so Skyfold leaves it in iCloud."""


@dataclass(slots=True)
class DriveItem:
    path: str  # posix path relative to the Drive root, "" for the root itself
    drivewsid: str
    docwsid: str
    zone: str
    etag: str | None
    is_dir: bool
    size: int = 0
    mtime: float | None = None  # epoch seconds (UTC)

    def __post_init__(self) -> None:
        if not self.etag:
            self.etag = None  # an empty version tag says nothing: the same as no tag (never "unchanged")


class DriveRemote(Protocol):
    def walk(self, on_progress: Callable[[int], None] | None = None,
             folders: list[DriveItem] | None = None,
             want: Callable[[str], bool] | None = None) -> dict[str, DriveItem]: ...
    def download(self, item: DriveItem, dest: Path, on_bytes: Callable[[int], None]) -> None: ...
    def upload(self, parent: DriveItem, local: Path) -> None: ...
    def mkdir(self, parent: DriveItem, name: str) -> None: ...
    def trash(self, item: DriveItem) -> None: ...
    def rename(self, item: DriveItem, new_name: str) -> str | None: ...
    def move(self, item: DriveItem, folder: DriveItem) -> str | None: ...


RETRIES = 3  # attempts for one request when the connection drops or Apple is briefly busy


def is_transient(exc: BaseException) -> bool:
    """A dropped connection, a timeout or a busy Apple server: worth trying again shortly."""
    import requests
    from pyicloud.exceptions import PyiCloudAPIResponseException, PyiCloudConnectionException

    seen = 0
    cur: BaseException | None = exc
    while cur is not None and seen < 8:  # pyicloud wraps a broken reply in its own error
        if isinstance(cur, (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                            requests.exceptions.ChunkedEncodingError, PyiCloudConnectionException)):
            return True
        cur, seen = cur.__cause__ or cur.__context__, seen + 1
    if isinstance(exc, PyiCloudAPIResponseException):
        try:
            code = int(exc.code or 0)
        except (TypeError, ValueError):
            return False
        return code == 429 or code >= 500
    return False


def with_retries(fn: Callable[[], Any], attempts: int = RETRIES, pause: float = 2.0) -> Any:
    for n in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if n == attempts or not is_transient(exc):
                raise
            log.info("iCloud request failed (%s), trying again (%d of %d)", exc, n + 1, attempts)
            time.sleep(pause * n)
    raise AssertionError("unreachable")


def _mtime(value: str | None) -> float | None:
    if not value:
        return None
    from pyicloud.services.drive import _date_to_utc

    dt = _date_to_utc(value)
    return dt.replace(tzinfo=timezone.utc).timestamp() if dt else None


def _size(data: dict[str, Any], is_dir: bool) -> int:
    """Bytes, or -1 when iCloud didn't say (never mistaken for an empty file)."""
    if is_dir:
        return 0
    try:
        return int(data["size"])
    except (KeyError, TypeError, ValueError):
        return -1


def item_name(data: dict[str, Any]) -> str:
    name = data.get("name") or data.get("drivewsid", "?")
    ext = data.get("extension")
    return f"{name}.{ext}" if ext else name


class ICloudDrive:
    def __init__(self, api: Any) -> None:
        self._api = api
        self._drive: Any = None
        self.incomplete: set[str] = set()  # folders iCloud answered with fewer items than it holds
        self.skipped: set[str] = set()  # items listed but not synced (hidden, a slash in the name)

    @property
    def drive(self) -> Any:
        if self._drive is None:
            try:
                self._drive = self._api.drive
            except Exception as exc:  # noqa: BLE001
                raise _translate(exc) from exc
        return self._drive

    def _folders(self, ids: list[str]) -> list[dict[str, Any]]:
        return with_retries(lambda: self._folders_once(ids))

    def _folders_once(self, ids: list[str]) -> list[dict[str, Any]]:
        d = self.drive
        resp = d.session.post(
            d.service_root + "/retrieveItemDetailsInFolders",
            params=d.params,
            json=[{"drivewsid": i, "partialData": False} for i in ids],
            timeout=180,  # Apple can take tens of seconds for a batch; never hang forever
        )
        d._raise_if_error(resp)
        return resp.json()

    def walk(self, on_progress: Callable[[int], None] | None = None,
             folders: list[DriveItem] | None = None,
             want: Callable[[str], bool] | None = None) -> dict[str, DriveItem]:
        """List the whole Drive, or only the direct contents of the given folders (quick sync).
        `want` skips folders that aren't synced, so they aren't even read."""
        try:
            root = DriveItem("", ROOT_ID, "root", "com.apple.CloudDocs", None, True)
            recursive = folders is None
            if folders is None:
                out: dict[str, DriveItem] = {"": root}
                queue: list[tuple[str, str]] = [(ROOT_ID, "")]
            else:
                out = {f.path: f for f in folders}
                root = out.get("", root)
                queue = [(f.drivewsid, f.path) for f in folders]
            _ = self.drive  # connect once here, not from several threads at the same time
            pending: dict[Future, dict[str, str]] = {}
            with ThreadPoolExecutor(WORKERS) as pool:
                while queue or pending:
                    stop = getattr(self, "should_stop", None)  # (set by the Drive engine: Stop, Quit)
                    if queue and stop is not None and stop():
                        from .engine import Cancelled

                        raise Cancelled()  # the requests under way finish; nothing new is asked
                    while queue and len(pending) < WORKERS:
                        # Small batches early on, so the first levels of the tree already run in parallel.
                        size = max(1, min(BATCH, -(-len(queue) // WORKERS)))
                        batch, queue = queue[:size], queue[size:]
                        pending[pool.submit(self._folders, [b[0] for b in batch])] = dict(batch)
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for fut in done:
                        paths = pending.pop(fut)
                        answered = fut.result()
                        # Never read "no answer" as "this folder is empty": that would look like deletions.
                        got = {f.get("drivewsid") for f in answered
                               if (isinstance(f.get("items"), list) or not f.get("numberOfItems"))
                               and str(f.get("status") or "OK").upper() in ("OK", "SUCCESS")
                               and not f.get("errorCode") and not f.get("error")}
                        if set(paths) - got:
                            raise RuntimeError(Text("iCloud returned an incomplete folder listing"))
                        for folder in answered:
                            base = paths.get(folder.get("drivewsid"), None)
                            if base is None:
                                continue
                            listed, count = folder.get("items"), folder.get("numberOfItems")
                            if isinstance(listed, list) and isinstance(count, int) and len(listed) < count:
                                self.incomplete.add(base)  # fewer items than iCloud says it holds
                            if base == "" and folder.get("docwsid"):
                                root.docwsid = folder["docwsid"]
                                root.zone = folder.get("zone", root.zone)
                                root.etag = folder.get("etag")
                            for it in folder.get("items", []):
                                name = item_name(it)
                                if it.get("hidden") or it.get("isHidden"):
                                    # never shown on the PC, so never deleted with its folder either
                                    self.skipped.add(f"{base}/{name}" if base else name)
                                    continue
                                if "/" in name or "\\" in name:
                                    log.info("Left in iCloud, name has a slash: %r", name)
                                    self.skipped.add(f"{base}/{name}" if base else name)
                                    continue
                                path = str(PurePosixPath(base) / name) if base else name
                                is_dir = str(it.get("type", "")).upper() in ("FOLDER", "APP_LIBRARY")
                                out[path] = DriveItem(
                                    path=path,
                                    drivewsid=it["drivewsid"],
                                    docwsid=it.get("docwsid", ""),
                                    zone=it.get("zone", "com.apple.CloudDocs"),
                                    etag=it.get("etag"),
                                    is_dir=is_dir,
                                    size=_size(it, is_dir),
                                    mtime=_mtime(it.get("dateModified")),
                                )
                                if is_dir and recursive and (want is None or want(path)):
                                    queue.append((it["drivewsid"], path))
                        if on_progress:
                            on_progress(len(out))
            return out
        except Exception as exc:  # noqa: BLE001
            from .engine import Cancelled

            if isinstance(exc, Cancelled):
                raise  # Stop pressed while listing: not a network problem
            raise _translate(exc) from exc

    def download(self, item: DriveItem, dest: Path, on_bytes: Callable[[int], None]) -> None:
        got = 0

        def counted(k: int) -> None:
            nonlocal got
            got += k
            on_bytes(k)

        def attempt() -> None:
            nonlocal got
            if got:
                on_bytes(-got)  # a retry starts over: take back what the failed attempt reported
                got = 0
            self._download_once(item, dest, counted)

        with_retries(attempt)

    def _download_once(self, item: DriveItem, dest: Path, on_bytes: Callable[[int], None]) -> None:
        try:
            with open(dest, "wb") as fh:
                if item.size == 0:
                    return
                d = self.drive
                params = dict(d.params, document_id=item.docwsid)
                resp = d.session.get(f"{d._document_root}/ws/{item.zone}/download/by_id", params=params,
                                     timeout=180)
                d._raise_if_error(resp)
                info = resp.json()
                token = info.get("data_token") or {}
                if not token.get("url"):
                    if (info.get("package_token") or {}).get("url"):
                        raise MacPackage(item.path)
                    raise RuntimeError(Text("iCloud gave no download link"))
                data = d.session.get(token["url"], params=d.params, stream=True, timeout=180)
                d._raise_if_error(data)
                if data.status_code == 206:  # only a part of the file: never taken for the file
                    raise RuntimeError(Text("iCloud sent only a part of the file"))
                for chunk in data.iter_content(1 << 20):
                    fh.write(chunk)
                    on_bytes(len(chunk))
        except MacPackage:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    streams_progress = True  # upload() takes on_bytes
    takes_handle = True  # and the file already opened (locked) by the caller

    def upload(self, parent: DriveItem, local: Path, data: bytes | None = None,
               on_bytes: Callable[[int], None] | None = None, handle: Any = None) -> tuple:
        """Sends the file; returns (size, sha256, etag) of exactly the bytes sent and the version
        iCloud gave them (etag None when its answer didn't say). Without `data` the file is
        streamed from disk: a big video never has to fit in memory. handle: the file already
        opened with _upload_reader (a replacement locks it before the old version is trashed)."""
        try:
            if data is not None:
                fh: Any = io.BytesIO(data)
                fh.name = local.name  # the Drive API takes the file name from the file object
                st = local.stat()
                self.drive.send_file(parent.docwsid, fh, zone=parent.zone, mtime=st.st_mtime, ctime=st.st_mtime)
                return len(data), hashlib.sha256(data).hexdigest(), None
            from contextlib import nullcontext

            from .engine import _upload_reader

            # Other programs may read the file meanwhile but not change it: one version, never a mix
            with (nullcontext(handle) if handle is not None else _upload_reader(local)) as raw:
                raw.seek(0)
                st = os.fstat(raw.fileno())
                named = _Named(raw, local.name)
                d = self.drive
                document_id, content_url = d._get_upload_contentws_url(file_object=named, zone=parent.zone)
                form = _StreamedForm(raw, local.name, st.st_size, on_bytes)
                _big_send_buffer(d.session, content_url)
                # one-shot body: a redirect would replay it empty, so it is never followed. It goes on
                # its own connection, never waiting for (or holding up) the other iCloud requests.
                began = time.monotonic()
                buffer = _adapter_buffer(d.session, content_url)
                resp = _unlocked_post(d.session, content_url, data=form,
                                      headers={"Content-Type": form.content_type},
                                      timeout=(30, 600), allow_redirects=False)
                _log_speed("Drive", st.st_size, time.monotonic() - began, buffer)
                if 300 <= int(getattr(resp, "status_code", 200) or 200) < 400:
                    raise RuntimeError(Text("iCloud redirected the upload; it is tried again later"))
                d._raise_if_error(resp)
                form.check_complete()  # the file shrank while it was sent: never taken as sent
                info = resp.json()["singleFile"]
                if isinstance(info, dict) and "size" in info and int(info["size"]) != st.st_size:
                    raise RuntimeError(Text("iCloud received a different size; it is tried again later"))
                # Stop pressed while iCloud answered, or while this waited for its turn: not registered
                with _before_send(d.session, (lambda: on_bytes(0)) if on_bytes else None):
                    result = d._update_contentws(parent.docwsid, info, document_id, named, parent.zone,
                                                 mtime=st.st_mtime, ctime=st.st_mtime)
                etag = _check_registered(result, local.name)
                return st.st_size, form.digest(), etag
        except Exception as exc:  # noqa: BLE001
            from .engine import Cancelled

            if isinstance(exc, Cancelled):
                raise  # the user stopped it: not a network problem
            raise _translate(exc) from exc

    def mkdir(self, parent: DriveItem, name: str) -> None:
        try:
            resp = self.drive.create_folders(parent.drivewsid, name)
            _check_first(resp, "folders", "folder creation")
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    def rename(self, item: DriveItem, new_name: str) -> str | None:
        """Rename in place; returns the new etag. The FULL name is sent: Apple splits off the
        extension itself, and a name without one would silently drop it (checked live)."""
        try:
            resp = self.drive.rename_items(item.drivewsid, item.etag, new_name)
            first = ((resp or {}).get("items") or [{}])[0]
            _check_item(first, "rename")
            return first.get("etag")
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    def move(self, item: DriveItem, folder: DriveItem) -> str | None:
        """Move into another folder; returns the new etag."""
        try:
            d = self.drive
            resp = d.session.post(d.service_root + "/moveItems", params=d.params, json={
                "destinationDrivewsId": folder.drivewsid,
                "items": [{"drivewsid": item.drivewsid, "etag": item.etag, "clientId": item.drivewsid}],
            }, timeout=120)
            d._raise_if_error(resp)
            first = (resp.json().get("items") or [{}])[0]
            _check_item(first, "move")
            return first.get("etag")
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    def trash(self, item: DriveItem) -> None:
        try:
            resp = self.drive.move_items_to_trash(item.drivewsid, item.etag)
            _check_first(resp, "items", "deletion")
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc


SEND_BUFFER = 0  # fixed send buffer for upload connections in bytes; 0 = Windows sizes it (the setting)


def _big_send_buffer(session: Any, url: str) -> None:
    """Connections to the upload server get the send buffer setting: automatic (Windows sizes it
    while sending; the default, measured fastest) or a fixed size. Only connections to that upload
    host (origin) on this session; a changed setting applies to new connections."""
    from urllib.parse import urlsplit

    mount = getattr(session, "mount", None)
    adapters = getattr(session, "adapters", None)
    if mount is None or adapters is None:
        return
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        return
    prefix = f"https://{parts.netloc}/"
    current = adapters.get(prefix)
    if isinstance(current, _BigSendAdapter) and current.buffer == SEND_BUFFER:
        return
    mount(prefix, _BigSendAdapter())  # (a changed setting: new connections with the new size)


try:
    from requests.adapters import HTTPAdapter as _HTTPAdapter
except ImportError:  # pragma: no cover - requests is always there with pyicloud
    _HTTPAdapter = object  # type: ignore[assignment,misc]


class _BigSendAdapter(_HTTPAdapter):  # type: ignore[misc,valid-type]
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.buffer = SEND_BUFFER  # (before the pool is made: init_poolmanager runs in the base init)
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["socket_options"] = _send_options(self.buffer)
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any) -> Any:
        proxy_kwargs.setdefault("socket_options", _send_options(self.buffer))  # through a proxy too
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def _send_options(size: int | None = None) -> list:
    """urllib3's usual socket options, plus a fixed send buffer unless the size is 0 (automatic:
    Windows sizes it while sending)."""
    import socket

    from urllib3.connection import HTTPConnection

    size = SEND_BUFFER if size is None else size
    options = list(HTTPConnection.default_socket_options)
    if size > 0:
        options.append((socket.SOL_SOCKET, socket.SO_SNDBUF, size))
    return options


class _Named:
    """A file object with only its name (the Drive API reads the name and size from it)."""

    def __init__(self, raw: Any, name: str) -> None:
        self._raw, self.name = raw, name

    def tell(self) -> int:
        return self._raw.tell()

    def seek(self, pos: int, whence: int = 0) -> int:
        return self._raw.seek(pos, whence)


class _StreamedForm:
    """The multipart body requests would build for files={name: file}, read from disk piece by
    piece (requests streams a body that has read() and a length). The bytes sent are hashed on
    the way, so what is recorded is exactly what iCloud got."""

    def __init__(self, raw: Any, name: str, size: int, on_bytes: Callable[[int], None] | None = None) -> None:
        from urllib3.fields import RequestField
        from urllib3.filepost import choose_boundary, encode_multipart_formdata

        field = RequestField(name=name, data=b"", filename=name)
        field.make_multipart(content_type=None)
        boundary = choose_boundary()
        body, self.content_type = encode_multipart_formdata([field], boundary=boundary)
        tail = b"\r\n--" + boundary.encode() + b"--\r\n"
        assert body.endswith(tail)
        self._parts = [body[:-len(tail)], None, tail]  # None: the file itself
        self._raw, self._size, self._left = raw, size, size
        self._raw.seek(0)
        self._hash = hashlib.sha256()
        self._len = len(body) + size
        self._on_bytes = on_bytes

    def __len__(self) -> int:
        return self._len

    def read(self, n: int = -1) -> bytes:
        if n == 0:
            return b""
        n = 1 << 20 if n is None or n < 0 else n
        while self._parts:
            part = self._parts[0]
            if part is None:
                if self._left <= 0:
                    self._parts.pop(0)
                    continue
                chunk = self._raw.read(min(max(n, 1 << 18), self._left))  # (http.client asks for 8-16 KB)
                if not chunk:
                    raise OSError("the file got shorter while it was sent")
                self._left -= len(chunk)
                self._hash.update(chunk)
                if self._on_bytes:
                    self._on_bytes(len(chunk))  # (read as the connection takes it: what is sent)
                return chunk
            if part:
                self._parts[0] = part[n:]
                return part[:n]
            self._parts.pop(0)
        return b""

    def check_complete(self) -> None:
        if self._left or self._raw.read(1):
            raise OSError("the file changed size while it was sent")

    def digest(self) -> str:
        return self._hash.hexdigest()


def _check_registered(result: Any, name: str | None = None) -> str | None:
    """update/documents answers {"status": {...}, "results": [{"status": {"status_code": 0,
    "error_message": ""}, "document": {...}}]}: the new document is the confirmation (its status is
    an object, not the "OK" string of the other Drive calls). Returns the document's version tag."""
    listed = result.get("results") if isinstance(result, dict) else None
    first = listed[0] if isinstance(listed, list) and listed and isinstance(listed[0], dict) else None
    doc = (first or {}).get("document")
    if isinstance(doc, dict):
        given = doc.get("name")
        if name and isinstance(given, str):
            ext = doc.get("extension")
            given = f"{given}.{ext}" if isinstance(ext, str) and ext else given
            if given.lower() != name.lower():
                return None  # saved under another name (the name was taken meanwhile): no version for ours
        etag = doc.get("etag")
        return etag if isinstance(etag, str) and etag else None
    status = (first or (result if isinstance(result, dict) else {})).get("status")
    if isinstance(status, dict) and status.get("status_code"):
        raise RuntimeError(Text(f"iCloud refused the upload: {code(str(status.get('status_code')))}"))
    raise RuntimeError(Text("iCloud didn't confirm the uploaded file; it is tried again later"))


def _adapter_buffer(session: Any, url: str) -> int:
    """The send buffer of the connections this request goes out on (a setting changed meanwhile
    only applies to new ones)."""
    try:
        adapter = session.get_adapter(url)
    except Exception:  # noqa: BLE001
        return SEND_BUFFER
    return getattr(adapter, "buffer", SEND_BUFFER)


def _log_speed(service: str, size: int, seconds: float, buffer: int | None = None) -> None:
    """One line per upload in the log: how fast it went, with which send buffer (to compare)."""
    if size >= 1 << 20 and seconds > 0:
        buffer = SEND_BUFFER if buffer is None else buffer
        buf = f"{buffer >> 20} MB" if buffer > 0 else "automatic"
        log.info("%s: upload of %.1f MB took %.1f s (%.2f MB/s, send buffer %s)",
                 service, size / 1e6, seconds, size / 1e6 / seconds, buf)


def _unlocked_post(session: Any, url: str, **kwargs: Any) -> Any:
    """A file body to a reserved upload URL: past the lock that keeps the other iCloud requests in
    turn (the session's own pipeline still runs), so uploads run side by side and a long one never
    holds up Photos or Notes."""
    if getattr(session, "_skyfold_locked", False):
        return type(session).request(session, "POST", url, **kwargs)
    return session.post(url, **kwargs)


class _before_send:
    """Runs check() right before the next request leaves, once it has its turn on the session
    (Stop pressed while it waited still keeps it from being sent)."""

    def __init__(self, session: Any, check: Callable[[], None] | None) -> None:
        self._local = getattr(session, "_skyfold_before_send", None)
        self._check = check

    def __enter__(self) -> None:
        if self._check is None:
            return
        if self._local is None:
            self._check()
        else:
            self._local.check = self._check

    def __exit__(self, *exc: Any) -> None:
        if self._local is not None:
            self._local.check = None


def _check_first(resp: Any, key: str, what: str) -> None:
    """The answer for a one-item request, when Apple sent one (an empty answer is taken as done)."""
    listed = resp.get(key) if isinstance(resp, dict) else None
    if isinstance(listed, list) and listed and isinstance(listed[0], dict):
        _check_item(listed[0], what)


def _check_item(item: dict[str, Any], what: str) -> None:
    """Apple answers 200 for the whole request and may still refuse the one item: never take that as done."""
    status = str(item.get("status") or "").upper()
    if item.get("errorCode") or item.get("error") or any(w in status for w in ("ERROR", "FAIL", "EXIST", "CONFLICT")):
        # only a short symbolic code is kept: Apple's free text never becomes part of the message
        why = code(item.get("errorCode")) if item.get("errorCode") else code(status)
        raise RuntimeError(Text(f"iCloud refused the {what}: {why}"))


def local_mtime(p: Path) -> float:
    return os.stat(p).st_mtime


def utc_now() -> float:
    return datetime.now(timezone.utc).timestamp()
