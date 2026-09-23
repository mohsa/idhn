"""Strict parsing of raw LLM output into a Capability IR.

LLM output -> strict parser -> Capability IR -> schema validation ->
(later: hard ceiling -> policy engine -> enforcement). This module is
only the first two steps of that pipeline. Malformed or nonsensical
output is a rejection, not a best-effort guess at what the model
"probably meant" -- the compiler is untrusted; guessing on its behalf
would just move the untrusted part somewhere less visible.
"""
from __future__ import annotations

import re
from typing import Any

import yaml

ALLOWED_TOP_LEVEL_KEYS = {"filesystem", "process", "network", "git", "secrets"}

_YAML_FENCE_RE = re.compile(r"```(?:yaml)?\s*\n(.*?)```", re.DOTALL)


class CompilerOutputError(Exception):
    """Raised when the LLM's output can't be parsed into a Capability IR."""


def extract_yaml(raw_output: str) -> str:
    """Pull a YAML document out of raw model output, tolerating a
    markdown fence around it even though the prompt asks for none.
    """
    match = _YAML_FENCE_RE.search(raw_output)
    if match:
        return match.group(1)
    return raw_output


def parse_capability_ir(raw_output: str) -> dict[str, Any]:
    yaml_text = extract_yaml(raw_output)
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise CompilerOutputError(f"could not parse YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise CompilerOutputError(f"expected a YAML mapping at the top level, got {type(data).__name__}")

    unknown_keys = set(data) - ALLOWED_TOP_LEVEL_KEYS
    if unknown_keys:
        raise CompilerOutputError(f"unknown top-level keys: {sorted(unknown_keys)}")

    return data


def validate_capability_ir(data: dict[str, Any]) -> list[str]:
    """Structural sanity checks -- not the hard ceiling (v0.2-C territory),
    just "is this shaped like a capability envelope at all".
    """
    errors: list[str] = []

    fs = data.get("filesystem")
    if fs is not None:
        if not isinstance(fs, dict):
            errors.append("filesystem must be a mapping")
        else:
            for key in ("read", "write", "deny"):
                if key in fs and not isinstance(fs[key], list):
                    errors.append(f"filesystem.{key} must be a list")

    net = data.get("network")
    if net is not None:
        if not isinstance(net, dict):
            errors.append("network must be a mapping")
        else:
            if "default" in net and net["default"] not in ("allow", "deny"):
                errors.append("network.default must be 'allow' or 'deny'")
            for key in ("allow", "deny"):
                if key in net and not isinstance(net[key], list):
                    errors.append(f"network.{key} must be a list")

    proc = data.get("process")
    if proc is not None:
        if not isinstance(proc, dict):
            errors.append("process must be a mapping")
        elif "allow" in proc and not isinstance(proc["allow"], (list, dict)):
            errors.append("process.allow must be a list or a category mapping")

    return errors
