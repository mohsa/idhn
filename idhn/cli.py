"""idhn command-line interface: init / validate / run / report / lab-reset."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import click
import yaml

from idhn.adapters.codex import CodexAdapter
from idhn.audit.m3_report import build_m3_report, render_m3_report
from idhn.audit.m4_report import build_m4_report, render_m4_report
from idhn.audit.report import build_report, render_text_report
from idhn.core.manifest import ManifestError, load_manifest
from idhn.core.manifest import validate as validate_manifest
from idhn.runtime.docker import DockerRuntime, read_canary_value

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
@click.option("--before-test", "before_test_cmd", default=None, help="Shell command run in-container before the agent, e.g. a test suite (captured to before-tests.log).")
@click.option("--after-test", "after_test_cmd", default=None, help="Shell command run in-container after the agent (captured to after-tests.log).")
@click.option("--run-id", "run_id", default=None, help="Reuse a specific run id instead of generating one (for repeated experiment runs).")
@click.option("--workspace", "workspace_path", default=None, type=click.Path(exists=True, file_okay=False), help="Lab repo to mount at /workspace (default: lab/repos/toy-app).")
@click.argument("task", required=False)
def run(
    policy_path: str,
    agent_name: str,
    before_test_cmd: str | None,
    after_test_cmd: str | None,
    run_id: str | None,
    workspace_path: str | None,
    task: str | None,
) -> None:
    """Run an agent inside the IDHN sandbox under the given policy."""
    try:
        manifest = load_manifest(policy_path)
    except ManifestError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    task_description = task or manifest.task.description
    adapter = AGENTS[agent_name]()
    command = adapter.command(task_description)

    execution_id = run_id or f"run-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6]}"
    run_dir = DEFAULT_RUNS_DIR / execution_id
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(policy_path, run_dir / "manifest.yaml")
    if task:
        # The report should reflect the prompt actually used, not the
        # manifest's stored default description.
        manifest_copy_path = run_dir / "manifest.yaml"
        with manifest_copy_path.open("r", encoding="utf-8") as f:
            manifest_data = yaml.safe_load(f)
        manifest_data["task"]["description"] = task
        with manifest_copy_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(manifest_data, f, sort_keys=False)

    click.echo(f"IDHN run {execution_id}")
    click.echo(f"  policy: {policy_path}")
    click.echo(f"  agent:  {agent_name}")
    click.echo(f"  task:   {task_description}")

    runtime = DockerRuntime()
    exit_code = runtime.start(
        manifest,
        command,
        run_dir,
        execution_id,
        before_test_cmd,
        after_test_cmd,
        task_description,
        Path(workspace_path) if workspace_path else None,
    )

    report = build_report(run_dir, execution_id)
    with (run_dir / "report.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    click.echo(f"\ndone (exit code {exit_code}). see: idhn report {execution_id} / idhn m3-report {execution_id}")


@main.command()
@click.option("--agent", "agent_name", default="codex", type=click.Choice(sorted(AGENTS)), help="Agent adapter to run.")
@click.option("--before-test", "before_test_cmd", default=None, help="Shell command run in-container before the agent.")
@click.option("--after-test", "after_test_cmd", default=None, help="Shell command run in-container after the agent.")
@click.option("--run-id", "run_id", default=None, help="Reuse a specific run id instead of generating one.")
@click.option("--workspace", "workspace_path", default=None, type=click.Path(exists=True, file_okay=False), help="Lab repo to mount at /workspace (default: lab/repos/toy-app).")
@click.argument("task")
def baseline(
    agent_name: str,
    before_test_cmd: str | None,
    after_test_cmd: str | None,
    run_id: str | None,
    workspace_path: str | None,
    task: str,
) -> None:
    """Run an agent with NO IDHN policy, gateway, or network isolation (M3-A).

    Same container, same repo, same task as `idhn run` -- but direct
    internet access and no capability boundary at all. Exists only as a
    comparison baseline; see docker/compose.baseline.yaml.
    """
    adapter = AGENTS[agent_name]()
    command = adapter.command(task)

    execution_id = run_id or f"baseline-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6]}"
    run_dir = DEFAULT_RUNS_DIR / execution_id
    run_dir.mkdir(parents=True, exist_ok=True)

    click.echo(f"IDHN baseline run {execution_id} (UNCONSTRAINED -- no IDHN policy)")
    click.echo(f"  agent: {agent_name}")
    click.echo(f"  task:  {task}")

    exit_code = DockerRuntime.run_baseline(
        command,
        run_dir,
        execution_id,
        before_test_cmd,
        after_test_cmd,
        task,
        Path(workspace_path) if workspace_path else None,
    )
    click.echo(f"\ndone (exit code {exit_code}). see: idhn m3-report {execution_id}")


@main.command("m3-report")
@click.argument("run_id")
@click.option("--policy", "policy_path", default=None, type=click.Path(exists=True), help="Policy manifest used for this run (omit for a baseline run).")
def m3_report(run_id: str, policy_path: str | None) -> None:
    """Print the M3 experiment report (task completion + capability usage) for a run."""
    run_dir = DEFAULT_RUNS_DIR / run_id
    if not run_dir.exists():
        click.echo(f"no such run: {run_id}", err=True)
        sys.exit(1)

    manifest = None
    manifest_path = Path(policy_path) if policy_path else (run_dir / "manifest.yaml")
    if manifest_path.exists():
        manifest = load_manifest(manifest_path)

    report = build_m3_report(run_dir, manifest)
    click.echo(render_m3_report(report))


@main.command("m4-report")
@click.argument("run_id")
@click.option("--policy", "policy_path", default=None, type=click.Path(exists=True), help="Policy manifest used for this run (omit for a baseline run).")
@click.option("--workspace", "workspace_path", default=None, type=click.Path(exists=True, file_okay=False), help="Lab repo the run used, for the host-side protected-file check (default: lab/repos/toy-app).")
def m4_report(run_id: str, policy_path: str | None, workspace_path: str | None) -> None:
    """Print the M4 security report (task completion + attack containment) for a run."""
    run_dir = DEFAULT_RUNS_DIR / run_id
    if not run_dir.exists():
        click.echo(f"no such run: {run_id}", err=True)
        sys.exit(1)

    manifest = None
    manifest_path = Path(policy_path) if policy_path else (run_dir / "manifest.yaml")
    if manifest_path.exists():
        manifest = load_manifest(manifest_path)

    workspace_dir = Path(workspace_path) if workspace_path else (REPO_ROOT / "lab" / "repos" / "toy-app")
    canary_value = read_canary_value(workspace_dir)

    report = build_m4_report(run_dir, manifest, workspace_dir, canary_value or None)
    click.echo(render_m4_report(report))


@main.command("lab-reset")
@click.option("--app", "app_name", default="toy-app", help="Which lab/repos/<app> to reset (default: toy-app).")
@click.option(
    "--ref",
    default="HEAD",
    help="git ref to restore the app to (default: HEAD, i.e. whatever's currently committed).",
)
def lab_reset(app_name: str, ref: str) -> None:
    """Restore lab/repos/<app> to a clean, committed state before a run.

    Discards any edits Codex (or anything else) made under that
    directory, including untracked files, so repeated experiment runs
    start from an identical baseline. Only touches that one directory --
    it never runs `git checkout`/`clean` on the rest of the repo.
    """
    app_dir = REPO_ROOT / "lab" / "repos" / app_name
    subprocess.run(["git", "checkout", ref, "--", str(app_dir)], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "clean", "-fd", "--", str(app_dir)], cwd=REPO_ROOT, check=True)
    click.echo(f"lab/repos/{app_name} reset to {ref}")


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
