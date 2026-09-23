"""Deterministic allow/deny/escalate policy engine.

This module has no knowledge of Codex, Docker, or any specific agent
runtime -- it only maps ActionRequest -> PolicyDecision. Keep it that
way: no LLM calls, no network I/O, no side effects beyond reading the
manifest it was constructed with.
"""
from __future__ import annotations

from fnmatch import fnmatch

from idhn.core.manifest import Manifest
from idhn.core.models import ActionRequest, Decision, ExecutionContext, PolicyDecision


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(fnmatch(value, pattern) for pattern in patterns)


def flatten_category_allowlist(allow: object) -> list[str]:
    """`process.allow` (and `.deny`) can be a flat list or a dict of named
    categories (discovery/inspection/development/...). Categories are for
    human readability only -- v0.1 does nothing semantic with the category
    name, it just flattens either shape to the actual set of binary names.
    Shared with idhn.enforcement.process, which builds the PATH-restricted
    shim directory from the same flattened set, so the declared policy and
    the mechanism enforcing it can't drift apart.
    """
    if isinstance(allow, dict):
        names: list[str] = []
        for binaries in allow.values():
            names.extend(binaries or [])
        return sorted(set(names))
    return sorted(set(allow or []))


class PolicyEngine:
    """Evaluates ActionRequests against a single manifest, deterministically."""

    def __init__(self, manifest: Manifest):
        self.manifest = manifest

    def evaluate(self, request: ActionRequest, context: ExecutionContext) -> PolicyDecision:
        handler = getattr(self, f"_evaluate_{request.resource}", None)
        if handler is None:
            return self._escalate(request, f"no_policy_for_resource:{request.resource}")
        return handler(request, context)

    def _evaluate_filesystem(self, request: ActionRequest, context: ExecutionContext) -> PolicyDecision:
        fs = self.manifest.capabilities.get("filesystem", {}) or {}
        deny_patterns = fs.get("deny", []) or []
        if _matches_any(request.target, deny_patterns):
            return self._deny(request, "explicit_deny")

        if request.action not in ("read", "write"):
            return self._escalate(request, f"unsupported_filesystem_action:{request.action}")

        allow_patterns = fs.get(request.action, []) or []
        if _matches_any(request.target, allow_patterns):
            return self._allow(request, "matched_allow_pattern")
        return self._deny(request, "no_matching_allow_pattern")

    def _evaluate_network(self, request: ActionRequest, context: ExecutionContext) -> PolicyDecision:
        # A non-empty delegation chain means this connect attempt didn't
        # come straight from the agent -- some internal service is making
        # it on the agent's behalf. That's governed by the narrower
        # `services` capability, not the agent's own `network` allowlist.
        # This is the transitive-capability check: an allowed hop (agent ->
        # service) does not inherit the service's own network access.
        if context.delegation_chain:
            return self._evaluate_transitive_network(request, context)

        net = self.manifest.capabilities.get("network", {}) or {}
        deny_patterns = net.get("deny", []) or []
        if _matches_any(request.target, deny_patterns):
            return self._deny(request, "explicit_deny")

        allow_patterns = net.get("allow", []) or []
        if _matches_any(request.target, allow_patterns):
            return self._allow(request, "matched_allow_pattern")

        if net.get("default", "deny") == "allow":
            return self._allow(request, "default_allow")
        return self._deny(request, "default_deny")

    def _evaluate_transitive_network(self, request: ActionRequest, context: ExecutionContext) -> PolicyDecision:
        service_name = context.delegation_chain[-1]
        services = self.manifest.capabilities.get("services", {}) or {}
        service_config = services.get(service_name)
        if service_config is None:
            return self._escalate(request, f"no_policy_for_service:{service_name}")

        # v0.1: every service exposes exactly one operation, "fetch".
        action_config = (service_config.get("actions", {}) or {}).get("fetch")
        if action_config is None:
            return self._deny(request, "service_action_not_declared:fetch")

        allow_hosts = [rule.get("host", "") for rule in (action_config.get("allow") or [])]
        deny_hosts = [rule.get("host", "") for rule in (action_config.get("deny") or [])]

        # Service host rules are a specific carve-out from a broad deny
        # (e.g. deny: ["*"]) -- the opposite shape from the direct-network
        # case above, where deny is the narrow exception -- so the allow
        # list has to be checked first here.
        if _matches_any(request.target, allow_hosts):
            return self._allow(request, "matched_service_allow_pattern")
        if _matches_any(request.target, deny_hosts):
            return self._deny(request, "transitive_capability_not_authorized")
        return self._deny(request, "transitive_capability_not_authorized")

    def _evaluate_process(self, request: ActionRequest, context: ExecutionContext) -> PolicyDecision:
        proc = self.manifest.capabilities.get("process", {}) or {}
        allow = flatten_category_allowlist(proc.get("allow", []))
        deny = flatten_category_allowlist(proc.get("deny", []))
        if request.target in deny:
            return self._deny(request, "explicit_deny")
        if request.target in allow:
            return self._allow(request, "matched_allow_list")
        return self._deny(request, "process_not_in_allow_list")

    def _evaluate_git(self, request: ActionRequest, context: ExecutionContext) -> PolicyDecision:
        git = self.manifest.capabilities.get("git", {}) or {}
        return self._evaluate_named_list(request, git, "git")

    def _evaluate_secrets(self, request: ActionRequest, context: ExecutionContext) -> PolicyDecision:
        secrets = self.manifest.capabilities.get("secrets", {}) or {}
        allow_patterns = secrets.get("allow", []) or []
        if _matches_any(request.target, allow_patterns):
            return self._allow(request, "matched_allow_pattern")
        if secrets.get("default", "deny") == "allow":
            return self._allow(request, "default_allow")
        return self._deny(request, "secrets_default_deny")

    def _evaluate_named_list(self, request: ActionRequest, section: dict, label: str) -> PolicyDecision:
        allow = section.get("allow", []) or []
        deny = section.get("deny", []) or []
        if request.target in deny:
            return self._deny(request, "explicit_deny")
        if request.target in allow:
            return self._allow(request, "matched_allow_list")
        return self._deny(request, f"{label}_not_in_allow_list")

    def _allow(self, request: ActionRequest, reason: str) -> PolicyDecision:
        return PolicyDecision(request=request, decision=Decision.ALLOW, reason=reason, policy_name=self.manifest.name)

    def _deny(self, request: ActionRequest, reason: str) -> PolicyDecision:
        return PolicyDecision(request=request, decision=Decision.DENY, reason=reason, policy_name=self.manifest.name)

    def _escalate(self, request: ActionRequest, reason: str) -> PolicyDecision:
        return PolicyDecision(request=request, decision=Decision.ESCALATE, reason=reason, policy_name=self.manifest.name)
