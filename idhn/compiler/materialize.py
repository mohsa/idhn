"""Deterministic materialization: validated Capability IR -> enforceable
policy manifest.

task -> compile (LLM, untrusted) -> Capability IR -> **materialize (this
module, no LLM call)** -> policy manifest -> `idhn validate` -> `idhn run`.

This is the missing bridge between `idhn compile` (which only ever
produces `runs/<id>/compiled-envelope.yaml`, never wired into
enforcement) and `idhn run` (which requires a normal manifest). It is
pure data transformation -- no model call, no I/O beyond what its caller
hands it -- built entirely out of already-existing, already-tested
pieces:

  - idhn.compiler.parser's structural validation (ALLOWED_RESOURCES,
    ALLOWED_CONFIDENCE, validate_capability_ir) -- re-run here, not
    trusted from whatever shape is on disk. idhn/cli.py's `compile`
    command saves compiled-envelope.yaml BEFORE checking budget
    violations, so a file existing on disk is not proof it was ever a
    valid proposal; this module re-validates from the ground up every
    time, exactly like the compiler's own output always has been.
  - idhn.compiler.budget's check_budget -- the same deterministic
    rejection of unscoped-wildcard/forbidden-primitive proposals used at
    compile time, re-run here so a budget-violating envelope can never
    reach enforcement by a second path.
  - idhn.audit.authority_sets.infrastructure_authority() -- the fixed,
    task-independent floor (agent infrastructure + execution substrate +
    tool authority), unioned with the compiler's own predicted
    capabilities. This is the SAME authority math v0.2-B/v0.2.1 already
    use to constrain a benchmark task's execution leg by exactly what
    the compiler proposed (idhn.audit.authority_sets.to_manifest_
    capabilities) -- materialization is that same operation, now reached
    through a normal CLI command instead of only from inside the
    benchmark harness.
  - idhn.core.manifest.validate() -- the same manifest-shape validation
    every hand-written policies/examples/*.yaml is already held to, run
    one final time against the manifest this module just built, so a
    materialized manifest is never trusted more than a hand-written one.

Confidence and reason are compiler METADATA, not part of the capability
identity (idhn.compiler.parser.CapabilityDecision.as_tuple() already
drops them for exactly this reason) -- they inform a human reviewer
reading compiled-envelope.yaml, and are not consulted anywhere in this
module to admit, widen, or otherwise influence what gets enforced.

Does not touch idhn.adapters.codex.CodexAdapter (web_search stays
disabled by default, unrelated to this module) or idhn.enforcement.* --
materialization produces a manifest; mount plans, PATH shims, and
gateway policy are built from that manifest exactly the same way as any
other, by idhn.runtime.docker and idhn.core.policy, both unmodified here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from idhn.audit.authority_sets import infrastructure_authority, predicted_from_decisions, to_manifest_capabilities
from idhn.compiler.budget import check_budget
from idhn.compiler.parser import ALLOWED_CONFIDENCE, ALLOWED_RESOURCES, CapabilityDecision, validate_capability_ir
from idhn.core import manifest as manifest_module

DEFAULT_LIMITS = {"duration": "30m", "cpu": 2, "memory": "4g"}


class MaterializeError(Exception):
    """Raised when a compiled envelope cannot be deterministically
    materialized into an enforceable manifest: malformed/unvalidated IR,
    a budget violation, or a constructed manifest that itself fails
    idhn.core.manifest.validate(). In every case, nothing is returned --
    the compiler's output never reaches enforcement.
    """


@dataclass
class MaterializeResult:
    manifest_data: dict[str, Any]
    decisions: list[CapabilityDecision]
    predicted: set[tuple[str, str, str]]
    infrastructure: set[tuple[str, str, str]]


def decisions_from_envelope(envelope: Any) -> list[CapabilityDecision]:
    """Reconstruct CapabilityDecision objects from the on-disk
    compiled-envelope.yaml shape (a flat list of `{capability: {resource,
    operation, scope}, confidence, reason}` mappings, or the equivalent
    flat shape -- see idhn.compiler.parser.parse_capability_ir, which
    this mirrors field-for-field, not a loosened version of it).

    Deliberately re-validates every field from scratch rather than
    assuming a dict that happens to have the right keys already went
    through parse_capability_ir -- the whole point is that the on-disk
    file is not trusted just because it's shaped right.
    """
    if not isinstance(envelope, list):
        raise MaterializeError(f"expected a list of capability decisions, got {type(envelope).__name__}")

    decisions: list[CapabilityDecision] = []
    for i, item in enumerate(envelope):
        if not isinstance(item, dict):
            raise MaterializeError(f"item {i}: expected a mapping, got {type(item).__name__}")

        cap = item.get("capability", item)
        if not isinstance(cap, dict):
            raise MaterializeError(f"item {i}: 'capability' must be a mapping")

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
            raise MaterializeError(f"item {i}: missing required field(s): {missing}")
        if resource not in ALLOWED_RESOURCES:
            raise MaterializeError(f"item {i}: unknown resource {resource!r} (allowed: {sorted(ALLOWED_RESOURCES)})")
        if confidence not in ALLOWED_CONFIDENCE:
            raise MaterializeError(f"item {i}: confidence must be one of {sorted(ALLOWED_CONFIDENCE)}, got {confidence!r}")

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


def materialize(
    envelope: Any,
    task_id: str,
    task_description: str,
    limits: dict[str, Any] | None = None,
) -> MaterializeResult:
    """The deterministic bridge. Raises MaterializeError (never returns a
    partial/best-effort result) if the envelope is malformed, violates
    budget, or somehow still produces an invalid manifest.
    """
    decisions = decisions_from_envelope(envelope)

    structural_errors = validate_capability_ir(decisions)
    if structural_errors:
        raise MaterializeError("structural validation failed:\n" + "\n".join(f"  - {e}" for e in structural_errors))

    budget_violations = check_budget(decisions)
    if budget_violations:
        raise MaterializeError("budget violations (rejected, not a valid proposal):\n" + "\n".join(f"  - {v}" for v in budget_violations))

    # Confidence/reason are dropped at exactly this point -- as_tuple()
    # keeps only (resource, operation, scope), the only thing that ever
    # reaches to_manifest_capabilities below. Nothing downstream ever
    # sees a confidence or reason value.
    predicted = predicted_from_decisions(decisions)
    infra = infrastructure_authority()
    capabilities = to_manifest_capabilities(infra | predicted)

    manifest_data = {
        "version": "0.1",
        "task": {"id": task_id, "description": task_description},
        "capabilities": capabilities,
        "limits": limits or dict(DEFAULT_LIMITS),
    }

    manifest_errors = manifest_module.validate(manifest_data)
    if manifest_errors:
        raise MaterializeError("generated manifest failed validation:\n" + "\n".join(f"  - {e}" for e in manifest_errors))

    return MaterializeResult(manifest_data=manifest_data, decisions=decisions, predicted=predicted, infrastructure=infra)
