"""Core data model: Capability, Task, ActionRequest, Decision.

Nothing in this module knows about Codex, Docker, or any specific
runtime -- it is the vocabulary the policy engine speaks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class Capability:
    """A single granted capability, e.g. write access to a path pattern."""

    resource: str
    action: str
    scope: str


@dataclass(frozen=True)
class ActionRequest:
    """Something an agent is trying to do, submitted to the policy engine."""

    resource: str
    action: str
    target: str
    agent: str = "unknown"


@dataclass(frozen=True)
class PolicyDecision:
    """The policy engine's answer to an ActionRequest."""

    request: ActionRequest
    decision: Decision
    reason: str
    policy_name: str = "default"


@dataclass
class Task:
    id: str
    description: str


@dataclass
class ExecutionContext:
    """Who is asking, for what task, and through what chain of delegation.

    v0.1 threads this through every evaluate() call but only one resource
    type (network) actually branches on it so far -- see
    PolicyEngine._evaluate_network. The rest is foundation for the
    effective-authority question: whether the same request should be
    decided differently depending on who's really asking and how they got
    here, not just what they're asking for.
    """

    run_id: str
    agent_id: str
    task_id: str
    parent_action_id: str | None = None
    delegation_chain: list[str] = field(default_factory=list)

    @property
    def direct(self) -> bool:
        return not self.delegation_chain
