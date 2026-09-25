"""Filesystem enforcement: turn `capabilities.filesystem` into a concrete
container mount plan.

This is OS/container-enforced, not Python code inspecting every open():
each MountSpec becomes a real Linux bind mount or tmpfs, so a path
outside its granted write scope is unwritable at the kernel level --
`EROFS`, not a policy check that something forgot to call -- and a
masked deny path serves empty content regardless of what tool tries to
read it.

The base workspace mount is an EMPTY tmpfs, not the real repo -- default
deny, not "everything except what's masked". Every `read` and `write`
pattern gets its own bind mount layered on top of that empty base (read
-> ro, write -> rw; a `rw` mount is inherently readable too, so `write`
alone still satisfies "read" for that path); every `deny` pattern gets a
mask layered on top of THAT, so it shadows even a broad read/write grant
it's nested inside. A path covered by neither a read nor a write pattern
simply doesn't exist inside the container (`ENOENT`), not merely
unlisted-but-still-reachable through some broader mount -- found the hard
way (v0.2-B's T01 causal ablation): before this, removing a `read` grant
from a manifest had NO effect at all, because the entire real repo was
always bind-mounted read-only as the base regardless of what `read`
declared. `read`/`write`/`deny` are all mechanically enforced now, not
two of three.

Scope, deliberately narrow: only patterns shaped like
"/workspace/dir/**" (a subtree) or "/workspace/exact/file" (no
wildcard) are translated into mounts -- those are the only shapes IDHN's
manifests actually use. Anything else is left alone (audited via
PolicyEngine, not mechanically enforced) rather than guessing at intent.
The single exception is "/workspace/**" itself (the ordinary, expected
"give me the whole repo" read grant, explicitly exempted from budget
rejection unlike its write counterpart) -- `_relpath` returns `("", True)`
for it, which mounts `host_workspace` directly at `WORKSPACE_ROOT`
instead of at a malformed trailing-slash path.

Also deliberately narrow: only patterns under the workspace root get a
mount. Patterns like "/home/**" or "/root/**" aren't mechanically masked
here, because nothing from the host is mounted at those paths to begin
with (see docker/compose.yaml) -- except /home/agent/.codex/auth.json,
which the agent legitimately needs to authenticate and isn't what those
deny patterns exist to guard against.

A tmpfs mask's target directory, and every bind mount's target, must
exist as the right kind of thing before this plan is applied -- with an
empty tmpfs base, that's true of every single mount now, not just deny
masks. idhn/runtime/docker.py creates missing mountpoints on the host
first (using MountSpec.is_dir to know which kind), except for `read`
targets that don't exist on the host at all, which get skipped rather
than fabricated -- there's nothing sensible to read, and creating an
empty placeholder would corrupt the actual corpus repo the way the
write-side bug once did (see MountSpec.is_dir's docstring).
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


def _relpath(pattern: str, widen_trailing_wildcard: bool = False) -> tuple[str, bool] | None:
    """Return (relative_path, is_directory_pattern) for a workspace-scoped
    pattern this module knows how to translate, or None if it doesn't.

    `rel == ""` means "the whole workspace" -- the caller mounts
    host_workspace/WORKSPACE_ROOT directly rather than a path with a
    dangling trailing slash.

    `widen_trailing_wildcard`, READ ONLY (see _bind_mounts_for): two
    wildcard shapes that can't be mounted any more precisely than a
    containing directory -- Docker bind mounts are whole-path, not
    glob-filtered -- get treated as "mount that directory recursively"
    instead of silently dropped, when this is set:
      - a glob confined to the final path segment ("tests/*.py", "*.json")
      - an explicit recursive-subtree glob ("src/**/*.js")
    Under v0.1's whole-repo-readable base a pattern like this being
    unsupported was a harmless no-op (the file was reachable anyway);
    under v0.2.1's default-deny base it isn't -- a dropped READ pattern
    makes the whole directory invisible. Found via two real, different
    failures: T11's test harness broke outright ("tests/ not found") on
    "tests/*.py", and T18's on "src/**/*.js" (`Cannot find module
    '../src/parser'` -- a real source file made invisible even though the
    compiler clearly intended to grant it, just one level more nested
    than the first fix covered). Deliberately NOT applied to `write`: the
    only write pattern of either shape across all 24 tasks (T23's
    "/workspace/eslint.config.*") has no directory prefix at all, so
    widening it would mount the ENTIRE workspace read-write -- a much
    worse failure mode (runaway over-grant) than the one being fixed.
    Read a missing directory wrong and a harness fails loudly; write the
    whole repo by accident and nothing fails loudly at all. A wildcard
    anywhere else (e.g. "*/config.json", or more than one "**") stays
    unsupported either way -- genuinely ambiguous which directory it
    would even mean, and not a shape this corpus's compiler output ever
    produces.
    """
    if pattern == f"{WORKSPACE_ROOT}/**":
        return "", True
    if not pattern.startswith(WORKSPACE_ROOT + "/"):
        return None
    rel = pattern[len(WORKSPACE_ROOT) + 1 :]
    if rel.endswith("/**"):
        return rel[: -len("/**")], True
    if "*" not in rel and "?" not in rel:
        return rel, False
    if not widen_trailing_wildcard:
        return None
    if "/**/" in rel:
        prefix, _, suffix = rel.partition("/**/")
        if "*" not in prefix and "?" not in prefix and "/" not in suffix:
            return prefix, True
        return None
    head, _, tail = rel.rpartition("/")
    if ("*" in tail or "?" in tail) and "*" not in head and "?" not in head:
        return head, True
    return None


def _bind_mounts_for(patterns: list[str], host_workspace: str, mode: str) -> list[MountSpec]:
    """Build one bind MountSpec per read/write pattern -- the only
    difference between the two is `mode` ("ro" vs "rw"), so both loops
    share this instead of duplicating the pattern-parsing/mount-building
    logic. Trailing-wildcard widening (see _relpath) only applies to
    `mode == "ro"` (read) -- see _relpath's docstring for why write stays
    conservative.
    """
    mounts: list[MountSpec] = []
    for pattern in patterns:
        parsed = _relpath(pattern, widen_trailing_wildcard=(mode == "ro"))
        if parsed is None:
            continue
        rel, is_dir = parsed
        container_path = WORKSPACE_ROOT if rel == "" else f"{WORKSPACE_ROOT}/{rel}"
        host_path = host_workspace if rel == "" else f"{host_workspace}/{rel}"
        mounts.append(
            MountSpec(
                container_path=container_path,
                mode=mode,
                kind="bind",
                host_path=host_path,
                is_dir=is_dir,
            )
        )
    return mounts


def build_mount_plan(capabilities: dict[str, Any], host_workspace: str) -> list[MountSpec]:
    """Compute the mounts that give `capabilities.filesystem` real teeth.

    Base workspace is an empty tmpfs -- default deny. Each `read` pattern
    gets a read-only bind mount layered on top; each `write` pattern gets
    a read-write one (Linux mount nesting means the more specific mount
    wins within its own subtree, regardless of list order, so a `write`
    nested inside a broader `read` correctly ends up read-write); each
    `deny` pattern gets an empty tmpfs (directory) or the empty file stub
    (exact file) layered on top of all of that, masking it even if it's
    nominally covered by a broad `read` or `write` grant.
    """
    fs = capabilities.get("filesystem", {}) or {}
    plan: list[MountSpec] = [
        MountSpec(container_path=WORKSPACE_ROOT, mode="ro", kind="tmpfs")
    ]

    plan.extend(_bind_mounts_for(fs.get("read", []) or [], host_workspace, "ro"))
    plan.extend(_bind_mounts_for(fs.get("write", []) or [], host_workspace, "rw"))

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
