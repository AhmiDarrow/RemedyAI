#!/usr/bin/env python3
"""Serve the Remedy web UI in a browser — no Tauri needed.

    python serve.py            # serve desktop/dist on http://127.0.0.1:5173
    python serve.py --build    # rebuild the UI first (npm run build)
    python serve.py --no-open  # don't auto-open the browser

The page talks to the local Remedy API at http://127.0.0.1:7400 (start it
with `remedy serve` if it isn't already running — this script will tell
you). Static files come from desktop/dist with an SPA fallback, so deep
links land on index.html. Stdlib only; Ctrl+C stops it.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import threading
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "desktop" / "dist"
DEFAULT_PORT = 5173
API_PORT = 7400


class SpaHandler(SimpleHTTPRequestHandler):
    """Static files with SPA fallback (unknown paths → index.html)."""

    def send_head(self):  # noqa: N802 — stdlib naming
        path = Path(self.translate_path(self.path))
        if not path.exists() and "." not in Path(self.path).name:
            self.path = "/index.html"
        return super().send_head()

    def end_headers(self):  # noqa: N802
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):  # quiet: one line per request is noise
        pass


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _build() -> bool:
    print("Building the UI (npm run build)…")
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    try:
        r = subprocess.run([npm, "run", "build"], cwd=ROOT / "desktop", check=False)
        return r.returncode == 0
    except FileNotFoundError:
        print("npm not found — install Node.js, or build once from the desktop/ folder.")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--build", action="store_true", help="npm run build first")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    args = ap.parse_args()

    if args.build or not (DIST / "index.html").is_file():
        if not _build() or not (DIST / "index.html").is_file():
            print(f"No build found at {DIST} — run: cd desktop && npm run build")
            return 1

    if not _port_open(API_PORT):
        print(
            f"Note: the Remedy API isn't answering on 127.0.0.1:{API_PORT} yet.\n"
            "      Start it in another window with:  remedy serve\n"
            "      The page will connect the moment it's up."
        )

    handler = partial(SpaHandler, directory=str(DIST))
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    except OSError as e:
        print(f"Port {args.port} unavailable ({e}) — try --port {args.port + 1}")
        return 1

    url = f"http://127.0.0.1:{args.port}"
    print(f"Remedy page: {url}  (Ctrl+C to stop)")
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
