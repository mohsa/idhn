"""IDHN Gateway: the only route the agent container has to the internet.

A minimal HTTP CONNECT forward proxy. Every outbound connection is
checked against the manifest's network capability via NetworkPolicy
before a tunnel is opened; the decision is logged through the audit
logger either way. Combined with the Docker network topology (the agent
container has no route to the internet other than this container), this
is the real enforcement point for the network boundary in the
architecture diagram.
"""
from __future__ import annotations

import os
import select
import socket
import socketserver
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from idhn.audit.logger import AuditLogger
from idhn.core.manifest import load_manifest
from idhn.core.models import Decision
from idhn.core.policy import PolicyEngine
from idhn.network.policy import NetworkPolicy

BUFFER_SIZE = 8192


class ProxyHandler(BaseHTTPRequestHandler):
    network_policy: NetworkPolicy
    audit_logger: AuditLogger
    execution_id: str
    agent_name: str

    def do_CONNECT(self) -> None:
        host, _, port = self.path.partition(":")
        self._handle(host, int(port or 443))

    def do_GET(self) -> None:
        host = self.headers.get("Host", "").split(":")[0]
        self._handle(host, 80)

    do_POST = do_GET

    def _handle(self, host: str, port: int) -> None:
        decision = self.network_policy.check(host)
        self.audit_logger.log_decision(self.execution_id, self.agent_name, decision)

        if decision.decision != Decision.ALLOW:
            self.send_error(403, f"IDHN: blocked by policy ({decision.reason})")
            return

        try:
            upstream = socket.create_connection((host, port), timeout=10)
        except OSError as exc:
            self.send_error(502, f"IDHN: upstream connect failed: {exc}")
            return

        if self.command == "CONNECT":
            self.send_response(200, "Connection Established")
            self.end_headers()
            self._pipe(self.connection, upstream)
        else:
            request_line = f"{self.command} {self.path} {self.request_version}\r\n"
            upstream.sendall(request_line.encode())
            for header, value in self.headers.items():
                upstream.sendall(f"{header}: {value}\r\n".encode())
            upstream.sendall(b"\r\n")
            self._pipe(self.connection, upstream)

    @staticmethod
    def _pipe(client: socket.socket, upstream: socket.socket) -> None:
        sockets = [client, upstream]
        try:
            while True:
                readable, _, exceptional = select.select(sockets, [], sockets, 60)
                if exceptional or not readable:
                    break
                done = False
                for sock in readable:
                    other = upstream if sock is client else client
                    data = sock.recv(BUFFER_SIZE)
                    if not data:
                        done = True
                        break
                    other.sendall(data)
                if done:
                    break
        finally:
            for sock in sockets:
                try:
                    sock.close()
                except OSError:
                    pass

    def log_message(self, fmt: str, *args) -> None:
        pass  # the audit logger is the log of record, not stderr


class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    manifest_path = Path(os.environ.get("IDHN_MANIFEST_PATH", "/policies/default.yaml"))
    run_dir = Path(os.environ.get("IDHN_RUN_DIR", "/run"))
    execution_id = os.environ.get("IDHN_RUN_ID", "unknown-run")
    agent_name = os.environ.get("IDHN_AGENT", "codex")
    port = int(os.environ.get("IDHN_GATEWAY_PORT", "8080"))

    manifest = load_manifest(manifest_path)
    engine = PolicyEngine(manifest)
    network_policy = NetworkPolicy(engine, agent=agent_name)
    run_dir.mkdir(parents=True, exist_ok=True)
    audit_logger = AuditLogger(run_dir)

    ProxyHandler.network_policy = network_policy
    ProxyHandler.audit_logger = audit_logger
    ProxyHandler.execution_id = execution_id
    ProxyHandler.agent_name = agent_name

    server = ThreadingHTTPServer(("0.0.0.0", port), ProxyHandler)
    print(f"IDHN gateway listening on :{port}, policy={manifest.name}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
