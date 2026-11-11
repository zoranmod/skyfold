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


@dataclass(slots=True)
class RemoteResource:
    key: str  # "original" or "original_video"
    filename: str
    size: int | None
    url: str | None
    is_video: bool


@dataclass(slots=True)
class RemoteAsset:
    id: str
    filename: str
    date: datetime
    is_video: bool
    resources: list[RemoteResource] = field(default_factory=list)


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
    text = str(exc)
    if "ZONE_NOT_FOUND" in text or "PRIVATE_DB_DISABLED" in text or "ACCESS_DENIED" in text:
        return RemoteUnavailable(t(
            "Access to iCloud was denied. If 'Advanced Data Protection' is on, also turn on "
            "'Access iCloud Data on the Web'."
        ))
    return exc


class ICloudRemote:
    def __init__(self, api: Any) -> None:
        self._api = api
        self._photos: Any = None
        self._assets: dict[str, Any] = {}

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
        return RemoteAsset(asset.id, asset.filename, date, is_video, resources)

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
                    if isinstance(rec, CKRecord) and rec.recordType == "CPLAsset" and not self._gone(rec):
                        recs[rec.recordName] = rec
            return {a.id: a for a in self._build(recs, {})}
        except (AuthRequired, RemoteUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc

    def delete(self, asset_id: str) -> None:
        asset = self._assets.get(asset_id)
        if asset is None:
            raise KeyError(asset_id)
        try:
            asset.delete()
        except Exception as exc:  # noqa: BLE001
            raise _translate(exc) from exc
        self._assets.pop(asset_id, None)

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
