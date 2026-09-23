"""Offline capability compiler (v0.2-A/B).

Given only a task description and limited, non-live repository context
(idhn.compiler.context), proposes an initial capability envelope --
before the agent has run, before it has any real access.

This module is untrusted, same as everything else under idhn/compiler/:
its output is a proposal, checked by parser.py's strict
parse-then-validate boundary and budget.py's deterministic rejection of
unscoped-wildcard proposals, and not wired into enforcement (core/,
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
from typing import Callable

from idhn.compiler.budget import check_budget
from idhn.compiler.context import RepoContext
from idhn.compiler.parser import CapabilityDecision, CompilerOutputError, parse_capability_ir, validate_capability_ir
from idhn.compiler.prompts import build_prompt

Completer = Callable[[str], str]


@dataclass
class CompileResult:
    prompt: str
    raw_output: str
    decisions: list[CapabilityDecision]
    errors: list[str]
    budget_violations: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors and not self.budget_violations


def compile_task(task: str, context: RepoContext, complete: Completer) -> CompileResult:
    prompt = build_prompt(task, context)
    raw_output = complete(prompt)

    try:
        decisions = parse_capability_ir(raw_output)
    except CompilerOutputError as exc:
        return CompileResult(prompt=prompt, raw_output=raw_output, decisions=[], errors=[str(exc)], budget_violations=[])

    errors = validate_capability_ir(decisions)
    if errors:
        return CompileResult(prompt=prompt, raw_output=raw_output, decisions=[], errors=errors, budget_violations=[])

    budget_violations = check_budget(decisions)
    return CompileResult(prompt=prompt, raw_output=raw_output, decisions=decisions, errors=[], budget_violations=budget_violations)
