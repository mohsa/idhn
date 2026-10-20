"""Docker-level proof that the T6 mitigation is actually wired in, not
just present as an unused import: DockerRuntime.start() must hold
idhn.runtime.workspace_lock's lock for its ENTIRE duration (not just
during write_filesystem_override's symlink check), so no second run
against the same workspace can ever be alive to race the first run's
check-to-mount window -- see docs/toctou-investigation-t6.md.

This deliberately does NOT re-run docs/evidence/t6-toctou/
race_experiment.py's bare-thread symlink swap -- that experiment
simulates a host-level attacker outside any IDHN-managed process,
which this lock explicitly does not and cannot defend against (see
that doc's threat model). What the lock actually closes is two
concurrent `idhn run`-style invocations sharing a workspace, which is
what this test proves directly: while one real DockerRuntime.start()
call is in flight, a second attempt to acquire the same workspace's
lock blocks until the first completes.

Requires Docker. Marked `docker`, excluded from the default `pytest`
run -- same convention as every other file in this directory.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from idhn.core.manifest import Manifest  # noqa: E402
from idhn.core.models import Task  # noqa: E402
from idhn.runtime.docker import DockerRuntime  # noqa: E402
from idhn.runtime.workspace_lock import workspace_lock  # noqa: E402

pytestmark = pytest.mark.docker

WORKSPACE_DIR = REPO_ROOT / "lab" / "repos" / "toy-app"


def _manifest() -> Manifest:
    return Manifest(
        version="0.1",
        task=Task(id="lock-integration-check", description="sleep briefly to hold the lock"),
        capabilities={
            "filesystem": {"read": ["/workspace/**"], "write": [], "deny": []},
            "process": {"allow": ["bash"]},
            "network": {"default": "deny", "allow": []},
        },
    )


def test_start_holds_the_workspace_lock_for_its_entire_duration(tmp_path):
    run_dir = tmp_path / "run"
    command = ["bash", "-lc", "sleep 4"]

    started = threading.Event()
    finished = threading.Event()

    def run_it():
        runtime = DockerRuntime()
        started.set()
        runtime.start(_manifest(), command, run_dir, "lock-integration-check", workspace_dir=WORKSPACE_DIR)
        finished.set()

    t = threading.Thread(target=run_it)
    t.start()
    assert started.wait(timeout=5)
    # Give start() time to get well past write_filesystem_override and
    # into (or past) `docker compose up` -- the whole point is that the
    # lock must still be held deep into the call, not just at the start.
    time.sleep(2)
    assert not finished.is_set(), "test command finished too fast to prove anything -- increase the sleep"

    # A second, independent attempt to acquire the SAME workspace's lock
    # must NOT succeed while the real start() call above is still in
    # flight -- this is the actual integration point, not just the bare
    # locking primitive (already covered by tests/test_workspace_lock.py).
    with pytest.raises(TimeoutError):
        with workspace_lock(WORKSPACE_DIR, timeout_s=0.5):
            pass  # pragma: no cover -- must not be reached

    t.join(timeout=60)
    assert finished.is_set(), "start() never completed"

    # Once start() has returned, the lock must be free again -- no
    # leaked lock from this or a prior run.
    with workspace_lock(WORKSPACE_DIR, timeout_s=2.0):
        pass
