"""Pictures for the Photos window, made off the window's thread.

Thumbnails and HEIC photos come from Windows itself (the same previews File Explorer shows, which
Windows also keeps in its own thumbnail cache), so nothing is decoded by Skyfold and nothing extra
is stored on disk. JPEG, PNG and the like are read by Qt. Without Windows' HEIF extension a HEIC
photo simply has no picture; the window then says where to get it."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader

log = logging.getLogger(__name__)

QT_FORMATS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
FULL_SIZE = 2560  # a HEIC photo in the viewer: sharp on a 1440p screen, quick to make

_com = threading.local()


def _com_init() -> None:
    if getattr(_com, "ready", False):
        return
    try:
        ctypes.OleDLL("ole32").CoInitializeEx(None, 2)  # apartment-threaded, as the Shell wants
    except OSError:
        pass  # already initialised differently on this thread: Shell calls still work
    _com.ready = True


if sys.platform == "win32":
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16),
                    ("d", ctypes.c_ubyte * 8)]

    class _SIZE(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class _BITMAP(ctypes.Structure):
        _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long), ("bmHeight", ctypes.c_long),
                    ("bmWidthBytes", ctypes.c_long), ("bmPlanes", ctypes.c_uint16),
                    ("bmBitsPixel", ctypes.c_uint16), ("bmBits", ctypes.c_void_p)]

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16),
                    ("biCompression", ctypes.c_uint32), ("biSizeImage", ctypes.c_uint32),
                    ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                    ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]

    _IID_FACTORY = _GUID()
    ctypes.OleDLL("ole32").CLSIDFromString("{bcc18b79-ba16-442f-80c4-8a59c30c463b}", ctypes.byref(_IID_FACTORY))
    _GetImage = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, _SIZE, ctypes.c_int,
                                   ctypes.POINTER(wintypes.HBITMAP))
    _Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)


def shell_image(path: Path, size: int, thumbnail: bool = True) -> QImage:
    """Windows' own picture of a file, at most size×size (aspect kept). thumbnail=False makes Windows
    decode the file itself instead of using a cached preview (for a sharp photo in the viewer)."""
    if sys.platform != "win32":
        return QImage()
    _com_init()
    gdi32, user32 = ctypes.windll.gdi32, ctypes.windll.user32
    factory = ctypes.c_void_p()
    try:
        ctypes.OleDLL("shell32").SHCreateItemFromParsingName(
            ctypes.c_wchar_p(str(path)), None, ctypes.byref(_IID_FACTORY), ctypes.byref(factory))
    except OSError:
        return QImage()
    vtbl = ctypes.cast(factory, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    hbmp = wintypes.HBITMAP()
    flags = 0x1 | (0x8 if thumbnail else 0)  # BIGGERSIZEOK | THUMBNAILONLY
    try:
        _GetImage(vtbl[3])(factory, _SIZE(size, size), flags, ctypes.byref(hbmp))
    except OSError:
        return QImage()
    finally:
        _Release(vtbl[2])(factory)
    try:
        bm = _BITMAP()
        gdi32.GetObjectW(hbmp, ctypes.sizeof(bm), ctypes.byref(bm))
        w, h = bm.bmWidth, bm.bmHeight
        if w <= 0 or h <= 0:
            return QImage()
        bi = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        hdc = user32.GetDC(None)
        try:
            if not gdi32.GetDIBits(hdc, hbmp, 0, h, buf, ctypes.byref(bi), 0):
                return QImage()
        finally:
            user32.ReleaseDC(None, hdc)
        return QImage(buf.raw, w, h, w * 4, QImage.Format_RGB32).copy()
    finally:
        gdi32.DeleteObject(hbmp)


def load_image(path: Path, size: int, full: bool = False) -> QImage:
    """A picture of the file whose shorter side is at least `size` (a thumbnail), or the photo itself
    up to FULL_SIZE (full=True). An empty QImage if there is none."""
    if path.suffix.lower() in QT_FORMATS:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)  # the camera's rotation
        src = reader.size()
        if src.isValid() and src.width() > 0 and src.height() > 0:
            limit = FULL_SIZE if full else size
            if full:
                scale = min(1.0, limit / max(src.width(), src.height()))
            else:
                scale = min(1.0, limit / min(src.width(), src.height()))
            if scale < 1.0:
                reader.setScaledSize(QSize(max(1, round(src.width() * scale)), max(1, round(src.height() * scale))))
        img = reader.read()
        if not img.isNull():
            return img
    # HEIC, videos, and anything Qt can't read: Windows' own picture of it
    if full:
        return shell_image(path, FULL_SIZE, thumbnail=False)
    return shell_image(path, round(size * 1.6))  # (a fit, not a fill: a bit larger for square tiles)


class _Job(QRunnable):
    def __init__(self, loader: "ImageLoader", key: str, path: Path, size: int, full: bool) -> None:
        super().__init__()
        self.loader, self.key, self.path, self.size, self.full = loader, key, path, size, full

    def run(self) -> None:
        lo = self.loader
        if not lo._still_wanted(self.key, self.size, self.full):
            return
        try:
            img = load_image(self.path, self.size, self.full)
        except Exception:  # noqa: BLE001
            log.debug("No picture for a photo", exc_info=True)
            img = QImage()
        lo._done(self.key, self.size, self.full, img)


class ImageLoader(QObject):
    """Makes pictures on a few background threads; the newest requests (what is on screen) first.
    Requests for things no longer on screen are skipped once they come up."""

    loaded = Signal(str, int, bool, QImage)  # key, size, full, picture (null: none)
    _delivered = Signal(str, int, bool, QImage)

    def __init__(self, parent: QObject | None = None, threads: int = 3) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(threads)
        self._lock = threading.Lock()
        self._queued: set[tuple[str, int, bool]] = set()
        self._wanted: set[str] | None = None  # None: everything
        self._order = 0
        self._delivered.connect(self._deliver)  # (queued onto this object's thread)

    def request(self, key: str, path: Path, size: int, full: bool = False) -> None:
        job = (key, size, full)
        with self._lock:
            if job in self._queued:
                return
            self._queued.add(job)
            self._order += 1
            order = self._order
        # higher priority runs first: the latest request is what the user looks at now
        self._pool.start(_Job(self, key, path, size, full), min(order, 2_000_000_000) if not full else 2_100_000_000)

    def keep_only(self, keys: set[str] | None) -> None:
        with self._lock:
            self._wanted = set(keys) if keys is not None else None

    def _still_wanted(self, key: str, size: int, full: bool) -> bool:
        with self._lock:
            ok = full or self._wanted is None or key in self._wanted
            if not ok:
                self._queued.discard((key, size, full))
            return ok

    def _done(self, key: str, size: int, full: bool, img: QImage) -> None:
        self._delivered.emit(key, size, full, img)

    def _deliver(self, key: str, size: int, full: bool, img: QImage) -> None:
        with self._lock:
            self._queued.discard((key, size, full))
        self.loaded.emit(key, size, full, img)

    def stop(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(3000)
