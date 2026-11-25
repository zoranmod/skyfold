"""Thin adapter over pyicloud's Photos service, so the sync engine can be tested with a fake."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .i18n import t

log = logging.getLogger(__name__)


class AuthRequired(Exception):
    """The iCloud session expired or was never established."""


class RemoteUnavailable(Exception):
    """iCloud Photos cannot be read (e.g. Advanced Data Protection is on)."""


class LookupIncomplete(Exception):
    """A looked-up photo exists in iCloud but couldn't be read completely yet: not an answer about it."""


class UploadRejected(Exception):
    """No asset was registered. Retrying these bytes later is safe."""


@dataclass(frozen=True)
class UploadReceipt:
    asset_id: str | None
    is_duplicate: bool


@dataclass(slots=True)
class RemoteResource:
    key: str  # "original" or "original_video"
    filename: str
    size: int | None
    url: str | None
    is_video: bool


@dataclass(slots=True, frozen=True)
class AssetInfo:
    """What the Photos window shows besides the files: read from iCloud with every listing, kept in
    the sync database, never needed for syncing itself."""

    favorite: bool = False
    added: datetime | None = None  # when it reached iCloud (an old scan added today is "recent")
    width: int | None = None
    height: int | None = None
    duration: float | None = None  # seconds, videos only
    screenshot: bool = False


@dataclass(slots=True)
class RemoteAsset:
    id: str
    filename: str
    date: datetime
    is_video: bool
    resources: list[RemoteResource] = field(default_factory=list)
    info: AssetInfo | None = None


@dataclass(slots=True)
class Changes:
    """What changed in the library since a cursor (CloudKit's zone change feed)."""

    alive: list[RemoteAsset]  # assets added or modified and still present
    gone: set[str]  # asset ids deleted, hidden or expunged
    cursor: str  # position after these changes


class Remote(Protocol):
    def cursor(self) -> str: ...
    def list_assets(self, on_page: Callable[[int], None] | None = None) -> Iterator[RemoteAsset]: ...
    def changes(self, since: str) -> Changes | None: ...
    def lookup(self, asset_ids: list[str]) -> dict[str, RemoteAsset]: ...
    def delete(self, asset_id: str) -> None: ...
    def albums(self) -> list[tuple[str, list[str]]]: ...  # (album path like "Trips/Rome", asset ids)
    def download(self, resource: RemoteResource, dest: Path, on_bytes: Callable[[int], None]) -> None: ...
    def upload(self, path: Path) -> UploadReceipt: ...


_WEB_ACCESS_CHECKS = ("requestWebAccessState", "requestPCS", "enableDeviceConsentForPCS")


def _web_access_check_failed(exc: Exception) -> bool:
    """A server error (5xx) answered to Apple's web-access (PCS) check of the session."""
    code = getattr(exc, "code", None)
    try:
        server_error = int(code) >= 500
    except (TypeError, ValueError):
        server_error = False
    if not server_error:
        return False
    url = str(getattr(getattr(exc, "response", None), "url", "") or "")
    if any(name in url for name in _WEB_ACCESS_CHECKS):
        return True
    tb = exc.__traceback__
    while tb is not None:  # (no response kept: which call failed is seen from where it was raised)
        if tb.tb_frame.f_code.co_name in ("_check_pcs_consent", "_send_pcs_request", "_request_pcs_for_service"):
            return True
        tb = tb.tb_next
    return False


def _translate(exc: Exception) -> Exception:
    """Map pyicloud errors onto the two conditions the UI cares about."""
    from pyicloud import exceptions as ex

    if isinstance(exc, (ex.PyiCloud2FARequiredException, ex.PyiCloud2SARequiredException,
                        ex.PyiCloudAuthRequiredException, ex.PyiCloudFailedLoginException,
                        ex.PyiCloudNoStoredPasswordAvailableException, ex.TokenException)):
        return AuthRequired(str(exc))
    if isinstance(exc, ex.PyiCloudPCSTimeoutException) or "PCS access" in str(exc):
        return RemoteUnavailable(t(
            "Apple asks to allow access to your iCloud data (Advanced Data Protection). Approve the request "
            "on your iPhone, or turn on Settings → [your name] → iCloud → 'Access iCloud Data on the Web'."
        ))
    if isinstance(exc, ex.PyiCloudServiceNotActivatedException):
        return RemoteUnavailable(t(
            "iCloud can't be reached over the web. On your iPhone check: Settings → [your name] → "
            "iCloud → 'Access iCloud Data on the Web' must be on."
        ))
    code = getattr(exc, "code", None)
    if code in (401, 421, 450):
        return AuthRequired(str(exc))
    if _web_access_check_failed(exc):
        # Apple's "may this session read the iCloud data?" check itself fails (seen as HTTP 500 after
        # a sign-in that stopped at the two-factor code). The session looks valid but reads nothing:
        # treated like an expired sign-in - renewed quietly first, then you are asked to sign in.
        return AuthRequired(str(exc))
    text = str(exc)
    if "ZONE_NOT_FOUND" in text or "PRIVATE_DB_DISABLED" in text or "ACCESS_DENIED" in text:
        return RemoteUnavailable(t(
            "Access to iCloud was denied. If 'Advanced Data Protection' is on, also turn on "
            "'Access iCloud Data on the Web'."
        ))
    return exc


def _info(asset: Any) -> AssetInfo | None:
    """Favourite, date added, size, length and screenshot flag of a pyicloud asset. Never fails the
    sync: what can't be read is left empty (and the Photos window shows less about that photo)."""
    try:
        from pyicloud.services.photos_cloudkit.mappers import record_field_value

        rec = asset.asset_record
        added = asset.added_date
        if added is not None and added.tzinfo is None:
            added = added.replace(tzinfo=timezone.utc)
        if added is not None and added.timestamp() <= 0:
            added = None
        width, height = asset.dimensions
        duration = record_field_value(rec, "duration")
        subtype = record_field_value(rec, "assetSubtypeV2")
        return AssetInfo(
            favorite=bool(record_field_value(rec, "isFavorite") or 0),
            added=added,
            width=int(width) if width else None,
            height=int(height) if height else None,
            # Apple keeps a video's length in milliseconds (66282 = 1:06, checked against Windows)
            duration=float(duration) / 1000 if isinstance(duration, (int, float)) and duration > 0 else None,
            screenshot=subtype == 3,  # Apple's subtype for screenshots
        )
    except Exception:  # noqa: BLE001
        log.debug("No extra details for an asset", exc_info=True)
        return None


class ICloudRemote:
    def __init__(self, api: Any) -> None:
        self._api = api
        self._photos: Any = None
        self._assets: dict[str, Any] = {}
        self.hidden: set[str] = set()  # photos last seen hidden in iCloud (not deleted): kept on the PC

    @property
    def photos(self) -> Any:
        if self._photos is None:
            try:
                self._photos = self._api.photos
            except Exception as exc:  # noqa: BLE001
                raise _translate(exc) from exc
        return self._photos

    def cursor(self) -> str:
        try:
            library = self.photos.libraries["root"]
            library.current_sync_token = None  # force a fresh zones/list round trip
            return library.sync_cursor()
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    def list_assets(self, on_page: Callable[[int], None] | None = None) -> Iterator[RemoteAsset]:
        self._assets = {}
        try:
            count = 0
            for asset in self.photos.all.photos:
                count += 1
                if on_page and count % 100 == 0:
                    on_page(count)
                self._assets[asset.id] = asset
                yield self._convert(asset)
        except (AuthRequired, RemoteUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    @staticmethod
    def _convert(asset: Any) -> RemoteAsset:
        is_video = asset.item_type == "movie"
        date = asset.asset_date
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        resources: list[RemoteResource] = []
        res = asset.resources
        primary = res.get("original")
        if primary is not None and primary.url:
            resources.append(RemoteResource("original", primary.filename, primary.size, primary.url, is_video))
        if not is_video and asset.is_live_photo:
            live = res.get("original_video")
            if live is not None and live.url:
                resources.append(RemoteResource("original_video", live.filename, live.size, live.url, True))
        return RemoteAsset(asset.id, asset.filename, date, is_video, resources, _info(asset))

    streams_progress = True  # upload() takes on_bytes

    def upload(self, path: Path, on_bytes: Callable[[int], None] | None = None) -> UploadReceipt:
        """Keep the registration receipt. The high-level helper discards it while waiting for indexing.
        on_bytes is told how much of the file has gone (and may stop it by raising)."""
        try:
            library = self.photos.libraries["root"]
            client = library._client
            zone = library.zone_id["zoneName"]
        except Exception as exc:  # noqa: BLE001
            from .drive_remote import is_transient

            translated = _translate(exc)
            if isinstance(translated, (AuthRequired, RemoteUnavailable)):
                raise translated from exc
            if is_transient(exc):  # (finding the library failed on the way: nothing about the file)
                raise RemoteUnavailable(t("iCloud Photos can't be reached right now")) from exc
            raise UploadRejected(type(exc).__name__) from exc
        uploader = getattr(client, "_uploader", None) if client is not None else None
        if client is None or (uploader is None and hasattr(client, "_uploader")):
            # This account's service list has no upload service right now: nothing was sent, and
            # nothing about the files is decided (they go when the service is back).
            raise UploadsUnavailable(t("Uploads to iCloud Photos aren't available for this account right now"))
        real_session = getattr(uploader, "_session", None)
        counting: _CountingSession | None = None
        try:
            if real_session is not None:
                # This upload's own copy of the uploader (it holds only its settings), with its own
                # counting session: uploads running side by side never share or swap one.
                import copy

                own = copy.copy(uploader)
                own._session = counting = _CountingSession(real_session, on_bytes)
                result = own.upload(str(path), zone_name=zone)
            else:
                result = client.upload_file(str(path), zone_name=zone)
        except Exception as exc:
            from .engine import Cancelled

            if isinstance(exc, Cancelled):
                raise  # stopped by the user while sending: nothing was registered
            translated = _translate(exc)
            if isinstance(translated, (AuthRequired, RemoteUnavailable)):
                raise translated from exc
            payload = getattr(exc, "payload", None)
            response = payload.get("response") if isinstance(payload, dict) else None
            status = response.get("status") if isinstance(response, dict) else None
            if status in (401, 421, 450):
                raise AuthRequired(str(status)) from exc
            if counting is not None:
                from .drive_remote import is_transient

                if not counting.registering:
                    # Nothing was registered: known not saved. A broken connection stops the batch (tried
                    # again next pass); anything else about this file waits its usual retry time.
                    if is_transient(exc):
                        raise RemoteUnavailable(t("iCloud Photos can't be reached right now")) from exc
                    raise UploadRejected(type(exc).__name__) from exc
                try:
                    http = int(getattr(exc, "code", None) or 0)
                except (TypeError, ValueError):
                    http = 0
                if _never_connected(exc) or http in (403, 408, 429, 503):
                    # the registration never reached iCloud, or was refused as a whole (no access,
                    # timed out waiting for it, busy): not saved; the batch waits for the next pass
                    raise RemoteUnavailable(t("iCloud Photos is busy; uploads continue later")) from exc
                if 400 <= http < 500 and http != 409:
                    raise UploadRejected(str(http)) from exc  # the request was refused: not saved
                if isinstance(status, int) and status >= 400 and status != 409:
                    raise UploadRejected(str(status)) from exc  # refused for this file: not saved
                raise  # registration sent and its answer lost: the photo may be there
            # In the pinned uploader these stages precede putAsset. A lost putAsset
            # reply, in contrast, can follow a successful registration.
            from pyicloud.services.photos_cloudkit.upload import PhotosUploader
            tb = exc.__traceback__
            while tb:
                if tb.tb_frame.f_code in (PhotosUploader.create_upload_url.__code__, PhotosUploader.send_bytes.__code__):
                    raise UploadRejected(type(exc).__name__) from exc
                tb = tb.tb_next
            if isinstance(status, int) and 400 <= status < 500 and status != 409:
                raise UploadRejected(str(status)) from exc
            raise
        return UploadReceipt(result.cplAsset, result.is_duplicate)

    def albums(self) -> list[tuple[str, list[str]]]:
        """User-made albums (not smart albums like Favorites, not album folders) and their photos."""
        from pyicloud.services.photos_cloudkit.service import PhotoAlbumFolder, SmartPhotoAlbum

        try:
            out: list[tuple[str, list[str]]] = []
            for album in self.photos.libraries["root"].refresh_albums():
                if isinstance(album, (SmartPhotoAlbum, PhotoAlbumFolder)):
                    continue
                name = getattr(album, "fullname", None) or album.name
                out.append((str(name), [a.id for a in album.photos]))
            return out
        except (AuthRequired, RemoteUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    # incremental ---------------------------------------------------------------
    @staticmethod
    def _gone(rec: Any) -> bool:
        from pyicloud.services.photos_cloudkit.mappers import record_field_value

        if getattr(rec, "deleted", False):
            return True
        return any(record_field_value(rec, f) == 1 for f in ("isDeleted", "isHidden", "isExpunged"))

    def _note_hidden(self, rec: Any) -> None:
        """Remember whether this photo is only hidden in iCloud (still there, in the Hidden album)."""
        from pyicloud.services.photos_cloudkit.mappers import record_field_value

        hidden = (not getattr(rec, "deleted", False) and record_field_value(rec, "isHidden") == 1
                  and not any(record_field_value(rec, f) == 1 for f in ("isDeleted", "isExpunged")))
        (self.hidden.add if hidden else self.hidden.discard)(rec.recordName)

    def _build(self, asset_recs: dict[str, Any], masters: dict[str, Any]) -> list[RemoteAsset]:
        """Pair CPLAsset records with their CPLMaster (fetching missing masters) into assets."""
        from pyicloud.common.cloudkit import CKRecord, CKZoneIDReq
        from pyicloud.services.photos_cloudkit.service import PhotoAsset

        lib = self.photos.libraries["root"]

        def master_of(rec: Any) -> str | None:
            ref = rec.fields.get_value("masterRef")
            return getattr(ref, "recordName", None)

        missing = sorted({m for r in asset_recs.values() if (m := master_of(r)) and m not in masters})
        for i in range(0, len(missing), 100):
            resp = self.photos.private_client.lookup(
                record_names=missing[i:i + 100], zone_id=CKZoneIDReq(**lib.zone_id)
            )
            for rec in resp.records:
                if isinstance(rec, CKRecord) and rec.recordType == "CPLMaster":
                    masters[rec.recordName] = rec
        out: list[RemoteAsset] = []
        for name, rec in asset_recs.items():
            master = masters.get(master_of(rec) or "")
            if master is None:
                continue
            asset = PhotoAsset(self.photos, master, rec, library=lib)
            self._assets[name] = asset
            out.append(self._convert(asset))
        return out

    def changes(self, since: str) -> Changes | None:
        """Read only what changed since `since`. None means: fall back to a full scan."""
        from pyicloud.common.cloudkit import CKRecord, CKTombstoneRecord, CKZoneChangesZoneReq, CKZoneID

        try:
            lib = self.photos.libraries["root"]
            req = CKZoneChangesZoneReq(zoneID=CKZoneID(**lib.zone_id), syncToken=since, reverse=False)
            assets: dict[str, Any] = {}
            masters: dict[str, Any] = {}
            gone: set[str] = set()
            cursor = since
            for zone in self.photos.private_client.iter_changes(zone_req=req):
                cursor = zone.syncToken or cursor
                for rec in zone.records:
                    if isinstance(rec, CKTombstoneRecord):
                        gone.add(rec.recordName)
                        assets.pop(rec.recordName, None)
                    elif isinstance(rec, CKRecord):
                        if rec.recordType == "CPLAsset":
                            self._note_hidden(rec)
                            if self._gone(rec):
                                gone.add(rec.recordName)
                                assets.pop(rec.recordName, None)
                            else:
                                gone.discard(rec.recordName)
                                assets[rec.recordName] = rec
                        elif rec.recordType == "CPLMaster":
                            masters[rec.recordName] = rec
            return Changes(self._build(assets, masters), gone, cursor)
        except (AuthRequired, RemoteUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001 - e.g. expired change token
            translated = _translate(exc)
            if isinstance(translated, (AuthRequired, RemoteUnavailable)):
                raise translated from exc
            log.warning("Change feed unavailable, falling back to full scan: %s", exc)
            return None

    def lookup(self, asset_ids: list[str]) -> dict[str, RemoteAsset]:
        """Fetch current records for specific assets; ids missing from the result are gone."""
        from pyicloud.common.cloudkit import CKRecord, CKZoneIDReq

        try:
            lib = self.photos.libraries["root"]
            recs: dict[str, Any] = {}
            for i in range(0, len(asset_ids), 100):
                resp = self.photos.private_client.lookup(
                    record_names=asset_ids[i:i + 100], zone_id=CKZoneIDReq(**lib.zone_id)
                )
                for rec in resp.records:
                    if isinstance(rec, CKRecord) and rec.recordType == "CPLAsset":
                        self._note_hidden(rec)
                        if not self._gone(rec):
                            recs[rec.recordName] = rec
            built = {a.id: a for a in self._build(recs, {})}
        except (AuthRequired, RemoteUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc
        if set(recs) - set(built):
            # a live photo whose original isn't ready in iCloud yet (just uploaded): not gone
            raise LookupIncomplete(f"{len(set(recs) - set(built))} photos aren't complete in iCloud yet")
        return built

    def delete(self, asset_id: str) -> None:
        asset = self._assets.get(asset_id)
        if asset is None:
            raise KeyError(asset_id)
        try:
            asset.delete()
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc
        self._assets.pop(asset_id, None)

    def set_favorite(self, asset_id: str, value: bool) -> None:
        """Mark or unmark a photo as a favourite in iCloud (as the heart on the iPhone does). KeyError
        if iCloud no longer has it."""
        asset = self._assets.get(asset_id)
        if asset is None:
            self.lookup([asset_id])  # (also keeps the record it finds)
            asset = self._assets.get(asset_id)
        if asset is None:
            raise KeyError(asset_id)
        try:
            asset.set_favorite(bool(value))
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    def download(self, resource: RemoteResource, dest: Path, on_bytes: Callable[[int], None]) -> None:
        if not resource.url:
            raise ValueError("resource has no download URL")
        try:
            client = self.photos.private_client._client  # typed CloudKit client
            stream = client.download_asset_stream(resource.url, chunk_size=1 << 20)
        except AttributeError:
            stream = None
        try:
            with open(dest, "wb") as fh:
                if stream is not None:
                    for chunk in stream:
                        fh.write(chunk)
                        on_bytes(len(chunk))
                else:
                    resp = self.photos.session.get(resource.url, stream=True, timeout=120)
                    resp.raise_for_status()
                    if resp.status_code == 206:  # only a part of the file: never taken for the file
                        raise ValueError("partial content")
                    for chunk in resp.iter_content(1 << 20):
                        fh.write(chunk)
                        on_bytes(len(chunk))
        except (AuthRequired, RemoteUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc


class UploadsUnavailable(RemoteUnavailable):
    """The account offers no Photos upload service right now (a read-only service list)."""


def _never_connected(exc: BaseException) -> bool:
    """The request provably never reached the server: the connection itself couldn't be made.
    (A timeout or break while waiting for the answer is not that: the server may have it.)"""
    import requests

    try:
        from urllib3.exceptions import NameResolutionError, NewConnectionError
    except ImportError:  # pragma: no cover
        NameResolutionError = NewConnectionError = ()  # type: ignore[assignment,misc]
    cur: BaseException | None = exc
    for _ in range(8):
        if cur is None:
            return False
        if isinstance(cur, (requests.exceptions.ConnectTimeout, NewConnectionError, NameResolutionError)):
            return True
        reason = getattr(cur, "reason", None)  # (urllib3's MaxRetryError keeps the cause here)
        if isinstance(reason, (NewConnectionError, NameResolutionError)):
            return True
        nested = cur.args[0] if getattr(cur, "args", None) and isinstance(cur.args[0], BaseException) else None
        cur = cur.__cause__ or cur.__context__ or nested
    return False


class _CountingReader:
    """The open photo as pyicloud hands it over, telling how much has been read (= sent)."""

    def __init__(self, raw: Any, on_bytes: Callable[[int], None] | None) -> None:
        import os

        self._raw, self._on_bytes = raw, on_bytes
        self._len = os.fstat(raw.fileno()).st_size - raw.tell()
        self.read_total = 0

    def __len__(self) -> int:
        return self._len

    def read(self, n: int = -1) -> bytes:
        if n == 0:
            return b""
        chunk = self._raw.read(n if n is None or n < 0 else max(n, 1 << 18))
        self.read_total += len(chunk)
        if chunk and self._on_bytes:
            self._on_bytes(len(chunk))
        return chunk


class _CountingSession:
    """pyicloud's session for one Photos upload: the file's bytes are counted on their way and the
    upload server's connections get the send buffer setting; everything else goes to the real session."""

    def __init__(self, real: Any, on_bytes: Callable[[int], None] | None) -> None:
        self._real, self._on_bytes = real, on_bytes
        self.registering = False  # True once the registration request has been handed over

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def post(self, *args: Any, **kwargs: Any) -> Any:
        from .drive_remote import _before_send, _big_send_buffer, _unlocked_post

        data = kwargs.get("data")
        if data is not None and hasattr(data, "read") and hasattr(data, "fileno"):
            reader = _CountingReader(data, self._on_bytes)
            kwargs["data"] = reader
            kwargs["allow_redirects"] = False  # one-shot body: a redirect would replay it empty
            url = str(kwargs.pop("url", None) or (args[0] if args else ""))
            _big_send_buffer(self._real, url)
            # its own connection: never waits for (or holds up) the other iCloud requests
            import time

            from .drive_remote import _adapter_buffer, _log_speed

            began = time.monotonic()
            buffer = _adapter_buffer(self._real, url)
            response = _unlocked_post(self._real, url, **kwargs)
            _log_speed("Photos", reader.read_total, time.monotonic() - began, buffer)
            if 300 <= int(getattr(response, "status_code", 200) or 200) < 400:
                raise RuntimeError("iCloud redirected the upload")
            try:
                told = response.json().get("singleFile", {}).get("size")
            except Exception:  # noqa: BLE001 - pyicloud reads (and judges) the reply itself
                told = None
            if told is not None and int(told) != reader.read_total:
                raise RuntimeError("iCloud received a different size")  # never registered: tried again
            self._sent = True
            if self._on_bytes:
                self._on_bytes(0)  # Stop pressed while iCloud was answering: the photo isn't registered
            return response
        if getattr(self, "_sent", False):
            # registering what was sent: once it has its turn, Stop is checked one last time
            def last_check() -> None:
                if self._on_bytes:
                    self._on_bytes(0)
                self.registering = True  # from here on, iCloud may have it

            with _before_send(self._real, last_check):
                return self._real.post(*args, **kwargs)
        return self._real.post(*args, **kwargs)
