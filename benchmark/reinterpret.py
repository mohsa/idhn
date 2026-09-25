"""v0.2.1: reinterpret the frozen compiler-v0.2-b0 benchmark under the
corrected authority model (mechanical filesystem.read enforcement,
execution substrate + tool authority folded into the fixed floor) --
same 24 tasks, same compiler, same generated envelope inputs (P),
same agent. The only thing that changes is what `infra | predicted`
mechanically translates into (idhn.enforcement.filesystem.build_mount_
plan, idhn.audit.authority_sets.infrastructure_authority).

Reruns ONLY the constrained leg (leg 3). Leg 1 (compile -> P) is reused
verbatim from benchmark/runs/<id>.json -- recompiling would violate the
"don't touch the compiler" instruction and isn't needed anyway, since P
doesn't change. Leg 2 (unconstrained execution -> E) is also reused --
idhn.runtime.docker.DockerRuntime.run_baseline has no manifest and no
gateway at all, so nothing about the authority model touches it; its
already-recorded trace under runs/bench-exec-<id>/ is exactly as valid
now as when it was first produced, and re-running it would just spend
real Codex quota to reproduce byte-identical input.

Results go to benchmark/runs-v0.2.1/<id>.json -- a parallel dataset,
never overwriting the frozen benchmark/runs/<id>.json (compiler-v0.2-b0's
committed record).

Usage: python benchmark/reinterpret.py T08
"""
from __future__ import annotations

import json
import sys
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
    to_manifest_capabilities,
)
from idhn.core.manifest import load_manifest  # noqa: E402
from idhn.runtime.docker import DockerRuntime  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "benchmark"))
from run_task import TEST_COMMANDS, _parse_test_counts, _reset_workspace  # noqa: E402
from run_task import _task_success as _raw_task_success  # noqa: E402
from summarize import task_succeeded  # noqa: E402

RUNS_DIR = REPO_ROOT / "runs"
BENCHMARK_RUNS_DIR = REPO_ROOT / "benchmark" / "runs"
V021_RUNS_DIR = REPO_ROOT / "benchmark" / "runs-v0.2.1"
GENERATED_POLICIES_DIR = REPO_ROOT / "policies" / "generated"


def reinterpret(task_id: str) -> dict:
    frozen = json.loads((BENCHMARK_RUNS_DIR / f"{task_id}.json").read_text(encoding="utf-8"))
    task_path = REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.yaml"
    task_data = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    repo_rel = task_data["repo"]
    workspace_dir = REPO_ROOT / repo_rel
    task_text = task_data["task"]
    test_cmd = TEST_COMMANDS[repo_rel]
    command = CodexAdapter().command(task_text)

    predicted = {(d["resource"], d["operation"], d["scope"]) for d in frozen["predicted"]}
    infra = infrastructure_authority()  # the corrected model

    # Leg 2 (E) reused verbatim -- see module docstring.
    exec_run_dir = RUNS_DIR / f"bench-exec-{task_id}"
    exercised = exercised_from_run(exec_run_dir)

    print(f"[{task_id}] resetting {repo_rel} to HEAD before the reinterpreted constrained leg...")
    _reset_workspace(workspace_dir)

    generated_capabilities = to_manifest_capabilities(infra | predicted)
    GENERATED_POLICIES_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = GENERATED_POLICIES_DIR / f"bench-{task_id}-v0.2.1.yaml"
    with manifest_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            {
                "version": "0.1",
                "task": {"id": f"bench-{task_id}-v0.2.1", "description": task_text},
                "capabilities": generated_capabilities,
                "limits": {"duration": "30m", "cpu": 2, "memory": "4g"},
            },
            f,
            sort_keys=False,
        )
    manifest = load_manifest(manifest_path)

    run_id = f"bench-v0.2.1-{task_id}"
    run_dir = RUNS_DIR / run_id
    runtime = DockerRuntime()
    print(f"[{task_id}] executing under the corrected authority model (I' union P)...")
    exit_code = runtime.start(
        manifest, command, run_dir, run_id,
        before_test_cmd=test_cmd, after_test_cmd=test_cmd,
        task_description=task_text, workspace_dir=workspace_dir,
    )
    constrained_before = _parse_test_counts(repo_rel, run_dir / "before-tests.log")
    constrained_after = _parse_test_counts(repo_rel, run_dir / "after-tests.log")

    network_allowed, network_denied = network_events_from_run(run_dir)
    exercised = exercised | network_allowed
    missing = compute_missing(exercised, infra | predicted) | network_denied
    unnecessary = compute_unnecessary(predicted, exercised)

    record = {
        "task_id": task_id,
        "family": task_data["family"],
        "repo": repo_rel,
        "task": task_text,
        "compiler_version": frozen["compiler_version"],
        "authority_model": "v0.2.1",
        "constrained_run_id": run_id,
        "predicted": frozen["predicted"],
        "exercised": sorted(list(t) for t in exercised),
        "missing": sorted(list(t) for t in missing),
        "missing_by_class": {
            c: sorted(list(t) for t in missing if capability_class(t) == c)
            for c in ("task_semantic", "tool_substrate", "infrastructure")
        },
        "unnecessary": sorted(list(t) for t in unnecessary),
        "constrained_container_exit_code": exit_code,
        "constrained_tests_before": list(constrained_before) if constrained_before else None,
        "constrained_tests_after": list(constrained_after) if constrained_after else None,
        "task_success_under_generated_authority": _raw_task_success(constrained_after),
    }
    record["task_succeeded"] = task_succeeded(record)

    V021_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = V021_RUNS_DIR / f"{task_id}.json"
    out_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(
        f"[{task_id}] done: v0.2-b0_success={task_succeeded(frozen)} "
        f"v0.2.1_success={record['task_succeeded']} missing={len(missing)} -> {out_path}"
    )
    return record


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        reinterpret(arg)
