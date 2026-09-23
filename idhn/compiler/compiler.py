"""Offline capability compiler (v0.2-A).

Given only a task description and limited, non-live repository context
(idhn.compiler.context), proposes an initial capability envelope --
before the agent has run, before it has any real access.

This module is untrusted, same as everything else under idhn/compiler/:
its output is a proposal, checked by parser.py's strict
parse-then-validate boundary, and not wired into enforcement (core/,
enforcement/) at all yet. "The model may propose authority. It never
grants authority."

The LLM call itself is injected as `complete`, a plain str -> str
function, so this module doesn't hardcode Codex or any other backend --
see idhn/cli.py's `compile` command for what's wired in for now (Codex
CLI in one-shot mode, no workspace access, via
idhn.runtime.docker.run_codex_prompt).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from idhn.compiler.context import RepoContext
from idhn.compiler.parser import CompilerOutputError, parse_capability_ir, validate_capability_ir
from idhn.compiler.prompts import build_prompt

Completer = Callable[[str], str]


@dataclass
class CompileResult:
    prompt: str
    raw_output: str
    capabilities: dict[str, Any] | None
    errors: list[str]

    @property
    def ok(self) -> bool:
        return self.capabilities is not None and not self.errors


def compile_task(task: str, context: RepoContext, complete: Completer) -> CompileResult:
    prompt = build_prompt(task, context)
    raw_output = complete(prompt)

    try:
        capabilities = parse_capability_ir(raw_output)
    except CompilerOutputError as exc:
        return CompileResult(prompt=prompt, raw_output=raw_output, capabilities=None, errors=[str(exc)])

    errors = validate_capability_ir(capabilities)
    if errors:
        return CompileResult(prompt=prompt, raw_output=raw_output, capabilities=None, errors=errors)

    return CompileResult(prompt=prompt, raw_output=raw_output, capabilities=capabilities, errors=[])
