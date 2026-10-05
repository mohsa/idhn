"""Regression tests for run-20261005-fd5119: the task's own validation
command (`python -m pytest ...`) failed with "python: command not
found" even though the compiler correctly predicted and materialized
process authority for both `python` and `python3` -- the agent IMAGE
simply never provided a `python` binary, only `python3`. Fixed in
docker/Dockerfile.agent (`python-is-python3`, a real symlink) rather
than rewriting commands or changing what the compiler/materializer
grants. See docs/python-substrate-fix.md.

These tests run a plain bash command through the real agent container
directly (no Codex invocation, no API credential use) -- DockerRuntime
always restricts the agent command's own PATH to /opt/idhn/bin
(enforcement/process.py), regardless of whether that command happens to
be `codex exec` or something else, so this exercises the exact same
process-enforcement path Codex itself runs under.

Requires Docker. Marked `docker`, excluded from the default `pytest`
run -- same convention as every other file in this directory.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from idhn.core.manifest import Manifest  # noqa: E402
from idhn.core.models import Task  # noqa: E402
from idhn.runtime.docker import DockerRuntime  # noqa: E402

pytestmark = pytest.mark.docker

WORKSPACE_DIR = REPO_ROOT / "lab" / "repos" / "toy-app"


def _manifest(process_allow: list[str]) -> Manifest:
    return Manifest(
        version="0.1",
        task=Task(id="python-substrate-check", description="verify python/python3 substrate"),
        capabilities={
            "filesystem": {"read": ["/workspace/**"], "write": [], "deny": []},
            "process": {"allow": process_allow},
            "network": {"default": "deny", "allow": []},
        },
    )


def _run(tmp_path: Path, process_allow: list[str], command: list[str]) -> tuple[str, str]:
    """Run `command` as the agent's own (PATH-restricted) invocation.
    Returns (codex-events.jsonl content, codex-stderr.log content) --
    the same two files Codex's real output would land in.
    """
    manifest = _manifest(process_allow)
    run_dir = tmp_path / "run"
    runtime = DockerRuntime()
    runtime.start(manifest, command, run_dir, "python-substrate-check", workspace_dir=WORKSPACE_DIR)
    stdout = (run_dir / "codex-events.jsonl").read_text(encoding="utf-8") if (run_dir / "codex-events.jsonl").exists() else ""
    stderr = (run_dir / "codex-stderr.log").read_text(encoding="utf-8") if (run_dir / "codex-stderr.log").exists() else ""
    return stdout, stderr


def test_python_resolves_in_the_real_agent_image_when_granted(tmp_path):
    # Minimal process.allow means the login shell's own /etc/profile may
    # itself warn about unrelated unshimmed utilities (e.g. "id: command
    # not found") -- that's incidental noise from how tightly PATH is
    # restricted in this test, not something about python, so check
    # specifically for "python" failing to resolve, not any failure.
    stdout, stderr = _run(tmp_path, ["bash", "python"], ["bash", "-lc", "python --version"])
    assert "python: command not found" not in stderr, stderr
    assert "Python 3" in stdout + stderr, (stdout, stderr)


def test_python3_still_works_when_granted(tmp_path):
    stdout, stderr = _run(tmp_path, ["bash", "python3"], ["bash", "-lc", "python3 --version"])
    assert "python3: command not found" not in stderr, stderr
    assert "Python 3" in stdout + stderr, (stdout, stderr)


def test_before_and_after_test_hooks_can_use_python_dash_m_pytest(tmp_path):
    """before/after-test commands run with the harness's own unrestricted
    PATH (enforcement/process.py's restriction is scoped to only the
    agent's own command, not IDHN's harness around it -- see
    build_agent_script's docstring), so this doesn't even require
    `python` in process.allow. This is the literal shape of the failing
    dogfood command.
    """
    manifest = _manifest(["bash"])
    run_dir = tmp_path / "run"
    runtime = DockerRuntime()
    runtime.start(
        manifest,
        ["bash", "-lc", "true"],
        run_dir,
        "python-substrate-check",
        before_test_cmd="python -m pytest --version",
        after_test_cmd="python3 -m pytest --version",
        workspace_dir=WORKSPACE_DIR,
    )
    before_log = (run_dir / "before-tests.log").read_text(encoding="utf-8")
    after_log = (run_dir / "after-tests.log").read_text(encoding="utf-8")
    assert "command not found" not in before_log, before_log
    assert "pytest" in before_log.lower(), before_log
    assert "command not found" not in after_log, after_log
    assert "pytest" in after_log.lower(), after_log


def test_python_execution_still_denied_without_python_authority(tmp_path):
    """The symlink makes `python` physically resolvable in the image --
    it must NOT make it reachable by Codex's own (PATH-restricted)
    invocation when process authority for it wasn't granted. Process
    enforcement (the /opt/idhn/bin shim, entrypoint.sh) is what gates
    this, unaffected by whether the real binary exists.
    """
    stdout, stderr = _run(tmp_path, ["bash"], ["bash", "-lc", "python --version; echo EXIT:$?"])
    assert "python: command not found" in stdout + stderr, (stdout, stderr)
    assert "EXIT:127" in stdout + stderr, (stdout, stderr)
