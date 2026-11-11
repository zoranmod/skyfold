"""Apple ID sign-in. The password is never stored; only Apple's session cookies are kept, in %LOCALAPPDATA%/Skyfold/session."""

from __future__ import annotations

import logging
import shutil
import threading
from dataclasses import dataclass
from typing import Any

from .config import PRIVATE_DIR, SESSION_DIR

SIGNED_OUT = PRIVATE_DIR / "signed-out"  # set by Sign out; a session left behind is then never used
from .i18n import t
from .safelog import value

log = logging.getLogger(__name__)


class LoginError(Exception):
    pass


class Offline(Exception):
    """Apple could not be reached; the saved session may still be fine."""


_SAVE_LOCK = threading.RLock()  # also guards _CURRENT: checked and saved in one step
_CURRENT: Any = None  # the PyiCloudSession whose state belongs on disk (None: any)
TIMEOUT = (20, 300)  # connect, read (per chunk): a stalled connection must not hang a thread forever


def make_current(api: Any) -> None:
    """This sign-in is the one in use: from now on only its session is saved to disk. An older
    session object that is still finishing a request must not overwrite the new token with its
    old one (the next start would then restore a stale sign-in)."""
    global _CURRENT
    session = getattr(api, "session", None)
    with _SAVE_LOCK:
        _CURRENT = session
        if session is not None:
            try:
                session._save_session_data()
            except Exception as exc:  # noqa: BLE001
                log.warning("Couldn't save the session: %s", _describe(exc))


_DELIVERY = ("sms", "voice", "trusted_device", "device", "security_key", "unknown")  # known ways a code is sent


def _describe(exc: BaseException) -> str:
    """What went wrong, safe for the log and the window (see safelog)."""
    from .safelog import describe

    return describe(exc)


def _server_code(code: Any) -> bool:
    """429 or 5xx, also when Apple sends the number as text ("503")."""
    try:
        n = int(str(code).strip())
    except (TypeError, ValueError):
        return False
    return n == 429 or 500 <= n <= 599


def _trouble_in(exc: BaseException) -> bool:
    from pyicloud.exceptions import PyiCloudConnectionException
    from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout

    if isinstance(exc, (PyiCloudConnectionException, RequestsConnectionError, Timeout)):
        return True
    return _server_code(getattr(exc, "code", None)) or \
        _server_code(getattr(getattr(exc, "response", None), "status_code", None))


def _had_trouble(api: Any) -> bool:
    return bool(getattr(getattr(api, "session", None), "_skyfold_trouble", False))


def _verification_page(method: str, url: str, exc: BaseException) -> Any:
    """After the password, pyicloud asks Apple for the two-factor page (GET .../appleauth/auth as
    HTML). Apple may answer it with 409 "verification needed" together with the page itself; pyicloud
    would raise on that 409 and the sign-in stops. Hand pyicloud the page it asked for instead."""
    from urllib.parse import urlparse

    from pyicloud.exceptions import PyiCloudAPIResponseException

    response = getattr(exc, "response", None)
    if (not isinstance(exc, PyiCloudAPIResponseException) or str(method).upper() != "GET"
            or response is None or getattr(response, "status_code", None) != 409
            or not urlparse(url).path.rstrip("/").endswith("/appleauth/auth")
            or "html" not in str(response.headers.get("Content-Type", "")).lower()):
        return None
    log.info("Sign-in: Apple sent the two-factor page with status 409; continuing with it")
    return response


def _serialize_session_saves() -> None:
    """pyicloud rewrites the session files after every request. Drive downloads run several
    requests at once, so those writes must take turns or the saved sign-in could be corrupted."""
    from pyicloud.session import PyiCloudSession

    original = PyiCloudSession._save_session_data
    if getattr(original, "_skyfold_locked", False):
        return

    def save(self: Any) -> None:
        import os

        with _SAVE_LOCK:  # checked under the same lock that sign-in and sign-out change it with
            if _CURRENT is not None and self is not _CURRENT:
                return  # a replaced sign-in: never write its old token over the new one
            folder = getattr(self, "_cookie_directory", None)
            if folder and not os.path.isdir(folder):
                return  # signed out meanwhile: don't bring the session back
            original(self)

    save._skyfold_locked = True  # type: ignore[attr-defined]
    PyiCloudSession._save_session_data = save  # type: ignore[method-assign]

    original_request = PyiCloudSession._request

    def request(self: Any, method: str, url: str, **kwargs: Any) -> Any:
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = TIMEOUT
        try:
            response = original_request(self, method, url, **kwargs)
        except Exception as exc:  # noqa: BLE001
            page = _verification_page(method, url, exc)
            if page is None:
                if _trouble_in(exc):
                    self._skyfold_trouble = True  # pyicloud may swallow it and fall back to a password
                raise
            return page
        if _server_code(getattr(response, "status_code", None)):
            self._skyfold_trouble = True
        return response

    PyiCloudSession._request = request  # type: ignore[method-assign]

    original_raw = PyiCloudSession._request_raw

    def request_raw(self: Any, method: str, url: str, **kwargs: Any) -> Any:
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = TIMEOUT
        return original_raw(self, method, url, **kwargs)

    PyiCloudSession._request_raw = request_raw  # type: ignore[method-assign]


@dataclass
class LoginStep:
    api: Any
    needs_code: bool
    notice: str = ""


def restore(apple_id: str) -> Any | None:
    """Reuse the saved session. Returns None when the user has to sign in again."""
    from pyicloud import PyiCloudService

    if not apple_id or not SESSION_DIR.exists() or SIGNED_OUT.exists():
        return None
    from pyicloud.exceptions import PyiCloudConnectionException
    from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout

    api = None
    try:
        # authenticate=False skips pyicloud's Windows Credential Manager lookup (we never store
        # the password there); the saved session token is then validated explicitly.
        _serialize_session_saves()
        api = PyiCloudService(apple_id, None, cookie_directory=str(SESSION_DIR), authenticate=False,
                              refresh_interval=None)  # no background Find My polling
        api.authenticate()
    except (PyiCloudConnectionException, RequestsConnectionError, Timeout) as exc:
        raise Offline(_describe(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - anything else means "sign in again"
        # pyicloud falls back to a password login when validating the token fails, and a 5xx there
        # then ends as "No password set": an outage seen on the way is still an outage
        if _server_trouble(exc) or _had_trouble(api):
            log.warning("Session restore: Apple's servers answered with an error (%s); trying again later",
                        _describe(exc))
            raise Offline(_describe(exc)) from exc
        log.warning("Session restore failed: %s", _describe(exc))
        return None
    if (api.requires_2fa or api.requires_2sa) and _had_trouble(api):
        log.warning("Session restore: Apple's servers had trouble; trying again later")
        raise Offline("Apple's servers answered with an error")
    if api.requires_2fa or api.requires_2sa:
        log.warning("Session restore: Apple asks for two-factor authentication again (trusted=%s)",
                    value(getattr(api, "is_trusted_session", None)))
        return None
    make_current(api)
    return api


def _server_trouble(exc: BaseException) -> bool:
    """An Apple 5xx or a connection problem anywhere in the exception chain."""
    from pyicloud.exceptions import PyiCloudAPIResponseException, PyiCloudConnectionException
    from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout

    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, (PyiCloudConnectionException, RequestsConnectionError, Timeout)):
            return True
        code = getattr(e, "code", None)
        if isinstance(e, PyiCloudAPIResponseException) and _server_code(code):
            return True
        e = e.__cause__ or e.__context__
    return False


def start_login(apple_id: str, password: str) -> LoginStep:
    from pyicloud import PyiCloudService
    from pyicloud import exceptions as ex

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Sign-in started")
    try:
        _serialize_session_saves()
        api = PyiCloudService(apple_id, password, cookie_directory=str(SESSION_DIR), authenticate=False,
                              refresh_interval=None)
        # Always check the password with Apple. pyicloud would otherwise reuse the saved session
        # token (even with force_refresh) and never look at the password: a wrong one "succeeded",
        # and services that want a fresh sign-in (Find My after a restart) kept refusing. The trust
        # token stays, so a trusted PC isn't asked for a 2FA code again.
        api.session.data.pop("session_token", None)
        api.authenticate(force_refresh=True)
    except ex.PyiCloudAcceptTermsException as exc:
        log.warning("Sign-in: Apple's new terms must be accepted first")
        raise LoginError(t("Apple has updated its terms. Accept them on your iPhone (Settings) or at "
                           "icloud.com, then sign in here again.")) from exc
    except ex.PyiCloudAccountLockedException as exc:
        log.warning("Sign-in: account locked")
        raise LoginError(t("This Apple account is locked. Unlock it at iforgot.apple.com.")) from exc
    except ex.PyiCloudFailedLoginException as exc:
        if _server_trouble(exc):  # an Apple outage, not a wrong password
            log.warning("Sign-in: Apple's servers failed: %s", _describe(exc))
            raise LoginError(t("Can't reach Apple's servers. Check your internet connection.")) from exc
        log.warning("Sign-in rejected: %s", _describe(exc))
        raise LoginError(t("Wrong Apple ID or password.")) from exc
    except ex.PyiCloudConnectionException as exc:
        log.warning("Sign-in: Apple unreachable: %s", _describe(exc))
        raise LoginError(t("Can't reach Apple's servers. Check your internet connection.")) from exc
    except Exception as exc:  # noqa: BLE001
        log.warning("Sign-in failed: %s", _describe(exc))  # no traceback text: Apple's page could be in it
        raise LoginError(t("Sign-in failed: {error}", error=_describe(exc).split(" at ")[0])) from None

    log.info("Sign-in: password accepted (2FA required=%s, 2SA=%s, trusted=%s)",
             value(api.requires_2fa), value(api.requires_2sa), value(getattr(api, "is_trusted_session", None)))
    if api.requires_2sa and not api.requires_2fa:
        raise LoginError(t("This account uses the old two-step verification, which isn't supported."))
    if not api.requires_2fa:
        return LoginStep(api, needs_code=False)

    # A security key is a property of the Apple account (not of USB devices plugged into this PC).
    if getattr(api, "security_key_names", None):
        log.warning("Sign-in: account uses security keys")
        raise LoginError(t("This account requires a security key (FIDO), which isn't supported yet."))
    notice = t("Enter the 6-digit code shown on your iPhone, iPad or Mac.")
    try:
        sent = api.request_2fa_code()
        log.info("Sign-in: code requested (sent=%s, via %s)", value(sent),
                 value(getattr(api, "two_factor_delivery_method", None), _DELIVERY))
        if not sent:
            # Apple usually shows the code on trusted devices on its own; ask for it anyway.
            api.use_existing_trusted_device_code()
        elif getattr(api, "two_factor_delivery_method", "") == "sms":
            notice = t("We sent a code by SMS to your phone number. Enter it below.")
    except Exception as exc:  # noqa: BLE001 - the code may still arrive on a trusted device
        log.warning("Sign-in: requesting the code failed: %s", _describe(exc))
        try:
            api.use_existing_trusted_device_code()
        except Exception:  # noqa: BLE001
            pass
    return LoginStep(api, needs_code=True, notice=notice)


def submit_code(api: Any, code: str) -> None:
    from pyicloud import exceptions as ex

    try:
        ok = api.validate_2fa_code(code.strip().replace(" ", ""))
    except ex.PyiCloudException as exc:
        log.warning("Sign-in: code check failed: %s", _describe(exc))
        raise LoginError(t("Code check failed: {error}", error=_describe(exc).split(" at ")[0])) from None
    log.info("Sign-in: code accepted=%s", value(ok))
    if not ok:
        raise LoginError(t("That code isn't right. Try again."))
    if not api.is_trusted_session and not api.trust_session():
        log.warning("Sign-in: Apple didn't mark this PC as trusted; the code may be asked again after a restart")


def commit_login(api: Any) -> None:
    """The user's sign-in is complete and accepted: it becomes the one in use."""
    with _SAVE_LOCK:
        SIGNED_OUT.unlink(missing_ok=True)
        make_current(api)
        _remove_other_sessions(api)


def _remove_other_sessions(api: Any) -> None:
    """Only the session in use stays on disk (an earlier Apple ID's token never lingers)."""
    from pathlib import Path

    session = getattr(api, "session", None)
    keep = set()
    for attr in ("session_path", "cookiejar_path"):
        value = getattr(session, attr, None) or getattr(api, attr, None)
        if value:
            keep.add(Path(value).name.lower())
    if not keep or not SESSION_DIR.exists():
        return
    for p in SESSION_DIR.iterdir():
        if p.is_file() and p.name.lower() not in keep:
            try:
                p.unlink()
            except OSError as exc:
                log.warning("Couldn't remove an old session file: %s", exc)


def logout() -> None:
    global _CURRENT
    with _SAVE_LOCK:  # a save already waiting for the lock then sees it is no longer current
        _CURRENT = object()  # no session object may save itself again after this
        try:
            SIGNED_OUT.parent.mkdir(parents=True, exist_ok=True)
            SIGNED_OUT.write_text("", encoding="utf-8")  # even if a session file can't be deleted, it's never used
        except OSError as exc:
            log.warning("Sign-out: couldn't record it: %s", exc)
    for _ in range(3):
        shutil.rmtree(SESSION_DIR, ignore_errors=True)
        if not SESSION_DIR.exists():
            return
        import time

        time.sleep(0.5)  # a file was still open (e.g. a request finishing): try again
    log.warning("Sign-out: couldn't delete all session files in %s", SESSION_DIR)
