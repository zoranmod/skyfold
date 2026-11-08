"""Entry point (also used by PyInstaller)."""
import sys

from skyfold._stdio import ensure_std_streams

ensure_std_streams()  # must run before pyicloud is imported

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        from skyfold.selftest import main as selftest

        sys.exit(selftest(sys.argv[sys.argv.index("--selftest") + 1]))
    from skyfold.app import main

    sys.exit(main())
