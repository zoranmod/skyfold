"""Small iCloud extras: storage usage and Find My (list devices, play a sound)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

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
                log.info("Find My wants a fresh sign-in: %s (cause: %r)", exc, getattr(cause, "__context__", cause))
                api._devices = None
                raise FindMyNeedsSignIn(sign_in_again_text()) from exc
            raise
        if refresh:
            try:
                mgr.refresh(locate=False)
            except Exception as exc:  # noqa: BLE001 - the previous data is still useful
                log.info("Find My refresh failed: %s", exc)
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
