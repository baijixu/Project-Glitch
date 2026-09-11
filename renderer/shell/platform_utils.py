"""Single place for OS-specific logic in the Renderer's pywebview shell
(SPEC.md section 7's rule: any platform branching lives behind one small
abstraction, never scattered `if sys.platform` checks). SPEC.md section 8's
proposed tree lists this under brain/, but brain/ and renderer/shell/ are
separate Python projects with their own venvs (SPEC.md section 4/5) -- a
brain-side module can't be imported here, and the only OS-specific need so
far is entirely shell-side (pywebview's backend selection), so it lives
next to launch.py instead.

Currently one concrete case: pywebview has no default GUI backend on
Linux (Windows gets WebView2, macOS gets WKWebView, both automatically via
pywebview's own dependency markers -- Linux needs a system-installed
WebKitGTK plus the `pywebview[gtk]` extra, see pyproject.toml), so a
missing backend there gets a platform-aware hint instead of a raw
traceback.
"""

import sys

LINUX_WEBVIEW_HINT = (
    "pywebview couldn't find a GUI backend. On Linux this needs the system "
    "WebKitGTK libraries, which pip can't install -- on Debian/Ubuntu:\n"
    "    sudo apt install python3-gi gir1.2-webkit2-4.1\n"
    "See SPEC.md section 7 for other distros, then re-run `uv sync` here."
)


def webview_backend_hint(exc: Exception) -> str | None:
    """A platform-tailored explanation for a pywebview startup failure, or
    None if this isn't the known Linux-backend case (nothing to add).
    """
    if sys.platform.startswith("linux"):
        return LINUX_WEBVIEW_HINT
    return None
