"""Cloudera AI Application entry point for the built Helios UI."""
from __future__ import annotations

import os
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(
    os.environ.get("HELIOS_ROOT")
    or Path(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw")) / "helios"
)
DIST = ROOT / "apps" / "ui" / "dist"


class HeliosUIHandler(SimpleHTTPRequestHandler):
    """Serve the Vite build with a fallback for client-side routes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST), **kwargs)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
            body = b"ok\n"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        requested = DIST / path.lstrip("/")
        if not requested.exists() or requested.is_dir():
            self.path = "/index.html"
        super().do_GET()


def main() -> None:
    if not (DIST / "index.html").is_file():
        raise SystemExit(
            f"Helios UI build not found at {DIST}; run npm install && npm run build "
            "in apps/ui before starting the Application"
        )

    host = "127.0.0.1"
    port = int(os.environ.get("CDSW_APP_PORT", "8080"))
    print(f"starting helios UI on http://{host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), HeliosUIHandler).serve_forever()


if __name__ == "__main__":
    main()
