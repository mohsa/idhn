"""Serializes IDHN runs against the same workspace_dir (T6 TOCTOU
mitigation -- see docs/toctou-investigation-t6.md).

Two concurrent `idhn run` invocations sharing a workspace could
otherwise race write_filesystem_override's symlink check
(idhn.runtime.docker, the T5d fix) against the later, separate `docker
compose up` call that actually performs the mount: one run's agent
swaps a path for a symlink in that window, redirecting the OTHER run's
write grant to a file it never authorized -- confirmed exploitable for
real in docs/evidence/t6-toctou/race_experiment.py (under 1ms to win,
inside an 8+ second window). Within a single run this can't happen --
the agent being tested isn't a process until every one of its own
mounts is already set up -- so the fix is to make sure no SECOND run's
process can ever be alive, racing against a DIFFERENT run's
check-to-mount window, in the first place.

Holding this lock for a run's entire DockerRuntime.start() call (not
just the brief override-generation moment) is deliberate: once two
runs against the same workspace can never be alive at the same time,
there is no window left to race, not just a narrower one.

Does NOT defend against a host-level attacker with independent code
execution outside any IDHN-managed process -- that's outside IDHN's
containment model regardless of this lock (see the TOCTOU writeup's
threat model section for why that's a deliberate, not accidental,
boundary).

Lock files live outside the workspace entirely (never written into the
repository being protected), keyed by a hash of the resolved workspace
path so two different workspaces never contend, and the same workspace
always maps to the same lock file regardless of how its path was
spelled (relative, absolute, trailing slash, etc).
"""
from __future__ import annotations

import hashlib
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

LOCK_DIR = Path(__file__).resolve().parents[2] / ".idhn-locks"
POLL_INTERVAL_S = 0.05


def _lock_path(workspace_dir: Path) -> Path:
    LOCK_DIR.mkdir(exist_ok=True)
    digest = hashlib.sha256(str(workspace_dir.resolve()).encode("utf-8")).hexdigest()[:16]
    return LOCK_DIR / f"{digest}.lock"


def _try_acquire(fileobj) -> bool:
    # Non-blocking on both platforms -- the blocking/retry behavior is
    # implemented once, uniformly, in workspace_lock's own poll loop
    # below, rather than relying on each platform's differing built-in
    # blocking/retry semantics (e.g. msvcrt.locking's LK_LOCK mode only
    # retries for ~10 seconds before raising, far too short for a real
    # agent run that can take minutes).
    if sys.platform == "win32":
        import msvcrt

        try:
            fileobj.seek(0)
            msvcrt.locking(fileobj.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    else:
        import fcntl

        try:
            fcntl.flock(fileobj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False


def _release(fileobj) -> None:
    if sys.platform == "win32":
        import msvcrt

        fileobj.seek(0)
        msvcrt.locking(fileobj.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fileobj.fileno(), fcntl.LOCK_UN)


@contextmanager
def workspace_lock(workspace_dir: Path, timeout_s: float | None = None) -> Iterator[None]:
    """Block until an exclusive lock on `workspace_dir` is acquired, then
    hold it for the duration of the `with` block.

    `timeout_s=None` (the default) waits indefinitely -- the right
    default for a CLI tool, where "another run is already using this
    workspace" should make the second one wait, not silently proceed
    unprotected or fail the run outright. Raises TimeoutError if a
    `timeout_s` is given and exceeded.
    """
    path = _lock_path(workspace_dir)
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    with path.open("a+b") as fileobj:
        while not _try_acquire(fileobj):
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError(f"timed out waiting for workspace lock: {workspace_dir}")
            time.sleep(POLL_INTERVAL_S)
        try:
            yield
        finally:
            _release(fileobj)
