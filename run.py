"""Entry point (also used by PyInstaller)."""
import sys

if __name__ == "__main__":
    from skyfold.native_host import is_native_call

    if is_native_call(sys.argv):  # started by a browser for the Safari Bookmarks extension
        from skyfold.native_host import serve

        sys.exit(serve())

from skyfold._stdio import ensure_std_streams

ensure_std_streams()  # must run before pyicloud is imported

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        from skyfold.selftest import main as selftest

        sys.exit(selftest(sys.argv[sys.argv.index("--selftest") + 1]))
    from skyfold.app import main

    sys.exit(main())
