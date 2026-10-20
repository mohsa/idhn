"""Does a READ-granted mount following a symlink leak unintended host
content, or worse, bypass deny-masking? The T5d fix
(write_filesystem_override) only checks WRITABLE ("rw") mounts for a
symlink -- read-only ("ro") mounts are untouched, a known, explicitly
flagged gap (docs/symlink-write-bypass-fix.md's "Scope, deliberately
narrow" section). This is a static reproduction (no race/timing needed
-- the symlink exists before the run starts, unlike T6), run for real
against Docker, not just reasoned about.

Two scenarios:
1. A read grant on an ordinary-looking file that's actually a symlink
   to some OTHER, never-granted file -- does the read return the
   OTHER file's content?
2. A read grant on a file that's actually a symlink to `.env` (the one
   path IDHN unconditionally deny-masks) -- does reading it, under a
   DIFFERENT container path, return .env's real content, bypassing the
   mask entirely?
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

SCRATCH = Path("/tmp/idhn-read-symlink") if sys.platform == "linux" else REPO_ROOT / "scratch-read-symlink"
WORKSPACE = SCRATCH / "workspace"
RUN_DIR = SCRATCH / "run"

# Defaults to this user's own Linux home (same default idhn/runtime/
# docker.py's own DEFAULT_CODEX_AUTH_PATH uses) -- set IDHN_CODEX_AUTH_PATH
# yourself first if your `codex login` credential lives elsewhere.
if sys.platform == "linux":
    os.environ.setdefault("IDHN_CODEX_AUTH_PATH", str(Path.home() / ".codex" / "auth.json"))

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
    RUN_DIR.mkdir(parents=True)
    if sys.platform == "linux":
        RUN_DIR.chmod(0o777)


def run_scenario(name, manifest_capabilities, command):
    reset()
    _scenario_setup[name]()

    manifest = Manifest(
        version="0.1",
        task=Task(id=f"read-symlink-{name}", description="read-symlink check"),
        capabilities=manifest_capabilities,
    )
    runtime = DockerRuntime()
    print(f"=== scenario: {name} ===")
    try:
        exit_code = runtime.start(manifest, command, RUN_DIR, f"read-symlink-{name}", workspace_dir=WORKSPACE)
        print(f"exit code: {exit_code}")
    except ValueError as exc:
        print(f"REJECTED before any container started: {exc}")
        print()
        return
    codex_events = RUN_DIR / "codex-events.jsonl"
    codex_stderr = RUN_DIR / "codex-stderr.log"
    if codex_events.exists():
        print("--- stdout ---")
        print(codex_events.read_text(encoding="utf-8", errors="replace"))
    if codex_stderr.exists() and codex_stderr.read_text(encoding="utf-8", errors="replace").strip():
        print("--- stderr ---")
        print(codex_stderr.read_text(encoding="utf-8", errors="replace"))
    print()


def setup_scenario_1():
    other = WORKSPACE / "never-granted-secret.txt"
    other.write_text("OTHER_FILE_CONTENT_NEVER_GRANTED")
    (WORKSPACE / "allowed-read.txt").symlink_to(other)


def setup_scenario_2():
    (WORKSPACE / ".env").write_text("REAL_DOTENV_SECRET=hunter2")
    (WORKSPACE / "allowed-read.txt").symlink_to(WORKSPACE / ".env")


def setup_scenario_3():
    # .env ITSELF is a symlink (e.g. to .env.production, a real pattern
    # some repos use) -- the deny mask must still apply cleanly, since
    # it substitutes a fixed empty file regardless of what .env points
    # to. Must NOT be rejected -- deny masks are deliberately exempt.
    real_env = WORKSPACE / ".env.production"
    real_env.write_text("REAL_DOTENV_SECRET=hunter2")
    (WORKSPACE / ".env").symlink_to(real_env)
    (WORKSPACE / "allowed-read.txt").write_text("ORDINARY_FILE_NOT_A_SYMLINK")


_scenario_setup = {
    "1-read-redirect": setup_scenario_1,
    "2-deny-mask-bypass": setup_scenario_2,
    "3-env-itself-is-symlink-mask-still-applies": setup_scenario_3,
}


def main():
    cmd = ["bash", "-lc", "cat /workspace/allowed-read.txt; echo EXIT:$?"]

    caps_no_deny = {
        "filesystem": {"read": ["/workspace/allowed-read.txt"], "write": [], "deny": []},
        "process": {"allow": ["bash", "cat"]},
        "network": {"default": "deny", "allow": []},
    }
    # Scenario 2 needs a REAL deny mask on /workspace/.env to test
    # whether reading the SAME underlying host file through a
    # DIFFERENTLY-NAMED, read-granted container path bypasses it --
    # without this, there's no mask to bypass in the first place.
    caps_with_env_deny = {
        "filesystem": {"read": ["/workspace/allowed-read.txt"], "write": [], "deny": ["/workspace/.env"]},
        "process": {"allow": ["bash", "cat"]},
        "network": {"default": "deny", "allow": []},
    }

    run_scenario("1-read-redirect", caps_no_deny, cmd)
    run_scenario("2-deny-mask-bypass", caps_with_env_deny, cmd)
    run_scenario(
        "3-env-itself-is-symlink-mask-still-applies",
        {
            "filesystem": {"read": ["/workspace/allowed-read.txt"], "write": [], "deny": ["/workspace/.env"]},
            "process": {"allow": ["bash", "cat"]},
            "network": {"default": "deny", "allow": []},
        },
        ["bash", "-lc", "cat /workspace/allowed-read.txt; echo ---; cat /workspace/.env; echo EXIT:$?"],
    )


if __name__ == "__main__":
    main()
