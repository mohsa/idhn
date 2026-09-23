"""Structured runtime/audit events shared across IDHN components."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from idhn.core.models import PolicyDecision


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class RuntimeEvent:
    execution_id: str
    agent: str
    event: str
    resource: str
    action: str
    target: str
    decision: str
    reason: str
    policy: str
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = utcnow_iso()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_policy_decision(cls, execution_id: str, agent: str, decision: PolicyDecision) -> "RuntimeEvent":
        return cls(
            execution_id=execution_id,
            agent=agent,
            event="capability_request",
            resource=decision.request.resource,
            action=decision.request.action,
            target=decision.request.target,
            decision=decision.decision.value,
            reason=decision.reason,
            policy=decision.policy_name,
        )
