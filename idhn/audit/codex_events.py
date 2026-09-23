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


def _first(d: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
    return None


def _extract_binary(command: str) -> str | None:
    """Return the binary actually run, unwrapping codex's `bash -lc "..."`.

    Every command_execution item wraps the real command as
    `/bin/bash -lc "<inner>"` (observed directly in a live run), so taking
    the first token of the outer command always yields "/bin/bash" and
    nothing ever matches a `process.allow` grant like "git" or "node".
    Unwrap one level and normalize to a basename so "/usr/bin/git",
    "./git", and "git" all match the same grant.
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
        return Path(inner_parts[0]).name

    return Path(parts[0]).name


def parse_codex_events(path: Path) -> list[dict[str, str]]:
    """Return [{"resource", "operation", "target"}, ...] usage records."""
    if not path.exists():
        return []

    records: list[dict[str, str]] = []
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
                        records.append({"resource": "process", "operation": "exec", "target": binary})
            elif item_type == "file_change":
                changes = _first(item, "changes", "files") or []
                if isinstance(changes, list):
                    for change in changes:
                        if not isinstance(change, dict):
                            continue
                        change_path = _first(change, "path", "file", "filename")
                        if change_path:
                            records.append(
                                {"resource": "filesystem", "operation": "write", "target": str(change_path)}
                            )

    return records
