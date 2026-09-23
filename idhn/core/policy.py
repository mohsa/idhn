"""Deterministic allow/deny/escalate policy engine.

This module has no knowledge of Codex, Docker, or any specific agent
runtime -- it only maps ActionRequest -> PolicyDecision. Keep it that
way: no LLM calls, no network I/O, no side effects beyond reading the
manifest it was constructed with.
"""
from __future__ import annotations

from fnmatch import fnmatch

from idhn.core.manifest import Manifest
from idhn.core.models import ActionRequest, Decision, PolicyDecision


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(fnmatch(value, pattern) for pattern in patterns)


class PolicyEngine:
    """Evaluates ActionRequests against a single manifest, deterministically."""

    def __init__(self, manifest: Manifest):
        self.manifest = manifest

    def evaluate(self, request: ActionRequest) -> PolicyDecision:
        handler = getattr(self, f"_evaluate_{request.resource}", None)
        if handler is None:
            return self._escalate(request, f"no_policy_for_resource:{request.resource}")
        return handler(request)

    def _evaluate_filesystem(self, request: ActionRequest) -> PolicyDecision:
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

    def _evaluate_network(self, request: ActionRequest) -> PolicyDecision:
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

    def _evaluate_process(self, request: ActionRequest) -> PolicyDecision:
        proc = self.manifest.capabilities.get("process", {}) or {}
        return self._evaluate_named_list(request, proc, "process")

    def _evaluate_git(self, request: ActionRequest) -> PolicyDecision:
        git = self.manifest.capabilities.get("git", {}) or {}
        return self._evaluate_named_list(request, git, "git")

    def _evaluate_secrets(self, request: ActionRequest) -> PolicyDecision:
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
