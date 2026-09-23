"""Docker enforcement backend.

Starts the two-container topology defined in docker/compose.yaml (agent +
gateway), wires the manifest into both via environment variables, and
reads back network-gateway events from the shared run directory so
`idhn report` can summarize them. This is the only module that knows
Docker/Compose is being used -- swap it for another Runtime implementation
without touching core/, adapters/, or the CLI.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Iterator
from pathlib import Path

DEFAULT_CODEX_AUTH_PATH = Path.home() / ".codex" / "auth.json"


def build_agent_script(
    command: list[str],
    before_test_cmd: str | None = None,
    after_test_cmd: str | None = None,
) -> str:
    """Build the shell script the agent container's entrypoint runs.

    Optionally captures a test-suite run before and after the agent
    command, plus the agent's own stdout/stderr and start/end timestamps,
    all under /run (the shared run directory) -- see
    idhn/audit/m3_report.py, which reads these back. `command` is quoted
    with shlex so a task string containing spaces or shell metacharacters
    can't break out of its argument position.
    """
    phases: list[str] = []
    if before_test_cmd:
        phases.append(f"( {before_test_cmd} ) > /run/before-tests.log 2>&1")

    quoted_command = shlex.join(command)
    phases.append(
        "date +%s > /run/codex-start.ts"
        f" && {quoted_command} > /run/codex-events.jsonl 2> /run/codex-stderr.log"
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
    ) -> int:
        self._run_dir = run_dir
        run_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.setdefault("IDHN_CODEX_AUTH_PATH", str(DEFAULT_CODEX_AUTH_PATH))
        env.update(
            {
                "IDHN_RUN_ID": execution_id,
                "IDHN_RUN_DIR": str(run_dir.resolve()),
                "IDHN_POLICY_FILE": self._policy_relpath(manifest),
                "IDHN_TASK": task_description or manifest.task.description,
                "IDHN_TASK_ID": manifest.task.id,
                "IDHN_AGENT_COMMAND": build_agent_script(command, before_test_cmd, after_test_cmd),
                "IDHN_CPU": str(manifest.limits.get("cpu", 2)),
                "IDHN_MEMORY": str(manifest.limits.get("memory", "4g")),
            }
        )

        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"

        with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
            result = subprocess.run(
                [
                    "docker", "compose", "-f", str(COMPOSE_FILE),
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
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
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
    ) -> int:
        """Run the agent with NO IDHN policy, gateway, or network isolation.

        This is the M3-A baseline: Codex in the same disposable container,
        same toy repo, same task, but with direct internet access and no
        capability boundary at all. It exists only to show the task is
        solvable without IDHN in the loop, so the constrained runs (M3-B)
        can be judged against a real baseline instead of an assumption.
        Never use docker/compose.baseline.yaml for anything else.
        """
        run_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.setdefault("IDHN_CODEX_AUTH_PATH", str(DEFAULT_CODEX_AUTH_PATH))
        env.update(
            {
                "IDHN_RUN_ID": execution_id,
                "IDHN_RUN_DIR": str(run_dir.resolve()),
                "IDHN_TASK": task_description or "",
                "IDHN_AGENT_COMMAND": build_agent_script(command, before_test_cmd, after_test_cmd),
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
