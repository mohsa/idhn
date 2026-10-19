"""T6 TOCTOU experiment: does swapping an authorized write target for a
symlink AFTER write_filesystem_override's symlink check but BEFORE
`docker compose up` actually performs the mount let a write land in an
unauthorized file?

Not a permanent regression test -- timing-dependent by nature, this is
a one-shot research script, same discipline as the ad-hoc reproduction
that found the T5d bug in the first place (see docs/symlink-write-
bypass-fix.md) before it became tests/test_runtime_symlinks.py.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

# Symlink creation needs no special privilege on Linux (unlike Windows,
# which needs Developer Mode / elevation) -- run the swap side of this
# race on a WSL-native path, not /mnt/c/..., so the experiment's own
# symlink creation can never be what fails. The repo's code is still
# imported from /mnt/c/... (REPO_ROOT above); only this scratch data
# directory is WSL-native.
SCRATCH = Path("/tmp/idhn-toctou") if sys.platform == "linux" else REPO_ROOT / "scratch-toctou"
# (Windows branch left as a relative scratch dir under the repo for a
# from-source read; the experiment itself only actually runs on Linux,
# since the Windows symlink privilege issue is exactly why -- see the
# doc this script is evidence for.)
WORKSPACE = SCRATCH / "workspace"
RUN_DIR = SCRATCH / "run"

# DockerRuntime.start() always bind-mounts this (read-only) into the
# agent regardless of whether Codex itself is invoked -- needs a real
# file to exist as the mount source. Real path confirmed from the T5d
# lab's own compose.filesystem.yaml.
if sys.platform == "linux":
    os.environ.setdefault("IDHN_CODEX_AUTH_PATH", "/mnt/c/Users/saifp/.codex/auth.json")

from idhn.core.manifest import Manifest  # noqa: E402
from idhn.core.models import Task  # noqa: E402
from idhn.runtime.docker import DockerRuntime  # noqa: E402


def reset():
    import shutil
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    if RUN_DIR.exists():
        shutil.rmtree(RUN_DIR)
    WORKSPACE.mkdir(parents=True)
    # The container's agent user has a different uid than whatever host
    # user created this directory -- world-writable so /run (events,
    # codex-*.ts, etc) is actually writable from inside the container.
    # Confirmed this matters: without it, every /run write failed with
    # "Permission denied" (an artifact of this experiment's own setup,
    # not the race itself) and masked the real result.
    RUN_DIR.mkdir(parents=True)
    if sys.platform == "linux":
        RUN_DIR.chmod(0o777)


def main():
    reset()
    target = WORKSPACE / "secret.txt"         # the unauthorized file we want writes to land in
    target.write_text("UNTOUCHED")
    if sys.platform == "linux":
        # World-writable so a successful redirect isn't masked by an
        # unrelated host-side ownership/permission mismatch between this
        # script's user and the container's agent user -- isolates the
        # actual question (does the mount get redirected at all) from
        # incidental permission confounds. A real attacker who planted
        # this file controls its permissions too.
        target.chmod(0o666)
    authorized = WORKSPACE / "allowed.txt"    # starts as a REGULAR file -- passes the symlink check
    authorized.write_text("ORIGINAL")

    manifest = Manifest(
        version="0.1",
        task=Task(id="toctou-check", description="race check"),
        capabilities={
            "filesystem": {"read": ["/workspace/**"], "write": ["/workspace/allowed.txt"], "deny": []},
            "process": {"allow": ["bash"]},
            "network": {"default": "deny", "allow": []},
        },
    )

    override_path = RUN_DIR / "compose.filesystem.yaml"
    swap_done = threading.Event()
    swap_timestamp = {}

    def watch_and_swap():
        # Poll for the override file (written right after the symlink
        # check passes, inside write_filesystem_override) -- the moment
        # it appears, the check is already done and docker compose up
        # hasn't run yet. Swap as fast as possible after that.
        deadline = time.time() + 30
        while time.time() < deadline:
            if override_path.exists():
                t0 = time.time()
                authorized.unlink()
                authorized.symlink_to(target)
                swap_timestamp["t"] = time.time()
                swap_timestamp["detect_to_swap_s"] = swap_timestamp["t"] - t0
                swap_done.set()
                return
            time.sleep(0.001)
        swap_done.set()

    watcher = threading.Thread(target=watch_and_swap, daemon=True)
    watcher.start()

    command = ["bash", "-lc", "printf '%s' RACE_WIN > /workspace/allowed.txt; echo exit:$?"]
    runtime = DockerRuntime()
    t_start = time.time()
    exit_code = runtime.start(manifest, command, RUN_DIR, "toctou-check", workspace_dir=WORKSPACE)
    t_end = time.time()

    watcher.join(timeout=5)

    print("=== TOCTOU race experiment result ===")
    print(f"container exit code: {exit_code}")
    for name in ("codex-events.jsonl", "codex-stderr.log"):
        p = RUN_DIR / name
        if p.exists() and p.read_text(encoding="utf-8", errors="replace").strip():
            print(f"--- {name} ---")
            print(p.read_text(encoding="utf-8", errors="replace"))
    print(f"total start() duration: {t_end - t_start:.2f}s")
    print(f"swap happened: {swap_done.is_set() and 't' in swap_timestamp}")
    if "t" in swap_timestamp:
        print(f"time from override-file-appearing to swap completing: {swap_timestamp['detect_to_swap_s']*1000:.1f}ms")
        print(f"time from start() call to swap: {swap_timestamp['t'] - t_start:.2f}s")

    print()
    print(f"authorized ('allowed.txt') is_symlink now: {authorized.is_symlink()}")
    print(f"target ('secret.txt') content: {target.read_text()!r}")
    if authorized.exists() and not authorized.is_symlink():
        print(f"authorized ('allowed.txt') content: {authorized.read_text()!r}")

    print()
    if target.read_text() == "RACE_WIN":
        print("RESULT: RACE SUCCEEDED -- the write landed in the unauthorized file.")
    else:
        print("RESULT: race did not redirect the write -- target file untouched.")
        print("(check exit code and logs above for whether the write even happened at all,")
        print(" e.g. it may have failed outright because the mount's source changed mid-flight.)")


if __name__ == "__main__":
    main()
