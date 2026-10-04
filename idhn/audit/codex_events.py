"""Best-effort parsing of `codex exec --json` output into capability-usage records.

Codex's own JSONL event stream is the only source IDHN has in v0.1 for
filesystem/process capability usage: there's no syscall-level
interception for those resources yet (see README's "known
limitations"), and network is the only resource instrumented by the
gateway itself.

Verified against real `codex exec --json` output (GPT-5.1-Codex,
2026-09-23): `command_execution` items have `command`/`exit_code`, and
`file_change` items have a `changes` array of `{"path", "kind"}`. Field
extraction here is still defensively tolerant (`_first` tries alternate
key names) since the schema isn't a stable wire format OpenAI guarantees
(see openai/codex#41216, a downstream-parser-breakage report against a
real schema change) -- it degrades by skipping unrecognized shapes
rather than raising.

KNOWN GAP, also observed in a real run: Codex doesn't always use its
structured `file_change` tool to edit files. In one run it wrote the fix
via an inline Python heredoc inside a `command_execution` (`python -
<<'PY' ... Path(...).write_bytes(...)`), which this parser has no way to
recognize as a filesystem write -- `command_execution` is opaque shell
text, not a structured record. That run's "filesystem writes" undercount
is real, not a parser bug to fix with a shell-text heuristic (too
fragile for what it'd buy); it means this metric is a lower bound, not
an exact count.
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

# Every item type a real run has ever produced that executes LOCALLY,
# inside the agent container -- subject to IDHN's filesystem/process
# mount+PATH enforcement (command_execution, file_change) or carrying no
# resource access at all (agent_message, error). Surveyed directly
# against every codex-events.jsonl this project has ever recorded, not
# guessed from documentation (see docs/web-search-out-of-band-capability.md
# for the full survey, including the item types that are NOT in this set
# -- `web_search`, confirmed to execute entirely on Codex's own backend,
# reaching the real internet without ever touching the container's
# network namespace or the IDHN gateway).
#
# out_of_band_items() below treats anything NOT in this set as suspect by
# construction -- a newly-introduced Codex tool this project has never
# seen would also be flagged, not silently passed through, which is the
# point: this is deliberately a denylist-of-the-complement (allowlist the
# known-local shapes, flag everything else) rather than a list of named
# out-of-band tools to keep growing by hand.
KNOWN_LOCAL_ITEM_TYPES = frozenset({"agent_message", "command_execution", "file_change", "error"})


def _first(d: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
    return None


_TRAILING_SHELL_PUNCTUATION = ";&|()"


def _extract_binary(command: str) -> str | None:
    """Return the binary actually run, unwrapping codex's `bash -lc "..."`.

    Every command_execution item wraps the real command as
    `/bin/bash -lc "<inner>"` (observed directly in a live run), so taking
    the first token of the outer command always yields "/bin/bash" and
    nothing ever matches a `process.allow` grant like "git" or "node".
    Unwrap one level and normalize to a basename so "/usr/bin/git",
    "./git", and "git" all match the same grant.

    shlex doesn't understand shell control operators (`;`, `&&`, ...), so
    "pwd; rg --files" tokenizes as ["pwd;", "rg", ...] -- strip trailing
    punctuation off the first inner token rather than reporting "pwd;" as
    a distinct, never-granted "binary" (observed directly in a live run).
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not parts:
        return None

    if Path(parts[0]).name in ("bash", "sh") and len(parts) >= 3 and parts[1] in ("-lc", "-c"):
        inner = parts[2]
        try:
            inner_parts = shlex.split(inner)
        except ValueError:
            inner_parts = inner.split()
        if not inner_parts:
            return None
        name = inner_parts[0].rstrip(_TRAILING_SHELL_PUNCTUATION)
        return Path(name).name if name else None

    name = parts[0].rstrip(_TRAILING_SHELL_PUNCTUATION)
    return Path(name).name if name else None


def _unwrap_bash(command: str) -> str:
    """Return the inner command text for `bash -lc "..."`/`sh -c "..."`
    (every command_execution item is wrapped this way -- see
    _extract_binary), or `command` unchanged if it isn't.
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if parts and Path(parts[0]).name in ("bash", "sh") and len(parts) >= 3 and parts[1] in ("-lc", "-c"):
        return parts[2]
    return command


def iter_command_executions(path: Path) -> list[dict[str, Any]]:
    """Every command_execution item's inner command text (bash -lc "..."
    unwrapped, but NOT reduced to just the binary name) and outcome, in
    order.

    Lower-level than parse_codex_events' process.exec records, which
    only keep the binary that ran -- callers that need to know what a
    command actually referenced as an ARGUMENT (idhn.audit.m3_report's
    deny-masked-read detection: did a command's own text reference a
    deny-listed path, as opposed to that path merely appearing in some
    unrelated command's OUTPUT, e.g. `ls -la` listing it) use this
    instead of re-parsing the JSONL themselves.
    """
    if not path.exists():
        return []

    results: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("type") != "item.completed":
                continue
            item = event.get("item")
            if not isinstance(item, dict) or item.get("type") != "command_execution":
                continue
            command = _first(item, "command", "cmd")
            if isinstance(command, list):
                command = " ".join(str(part) for part in command)
            if isinstance(command, str) and command.strip():
                results.append({"command": _unwrap_bash(command.strip()), "succeeded": _succeeded(item)})
    return results


def _succeeded(item: dict[str, Any]) -> bool | None:
    """Best-effort outcome for a command_execution/file_change item.

    True/False when we have real signal (exit_code, or an explicit
    failure status); None when we don't know -- callers should treat
    None as "can't confirm either way", not as success.
    """
    exit_code = item.get("exit_code")
    if isinstance(exit_code, int):
        return exit_code == 0
    status = item.get("status")
    if status in ("failed", "error"):
        return False
    if status == "completed":
        return True
    return None


def parse_codex_events(path: Path) -> list[dict[str, Any]]:
    """Return [{"resource", "operation", "target", "succeeded"}, ...].

    `succeeded` is True/False/None (see _succeeded) -- callers deciding
    whether an observed action is a real enforcement gap (succeeded
    despite being policy-denied) versus a correctly-blocked attempt
    (failed) need this, not just what was attempted.
    """
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("type") != "item.completed":
                continue

            item = event.get("item")
            if not isinstance(item, dict):
                continue

            item_type = item.get("type")
            if item_type == "command_execution":
                command = _first(item, "command", "cmd")
                if isinstance(command, list):
                    command = " ".join(str(part) for part in command)
                if isinstance(command, str) and command.strip():
                    binary = _extract_binary(command.strip())
                    if binary:
                        records.append(
                            {
                                "resource": "process",
                                "operation": "exec",
                                "target": binary,
                                "succeeded": _succeeded(item),
                            }
                        )
            elif item_type == "file_change":
                changes = _first(item, "changes", "files") or []
                succeeded = _succeeded(item)
                if isinstance(changes, list):
                    for change in changes:
                        if not isinstance(change, dict):
                            continue
                        change_path = _first(change, "path", "file", "filename")
                        if change_path:
                            records.append(
                                {
                                    "resource": "filesystem",
                                    "operation": "write",
                                    "target": str(change_path),
                                    "succeeded": succeeded,
                                }
                            )

    return records


def out_of_band_items(path: Path) -> list[dict[str, Any]]:
    """Every completed item whose type is NOT one of KNOWN_LOCAL_ITEM_TYPES
    -- i.e. something Codex did that did not execute as a process or file
    edit inside this container at all. Returns the raw item dicts
    (whatever shape that tool happens to use), not normalized capability
    records -- there's no (resource, operation, target) shape to
    normalize to in general, since by definition this covers tools IDHN
    has no model of.

    Confirmed directly (docs/web-search-out-of-band-capability.md): a
    `web_search`/`open_page` item fetches the real page using Codex's own
    backend infrastructure, never touching the agent's network namespace
    or the IDHN gateway -- no container/network enforcement can see or
    govern it, regardless of what the manifest's network.allow/deny says.
    This is the regression test this function exists for: it fails
    closed (flags anything unrecognized) rather than needing every
    out-of-band tool enumerated by name ahead of time.
    """
    if not path.exists():
        return []

    items: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("type") != "item.completed":
                continue
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") not in KNOWN_LOCAL_ITEM_TYPES:
                items.append(item)
    return items


def extract_agent_messages(path: Path) -> list[str]:
    """Return the text of every agent_message item, in order.

    Used by idhn.runtime.docker.run_codex_prompt (the compiler's
    one-shot LLM backend, v0.2-A) to get Codex's actual answer text out
    of the --json stream -- the agent_message is the only place a plain
    text answer to "propose a capability envelope" shows up; everything
    else in the stream is tool-call bookkeeping.
    """
    if not path.exists():
        return []
    messages: list[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("type") != "item.completed":
                continue
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str):
                    messages.append(text)
    return messages
