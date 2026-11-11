"""What may be written to the log or shown in a window about an error.

Users attach the log to bug reports. Errors that come from talking to Apple (pyicloud, requests)
can carry Apple's whole answer in their message: names, e-mail addresses, phone numbers, sign-in
values. Those are therefore described only by kind, numeric codes and the place in the code;
Skyfold's own messages and plain Windows errors (which name a file and a reason) stay readable.
"""

from __future__ import annotations

import traceback
from pathlib import Path

_OWN_TEXT = ("SyncError", "RemoteUnavailable", "Cancelled", "MacPackage", "LoginError", "Offline",
             "FindMyNeedsSignIn")


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


def _plain(exc: BaseException) -> bool:
    """Skyfold's own message, or a plain Python/Windows/database error: its text is safe to show."""
    kind = type(exc)
    if kind.__name__ in _OWN_TEXT and kind.__module__.startswith("skyfold"):
        return True
    return kind.__module__ in ("builtins", "sqlite3", "zipfile", "json", "json.decoder")


def brief(exc: BaseException) -> str:
    """One line for an activity entry or a log line: the message when it is safe (see _plain),
    otherwise kinds and codes only."""
    if _plain(exc) and not getattr(exc, "response", None):
        text = str(exc).strip()
        if type(exc).__module__.startswith("skyfold"):
            return text[:300]
        return (f"{type(exc).__name__}: {text}" if text else type(exc).__name__)[:300]
    return describe(exc, frames=0)
