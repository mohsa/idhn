"""Static risk classification for capability requests.

Deterministic and table-driven. Used for reporting/prioritization only --
it never influences allow/deny decisions, which stay entirely in
policy.py.
"""
from __future__ import annotations

from idhn.core.models import ActionRequest

_RISK_TABLE: dict[tuple[str, str], str] = {
    ("secrets", "read"): "critical",
    ("secrets", "write"): "critical",
    ("git", "push"): "high",
    ("network", "connect"): "medium",
    ("process", "exec"): "medium",
    ("filesystem", "write"): "medium",
    ("filesystem", "read"): "low",
    ("git", "status"): "low",
    ("git", "diff"): "low",
}

_DEFAULT_RISK = "medium"


def classify(request: ActionRequest) -> str:
    """Return a risk label: low, medium, high, or critical."""
    return _RISK_TABLE.get((request.resource, request.action), _DEFAULT_RISK)
