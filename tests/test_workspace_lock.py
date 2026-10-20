"""Unit tests for the T6 TOCTOU mitigation's locking primitive --
see idhn.runtime.workspace_lock's module docstring and
docs/toctou-investigation-t6.md for the vulnerability this closes.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from idhn.runtime.workspace_lock import workspace_lock


def test_second_acquire_blocks_until_first_releases(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    events: list[str] = []
    first_acquired = threading.Event()
    release_first = threading.Event()

    def holder():
        with workspace_lock(workspace):
            events.append("first:acquired")
            first_acquired.set()
            release_first.wait(timeout=5)
            events.append("first:released")

    t = threading.Thread(target=holder)
    t.start()
    assert first_acquired.wait(timeout=5)

    # The second attempt must not succeed while the first thread still
    # holds the lock -- a short timeout should time out, not acquire.
    with pytest.raises(TimeoutError):
        with workspace_lock(workspace, timeout_s=0.3):
            pass  # pragma: no cover -- must not be reached

    release_first.set()
    t.join(timeout=5)

    # Now that the first holder released it, acquisition succeeds.
    with workspace_lock(workspace, timeout_s=1.0):
        events.append("second:acquired")

    assert events == ["first:acquired", "first:released", "second:acquired"]


def test_lock_is_reentrant_safe_across_sequential_uses(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    for _ in range(3):
        with workspace_lock(workspace, timeout_s=1.0):
            pass


def test_different_workspaces_do_not_contend(tmp_path):
    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    workspace_a.mkdir()
    workspace_b.mkdir()

    a_acquired = threading.Event()
    release_a = threading.Event()

    def hold_a():
        with workspace_lock(workspace_a):
            a_acquired.set()
            release_a.wait(timeout=5)

    t = threading.Thread(target=hold_a)
    t.start()
    assert a_acquired.wait(timeout=5)

    # A different workspace must acquire immediately, unaffected by A's lock.
    start = time.monotonic()
    with workspace_lock(workspace_b, timeout_s=1.0):
        elapsed = time.monotonic() - start
    assert elapsed < 0.5

    release_a.set()
    t.join(timeout=5)


def test_lock_path_depends_only_on_resolved_workspace_path(tmp_path):
    from idhn.runtime.workspace_lock import _lock_path

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    direct = _lock_path(workspace)
    via_dot = _lock_path(workspace / "." / ".")
    assert direct == via_dot
