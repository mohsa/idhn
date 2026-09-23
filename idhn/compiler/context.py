"""Gather LIMITED, non-live repository metadata for the capability
compiler.

Deliberately not full filesystem access. The whole point of compiling
an envelope BEFORE granting access is that the compiler only sees what
we hand it in a prompt -- language signals, a file tree listing,
manifest filenames (never file contents), available tool names -- not
whatever it could discover by exploring the real repo, which is exactly
what the agent doesn't have yet at this point.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

LANGUAGE_MARKERS = {
    "package.json": "javascript/node",
    "requirements.txt": "python",
    "pyproject.toml": "python",
    "go.mod": "go",
    "Cargo.toml": "rust",
}

# Deliberately small and fixed -- what a v0.1 manifest can actually grant
# (see idhn/enforcement/process.py), not "whatever happens to be
# installed in this particular image". Keeps the compiler's proposal
# reproducible regardless of container details it was never shown.
AVAILABLE_TOOLS = ["pwd", "ls", "find", "cat", "rg", "python3", "pytest", "node", "npm", "git"]


@dataclass
class RepoContext:
    languages: list[str]
    file_tree: list[str]
    manifest_files: list[str]
    available_tools: list[str] = field(default_factory=lambda: list(AVAILABLE_TOOLS))


def gather_context(workspace_dir: Path, max_files: int = 200) -> RepoContext:
    """Walk the repo for cheap, structural signals only -- file names,
    never contents. This is what "task + repository/environment context"
    means at this stage: a compact summary we compose, not the agent
    exploring.
    """
    manifest_files = [name for name in LANGUAGE_MARKERS if (workspace_dir / name).exists()]
    languages = sorted({LANGUAGE_MARKERS[name] for name in manifest_files})

    file_tree: list[str] = []
    for path in sorted(workspace_dir.rglob("*")):
        if path.is_dir() or ".git" in path.parts:
            continue
        file_tree.append(str(path.relative_to(workspace_dir)).replace("\\", "/"))
        if len(file_tree) >= max_files:
            break

    return RepoContext(languages=languages, file_tree=file_tree, manifest_files=manifest_files)
