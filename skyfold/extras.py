"""Small iCloud extras: storage usage and Find My (list devices, play a sound)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .auth import _describe
from .i18n import t
from .remote import AuthRequired, RemoteUnavailable, _translate

log = logging.getLogger(__name__)


def _call(fn):
    try:
        return fn()
    except (AuthRequired, RemoteUnavailable, FindMyNeedsSignIn):
        raise
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


def storage(api: Any) -> dict[str, Any]:
    """{"used": bytes, "total": bytes, "parts": [(label, bytes, key)]} for the iCloud plan.
    Keys seen: photos, backup, docs (iCloud Drive and app documents), mail, messages."""
    def run() -> dict[str, Any]:
        st = api.account.storage
        usage = st.usage
        parts = [(m.label, int(m.usage_in_bytes), str(key)) for key, m in st.usages_by_media.items()
                 if m.usage_in_bytes]
        return {"used": int(usage.used_storage_in_bytes), "total": int(usage.total_storage_in_bytes),
                "parts": sorted(parts, key=lambda p: -p[1])}
    return _call(run)


PHOTO_MAX = 8 * 1024 * 1024


def _card_photo_url(raw: Any) -> str | None:
    contacts = raw.get("contacts") if isinstance(raw, dict) else None
    contact = contacts[0] if isinstance(contacts, list) and contacts and isinstance(contacts[0], dict) else {}
    photo = contact.get("photo")
    url = photo.get("url") if isinstance(photo, dict) else None
    return url if isinstance(url, str) and url.startswith("https://") else None


def account_photo(api: Any) -> bytes | None:
    """The picture on the user's own card in Contacts ("My Card"), as iCloud.com shows it, or None
    when the card has none. Read-only, a few small requests.

    Apple answers the card only with the parameters its own Contacts web app sends (pyicloud's
    plain request got 400), and on some accounts only after the app's start-up request, whose
    tokens it then passes along: tried in that order, stopping at the first answer."""
    def get(url: str, params: dict) -> Any:
        try:
            r = api.session.get(url, params=params, timeout=30)
        except Exception as exc:  # noqa: BLE001 - pyicloud raises on Apple's error answers
            code = getattr(exc, "code", None) or getattr(getattr(exc, "response", None), "status_code", None)
            tried.append(str(code or exc.__class__.__name__))
            return None
        tried.append(str(r.status_code))
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def run() -> bytes | None:
        root = api.get_webservice_url("contacts") + "/co"
        params = dict(api.params)
        params.update({"clientVersion": "2.1", "locale": "en_US", "order": "last,first"})
        raw = get(f"{root}/mecard/", params) or get(f"{root}/mecard", params)
        if raw is None:
            start = get(f"{root}/startup", params)
            if isinstance(start, dict) and start.get("prefToken") is not None:
                params.update({"prefToken": start.get("prefToken"), "syncToken": start.get("syncToken")})
                raw = get(f"{root}/mecard/", params)
        if raw is None:
            log.info("Account picture: no card (answers %s)", ", ".join(tried))
            return None
        url = _card_photo_url(raw)
        if url is None:
            return None  # a card without a photo
        r = api.session.get(url, timeout=30, stream=True)
        if r.status_code != 200:
            log.info("Account picture: photo not downloaded (%s)", r.status_code)
            return None
        kind = str(getattr(r, "headers", {}).get("Content-Type", "image/")).lower()
        if not kind.startswith("image/"):
            return None  # (only a picture is ever kept)
        data = bytearray()
        for chunk in r.iter_content(64 * 1024):  # read at most PHOTO_MAX: a huge answer is never held
            data += chunk
            if len(data) > PHOTO_MAX:
                return None
        return bytes(data) or None

    tried: list[str] = []
    return _call(run)


@dataclass
class Device:
    id: str
    name: str
    model: str
    battery: float | None  # 0..1
    charging: bool
    online: bool
    lat: float | None
    lon: float | None
    located_at: str | None  # ISO time of the last location fix
    can_sound: bool


class FindMyNeedsSignIn(Exception):
    """Apple wants the password again before it shows the devices (a sign-in restored from the
    saved session isn't always enough for Find My). The password is never stored, so the user
    signs in once more."""


def sign_in_again_text() -> str:
    return t("After a restart, Apple asks for your password once more before it shows your devices. "
             "Photos, Drive and Notes keep syncing without it, and the password still isn't stored.")


def devices(api: Any, refresh: bool = False) -> list[Device]:
    """Devices on the account (and family, if shared) with their last reported location.

    We deliberately don't ask Apple to locate the devices again: while a fresh fix is in
    progress Apple returns the devices without battery and online status. The devices report
    their location regularly on their own, so the last known one is usually minutes old.
    """
    def run() -> list[Device]:
        try:
            mgr = api.devices
        except Exception as exc:  # noqa: BLE001
            from pyicloud.exceptions import PyiCloudAuthRequiredException, PyiCloudFailedLoginException

            chain = [exc, exc.__cause__, exc.__context__]
            if any(isinstance(e, (PyiCloudFailedLoginException, PyiCloudAuthRequiredException))
                   or getattr(e, "code", None) in (401, 421, 450) for e in chain):
                cause = exc.__cause__ or exc.__context__
                log.info("Find My wants a fresh sign-in: %s", _describe(exc))
                api._devices = None
                raise FindMyNeedsSignIn(sign_in_again_text()) from exc
            raise
        if refresh:
            try:
                mgr.refresh(locate=False)
            except Exception as exc:  # noqa: BLE001 - the previous data is still useful
                log.info("Find My refresh failed: %s", _describe(exc))
        out: list[Device] = []
        for dev_id, dev in mgr.devices.items():
            data = dev.data
            loc = None
            try:
                loc = dev.location
            except Exception:  # noqa: BLE001
                loc = None
            ts = None
            if loc and loc.get("timeStamp"):
                ts = datetime.fromtimestamp(loc["timeStamp"] / 1000, timezone.utc).isoformat()
            battery = data.get("batteryLevel")
            out.append(Device(
                id=str(dev_id),
                name=str(data.get("name") or dev.name),
                model=str(data.get("deviceDisplayName") or getattr(dev, "model_name", "")),
                battery=float(battery) if isinstance(battery, (int, float)) and battery > 0 else None,
                charging=str(data.get("batteryStatus", "")).lower() == "charging",
                online=str(data.get("deviceStatus")) == "200",
                lat=loc.get("latitude") if loc else None,
                lon=loc.get("longitude") if loc else None,
                located_at=ts,
                can_sound=bool(getattr(dev, "sound_available", True)),
            ))
        return sorted(out, key=lambda d: (not d.online, d.name.lower()))
    return _call(run)


def play_sound(api: Any, device_id: str) -> None:
    def run() -> None:
        dev = api.devices.devices[device_id]
        dev.play_sound(subject="Skyfold")
    _call(run)
