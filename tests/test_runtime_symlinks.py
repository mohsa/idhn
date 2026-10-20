"""Regression tests for symlink-redirect bypasses in
write_filesystem_override's mount-planning.

Write side (T5d): a manifest granting `filesystem.write` on an existing
file was bypassed when that file was actually a pre-existing symlink --
Docker's bind mount followed the symlink, so a write Codex believed
landed in the authorized file landed, verbatim, in a completely
different one the manifest never granted.

Read side: a manifest granting ONLY `filesystem.read` on an existing
file was similarly bypassed when that file was a pre-existing symlink
to `.env` -- reading it returned `.env`'s REAL content, completely
bypassing the deny mask that correctly empties out `/workspace/.env`
itself (the mask only shadows that one container path, not every other
path that happens to point at the same underlying host file).

Both reproduced for real before their respective fixes (a known marker
string / .env's real content ending up somewhere the manifest never
granted) -- see idhn.runtime.docker.write_filesystem_override's
docstring, docs/symlink-write-bypass-fix.md, and
docs/read-symlink-bypass-fix.md for the full writeups.
"""
from types import SimpleNamespace

import pytest

from idhn.runtime.docker import write_filesystem_override


def make_manifest(write=None, read=None, deny=None):
    return SimpleNamespace(
        capabilities={
            "filesystem": {
                "write": [f"/workspace/{p}" for p in (write or [])],
                "read": [f"/workspace/{p}" for p in (read or [])],
                "deny": [f"/workspace/{p}" for p in (deny or [])],
            }
        }
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
            make_manifest(write=["allowed.txt"]),
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
            make_manifest(write=["linked/file.txt"]),
            prepare(tmp_path, workspace),
            "/unused/auth.json",
            workspace,
        )


def test_allows_normal_writable_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "allowed.txt").write_text("ORIGINAL")

    override = write_filesystem_override(
        make_manifest(write=["allowed.txt"]),
        prepare(tmp_path, workspace),
        "/unused/auth.json",
        workspace,
    )

    assert override.exists()


def test_rejects_symlinked_read_granted_file(tmp_path):
    """A read grant on a symlink must not silently redirect to whatever
    it points at -- confirmed for real: `cat` on the granted path
    returned a completely different, never-granted file's content
    before this fix (docs/read-symlink-bypass-fix.md).
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    other = workspace / "never-granted-secret.txt"
    other.write_text("OTHER_FILE_CONTENT_NEVER_GRANTED")
    (workspace / "allowed-read.txt").symlink_to(other)

    with pytest.raises(ValueError, match="read-granted mount contains symlink"):
        write_filesystem_override(
            make_manifest(read=["allowed-read.txt"]),
            prepare(tmp_path, workspace),
            "/unused/auth.json",
            workspace,
        )

    assert other.read_text() == "OTHER_FILE_CONTENT_NEVER_GRANTED"


def test_allows_normal_read_granted_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "allowed-read.txt").write_text("ORIGINAL")

    override = write_filesystem_override(
        make_manifest(read=["allowed-read.txt"]),
        prepare(tmp_path, workspace),
        "/unused/auth.json",
        workspace,
    )

    assert override.exists()


def test_deny_mask_is_exempt_even_if_dotenv_itself_is_a_symlink(tmp_path):
    """A deny mask substitutes a fixed empty file regardless of what the
    container path points to on the host -- it must NOT be rejected just
    because `.env` itself happens to be a symlink (a real pattern some
    repos use, e.g. `.env -> .env.production`). Rejecting this would be
    a false positive: the mask makes the host's symlink structure here
    irrelevant to what actually gets mounted.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    real_env = workspace / ".env.production"
    real_env.write_text("REAL_DOTENV_SECRET=hunter2")
    (workspace / ".env").symlink_to(real_env)
    (workspace / "allowed-read.txt").write_text("ORDINARY_FILE_NOT_A_SYMLINK")

    override = write_filesystem_override(
        make_manifest(read=["allowed-read.txt"], deny=[".env"]),
        prepare(tmp_path, workspace),
        "/unused/auth.json",
        workspace,
    )

    assert override.exists()
