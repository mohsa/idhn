"""Resolve which container is speaking to the gateway.

Docker's embedded DNS on the `internal` network lets the gateway resolve
known container hostnames to their current IPs. We use *that* -- not a
header the caller could set on its own request -- to tell the agent
container apart from an internal service like fetch-service. A header
would be spoofable by the agent itself; a TCP peer address on a network
the agent has no raw-socket access to (it runs with cap_drop: ALL) is
not.

LAB_ONLY_TRUST_MECHANISM: this is sound for a single-host Docker Compose
lab, not a production identity boundary. It trusts "whichever process
currently holds this container's IP", which is fine when we built and
control every container on this network, but it is not workload
identity, a mutually authenticated channel, or a signed execution
context. Don't let this quietly become how IDHN attributes callers
outside this lab -- that's real work for a later milestone, not a
one-line swap.
"""
from __future__ import annotations

import socket


class CallerRegistry:
    def __init__(self, agent_hostname: str, service_hostnames: list[str]):
        self.agent_hostname = agent_hostname
        self.service_hostnames = list(service_hostnames)
        self._ip_to_name: dict[str, str] = {}

    def identify(self, ip: str) -> tuple[str, bool]:
        """Return (caller_name, is_service). caller_name is 'unknown' if unresolved."""
        name = self._ip_to_name.get(ip)
        if name is None:
            self._refresh()
            name = self._ip_to_name.get(ip)
        if name is None:
            return "unknown", False
        return name, name != self.agent_hostname

    def _refresh(self) -> None:
        for name in (self.agent_hostname, *self.service_hostnames):
            try:
                self._ip_to_name[socket.gethostbyname(name)] = name
            except OSError:
                continue
