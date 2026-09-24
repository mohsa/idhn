"""Filesystem enforcement: turn `capabilities.filesystem` into a concrete
container mount plan.

This is OS/container-enforced, not Python code inspecting every open():
each MountSpec becomes a real Linux bind mount or tmpfs, so a path
outside its granted write scope is unwritable at the kernel level --
`EROFS`, not a policy check that something forgot to call -- and a
masked deny path serves empty content regardless of what tool tries to
read it.

Scope, deliberately narrow: only patterns shaped like
"/workspace/dir/**" (a subtree) or "/workspace/exact/file" (no
wildcard) are translated into mounts -- those are the only shapes IDHN's
manifests actually use. Anything else is left alone (audited via
PolicyEngine, not mechanically enforced) rather than guessing at intent.

Also deliberately narrow: only patterns under the workspace root get a
mount. Patterns like "/home/**" or "/root/**" aren't mechanically masked
here, because nothing from the host is mounted at those paths to begin
with (see docker/compose.yaml) -- except /home/agent/.codex/auth.json,
which the agent legitimately needs to authenticate and isn't what those
deny patterns exist to guard against.

A tmpfs mask's target directory must already exist in the toy repo (even
if empty) before this plan is applied: the base workspace mount is
read-only, so the container runtime can't create a missing mountpoint
inside it at container-init time (found by actually running this --
`mkdirat ...: read-only file system`). idhn/runtime/docker.py creates
any missing directory on the host before generating mounts from this
plan, so this is handled automatically, not something every deny
pattern needs a hand-added placeholder for.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = "/workspace"

# A tracked, always-empty file used to mask a denied file path: bind-
# mounting this over e.g. /workspace/.env makes its real content
# unreadable regardless of what's actually on disk there.
EMPTY_FILE_STUB = str(Path(__file__).resolve().parent / "masks" / "empty-file")


@dataclass(frozen=True)
class MountSpec:
    container_path: str
    mode: str  # "ro" or "rw"
    kind: str  # "bind" or "tmpfs"
    host_path: str | None = None  # required for "bind", ignored for "tmpfs"
    is_dir: bool = False  # whether the target is a directory tree or an exact file

    # Whoever prepares mountpoints on the host (idhn.runtime.docker) needs
    # this: a write grant for a file that doesn't exist yet in the corpus
    # (e.g. `filesystem.write: /workspace/requirements.txt` on a repo with
    # no requirements.txt) has a source path that doesn't exist either,
    # and Docker's default behavior for a missing bind-mount source is to
    # create it as a DIRECTORY -- silently turning the intended file into
    # one, corrupting anything that tries to read or write it as a file
    # afterward. Found by actually running this: two corpus repos ended up
    # with directories named requirements.txt and package-lock.json.


def _relpath(pattern: str) -> tuple[str, bool] | None:
    """Return (relative_path, is_directory_pattern) for a workspace-scoped
    pattern this module knows how to translate, or None if it doesn't.
    """
    if not pattern.startswith(WORKSPACE_ROOT + "/"):
        return None
    rel = pattern[len(WORKSPACE_ROOT) + 1 :]
    if rel.endswith("/**"):
        return rel[: -len("/**")], True
    if "*" in rel or "?" in rel:
        return None
    return rel, False


def build_mount_plan(capabilities: dict[str, Any], host_workspace: str) -> list[MountSpec]:
    """Compute the mounts that give `capabilities.filesystem` real teeth.

    Base workspace is read-only; each `write` pattern gets its own
    read-write bind mount layered on top (Linux mount nesting means the
    more specific mount wins within its own subtree, regardless of list
    order); each `deny` pattern gets an empty tmpfs (directory) or the
    empty file stub (exact file) layered on top of THAT, masking it even
    though it's nominally still inside the broad `read` grant.
    """
    fs = capabilities.get("filesystem", {}) or {}
    plan: list[MountSpec] = [
        MountSpec(container_path=WORKSPACE_ROOT, mode="ro", kind="bind", host_path=host_workspace)
    ]

    for pattern in fs.get("write", []) or []:
        parsed = _relpath(pattern)
        if parsed is None:
            continue
        rel, is_dir = parsed
        plan.append(
            MountSpec(
                container_path=f"{WORKSPACE_ROOT}/{rel}",
                mode="rw",
                kind="bind",
                host_path=f"{host_workspace}/{rel}",
                is_dir=is_dir,
            )
        )

    for pattern in fs.get("deny", []) or []:
        parsed = _relpath(pattern)
        if parsed is None:
            continue
        rel, is_dir = parsed
        if is_dir:
            plan.append(MountSpec(container_path=f"{WORKSPACE_ROOT}/{rel}", mode="ro", kind="tmpfs"))
        else:
            plan.append(
                MountSpec(
                    container_path=f"{WORKSPACE_ROOT}/{rel}",
                    mode="ro",
                    kind="bind",
                    host_path=EMPTY_FILE_STUB,
                )
            )

    return plan
