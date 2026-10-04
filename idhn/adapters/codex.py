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
        #
        # `-c web_search="disabled"` closes a real, confirmed containment
        # gap: Codex's native `web_search`/`open_page` tool is a
        # PROVIDER-SIDE capability -- it fetches the page using OpenAI's
        # own backend infrastructure, not a process inside this
        # container, so it never touches the agent's network namespace,
        # the `internal`-only Docker network, or the IDHN gateway at all.
        # A manifest's `network.deny`/default-deny is enforced entirely
        # by the container/gateway boundary (idhn/network/proxy.py); this
        # tool sits entirely outside that boundary, so no network policy
        # can ever see or govern it (confirmed directly: a real run asked
        # to access a non-allowlisted host got real page content back via
        # `web_search`, with zero entries in the gateway's own event log
        # -- see docs/web-search-out-of-band-capability.md). Enabled by
        # default by Codex itself ("`[features].web_search` is deprecated
        # because web search is enabled by default" -- Codex's own error
        # message, when probed), not something IDHN ever opted into.
        # `"disabled"` is one of Codex's own documented values
        # (`"live"`/`"indexed"`/`"cached"`/`"disabled"`), confirmed to
        # remove the tool from the model's toolset entirely (re-verified:
        # the same task then falls back to an ordinary shell command,
        # which correctly goes through the container network and gets
        # denied by gateway policy like anything else) -- not a sandbox
        # setting IDHN is faking or a flag that merely discourages use.
        return [
            "codex",
            "exec",
            "--sandbox",
            "danger-full-access",
            "--skip-git-repo-check",
            "--json",
            "-c",
            'web_search="disabled"',
            task,
        ]
