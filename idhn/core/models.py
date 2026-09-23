"""Core data model: Capability, Task, ActionRequest, Decision.

Nothing in this module knows about Codex, Docker, or any specific
runtime -- it is the vocabulary the policy engine speaks.
"""
from __future__ import annotations

from dataclasses import dataclass
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
