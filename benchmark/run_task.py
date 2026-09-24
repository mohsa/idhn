"""Run one v0.2-B benchmark task, three legs per task:

  1. compile        -> P (the compiler's proposal, with confidence/reason)
  2. unconstrained   -> E (what the task actually needs, observed with
                           nothing in the way -- see
                           idhn.audit.authority_sets' docstring on why
                           this has to be unconstrained, not gated by P)
  3. constrained by
     I union P       -> does the task actually succeed under exactly
                         what the compiler proposed, plus the fixed
                         infrastructure floor? This is the literal
                         "task success under generated authority" metric,
                         which the unconstrained run alone can't answer.

Freezes the result to benchmark/runs/<id>.json -- the permanent,
git-tracked record. The raw per-run traces it's derived from live under
runs/<...>/ as usual (gitignored, ephemeral); the generated manifest
lives under policies/generated/ (also gitignored -- it's a byproduct of
one compile run, not a curated policy example).

Usage: python benchmark/run_task.py T07
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from idhn.adapters.codex import CodexAdapter  # noqa: E402
from idhn.audit.authority_sets import (  # noqa: E402
    capability_class,
    compute_missing,
    compute_unnecessary,
    exercised_from_run,
    infrastructure_authority,
    network_events_from_run,
    predicted_from_decisions,
    to_manifest_capabilities,
)
from idhn.audit.m3_report import _parse_test_counts as _parse_node_test_counts  # noqa: E402
from idhn.compiler.compiler import compile_task  # noqa: E402
from idhn.compiler.context import gather_context  # noqa: E402
from idhn.core.manifest import load_manifest  # noqa: E402
from idhn.runtime.docker import DockerRuntime, run_codex_prompt  # noqa: E402

RUNS_DIR = REPO_ROOT / "runs"
BENCHMARK_RUNS_DIR = REPO_ROOT / "benchmark" / "runs"
GENERATED_POLICIES_DIR = REPO_ROOT / "policies" / "generated"
TEST_COMMANDS = {
    "lab/repos/toy-app": 'node --test "tests/*.test.js"',
    "benchmark/corpus/python-repo-1": "pytest tests/ -v",
    "benchmark/corpus/js-repo-2": 'node --test "tests/*.test.js"',
}

_PYTEST_SUMMARY_RE = re.compile(r"(\d+) (passed|failed)")


def _parse_pytest_counts(log_path: Path) -> tuple[int, int] | None:
    """Return (passed, failed) from pytest's summary line, e.g.
    "1 failed, 2 passed in 0.04s" -- a completely different format from
    Node's test runner, found by actually checking (m3_report.py's
    _parse_test_counts is tuned for "ℹ pass N"/"# pass N", which never
    appears in pytest output at all).
    """
    if not log_path.exists():
        return None
    text = log_path.read_text(encoding="utf-8", errors="replace")
    counts = {kind: int(n) for n, kind in _PYTEST_SUMMARY_RE.findall(text)}
    if not counts:
        return None
    return counts.get("passed", 0), counts.get("failed", 0)


def _parse_test_counts(repo_rel: str, log_path: Path) -> tuple[int, int] | None:
    if repo_rel == "benchmark/corpus/python-repo-1":
        return _parse_pytest_counts(log_path)
    return _parse_node_test_counts(log_path)


def _task_success(counts: tuple[int, int] | None) -> bool:
    return bool(counts) and counts[1] == 0


def _reset_workspace(workspace_dir: Path) -> None:
    """Both the unconstrained and constrained legs mount and let Codex
    edit the SAME corpus repo -- without resetting between them, the
    second leg would start from whatever the first one already fixed,
    which would make its result meaningless (found by reasoning through
    the pipeline before running it, not by watching it happen). Reset
    to the last commit before every execution leg, not just once.
    """
    subprocess.run(["git", "checkout", "HEAD", "--", str(workspace_dir)], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "clean", "-fd", "--", str(workspace_dir)], cwd=REPO_ROOT, check=True)


def _write_generated_manifest(task_id: str, task_text: str, capabilities: dict) -> Path:
    GENERATED_POLICIES_DIR.mkdir(parents=True, exist_ok=True)
    path = GENERATED_POLICIES_DIR / f"bench-{task_id}.yaml"
    data = {
        "version": "0.1",
        "task": {"id": f"bench-{task_id}", "description": task_text},
        "capabilities": capabilities,
        "limits": {"duration": "30m", "cpu": 2, "memory": "4g"},
    }
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)
    return path


def run_task(task_id: str) -> dict:
    task_path = REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.yaml"
    task_data = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    repo_rel = task_data["repo"]
    workspace_dir = REPO_ROOT / repo_rel
    task_text = task_data["task"]
    test_cmd = TEST_COMMANDS[repo_rel]
    command = CodexAdapter().command(task_text)

    # -- 1. compile: P --
    print(f"[{task_id}] compiling...")
    context = gather_context(workspace_dir)
    compile_run_id = f"bench-compile-{task_id}"
    compile_run_dir = RUNS_DIR / compile_run_id

    def complete(prompt: str) -> str:
        return run_codex_prompt(prompt, compile_run_id, compile_run_dir)

    compile_result = compile_task(task_text, context, complete)
    infra = infrastructure_authority()
    predicted = predicted_from_decisions(compile_result.decisions) if compile_result.ok else set()

    # -- 2. unconstrained: E --
    print(f"[{task_id}] resetting {repo_rel} to HEAD before executing...")
    _reset_workspace(workspace_dir)
    print(f"[{task_id}] executing (unconstrained, to observe E)...")
    exec_run_id = f"bench-exec-{task_id}"
    exec_run_dir = RUNS_DIR / exec_run_id
    exec_run_dir.mkdir(parents=True, exist_ok=True)
    exit_code = DockerRuntime.run_baseline(
        command, exec_run_dir, exec_run_id,
        before_test_cmd=test_cmd, after_test_cmd=test_cmd,
        task_description=task_text, workspace_dir=workspace_dir,
    )
    before = _parse_test_counts(repo_rel, exec_run_dir / "before-tests.log")
    after = _parse_test_counts(repo_rel, exec_run_dir / "after-tests.log")
    task_success = _task_success(after)

    # Process/filesystem exercised authority comes from this leg. Network
    # can't: run_baseline has no gateway, so there's nothing here to log
    # an allowed connect -- see network_events_from_run's docstring.
    exercised = exercised_from_run(exec_run_dir)

    # -- 3. constrained by I union P: does the compiler's own proposal work? --
    print(f"[{task_id}] resetting {repo_rel} to HEAD before the constrained leg...")
    _reset_workspace(workspace_dir)
    print(f"[{task_id}] executing under generated authority (I union P)...")
    generated_capabilities = to_manifest_capabilities(infra | predicted)
    generated_manifest_path = _write_generated_manifest(task_id, task_text, generated_capabilities)
    generated_manifest = load_manifest(generated_manifest_path)

    constrained_run_id = f"bench-constrained-{task_id}"
    constrained_run_dir = RUNS_DIR / constrained_run_id
    runtime = DockerRuntime()
    constrained_exit_code = runtime.start(
        generated_manifest, command, constrained_run_dir, constrained_run_id,
        before_test_cmd=test_cmd, after_test_cmd=test_cmd,
        task_description=task_text, workspace_dir=workspace_dir,
    )
    constrained_before = _parse_test_counts(repo_rel, constrained_run_dir / "before-tests.log")
    constrained_after = _parse_test_counts(repo_rel, constrained_run_dir / "after-tests.log")
    task_success_under_generated_authority = _task_success(constrained_after)

    # Merge in network signal from the constrained leg's gateway log now
    # that it exists: an allow confirms a predicted host was real; a
    # deny is direct, confirmed evidence of a missing capability (not
    # just "wasn't observed" -- the agent tried it and IDHN watched it
    # fail), so it's added to `missing` unconditionally rather than run
    # through compute_missing's covered-by-check.
    network_allowed, network_denied = network_events_from_run(constrained_run_dir)
    exercised |= network_allowed
    missing = compute_missing(exercised, infra | predicted) | network_denied
    unnecessary = compute_unnecessary(predicted, exercised)

    result = {
        "task_id": task_id,
        "family": task_data["family"],
        "repo": repo_rel,
        "task": task_text,
        "compiler_version": compile_result.compiler_version,
        "compile_run_id": compile_run_id,
        "exec_run_id": exec_run_id,
        "constrained_run_id": constrained_run_id,
        "compile_ok": compile_result.ok,
        "compile_errors": compile_result.errors,
        "budget_violations": compile_result.budget_violations,
        "predicted": [
            {"resource": d.resource, "operation": d.operation, "scope": d.scope, "confidence": d.confidence, "reason": d.reason}
            for d in compile_result.decisions
        ],
        "exercised": sorted(list(t) for t in exercised),
        "missing": sorted(list(t) for t in missing),
        # Mechanical (resource-type-based), not a judgment call -- see
        # idhn.audit.miss_taxonomy for the M1-M9 *why* classification,
        # which requires actually reading the trace and is recorded by
        # hand in benchmark/results/, not computed here.
        "missing_by_class": {c: sorted(list(t) for t in missing if capability_class(t) == c) for c in ("task_semantic", "tool_substrate", "infrastructure")},
        "unnecessary": sorted(list(t) for t in unnecessary),
        "container_exit_code": exit_code,
        "tests_before": list(before) if before else None,
        "tests_after": list(after) if after else None,
        "task_success": task_success,
        "constrained_container_exit_code": constrained_exit_code,
        "constrained_tests_before": list(constrained_before) if constrained_before else None,
        "constrained_tests_after": list(constrained_after) if constrained_after else None,
        "task_success_under_generated_authority": task_success_under_generated_authority,
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    BENCHMARK_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = BENCHMARK_RUNS_DIR / f"{task_id}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(
        f"[{task_id}] done: success(unconstrained)={task_success} "
        f"success(generated)={task_success_under_generated_authority} "
        f"missing={len(missing)} unnecessary={len(unnecessary)} -> {out_path}"
    )
    return result


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        run_task(arg)
