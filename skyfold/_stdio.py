"""A windowed exe (or pythonw) has no console: sys.stdin/stdout/stderr are None.

Several libraries assume they exist (pyicloud calls sys.stdout.isatty() at import time),
so give them harmless stand-ins before anything else is imported.
"""

import os
import sys


def ensure_std_streams() -> None:
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
