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
import subprocess
from collections.abc import Iterator
from pathlib import Path

from idhn.core.manifest import Manifest
from idhn.runtime.events import RuntimeEvent
from idhn.runtime.runtime import Runtime

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker" / "compose.yaml"


class DockerRuntime(Runtime):
    """Runs the agent inside a network-isolated Docker Compose topology."""

    def __init__(self) -> None:
        self._run_dir: Path | None = None

    def start(self, manifest: Manifest, command: list[str], run_dir: Path, execution_id: str) -> int:
        self._run_dir = run_dir
        run_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update(
            {
                "IDHN_RUN_ID": execution_id,
                "IDHN_RUN_DIR": str(run_dir.resolve()),
                "IDHN_POLICY_FILE": self._policy_relpath(manifest),
                "IDHN_TASK": manifest.task.description,
                "IDHN_TASK_ID": manifest.task.id,
                "IDHN_AGENT_COMMAND": " ".join(command),
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
    def _policy_relpath(manifest: Manifest) -> str:
        if manifest.source_path is None:
            return "default.yaml"
        policies_dir = REPO_ROOT / "policies"
        try:
            return str(Path(manifest.source_path).resolve().relative_to(policies_dir.resolve()))
        except ValueError:
            return str(manifest.source_path)
