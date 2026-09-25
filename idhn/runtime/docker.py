"""Docker enforcement backend.

Starts the two-container topology defined in docker/compose.yaml (agent +
gateway), wires the manifest into both via environment variables and a
generated per-run mount override (see _write_filesystem_override), and
reads back network-gateway events from the shared run directory so
`idhn report` can summarize them. This is the only module that knows
Docker/Compose is being used -- swap it for another Runtime implementation
without touching core/, adapters/, enforcement/, or the CLI.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Iterator
from pathlib import Path

import yaml

from idhn.enforcement.filesystem import EMPTY_FILE_STUB, build_mount_plan
from idhn.enforcement.process import allowed_binaries

DEFAULT_CODEX_AUTH_PATH = Path.home() / ".codex" / "auth.json"

# Where entrypoint.sh builds the PATH-restricted binary shim directory.
SHIM_DIR = "/opt/idhn/bin"


def build_agent_script(
    command: list[str],
    before_test_cmd: str | None = None,
    after_test_cmd: str | None = None,
    restrict_path: bool = False,
) -> str:
    """Build the shell script the agent container's entrypoint runs.

    Optionally captures a test-suite run before and after the agent
    command, plus the agent's own stdout/stderr and start/end timestamps,
    all under /run (the shared run directory) -- see
    idhn/audit/m3_report.py, which reads these back. `command` is quoted
    with shlex so a task string containing spaces or shell metacharacters
    can't break out of its argument position.

    `restrict_path` scopes process enforcement (see enforcement/process.py)
    to ONLY the agent's own command, not the before/after test phases:
    those are IDHN's own harness running around the agent, not something
    the agent's process.allow grant should apply to. PATH=/opt/idhn/bin
    on just that one command is inherited by whatever it execs (Codex's
    own child processes), which is exactly the scope that matters.
    """
    phases: list[str] = []
    if before_test_cmd:
        phases.append(f"( {before_test_cmd} ) > /run/before-tests.log 2>&1")

    quoted_command = shlex.join(command)
    path_prefix = f"PATH={SHIM_DIR} " if restrict_path else ""
    phases.append(
        "date +%s > /run/codex-start.ts"
        f" && {path_prefix}{quoted_command} > /run/codex-events.jsonl 2> /run/codex-stderr.log"
        "; echo $? > /run/codex-exit-code"
        "; date +%s > /run/codex-end.ts"
    )

    if after_test_cmd:
        phases.append(f"( {after_test_cmd} ) > /run/after-tests.log 2>&1")

    return " ; ".join(phases)


from idhn.core.manifest import Manifest
from idhn.runtime.events import RuntimeEvent
from idhn.runtime.runtime import Runtime

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker" / "compose.yaml"
DEFAULT_WORKSPACE_DIR = REPO_ROOT / "lab" / "repos" / "toy-app"


def read_canary_value(workspace_dir: Path) -> str:
    """Read lab/repos/<app>/protected/canary.txt if present, for
    lab/services/exfil-sink to check received bodies against (M4). Empty
    string (not an error) when a workspace has no canary fixture -- most
    don't.
    """
    canary_path = workspace_dir / "protected" / "canary.txt"
    if not canary_path.exists():
        return ""
    return canary_path.read_text(encoding="utf-8").strip()


def _mount_spec_to_compose_volume(spec) -> dict:
    if spec.kind == "tmpfs":
        return {"type": "tmpfs", "target": spec.container_path, "read_only": spec.mode == "ro"}
    return {
        "type": "bind",
        "source": spec.host_path,
        "target": spec.container_path,
        "read_only": spec.mode == "ro",
    }


def write_filesystem_override(manifest: Manifest, run_dir: Path, codex_auth_path: str, workspace_dir: Path) -> Path:
    """Generate a per-run compose override giving the agent service the
    EXACT volumes its filesystem policy implies (see
    enforcement/filesystem.py), plus the auth and run-dir mounts every
    run needs.

    A generated file, not hand-edited compose.yaml, because a Compose
    override REPLACES a service's `volumes:` list wholesale rather than
    merging it -- and because the mount plan is manifest-dependent, it
    can't be a static file at all without contorting Compose into being
    IDHN's filesystem policy engine.
    """
    plan = build_mount_plan(manifest.capabilities, str(workspace_dir))

    # Every mount's target has to already exist as the right kind of
    # thing: the base /workspace mount is now an empty tmpfs (default
    # deny, not the real repo -- see enforcement/filesystem.py), so every
    # single read/write/deny mount is a fresh mountpoint the container
    # runtime needs pre-created, not just deny masks as before. And
    # separately: for a "rw" bind mount (a write grant) whose target
    # doesn't exist in the corpus repo yet, its HOST source doesn't exist
    # either, and Docker's default for a missing bind-mount source is to
    # create it as a directory -- silently turning an intended file (e.g.
    # `filesystem.write: /workspace/requirements.txt` on a repo with no
    # requirements.txt yet) into a directory, corrupting it for anything
    # that reads or writes it afterward (found by actually running this
    # -- two corpus repos ended up with directories named
    # requirements.txt and package-lock.json). Pre-create every target as
    # the right kind of thing on the host first, using MountSpec.is_dir
    # to know which -- except a "ro" bind target (a `read` grant) that
    # doesn't exist on the host at all, which is dropped instead: there's
    # nothing sensible to read, and fabricating an empty placeholder would
    # be the same corpus-corrupting mistake as above, just on the read
    # side instead of the write side.
    resolved_plan = []
    for spec in plan:
        target_path = workspace_dir / Path(spec.container_path).relative_to("/workspace")
        if spec.kind == "tmpfs":
            target_path.mkdir(parents=True, exist_ok=True)
        elif spec.kind == "bind" and not target_path.exists():
            if spec.mode == "ro" and spec.host_path != EMPTY_FILE_STUB:
                continue
            if spec.is_dir:
                target_path.mkdir(parents=True, exist_ok=True)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.touch()
        resolved_plan.append(spec)

    volumes = [_mount_spec_to_compose_volume(spec) for spec in resolved_plan]
    volumes.append({"type": "bind", "source": codex_auth_path, "target": "/home/agent/.codex/auth.json", "read_only": True})
    volumes.append({"type": "bind", "source": str(run_dir.resolve()), "target": "/run", "read_only": False})

    override = {"services": {"agent": {"volumes": volumes}}}
    override_path = run_dir / "compose.filesystem.yaml"
    with override_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(override, f, sort_keys=False)
    return override_path


class DockerRuntime(Runtime):
    """Runs the agent inside a network-isolated Docker Compose topology."""

    def __init__(self) -> None:
        self._run_dir: Path | None = None

    def start(
        self,
        manifest: Manifest,
        command: list[str],
        run_dir: Path,
        execution_id: str,
        before_test_cmd: str | None = None,
        after_test_cmd: str | None = None,
        task_description: str | None = None,
        workspace_dir: Path | None = None,
    ) -> int:
        self._run_dir = run_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        workspace_dir = (workspace_dir or DEFAULT_WORKSPACE_DIR).resolve()

        env = os.environ.copy()
        env.setdefault("IDHN_CODEX_AUTH_PATH", str(DEFAULT_CODEX_AUTH_PATH))
        override_path = write_filesystem_override(manifest, run_dir, env["IDHN_CODEX_AUTH_PATH"], workspace_dir)
        env.update(
            {
                "IDHN_RUN_ID": execution_id,
                "IDHN_RUN_DIR": str(run_dir.resolve()),
                "IDHN_POLICY_FILE": self._policy_relpath(manifest),
                "IDHN_TASK": task_description or manifest.task.description,
                "IDHN_TASK_ID": manifest.task.id,
                "IDHN_AGENT_COMMAND": build_agent_script(command, before_test_cmd, after_test_cmd, restrict_path=True),
                "IDHN_ALLOWED_BINARIES": ",".join(allowed_binaries(manifest.capabilities)),
                "IDHN_CPU": str(manifest.limits.get("cpu", 2)),
                "IDHN_MEMORY": str(manifest.limits.get("memory", "4g")),
                "IDHN_CANARY_VALUE": read_canary_value(workspace_dir),
            }
        )

        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"

        with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
            result = subprocess.run(
                [
                    "docker", "compose", "-f", str(COMPOSE_FILE), "-f", str(override_path),
                    "up", "--build",
                    "--abort-on-container-exit",
                    "--exit-code-from", "agent",
                ],
                cwd=REPO_ROOT,
                env=env,
                stdout=out,
                stderr=err,
            )

        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "-f", str(override_path), "down", "-v"],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        return result.returncode

    def stop(self) -> None:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def events(self) -> Iterator[RuntimeEvent]:
        if self._run_dir is None:
            return
        events_path = self._run_dir / "events.jsonl"
        if not events_path.exists():
            return
        with events_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield RuntimeEvent(**json.loads(line))

    @staticmethod
    def run_baseline(
        command: list[str],
        run_dir: Path,
        execution_id: str,
        before_test_cmd: str | None = None,
        after_test_cmd: str | None = None,
        task_description: str | None = None,
        workspace_dir: Path | None = None,
    ) -> int:
        """Run the agent with NO IDHN policy, gateway, or network isolation.

        This is the M3-A baseline (and M4's Run B): Codex in the same
        disposable container, same repo, same task, but with direct
        internet access and no capability boundary at all. It exists only
        to show a task is solvable, or an attack succeeds, without IDHN in
        the loop, so the constrained runs can be judged against a real
        baseline instead of an assumption. Never use
        docker/compose.baseline.yaml for anything else.
        """
        run_dir.mkdir(parents=True, exist_ok=True)
        workspace_dir = (workspace_dir or DEFAULT_WORKSPACE_DIR).resolve()

        env = os.environ.copy()
        env.setdefault("IDHN_CODEX_AUTH_PATH", str(DEFAULT_CODEX_AUTH_PATH))
        env.update(
            {
                "IDHN_RUN_ID": execution_id,
                "IDHN_RUN_DIR": str(run_dir.resolve()),
                "IDHN_TASK": task_description or "",
                "IDHN_AGENT_COMMAND": build_agent_script(command, before_test_cmd, after_test_cmd),
                "IDHN_WORKSPACE_DIR": str(workspace_dir.resolve()),
                "IDHN_CANARY_VALUE": read_canary_value(workspace_dir),
            }
        )

        compose_file = REPO_ROOT / "docker" / "compose.baseline.yaml"
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
            result = subprocess.run(
                ["docker", "compose", "-f", str(compose_file), "up", "--build", "--abort-on-container-exit"],
                cwd=REPO_ROOT,
                env=env,
                stdout=out,
                stderr=err,
            )
        subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "down", "-v"],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode

    @staticmethod
    def _policy_relpath(manifest: Manifest) -> str:
        # Always POSIX-separated: this gets interpolated into a Linux
        # container path (/policies/<this>) regardless of host OS, so a
        # Windows-style backslash here would silently break the mount path.
        if manifest.source_path is None:
            return "default.yaml"
        policies_dir = REPO_ROOT / "policies"
        try:
            return Path(manifest.source_path).resolve().relative_to(policies_dir.resolve()).as_posix()
        except ValueError:
            return Path(manifest.source_path).as_posix()


def run_codex_prompt(prompt: str, execution_id: str, run_dir: Path) -> str:
    """One-shot LLM backend for idhn.compiler.compiler.compile_task: run
    a prompt through Codex with NO workspace access (an empty temp
    directory mounted at /workspace) and return its final agent_message
    text.

    This is deliberately the baseline (unconstrained-network) topology,
    not the contained one -- there's no untrusted workspace content in
    play here for it to matter, and the compiler needs to just answer,
    not explore. Lives in runtime/docker.py (not compiler/) because it's
    Docker/Codex-specific plumbing; idhn.compiler.compiler takes this as
    an injected `complete` function and knows nothing about how it runs.
    """
    import tempfile

    from idhn.adapters.codex import CodexAdapter
    from idhn.audit.codex_events import extract_agent_messages

    command = CodexAdapter().command(prompt)

    with tempfile.TemporaryDirectory(prefix="idhn-compile-") as empty_workspace:
        exit_code = DockerRuntime.run_baseline(
            command,
            run_dir,
            execution_id,
            task_description="capability compiler one-shot prompt",
            workspace_dir=Path(empty_workspace),
        )
        if exit_code != 0:
            raise RuntimeError(f"codex exited {exit_code} while compiling a capability envelope; see {run_dir}")

    messages = extract_agent_messages(run_dir / "codex-events.jsonl")
    if not messages:
        raise RuntimeError(f"codex produced no agent_message; see {run_dir}/codex-events.jsonl")
    return messages[-1]
