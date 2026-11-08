"""iCloud Drive adapter: lists the whole Drive tree and performs file operations.

iCloud Drive's web service has no change feed, so every sync walks the folder tree.
Folders are fetched in batches (one request per up to 50 folders) to keep that cheap.
"""

from __future__ import annotations

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

log = logging.getLogger(__name__)

ROOT_ID = "FOLDER::com.apple.CloudDocs::root"
BATCH = 50
WORKERS = 6  # requests to Apple at the same time (listing folders, downloading files)


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

    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                        requests.exceptions.ChunkedEncodingError, PyiCloudConnectionException)):
        return True
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


def item_name(data: dict[str, Any]) -> str:
    name = data.get("name") or data.get("drivewsid", "?")
    ext = data.get("extension")
    return f"{name}.{ext}" if ext else name


class ICloudDrive:
    def __init__(self, api: Any) -> None:
        self._api = api
        self._drive: Any = None

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
                               if isinstance(f.get("items"), list) or not f.get("numberOfItems")}
                        if set(paths) - got:
                            raise RuntimeError("iCloud returned an incomplete folder listing")
                        for folder in answered:
                            base = paths.get(folder.get("drivewsid"), None)
                            if base is None:
                                continue
                            if base == "" and folder.get("docwsid"):
                                root.docwsid = folder["docwsid"]
                                root.zone = folder.get("zone", root.zone)
                                root.etag = folder.get("etag")
                            for it in folder.get("items", []):
                                if it.get("hidden") or it.get("isHidden"):
                                    continue
                                name = item_name(it)
                                if "/" in name or "\\" in name:
                                    log.info("Left in iCloud, name has a slash: %r", name)
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
                                    size=int(it.get("size") or 0),
                                    mtime=_mtime(it.get("dateModified")),
                                )
                                if is_dir and recursive and (want is None or want(path)):
                                    queue.append((it["drivewsid"], path))
                        if on_progress:
                            on_progress(len(out))
            return out
        except Exception as exc:  # noqa: BLE001
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
                    raise RuntimeError("iCloud gave no download link")
                data = d.session.get(token["url"], params=d.params, stream=True, timeout=180)
                d._raise_if_error(data)
                for chunk in data.iter_content(1 << 20):
                    fh.write(chunk)
                    on_bytes(len(chunk))
        except MacPackage:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    def upload(self, parent: DriveItem, local: Path, data: bytes | None = None) -> None:
        try:
            if data is None:
                data = local.read_bytes()
            fh = io.BytesIO(data)
            fh.name = local.name  # the Drive API takes the file name from the file object
            st = local.stat()
            self.drive.send_file(parent.docwsid, fh, zone=parent.zone, mtime=st.st_mtime, ctime=st.st_mtime)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    def mkdir(self, parent: DriveItem, name: str) -> None:
        try:
            self.drive.create_folders(parent.drivewsid, name)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    def rename(self, item: DriveItem, new_name: str) -> str | None:
        """Rename in place; returns the new etag. The FULL name is sent: Apple splits off the
        extension itself, and a name without one would silently drop it (checked live)."""
        try:
            resp = self.drive.rename_items(item.drivewsid, item.etag, new_name)
            return ((resp or {}).get("items") or [{}])[0].get("etag")
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
            return (resp.json().get("items") or [{}])[0].get("etag")
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    def trash(self, item: DriveItem) -> None:
        try:
            self.drive.move_items_to_trash(item.drivewsid, item.etag)
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc


def local_mtime(p: Path) -> float:
    return os.stat(p).st_mtime


def utc_now() -> float:
    return datetime.now(timezone.utc).timestamp()
