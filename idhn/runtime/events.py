"""Structured runtime/audit events shared across IDHN components.

The event schema distinguishes what happened (`action`) from who it
happened on behalf of (`principal`) and how the request got there
(`provenance`) -- so a direct `Codex -> example.com` connect and a
`Codex -> fetch-service -> example.com` one are visibly different events,
not the same log line with a different outcome.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from idhn.core.models import ExecutionContext, PolicyDecision


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Principal:
    type: str
    id: str


@dataclass
class ActionInfo:
    resource: str
    operation: str
    target: str


@dataclass
class Provenance:
    direct: bool
    via: list[str] = field(default_factory=list)


@dataclass
class RuntimeEvent:
    run_id: str
    principal: Principal
    action: ActionInfo
    provenance: Provenance
    decision: str
    reason: str
    policy: str
    task_id: str = ""
    event: str = "capability_request"
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = utcnow_iso()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_policy_decision(cls, decision: PolicyDecision, context: ExecutionContext) -> "RuntimeEvent":
        return cls(
            run_id=context.run_id,
            principal=Principal(type="agent", id=context.agent_id),
            action=ActionInfo(
                resource=decision.request.resource,
                operation=decision.request.action,
                target=decision.request.target,
            ),
            provenance=Provenance(direct=context.direct, via=list(context.delegation_chain)),
            decision=decision.decision.value,
            reason=decision.reason,
            policy=decision.policy_name,
            task_id=context.task_id,
        )
