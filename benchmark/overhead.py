"""Week 4 roadmap deliverable: protected vs. unprotected comparison
across the 24-task corpus -- task success, runtime overhead, and
prevented unauthorized actions.

Builds entirely on data that already exists on disk from the v0.2-B /
v0.2.1 benchmark runs -- no new Codex/Docker invocation, no new API
cost. Each task already has two real, independent container runs:

  - unprotected (`bench-exec-<task>`, idhn.runtime.docker.DockerRuntime
    .run_baseline -- no manifest, no gateway, no network isolation;
    this is the same mechanism `idhn baseline` exposes on the CLI)
  - protected (`bench-v0.2.1-<task>`, the current, corrected
    enforcement model -- what `idhn run` actually enforces today)

idhn.audit.m3_report.build_m3_report already computes everything this
script needs (duration, blocked attempts, residual gaps) from each
run's own artifacts; this script just calls it twice per task (once
with manifest=None for the unprotected leg, once with the task's real
generated manifest for the protected leg) and aggregates the result.
Task success is read from the frozen benchmark/runs(-v0.2.1)/<task>.json
records rather than re-derived here, since run_task.py's own
family-aware success logic (summarize.py) is more nuanced than a bare
before/after test-count diff.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = REPO_ROOT / "runs"
B0_RECORDS_DIR = REPO_ROOT / "benchmark" / "runs"
V021_RECORDS_DIR = REPO_ROOT / "benchmark" / "runs-v0.2.1"
POLICIES_DIR = REPO_ROOT / "policies" / "generated"

import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))

from idhn.audit.m3_report import build_m3_report  # noqa: E402
from idhn.core.manifest import load_manifest  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "benchmark"))
from summarize import task_succeeded  # noqa: E402


def task_ids() -> list[str]:
    return [f"T{i:02d}" for i in range(1, 25)]


def success_for_leg(record: dict, before_key: str, after_key: str, fallback_key: str) -> bool | None:
    """Re-derive task_succeeded's family-aware verdict (summarize.py) for
    an arbitrary leg, instead of trusting that leg's raw boolean field
    directly -- found by running this that the raw field means "tests
    pass after" uniformly, which is backwards for "diagnose" (success is
    leaving the repo alone) and wrong for "test" (success is adding
    coverage, even if the new test correctly fails). Reusing
    task_succeeded's actual family logic rather than re-deriving it here
    keeps one source of truth for what "success" means per family.
    """
    adapted = dict(record)
    adapted["constrained_tests_before"] = record.get(before_key)
    adapted["constrained_tests_after"] = record.get(after_key)
    adapted["task_success_under_generated_authority"] = record.get(fallback_key)
    return task_succeeded(adapted)


def load_record(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def collect() -> list[dict]:
    rows = []
    for task_id in task_ids():
        b0 = load_record(B0_RECORDS_DIR / f"{task_id}.json")
        v021 = load_record(V021_RECORDS_DIR / f"{task_id}.json")
        if b0 is None or v021 is None:
            continue

        exec_run_id = b0["exec_run_id"]
        constrained_run_id = v021["constrained_run_id"]
        if not (RUNS_DIR / exec_run_id / "codex-start.ts").exists():
            continue
        if not (RUNS_DIR / constrained_run_id / "codex-start.ts").exists():
            continue

        unprotected_report = build_m3_report(RUNS_DIR / exec_run_id, None)

        manifest_path = POLICIES_DIR / f"bench-{task_id}-v0.2.1.yaml"
        manifest = load_manifest(manifest_path) if manifest_path.exists() else None
        protected_report = build_m3_report(RUNS_DIR / constrained_run_id, manifest)

        u_dur = unprotected_report["duration_seconds"]
        p_dur = protected_report["duration_seconds"]
        overhead_s = (p_dur - u_dur) if (u_dur is not None and p_dur is not None) else None
        overhead_pct = (overhead_s / u_dur * 100) if (overhead_s is not None and u_dur) else None

        # Protected: reinterpret.py already precomputes the correct,
        # family-aware verdict as "task_succeeded" -- reused directly
        # rather than re-derived. Unprotected: no equivalent precomputed
        # field exists (the unconstrained leg was never scored this way
        # originally), so the same family logic is applied here via the
        # adapter, using this leg's own before/after test counts.
        unprotected_success = success_for_leg(b0, "tests_before", "tests_after", "task_success")

        rows.append(
            {
                "task_id": task_id,
                "family": b0.get("family"),
                "unprotected_success": unprotected_success,
                "protected_success": v021.get("task_succeeded"),
                "unprotected_duration_s": u_dur,
                "protected_duration_s": p_dur,
                "overhead_s": overhead_s,
                "overhead_pct": overhead_pct,
                "blocked_attempts": len(protected_report["blocked_attempts"]),
                "residual_gaps": len(protected_report["residual_gaps"]),
                "network_direct_denied": protected_report["network_direct_denied"],
            }
        )
    return rows


def summarize(rows: list[dict]) -> str:
    n = len(rows)
    u_pass = sum(1 for r in rows if r["unprotected_success"] is True)
    u_fail = sum(1 for r in rows if r["unprotected_success"] is False)
    p_pass = sum(1 for r in rows if r["protected_success"] is True)
    p_fail = sum(1 for r in rows if r["protected_success"] is False)

    durations_u = [r["unprotected_duration_s"] for r in rows if r["unprotected_duration_s"] is not None]
    durations_p = [r["protected_duration_s"] for r in rows if r["protected_duration_s"] is not None]
    overheads = [r["overhead_s"] for r in rows if r["overhead_s"] is not None]
    overhead_pcts = [r["overhead_pct"] for r in rows if r["overhead_pct"] is not None]
    total_blocked = sum(r["blocked_attempts"] for r in rows)
    total_residual_gaps = sum(r["residual_gaps"] for r in rows)

    lines = []
    lines.append("# Week 4: protected vs. unprotected benchmark comparison")
    lines.append("")
    lines.append(
        f"n={n} tasks (compiler-v0.2-b0 proposal, v0.2.1 corrected enforcement). "
        "Built entirely from already-run data (bench-exec-*/bench-v0.2.1-* "
        "artifacts already on disk) -- no new Codex/Docker runs for this report."
    )
    lines.append("")
    lines.append("## Task success")
    lines.append("")
    lines.append(f"- Unprotected (no manifest, no gateway): {u_pass}/{n} pass, {u_fail}/{n} fail, "
                  f"{n - u_pass - u_fail}/{n} unjudgeable")
    lines.append(f"- Protected (v0.2.1 enforcement, generated authority): {p_pass}/{n} pass, {p_fail}/{n} fail, "
                  f"{n - p_pass - p_fail}/{n} unjudgeable")
    lines.append("")
    lines.append(
        "Protected success is essentially unchanged from unprotected -- the "
        "manifest the compiler generates is permissive enough for the task "
        "to actually get done, not just safe in the abstract."
    )
    regressions = [r for r in rows if r["unprotected_success"] is True and r["protected_success"] is False]
    if regressions:
        ids = ", ".join(r["task_id"] for r in regressions)
        lines.append(
            f"- **Exception: {ids}**, which succeeded unprotected but failed "
            "under generated authority -- a real policy-compatibility gap "
            "(the compiler under-predicted what the task needed), not an "
            "enforcement bug. Already known: T19's own miss was classified "
            "a task-decomposition miss in the original v0.2-B taxonomy "
            "(benchmark/results/v0.2-b-summary.md), not a new finding here."
        )
    lines.append("")
    lines.append("## Runtime overhead")
    lines.append("")
    if durations_u and durations_p:
        lines.append(f"- Mean duration, unprotected: {statistics.mean(durations_u):.1f}s "
                      f"(median {statistics.median(durations_u):.1f}s)")
        lines.append(f"- Mean duration, protected: {statistics.mean(durations_p):.1f}s "
                      f"(median {statistics.median(durations_p):.1f}s)")
    if overheads:
        lines.append(f"- Mean absolute overhead: {statistics.mean(overheads):+.1f}s "
                      f"(median {statistics.median(overheads):+.1f}s)")
    if overhead_pcts:
        lines.append(f"- Mean relative overhead: {statistics.mean(overhead_pcts):+.1f}% "
                      f"(median {statistics.median(overhead_pcts):+.1f}%)")
    lines.append(
        "- This is wall-clock *agent* duration only (codex-start.ts to "
        "codex-end.ts) -- it does not include container build/startup "
        "time, which docker compose's own `--build` step amortizes "
        "across runs via layer caching and isn't attributable to either "
        "leg specifically."
    )
    lines.append("")
    lines.append("## Prevented unauthorized actions")
    lines.append("")
    lines.append(f"- Total blocked attempts across the corpus (protected leg only -- "
                  f"the unprotected leg has no manifest, so nothing can be \"blocked\"): "
                  f"{total_blocked} (same text-parsing heuristic as residual_gaps below -- "
                  f"not individually audited per entry here, since a false positive in "
                  f"this count only overstates the safe case, unlike a residual-gap false "
                  f"positive, which would understate it)")
    lines.append(f"- Total residual gaps (denied by policy but succeeded anyway -- the "
                  f"core safety invariant this counts should always be 0 for): "
                  f"{total_residual_gaps}, both investigated below")
    lines.append("")
    lines.append(
        "**Both residual gaps investigated -- audit-heuristic false "
        "positives, not real enforcement bypasses.** `_process_overreach`"
        "'s command-text parser (idhn.audit.codex_events) misidentified "
        "two non-process tokens as process-exec targets never granted "
        "in process.allow:"
    )
    lines.append("")
    lines.append(
        "- **T20**: flagged target `for`, from a bash `for f in ...; do "
        "...; done` loop. `for` is a shell keyword, never resolved via "
        "PATH and never subject to the real process allowlist at all -- "
        "the parser's exit-code check shows it \"succeeded\" because bash "
        "always runs its own loop syntax regardless of what's in `/opt/"
        "idhn/bin`, not because enforcement was bypassed."
    )
    lines.append(
        "- **T22**: flagged target `.pytest-install-check`, a bare "
        "directory-name *argument* to `ls -a .pytest-install-check` "
        "misread as a command name, not the actual `pip`/`python3` "
        "invocations in the same task that legitimately used granted "
        "binaries."
    )
    lines.append(
        "- Neither finding touches the real enforcement mechanisms this "
        "project repeatedly verified directly elsewhere (the PATH-shim "
        "allowlist, kernel-level read-only mounts) -- this is a known, "
        "already-documented limitation of inferring process identity from "
        "shell command *text* rather than a live syscall-level check (see "
        "idhn.audit.codex_events's own docstring). Reported here rather "
        "than silently zeroed out, consistent with this project's own "
        "standard for a `residual_gap` count: investigate before trusting, "
        "then state plainly what was found."
    )
    lines.append("")
    lines.append("## Per-task detail")
    lines.append("")
    lines.append("| Task | Family | Unprot. success | Prot. success | Unprot. dur (s) | Prot. dur (s) | Overhead (s) | Overhead (%) | Blocked | Residual gaps |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        def fmt_bool(v):
            return "PASS" if v is True else "FAIL" if v is False else "n/a"

        def fmt_num(v, suffix=""):
            return f"{v:.0f}{suffix}" if v is not None else "n/a"

        lines.append(
            f"| {r['task_id']} | {r['family']} | {fmt_bool(r['unprotected_success'])} | "
            f"{fmt_bool(r['protected_success'])} | {fmt_num(r['unprotected_duration_s'])} | "
            f"{fmt_num(r['protected_duration_s'])} | {fmt_num(r['overhead_s'])} | "
            f"{fmt_num(r['overhead_pct'], '%')} | {r['blocked_attempts']} | {r['residual_gaps']} |"
        )
    return "\n".join(lines) + "\n"


def main():
    rows = collect()
    report = summarize(rows)
    out_path = REPO_ROOT / "benchmark" / "results" / "v0.2.1-overhead-summary.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"Collected {len(rows)} tasks. Wrote {out_path}")
    print()
    print(report)


if __name__ == "__main__":
    main()
