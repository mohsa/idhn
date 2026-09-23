"""fetch-service: a deliberately dangerous internal service for M2.

IDHN is allowed to let the agent reach this service (it's a normal,
narrow `network.allow` entry). The service itself, though, can fetch any
URL it's given -- `GET /fetch?url=<anything>` -- which is exactly the
kind of "allowed capability that can be turned into an arbitrary one"
IDHN's transitive enforcement (idhn/core/policy.py's
_evaluate_transitive_network) exists to catch. This process does no
policy checking of its own; its own outbound egress is routed through
the IDHN gateway via HTTP(S)_PROXY like every other container (see
docker/compose.yaml), so the gateway is where the actual decision and
audit event happen.
"""
from __future__ import annotations

import os
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


class FetchHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/fetch":
            self.send_error(404, "unknown endpoint")
            return

        target_url = (parse_qs(parsed.query).get("url") or [None])[0]
        if not target_url:
            self.send_error(400, "missing url parameter")
            return

        try:
            with urllib.request.urlopen(target_url, timeout=10) as resp:
                body = resp.read(2000)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:  # noqa: BLE001 -- report any upstream failure verbatim
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"fetch-service: upstream error: {exc}".encode())

    def log_message(self, fmt: str, *args) -> None:
        pass


def main() -> None:
    port = int(os.environ.get("PORT", "9000"))
    server = HTTPServer(("0.0.0.0", port), FetchHandler)
    print(f"fetch-service listening on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
