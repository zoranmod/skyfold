"""Text helpers shared by the engines and the UI (no Qt imports here)."""

from __future__ import annotations


def _european() -> bool:
    """Bosnian writes 1.234,5; English 1,234.5."""
    from .i18n import language

    return language() == "bs"


def size_text(n: int, base: int = 1000) -> str:
    """Human size like Windows/Apple storage pages: 1 GB = 1000^3 by default."""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < base or unit == "TB":
            if unit in ("B", "KB"):
                return f"{size:.0f} {unit}"
            text = f"{size:.1f} {unit}"
            return text.replace(".", ",") if _european() else text
        size /= base
    return f"{n} B"


def count_text(n: int) -> str:
    """1234567 -> '1,234,567' in English, '1.234.567' in Bosnian."""
    return f"{n:,}".replace(",", ".") if _european() else f"{n:,}"


class Rate:
    """Download speed over the last minute, and how long the rest should take.

    Two speeds are tracked: bytes per second and files per second. Big files are limited by the
    line, small ones by how fast Apple answers per file, so the time left is the larger of the two
    estimates (never optimistic)."""

    def __init__(self, window: float = 60.0, clock=None) -> None:
        import time
        from collections import deque

        self.window = window
        self.clock = clock or time.monotonic
        self.samples: deque[tuple[float, int, int]] = deque()

    def add(self, done_bytes: int, done_files: int) -> None:
        now = self.clock()
        self.samples.append((now, done_bytes, done_files))
        while len(self.samples) > 2 and now - self.samples[1][0] >= self.window:
            self.samples.popleft()

    def speed(self) -> tuple[float, float]:
        """(bytes per second, files per second); zeros until there are a few seconds of data."""
        if len(self.samples) < 2:
            return 0.0, 0.0
        (t0, b0, f0), (t1, b1, f1) = self.samples[0], self.samples[-1]
        span = t1 - t0
        if span < 3:
            return 0.0, 0.0
        return max(0.0, (b1 - b0) / span), max(0.0, (f1 - f0) / span)

    def seconds_left(self, bytes_left: int, files_left: int) -> float | None:
        bps, fps = self.speed()
        guesses = []
        if bytes_left > 0 and bps > 0:
            guesses.append(bytes_left / bps)
        if files_left > 0 and fps > 0:
            guesses.append(files_left / fps)
        return max(guesses) if guesses else None
