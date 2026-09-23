"""Adapter for the Codex CLI agent.

IDHN treats Codex as one interchangeable agent among many. This module's
only job is turning a task description into the command line that runs
Codex non-interactively inside the agent container -- it knows nothing
about policy, Docker, or enforcement. Future adapters (claude.py,
gemini.py, generic_mcp.py) live alongside it with the same shape.
"""
from __future__ import annotations


class CodexAdapter:
    name = "codex"

    def command(self, task: str) -> list[str]:
        # `codex exec` is Codex's non-interactive mode, suitable for running
        # headless with no TTY. `--full-auto` accepts the agent's own file
        # edits/command executions without interactive approval -- IDHN's
        # container and network isolation is the enforcement boundary here,
        # not Codex's own approval prompts.
        return ["codex", "exec", "--full-auto", task]
