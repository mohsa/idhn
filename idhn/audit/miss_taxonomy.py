"""Miss taxonomy for v0.2-B: assign exactly one primary cause to each
missing capability, after reviewing the trace -- not automated. A
classifier that could reliably do this on its own would already be a
better compiler than the one being measured; this module just fixes the
vocabulary and stores human-assigned classifications, so aggregate
counts stay comparable across tasks instead of everyone using slightly
different labels.

The distribution this produces is the actual decision input for what
IDHN builds next: if most misses are M1/M2 (things a better task
decomposition could have predicted), the answer is a better compiler.
If most are M8 (genuinely execution-state-dependent), the answer is
adaptive escalation. Don't guess which -- count it.
"""
from __future__ import annotations

from dataclasses import dataclass

MISS_CAUSES: dict[str, str] = {
    "M1": "task_decomposition",  # the work itself needed a step the compiler's plan never named (e.g. "install" without "use")
    "M2": "resource_discovery",  # the specific target (file/path/host) only became knowable during execution
    "M3": "tool_substrate",  # a shell/discovery/verification tool (cat, ls, pwd, rg, ...) -- process execution, not a task consequence
    "M4": "agent_infrastructure",  # should have been on the fixed infra floor (or in the compiler's context) and wasn't
    "M5": "dependency_network",  # a specific registry/endpoint the task needed that wasn't predicted
    "M6": "scope_too_narrow",  # right resource+operation, but the granted pattern doesn't cover the actual target
    "M7": "environment_runtime",  # an environment quirk (missing shell feature, path oddity) not derivable from the task text
    "M8": "unpredictable_runtime",  # genuinely could not have been known before execution started
    "M9": "measurement_instrumentation",  # an IDHN measurement gap (e.g. an unrecorded inline edit), not a real compiler miss
}


@dataclass(frozen=True)
class MissClassification:
    capability: tuple[str, str, str]
    cause: str  # one of MISS_CAUSES' keys
    note: str

    def __post_init__(self) -> None:
        if self.cause not in MISS_CAUSES:
            raise ValueError(f"unknown miss cause {self.cause!r} (allowed: {sorted(MISS_CAUSES)})")
