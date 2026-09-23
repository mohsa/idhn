"""Network-specific policy resolution.

A thin adapter over the core PolicyEngine: turns a requested hostname
into an ActionRequest and returns the engine's decision. The request
shape is the same whether the connect attempt is direct from the agent
or arrives via an internal service (e.g. fetch-service) -- what differs
is the ExecutionContext's delegation_chain, which PolicyEngine uses to
pick whether the agent's own `network` allowlist or the narrower
`services` capability governs the decision. See
idhn.core.policy.PolicyEngine._evaluate_network.
"""
from __future__ import annotations

from idhn.core.models import ActionRequest, ExecutionContext, PolicyDecision
from idhn.core.policy import PolicyEngine


class NetworkPolicy:
    def __init__(self, engine: PolicyEngine):
        self.engine = engine

    def check(self, host: str, context: ExecutionContext) -> PolicyDecision:
        request = ActionRequest(resource="network", action="connect", target=host, agent=context.agent_id)
        return self.engine.evaluate(request, context)
