"""Ground-truth filesystem-write observation: hash every file in the
workspace before and after a run, independent of HOW an agent wrote to
it.

idhn.audit.codex_events' write count is a LOWER BOUND by its own
docstring: it only recognizes Codex's structured `file_change` tool
calls, and Codex doesn't always use it -- an inline heredoc executed as
a `command_execution` (`node <<'NODE' ... fs.writeFileSync(...) NODE`,
observed directly in a real run) is opaque shell text with no
recognizable write record at all, so a real edit gets reported as zero
writes even though enforcement correctly allowed it and the file
genuinely changed. Parsing shell text harder was explicitly rejected
in that module's docstring as "too fragile for what it'd buy" -- and it
still would be, since there's no bounded set of ways to write a file
from a shell command. The fix isn't to parse harder, it's to stop
inferring writes from what the agent SAID it did and instead observe
what's actually different on disk, the same way the mount enforcement
itself doesn't care how a write happened.

Content-hashed, not mtime-based: this project has already hit real
cross-platform mtime/timing issues once this session (bind-mount
propagation on Windows/Docker Desktop), and a hash comparison is exact
regardless of clock resolution or filesystem timestamp granularity.
Works for any workspace, git repo or not -- it walks the real directory
tree, not git's index.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def snapshot(workspace_dir: Path) -> dict[str, str]:
    """path (relative to workspace_dir, forward-slash) -> sha256 hex
    digest, for every regular file under workspace_dir.

    Skips `.git/` (not part of the agent's writable surface via any
    manifest this codebase generates, and can be large/irrelevant to
    what an agent wrote). Skips a path that can't be read rather than
    raising -- a deny-masked directory looks empty from the host side
    too, and a transient permission hiccup shouldn't crash the run's
    bookkeeping.
    """
    result: dict[str, str] = {}
    if not workspace_dir.exists():
        return result
    for path in workspace_dir.rglob("*"):
        if not path.is_file():
            continue
        if ".git" in path.relative_to(workspace_dir).parts:
            continue
        rel = str(path.relative_to(workspace_dir)).replace("\\", "/")
        try:
            result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return result


def diff(before: dict[str, str], after: dict[str, str]) -> set[str]:
    """Paths added, modified, or removed between two snapshots."""
    return {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
