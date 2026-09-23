"""Strict parsing of raw LLM output into a Capability IR.

LLM output -> strict parser -> Capability IR -> schema validation ->
budget check -> (later: hard ceiling -> policy engine -> enforcement).
This module is only the parse-and-validate steps. Malformed or
nonsensical output is a rejection, not a best-effort guess at what the
model "probably meant" -- the compiler is untrusted; guessing on its
behalf would just move the untrusted part somewhere less visible.

v0.2-B schema: a flat list of individual capability decisions, each
carrying categorical confidence and a reason, not a nested
filesystem/process/network manifest fragment (that was v0.2-A's shape).
Confidence isn't used to gate anything yet -- v0.2-B only collects it,
per the design note that added it: "later we can ask a very valuable
empirical question: when the compiler says low, how often does runtime
actually require that capability."
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

ALLOWED_RESOURCES = {"filesystem", "process", "network", "git", "secrets"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}

_YAML_FENCE_RE = re.compile(r"```(?:yaml)?\s*\n(.*?)```", re.DOTALL)


class CompilerOutputError(Exception):
    """Raised when the LLM's output can't be parsed into a Capability IR."""


@dataclass(frozen=True)
class CapabilityDecision:
    resource: str
    operation: str
    scope: str
    confidence: str
    reason: str

    def as_tuple(self) -> tuple[str, str, str]:
        """(resource, operation, scope) -- the identity used for set
        comparisons (I, P, E, M, U in idhn.audit.authority_sets).
        Confidence/reason are metadata about the decision, not part of
        what makes two capabilities "the same" one.
        """
        return (self.resource, self.operation, self.scope)


def extract_yaml(raw_output: str) -> str:
    """Pull a YAML document out of raw model output, tolerating a
    markdown fence around it even though the prompt asks for none.
    """
    match = _YAML_FENCE_RE.search(raw_output)
    if match:
        return match.group(1)
    return raw_output


def parse_capability_ir(raw_output: str) -> list[CapabilityDecision]:
    yaml_text = extract_yaml(raw_output)
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise CompilerOutputError(f"could not parse YAML: {exc}") from exc

    if isinstance(data, dict) and "capabilities" in data:
        items = data["capabilities"]
    else:
        items = data

    if not isinstance(items, list):
        raise CompilerOutputError(f"expected a YAML list of capability decisions, got {type(items).__name__}")

    decisions: list[CapabilityDecision] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise CompilerOutputError(f"item {i}: expected a mapping, got {type(item).__name__}")

        # Tolerate both the nested `capability: {resource, operation, scope}`
        # wrapper and a flat shape -- the schema in the prompt asks for the
        # nested one, but strict-parsing a superficial format variation the
        # model didn't get exactly right is different from accepting
        # nonsensical content.
        cap = item.get("capability", item)
        if not isinstance(cap, dict):
            raise CompilerOutputError(f"item {i}: 'capability' must be a mapping")

        resource = cap.get("resource")
        operation = cap.get("operation")
        scope = cap.get("scope")
        confidence = item.get("confidence", cap.get("confidence"))
        reason = item.get("reason", cap.get("reason"))

        missing = [
            name
            for name, value in (("resource", resource), ("operation", operation), ("scope", scope), ("confidence", confidence))
            if value is None
        ]
        if missing:
            raise CompilerOutputError(f"item {i}: missing required field(s): {missing}")

        if resource not in ALLOWED_RESOURCES:
            raise CompilerOutputError(f"item {i}: unknown resource {resource!r} (allowed: {sorted(ALLOWED_RESOURCES)})")
        if confidence not in ALLOWED_CONFIDENCE:
            raise CompilerOutputError(f"item {i}: confidence must be one of {sorted(ALLOWED_CONFIDENCE)}, got {confidence!r}")

        decisions.append(
            CapabilityDecision(
                resource=str(resource),
                operation=str(operation),
                scope=str(scope),
                confidence=str(confidence),
                reason=str(reason or ""),
            )
        )

    return decisions


def validate_capability_ir(decisions: list[CapabilityDecision]) -> list[str]:
    """Structural sanity checks beyond parsing -- not the hard ceiling
    (v0.2-C territory), just "is every decision shaped sensibly".
    """
    errors: list[str] = []
    for i, d in enumerate(decisions):
        if not d.scope.strip():
            errors.append(f"item {i}: empty scope")
        if d.resource == "filesystem" and d.operation not in ("read", "write"):
            errors.append(f"item {i}: filesystem operation must be 'read' or 'write', got {d.operation!r}")
        if d.resource == "network" and d.operation not in ("connect",):
            errors.append(f"item {i}: network operation must be 'connect', got {d.operation!r}")
    return errors


def to_capabilities_dict(decisions: list[CapabilityDecision]) -> dict[str, Any]:
    """Collapse the flat decision list into the nested
    filesystem/process/network shape idhn.core.manifest and
    idhn.audit.authority_sets already work with, so this doesn't require
    a second parallel representation everywhere else in the codebase.
    Confidence/reason are dropped here deliberately -- this view is for
    "what would this grant", not "how sure was the compiler".
    """
    result: dict[str, Any] = {}
    for d in decisions:
        if d.resource == "filesystem":
            bucket = result.setdefault("filesystem", {}).setdefault(d.operation, [])
        elif d.resource == "process":
            bucket = result.setdefault("process", {}).setdefault("allow", [])
        elif d.resource == "network":
            bucket = result.setdefault("network", {}).setdefault("allow", [])
        elif d.resource == "git":
            bucket = result.setdefault("git", {}).setdefault("allow", [])
        else:
            bucket = result.setdefault(d.resource, {}).setdefault("allow", [])
        if d.scope not in bucket:
            bucket.append(d.scope)
    return result
