"""Deterministic compiler-output budget: reject the "uncertain -> grant
everything" failure mode.

This is not the hard ceiling (v0.2-C's "Deterministic Guard" -- an
organizational maximum the compiler's proposal is checked against).
It's narrower and runs earlier: a fixed, non-negotiable rejection of
specific unscoped-wildcard shapes, regardless of what the compiler's
`reason` claims justifies them. The failure mode this exists to catch
isn't "the compiler was wrong about scope" (ordinary miss, expected,
measured via precision/recall) -- it's "the compiler resolved its own
uncertainty by proposing unlimited authority", which would show up as
excellent recall and be a completely different, much worse problem.

Once v0.2-C's ceiling exists, "unless justified" exceptions belong
there (compare the proposal against an organizational maximum it's
allowed to reach, not against free-text reasoning) -- there's no
ceiling to be compatible with yet, so there's no exception path here.
"""
from __future__ import annotations

from idhn.compiler.parser import CapabilityDecision

# (resource, operation) -> scope patterns that are rejected outright,
# never granted regardless of confidence or stated reason.
_FORBIDDEN_UNSCOPED = {
    ("filesystem", "write"): {"/workspace/**", "/**", "**", "*"},
    ("filesystem", "read"): {"/**", "**", "*"},  # /workspace/** read is the normal, expected grant -- not banned
    ("network", "connect"): {"*", "**"},
}


def check_budget(decisions: list[CapabilityDecision]) -> list[str]:
    """Return violation messages; empty means the proposal stays within
    budget. Called on every compile, regardless of confidence -- a
    "just in case" high-confidence /workspace/** is exactly as rejected
    as a hedging low-confidence one.
    """
    violations: list[str] = []
    for i, d in enumerate(decisions):
        forbidden = _FORBIDDEN_UNSCOPED.get((d.resource, d.operation), set())
        if d.scope in forbidden:
            violations.append(
                f"item {i}: {d.resource}.{d.operation} scope {d.scope!r} is an unscoped wildcard -- "
                f"rejected regardless of stated reason ({d.reason!r})"
            )
    return violations
