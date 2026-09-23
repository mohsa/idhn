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
        # `codex exec` is Codex's non-interactive mode.
        #
        # `--sandbox danger-full-access` (not `workspace-write`) is
        # deliberate, not a shortcut: `workspace-write` sandboxes each shell
        # command with bubblewrap, which needs to create a Linux user
        # namespace -- and the agent container runs with cap_drop: ALL, so
        # that silently fails (`bwrap: No permissions to create a new
        # namespace`) and Codex gives up without doing anything, exit code
        # 0, no error surfaced. `danger-full-access` skips Codex's own
        # internal sandbox entirely, which is fine here specifically
        # because IDHN's container/network isolation is the real
        # enforcement boundary -- Codex's own sandbox would be a redundant
        # second layer even if it worked. OpenAI's own docs call out
        # "an isolated CI runner or container" as the intended use case for
        # this flag; don't use it outside a container like this one.
        #
        # `--skip-git-repo-check` is needed because lab/repos/toy-app is
        # plain files, not a git repo (see lab/README.md for why). `--json`
        # emits Codex's own JSONL tool-call event stream on stdout, which
        # idhn/audit/codex_events.py parses for filesystem/process
        # capability usage.
        return [
            "codex",
            "exec",
            "--sandbox",
            "danger-full-access",
            "--skip-git-repo-check",
            "--json",
            task,
        ]
