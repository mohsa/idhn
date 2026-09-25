"""Deterministic compiler-output budget: reject the "uncertain -> grant
everything" failure mode.

This is not the hard ceiling (v0.2-C's "Deterministic Guard" -- an
organizational maximum the compiler's proposal is checked against).
It's narrower and runs earlier: a fixed, non-negotiable rejection of
specific forbidden scopes, regardless of what the compiler's `reason`
claims justifies them. The failure mode the wildcard entries exist to
catch isn't "the compiler was wrong about scope" (ordinary miss,
expected, measured via precision/recall) -- it's "the compiler resolved
its own uncertainty by proposing unlimited authority", which would show
up as excellent recall and be a completely different, much worse
problem. The process.exec entry (v0.2.1) is the same mechanism applied
to a different failure mode: dangerous execution primitives forbidden
outright as defense in depth, on top of (idhn.enforcement.process's)
allowlist model already excluding anything never granted.

Once v0.2-C's ceiling exists, "unless justified" exceptions belong
there (compare the proposal against an organizational maximum it's
allowed to reach, not against free-text reasoning) -- there's no
ceiling to be compatible with yet, so there's no exception path here.
"""
from __future__ import annotations

from idhn.compiler.parser import CapabilityDecision

# (resource, operation) -> scopes that are rejected outright, never
# granted regardless of confidence or stated reason.
_FORBIDDEN_SCOPES = {
    ("filesystem", "write"): {"/workspace/**", "/**", "**", "*"},
    ("filesystem", "read"): {"/**", "**", "*"},  # /workspace/** read is the normal, expected grant -- not banned
    ("network", "connect"): {"*", "**"},
    # v0.2.1: dangerous execution primitives. Inert against every
    # already-recorded compiler-v0.2-b0 decision: none of the 24 tasks'
    # frozen `predicted` sets propose any of these, so this doesn't
    # change any existing output, only guards against a future compiler
    # proposing one.
    ("process", "exec"): {"sudo", "docker", "mount", "nsenter"},
}


def check_budget(decisions: list[CapabilityDecision]) -> list[str]:
    """Return violation messages; empty means the proposal stays within
    budget. Called on every compile, regardless of confidence -- a
    "just in case" high-confidence /workspace/** is exactly as rejected
    as a hedging low-confidence one.
    """
    violations: list[str] = []
    for i, d in enumerate(decisions):
        forbidden = _FORBIDDEN_SCOPES.get((d.resource, d.operation), set())
        if d.scope in forbidden:
            violations.append(
                f"item {i}: {d.resource}.{d.operation} scope {d.scope!r} is forbidden outright -- "
                f"rejected regardless of stated reason ({d.reason!r})"
            )
    return violations
