"""Entry point for the RS485 Sensor Dashboard.

Usage:  python -m gui.main
"""

import os
import sys


def _find_libxcb_cursor():
    """Return full path to libxcb-cursor.so or None."""
    paths = [
        "/usr/lib/x86_64-linux-gnu/libxcb-cursor.so.0",
        "/usr/lib/libxcb-cursor.so.0",
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def _ensure_xcb_cursor():
    """PySide6 >= 6.5 requires libxcb-cursor at runtime on X11.

    Re-exec with LD_PRELOAD if the library isn't already loaded.
    """
    if sys.platform != "linux":
        return
    if os.environ.get("_XCB_FIXED"):
        return

    libpath = _find_libxcb_cursor()
    if not libpath:
        return

    ld_preload = os.environ.get("LD_PRELOAD", "")
    if libpath in ld_preload:
        return

    new_preload = f"{libpath}:{ld_preload}" if ld_preload else libpath
    os.environ["LD_PRELOAD"] = new_preload
    os.environ["_XCB_FIXED"] = "1"
    os.execv(sys.executable, [sys.executable, "-m", "gui.main"])


def main():
    _ensure_xcb_cursor()
    from gui.app import App
    app = App(sys.argv)
    sys.exit(app.run())


if __name__ == "__main__":
    main()
