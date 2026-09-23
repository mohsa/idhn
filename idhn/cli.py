"""idhn command-line interface: init / validate / run / report."""
from __future__ import annotations

import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import click
import yaml

from idhn.adapters.codex import CodexAdapter
from idhn.audit.report import build_report, render_text_report
from idhn.core.manifest import ManifestError, load_manifest
from idhn.core.manifest import validate as validate_manifest
from idhn.runtime.docker import DockerRuntime

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_DIR = REPO_ROOT / "runs"

AGENTS = {
    "codex": CodexAdapter,
}


@click.group()
def main() -> None:
    """IDHN -- Interchangeable Deterministic Host Network.

    Runs untrusted coding agents under a deterministic policy boundary.
    """


@main.command()
def init() -> None:
    """Scaffold local config: .env and runs/ directory."""
    env_example = REPO_ROOT / ".env.example"
    env_path = REPO_ROOT / ".env"
    if not env_path.exists() and env_example.exists():
        shutil.copy(env_example, env_path)
        click.echo(f"created {env_path}")
    else:
        click.echo(f"{env_path} already exists, skipping")

    DEFAULT_RUNS_DIR.mkdir(exist_ok=True)
    click.echo(f"ready: {DEFAULT_RUNS_DIR}")


@main.command()
@click.argument("policy_path", type=click.Path(exists=True))
def validate(policy_path: str) -> None:
    """Validate a policy manifest without running anything."""
    with open(policy_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    errors = validate_manifest(data)
    if errors:
        click.echo(f"INVALID: {policy_path}")
        for error in errors:
            click.echo(f"  - {error}")
        sys.exit(1)
    click.echo(f"OK: {policy_path}")


@main.command()
@click.option("--policy", "policy_path", required=True, type=click.Path(exists=True), help="Path to a policy manifest YAML.")
@click.option("--agent", "agent_name", default="codex", type=click.Choice(sorted(AGENTS)), help="Agent adapter to run.")
@click.argument("task", required=False)
def run(policy_path: str, agent_name: str, task: str | None) -> None:
    """Run an agent inside the IDHN sandbox under the given policy."""
    try:
        manifest = load_manifest(policy_path)
    except ManifestError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    task_description = task or manifest.task.description
    adapter = AGENTS[agent_name]()
    command = adapter.command(task_description)

    execution_id = f"run-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6]}"
    run_dir = DEFAULT_RUNS_DIR / execution_id
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(policy_path, run_dir / "manifest.yaml")

    click.echo(f"IDHN run {execution_id}")
    click.echo(f"  policy: {policy_path}")
    click.echo(f"  agent:  {agent_name}")
    click.echo(f"  task:   {task_description}")

    runtime = DockerRuntime()
    exit_code = runtime.start(manifest, command, run_dir, execution_id)

    report = build_report(run_dir, execution_id)
    with (run_dir / "report.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    click.echo(f"\ndone (exit code {exit_code}). see: idhn report {execution_id}")


@main.command()
@click.argument("run_id")
def report(run_id: str) -> None:
    """Print the execution report for a completed run."""
    run_dir = DEFAULT_RUNS_DIR / run_id
    report_path = run_dir / "report.json"
    if report_path.exists():
        with report_path.open("r", encoding="utf-8") as f:
            report_data = json.load(f)
    elif run_dir.exists():
        report_data = build_report(run_dir, run_id)
    else:
        click.echo(f"no such run: {run_id}", err=True)
        sys.exit(1)

    click.echo(render_text_report(report_data))


if __name__ == "__main__":
    main()
