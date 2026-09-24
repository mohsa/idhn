"""Causal capability ablation for v0.2-B: take a task that already PASSED
under its generated envelope (I union P, frozen in benchmark/runs/<id>.json),
remove exactly one capability believed necessary, and confirm PASS -> FAIL.

This is the only way to tell "exercised" apart from "necessary" with real
ground truth -- a capability can be exercised without being required, and a
task can succeed without ever touching everything it was granted. See
benchmark/results/ablations.md for which six tasks were chosen and why.

Reuses the frozen compile result (predicted decisions) from the task's
already-recorded JSON rather than recompiling -- this is a runtime-leg-only
experiment, not a second compile, so it doesn't touch compiler-v0.2-b0 at
all and doesn't cost a second compile call.

Usage: python benchmark/ablate.py T08 filesystem write /workspace/src/dedupe.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from idhn.adapters.codex import CodexAdapter  # noqa: E402
from idhn.audit.authority_sets import infrastructure_authority, to_manifest_capabilities  # noqa: E402
from idhn.core.manifest import load_manifest  # noqa: E402
from idhn.runtime.docker import DockerRuntime  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "benchmark"))
from run_task import TEST_COMMANDS, _parse_test_counts, _reset_workspace  # noqa: E402
from run_task import _task_success as _raw_task_success  # noqa: E402
from summarize import task_succeeded  # noqa: E402

RUNS_DIR = REPO_ROOT / "runs"
BENCHMARK_RUNS_DIR = REPO_ROOT / "benchmark" / "runs"
GENERATED_POLICIES_DIR = REPO_ROOT / "policies" / "generated"
ABLATIONS_DIR = REPO_ROOT / "benchmark" / "results" / "ablations"


def ablate(task_id: str, resource: str, operation: str, scope: str) -> dict:
    baseline = json.loads((BENCHMARK_RUNS_DIR / f"{task_id}.json").read_text(encoding="utf-8"))
    task_path = REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.yaml"
    task_data = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    repo_rel = task_data["repo"]
    workspace_dir = REPO_ROOT / repo_rel
    task_text = task_data["task"]
    test_cmd = TEST_COMMANDS[repo_rel]
    command = CodexAdapter().command(task_text)

    target = (resource, operation, scope)
    predicted = {(d["resource"], d["operation"], d["scope"]) for d in baseline["predicted"]}
    if target not in predicted:
        raise SystemExit(f"{target} is not in {task_id}'s predicted set -- nothing to ablate: {sorted(predicted)}")

    infra = infrastructure_authority()
    ablated_authority = (infra | predicted) - {target}

    print(f"[{task_id}] ablating {target}")
    print(f"[{task_id}] resetting {repo_rel} to HEAD before the ablated leg...")
    _reset_workspace(workspace_dir)

    generated_capabilities = to_manifest_capabilities(ablated_authority)
    GENERATED_POLICIES_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = GENERATED_POLICIES_DIR / f"bench-{task_id}-ablated.yaml"
    with manifest_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            {
                "version": "0.1",
                "task": {"id": f"bench-{task_id}-ablated", "description": task_text},
                "capabilities": generated_capabilities,
                "limits": {"duration": "30m", "cpu": 2, "memory": "4g"},
            },
            f,
            sort_keys=False,
        )
    manifest = load_manifest(manifest_path)

    run_id = f"bench-ablate-{task_id}"
    run_dir = RUNS_DIR / run_id
    runtime = DockerRuntime()
    print(f"[{task_id}] executing under I union P minus {target}...")
    exit_code = runtime.start(
        manifest, command, run_dir, run_id,
        before_test_cmd=test_cmd, after_test_cmd=test_cmd,
        task_description=task_text, workspace_dir=workspace_dir,
    )
    before = _parse_test_counts(repo_rel, run_dir / "before-tests.log")
    after = _parse_test_counts(repo_rel, run_dir / "after-tests.log")

    # Use the same family-aware success definition as summarize.py, not
    # the raw "tests pass" field -- for diagnose/test/T21-T23-shaped
    # dependency tasks the raw field means something other than success
    # (see summarize.task_succeeded's docstring), and comparing baseline
    # vs ablated under two different definitions would make the PASS ->
    # FAIL claim meaningless.
    ablated_record = {
        "task_id": task_id,
        "family": task_data["family"],
        "constrained_tests_before": list(before) if before else None,
        "constrained_tests_after": list(after) if after else None,
        "task_success_under_generated_authority": _raw_task_success(after),
    }
    baseline_success = task_succeeded(baseline)
    ablated_success = task_succeeded(ablated_record)

    result = {
        "task_id": task_id,
        "family": task_data["family"],
        "ablated_capability": list(target),
        "baseline_success": baseline_success,
        "baseline_tests_before": baseline["constrained_tests_before"],
        "baseline_tests_after": baseline["constrained_tests_after"],
        "ablated_container_exit_code": exit_code,
        "ablated_tests_before": list(before) if before else None,
        "ablated_tests_after": list(after) if after else None,
        "ablated_success": ablated_success,
        "run_id": run_id,
    }

    ABLATIONS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ABLATIONS_DIR / f"{task_id}.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        f"[{task_id}] done: baseline_success={baseline_success} "
        f"ablated_success={ablated_success} -> {out_path}"
    )
    return result


if __name__ == "__main__":
    ablate(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
