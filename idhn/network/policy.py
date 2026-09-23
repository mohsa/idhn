"""Network-specific policy resolution.

A thin adapter over the core PolicyEngine: turns a requested hostname
into an ActionRequest and returns the engine's decision. Kept separate so
the gateway process only needs to import this, not know about
filesystem, process, or git capability shapes.
"""
from __future__ import annotations

from idhn.core.models import ActionRequest, PolicyDecision
from idhn.core.policy import PolicyEngine


class NetworkPolicy:
    def __init__(self, engine: PolicyEngine, agent: str = "codex"):
        self.engine = engine
        self.agent = agent

    def check(self, host: str) -> PolicyDecision:
        request = ActionRequest(resource="network", action="connect", target=host, agent=self.agent)
        return self.engine.evaluate(request)
