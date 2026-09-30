"""Serve this folder so a browser can open the Jarvis frontend.

    python serve.py                                   # http://localhost:8080
    python serve.py --port 9000
    python serve.py --server http://192.168.1.20:8765 # connect to that backend

Browsers will not run JavaScript modules from a file:// page, so the pages
need a web server. Any static server works; this one needs nothing but a
standard Python install (3.8+), and nothing from the Jarvis backend.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import socketserver
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent


class Handler(http.server.SimpleHTTPRequestHandler):
    # Windows can register .js as text/plain, and Python's own table reads the
    # registry. Browsers refuse to run a module served that way, so pin the
    # types that matter instead of trusting the machine.
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".html": "text/html",
        ".js": "text/javascript",
        ".css": "text/css",
        ".svg": "image/svg+xml",
    }

    def end_headers(self) -> None:
        # Edits to the files should show up on a plain reload.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the Jarvis frontend.")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="127.0.0.1 = this computer only; 0.0.0.0 = the whole network",
    )
    parser.add_argument(
        "--server",
        default="",
        help="Jarvis backend to connect to (default: the one in config.js)",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not open a browser tab"
    )
    args = parser.parse_args()

    handler = functools.partial(Handler, directory=str(HERE))
    socketserver.TCPServer.allow_reuse_address = True
    try:
        httpd = socketserver.ThreadingTCPServer((args.host, args.port), handler)
    except OSError as exc:
        print(f"Could not listen on port {args.port}: {exc}", file=sys.stderr)
        print("Try another one, e.g.  python serve.py --port 8081", file=sys.stderr)
        return 1

    url = f"http://localhost:{args.port}/"
    if args.server:
        url += "?" + urlencode({"server": args.server})
    print(f"Jarvis frontend: {url}")
    print("Press Ctrl-C to stop.")
    if not args.no_browser:
        webbrowser.open(url)
    with httpd, contextlib.suppress(KeyboardInterrupt):
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
