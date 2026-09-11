"""Native window shell for the Renderer -- opens a pywebview window loading
the Renderer's HTML/JS app. This is the *only* Python involved in the
Renderer component (spec section 4): it contains no app logic, just window
creation. Points at the Vite dev server during development; a production
build (vite build -> dist/index.html) is a later, separate concern.

Run with:
    uv run launch.py
"""

import os

import webview

DEV_SERVER_URL = "http://localhost:5173"
RENDERER_URL = os.environ.get("GLITCH_RENDERER_URL", DEV_SERVER_URL)


def main() -> None:
    webview.create_window("Glitch", RENDERER_URL, width=900, height=700)
    webview.start()


if __name__ == "__main__":
    main()
