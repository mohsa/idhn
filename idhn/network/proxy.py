"""IDHN Gateway: the only route any container has to the internet.

A minimal HTTP CONNECT forward proxy. Every connection -- whether it's
the agent reaching out directly, the agent reaching an internal service
like fetch-service, or that service reaching back out to the internet on
the agent's behalf -- passes through here, because every container's
HTTP(S)_PROXY points at the gateway and none of them have another route
out. The gateway identifies the caller by TCP peer address (see
callers.py) and builds the ExecutionContext accordingly: a direct call
gets an empty delegation_chain, a service-mediated one gets
delegation_chain=[service_name]. PolicyEngine uses that to decide which
capability section governs -- this is the actual enforcement point for
transitive capability escapes.
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
from idhn.core.models import ActionRequest, Decision, ExecutionContext, PolicyDecision
from idhn.core.policy import PolicyEngine
from idhn.network.callers import CallerRegistry
from idhn.network.policy import NetworkPolicy

BUFFER_SIZE = 8192


class ProxyHandler(BaseHTTPRequestHandler):
    network_policy: NetworkPolicy
    audit_logger: AuditLogger
    callers: CallerRegistry
    execution_id: str
    agent_id: str
    task_id: str
    policy_name: str

    def do_CONNECT(self) -> None:
        host, _, port = self.path.partition(":")
        self._handle(host, int(port or 443))

    def do_GET(self) -> None:
        host_header = self.headers.get("Host", "")
        host, _, port_str = host_header.partition(":")
        self._handle(host, int(port_str) if port_str else 80)

    do_POST = do_GET

    def _handle(self, host: str, port: int) -> None:
        caller_name, is_service = self.callers.identify(self.client_address[0])

        if caller_name == "unknown":
            self._deny_unrecognized_caller(host)
            return

        context = ExecutionContext(
            run_id=self.execution_id,
            agent_id=self.agent_id,
            task_id=self.task_id,
            parent_action_id=None,
            delegation_chain=[caller_name] if is_service else [],
        )
        decision = self.network_policy.check(host, context)
        self.audit_logger.log_decision(decision, context)

        if decision.decision != Decision.ALLOW:
            # Includes the actual host, not just the reason code -- a bare
            # "blocked by policy (default_deny)" tells a developer THAT
            # something was denied but not WHAT, forcing a separate trip
            # through events.jsonl to find out. The host is already in this
            # process's own hands; there's no reason to make the caller go
            # elsewhere for it. The hint is conditional on the reason: for
            # an explicit deny, suggesting "add it to network.allow" would
            # be actively wrong advice -- that host was blocked on purpose,
            # not merely missing from the list.
            hint = (
                f" Add '{host}' to this manifest's network.allow if this is expected."
                if decision.reason == "default_deny"
                else " This host is explicitly denied in this manifest's network.deny."
                if decision.reason == "explicit_deny"
                else ""
            )
            self.send_error(
                403,
                f"IDHN: network request to '{host}' denied (reason: {decision.reason}).{hint}",
            )
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

    def _deny_unrecognized_caller(self, host: str) -> None:
        # Fail closed: a TCP peer we can't map to the agent or a declared
        # service gets no policy lookup at all, just a flat deny.
        context = ExecutionContext(
            run_id=self.execution_id,
            agent_id="unknown",
            task_id=self.task_id,
            parent_action_id=None,
            delegation_chain=["unknown-caller"],
        )
        request = ActionRequest(resource="network", action="connect", target=host, agent="unknown")
        decision = PolicyDecision(
            request=request,
            decision=Decision.DENY,
            reason="unrecognized_caller_address",
            policy_name=self.policy_name,
        )
        self.audit_logger.log_decision(decision, context)
        self.send_error(403, "IDHN: blocked (unrecognized caller)")

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
    agent_hostname = os.environ.get("IDHN_AGENT_HOSTNAME", "agent")
    agent_id = os.environ.get("IDHN_AGENT", "codex")
    task_id = os.environ.get("IDHN_TASK_ID", "unknown-task")
    port = int(os.environ.get("IDHN_GATEWAY_PORT", "8080"))

    manifest = load_manifest(manifest_path)
    engine = PolicyEngine(manifest)
    network_policy = NetworkPolicy(engine)

    service_hostnames = list((manifest.capabilities.get("services") or {}).keys())
    callers = CallerRegistry(agent_hostname=agent_hostname, service_hostnames=service_hostnames)

    run_dir.mkdir(parents=True, exist_ok=True)
    audit_logger = AuditLogger(run_dir)

    ProxyHandler.network_policy = network_policy
    ProxyHandler.audit_logger = audit_logger
    ProxyHandler.callers = callers
    ProxyHandler.execution_id = execution_id
    ProxyHandler.agent_id = agent_id
    ProxyHandler.task_id = task_id
    ProxyHandler.policy_name = manifest.name

    server = ThreadingHTTPServer(("0.0.0.0", port), ProxyHandler)
    print(
        f"IDHN gateway listening on :{port}, policy={manifest.name}, services={service_hostnames}",
        file=sys.stderr,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
