"""What may be written to the log or shown in a window about an error.

Users attach the log to bug reports. Errors that come from talking to Apple (pyicloud, requests)
can carry Apple's whole answer in their message: names, e-mail addresses, phone numbers, sign-in
values. Those are therefore described only by kind, numeric codes and the place in the code;
Skyfold's own messages and plain Windows errors (which name a file and a reason) stay readable.
"""

from __future__ import annotations

import traceback
from pathlib import Path

class Text(str):
    """A message Skyfold put together itself (i18n.t() returns one). Only such text, and what
    Windows or the database say about a failed file operation, is ever shown or logged as text;
    anything else about an error is reduced to kinds and codes."""

    __slots__ = ()


def describe(exc: BaseException, frames: int = 3) -> str:
    """Kinds of errors along the chain, numeric codes (Apple's, HTTP, Windows) and where it happened.
    Never any message text."""
    parts: list[str] = []
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen and len(parts) < 6:
        seen.add(id(e))
        code = getattr(e, "code", None)
        try:
            code = int(str(code).strip()) if code is not None else None
        except ValueError:
            code = None  # Apple's own text codes are left out
        http = getattr(getattr(e, "response", None), "status_code", None)
        http = http if isinstance(http, int) else None
        if code is None:
            code, http = http, None
        errno = getattr(e, "winerror", None) or getattr(e, "errno", None)
        extra = [f"HTTP {http}"] if http is not None and http != code else []
        extra += [f"errno {errno}"] if isinstance(errno, int) else []
        label = type(e).__name__ + (f" ({code})" if code is not None else "")
        parts.append(label + (f" [{', '.join(extra)}]" if extra else ""))
        e = e.__cause__ or e.__context__
    where = traceback.extract_tb(exc.__traceback__)[-frames:] if exc.__traceback__ and frames else []
    at = ", ".join(f"{Path(f.filename).name}:{f.lineno}" for f in where)
    return " <- ".join(parts) + (f" at {at}" if at else "")


KNOWN_CODES = (
    "OK", "SUCCESS", "ERROR", "FAILED", "FAILURE", "UNKNOWN", "CONFLICT", "ETAG_CONFLICT", "NAME_CONFLICT",
    "ITEM_EXISTS", "ALREADY_EXISTS", "EXISTS", "NOT_FOUND", "ITEM_NOT_FOUND", "ZONE_NOT_FOUND", "ACCESS_DENIED",
    "PERMISSION_DENIED", "QUOTA_EXCEEDED", "OVER_QUOTA", "INVALID_NAME", "INVALID_REQUEST", "BAD_REQUEST",
    "THROTTLED", "TRY_AGAIN_LATER", "SERVICE_UNAVAILABLE", "INTERNAL_ERROR", "PRIVATE_DB_DISABLED",
)


def code(value: object) -> str:
    """One of the codes Apple is known to answer with (CONFLICT, ITEM_NOT_FOUND…), a plain number,
    or "other". Whatever else the field holds is never kept: it is Apple's text, not ours."""
    if isinstance(value, bool):
        return "other"
    if isinstance(value, int):
        return str(value)
    text = str(value or "").strip().upper()
    if text.isdigit() and len(text) <= 6:
        return text
    return text if text in KNOWN_CODES else "other"


def value(v: object, allowed: tuple[str, ...] = ()) -> str:
    """A value that came from Apple (a status, a flag, a field), made safe for the log: real
    booleans and numbers as they are; text only when it is one of the words `allowed` for that
    field (anything else is "invalid", however harmless it looks); a container is only counted."""
    if v is None or isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v if v in allowed else "invalid"
    if isinstance(v, (list, tuple, set, frozenset, dict)):
        return f"{type(v).__name__} of {len(v)}"
    if isinstance(v, (bytes, bytearray)):
        return f"{len(v)} bytes"
    return type(v).__name__


def brief(exc: BaseException) -> str:
    """One line for an activity entry or a log line. The message is used only when Skyfold wrote it
    (a Text) or when it is a plain Windows/database error about a file; that is decided by where the
    text comes from, never by the kind of exception. Otherwise kinds and codes only."""
    first = exc.args[0] if getattr(exc, "args", None) else None
    if isinstance(first, Text):
        return str(first)[:300]
    plain_os = isinstance(exc, OSError) and type(exc).__module__ == "builtins"
    if (plain_os or type(exc).__module__ == "sqlite3") and getattr(exc, "response", None) is None:
        text = str(exc).strip()
        return (f"{type(exc).__name__}: {text}" if text else type(exc).__name__)[:300]
    return describe(exc, frames=0)
