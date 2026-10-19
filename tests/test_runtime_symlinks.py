"""Regression tests for T5d: a manifest granting `filesystem.write` on an
existing file was bypassed when that file was actually a pre-existing
symlink -- Docker's bind mount followed the symlink, so a write Codex
believed landed in the authorized file landed, verbatim, in a
completely different one the manifest never granted. Reproduced for
real (a known marker string ended up in the symlink's target file, not
the authorized path) before this fix. See
idhn.runtime.docker.write_filesystem_override's docstring for the full
writeup.
"""
from types import SimpleNamespace

import pytest

from idhn.runtime.docker import write_filesystem_override


def make_manifest(path):
    return SimpleNamespace(
        capabilities={"filesystem": {"write": [f"/workspace/{path}"]}}
    )


def prepare(tmp_path, workspace):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return run_dir


def test_rejects_symlinked_writable_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "secret.txt"
    target.write_text("ORIGINAL")
    (workspace / "allowed.txt").symlink_to(target)

    with pytest.raises(ValueError, match="writable mount contains symlink"):
        write_filesystem_override(
            make_manifest("allowed.txt"),
            prepare(tmp_path, workspace),
            "/unused/auth.json",
            workspace,
        )

    assert target.read_text() == "ORIGINAL"


def test_rejects_symlinked_parent_directory(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    real_dir = workspace / "real"
    real_dir.mkdir()
    (real_dir / "file.txt").write_text("ORIGINAL")
    (workspace / "linked").symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="writable mount contains symlink"):
        write_filesystem_override(
            make_manifest("linked/file.txt"),
            prepare(tmp_path, workspace),
            "/unused/auth.json",
            workspace,
        )


def test_allows_normal_writable_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "allowed.txt").write_text("ORIGINAL")

    override = write_filesystem_override(
        make_manifest("allowed.txt"),
        prepare(tmp_path, workspace),
        "/unused/auth.json",
        workspace,
    )

    assert override.exists()
