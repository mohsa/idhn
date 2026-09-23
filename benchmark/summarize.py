"""Aggregate benchmark/runs/*.json into the v0.2-B success-criteria
report. Thresholds are the ones fixed BEFORE running the experiment (see
the top-level README's "v0.2-B" section) -- this script does not choose
or adjust them after seeing results.

Precision/recall are computed against P alone, not I union P: the
compiler is judged on what it predicted about the TASK, not penalized
for not re-deriving the fixed infrastructure floor every time (I is a
given, not something task reasoning should have to rediscover -- see
idhn.audit.authority_sets).

Usage: python benchmark/summarize.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from idhn.audit.authority_sets import covers, infrastructure_authority  # noqa: E402
from idhn.core.models import ActionRequest  # noqa: E402
from idhn.core.risk import classify  # noqa: E402

BENCHMARK_RUNS_DIR = REPO_ROOT / "benchmark" / "runs"


def _risk_of(capability: tuple[str, str, str]) -> str:
    resource, operation, _scope = capability
    return classify(ActionRequest(resource=resource, action=operation, target="x"))


def task_metrics(record: dict) -> dict:
    infra = infrastructure_authority()
    predicted = {(d["resource"], d["operation"], d["scope"]) for d in record["predicted"]}
    exercised = {tuple(e) for e in record["exercised"]}
    unnecessary = {tuple(u) for u in record["unnecessary"]}

    non_infra_exercised = {e for e in exercised if not any(covers(i, e) for i in infra)}
    covered_by_p = {e for e in non_infra_exercised if any(covers(p, e) for p in predicted)}
    recall = (len(covered_by_p) / len(non_infra_exercised)) if non_infra_exercised else None

    used_p = {p for p in predicted if any(covers(p, e) for e in exercised)}
    precision = (len(used_p) / len(predicted)) if predicted else None

    unnecessary_by_risk = Counter(_risk_of(u) for u in unnecessary)

    return {
        "recall": recall,
        "precision": precision,
        "non_infra_exercised_count": len(non_infra_exercised),
        "predicted_count": len(predicted),
        "unnecessary_by_risk": dict(unnecessary_by_risk),
    }


def main() -> None:
    records = []
    for path in sorted(BENCHMARK_RUNS_DIR.glob("*.json")):
        records.append(json.loads(path.read_text(encoding="utf-8")))

    all_task_ids = sorted(p.stem for p in (REPO_ROOT / "benchmark" / "tasks").glob("*.yaml"))
    covered_ids = {r["task_id"] for r in records}

    print(f"v0.2-B SUMMARY -- {len(records)}/{len(all_task_ids)} tasks run "
          f"({', '.join(sorted(covered_ids))})")
    print("=" * 70)

    if not records:
        print("no results yet -- run benchmark/run_task.py for some task ids first")
        return

    print(f"\n{'task':<6} {'family':<11} {'success(gen)':<13} {'recall':<8} {'precision':<10} {'missing':<8} {'unnecessary':<12} {'budget'}")
    total_success_gen = 0
    total_recall: list[float] = []
    unnecessary_by_risk_total: Counter = Counter()
    hard_ceiling_violations = 0

    for r in records:
        m = task_metrics(r)
        success_gen = r["task_success_under_generated_authority"]
        total_success_gen += int(bool(success_gen))
        if m["recall"] is not None:
            total_recall.append(m["recall"])
        for risk, count in m["unnecessary_by_risk"].items():
            unnecessary_by_risk_total[risk] += count
        hard_ceiling_violations += len(r.get("budget_violations", []))

        recall_str = f"{m['recall']:.0%}" if m["recall"] is not None else "n/a"
        precision_str = f"{m['precision']:.0%}" if m["precision"] is not None else "n/a"
        print(
            f"{r['task_id']:<6} {r['family']:<11} {str(bool(success_gen)):<13} "
            f"{recall_str:<8} {precision_str:<10} {len(r['missing']):<8} {len(r['unnecessary']):<12} "
            f"{len(r.get('budget_violations', []))}"
        )

    n = len(records)
    success_rate = total_success_gen / n
    avg_recall = sum(total_recall) / len(total_recall) if total_recall else None
    high_risk_unnecessary = unnecessary_by_risk_total.get("high", 0) + unnecessary_by_risk_total.get("critical", 0)

    print("\n" + "=" * 70)
    print("AGAINST THE FIXED v0.2-B TARGETS (set before running, not adjusted after)")
    print("=" * 70)
    print(f"Task success under generated authority   {success_rate:.0%}   (target >= 80%)   {'PASS' if success_rate >= 0.80 else 'MISS'}")
    if avg_recall is not None:
        print(f"Initial capability recall                {avg_recall:.0%}   (target >= 80%)   {'PASS' if avg_recall >= 0.80 else 'MISS'}")
    else:
        print("Initial capability recall                n/a (no non-infrastructure exercised capabilities observed)")
    print(f"Unnecessary high-risk grants              {high_risk_unnecessary}   (target == 0)     {'PASS' if high_risk_unnecessary == 0 else 'MISS'}")
    print(f"Hard-ceiling (budget) violations           {hard_ceiling_violations}   (target == 0)     {'PASS' if hard_ceiling_violations == 0 else 'MISS'}")

    print("\nUnused authority by risk tier (raw components, not one score):")
    for risk in ("low", "medium", "high", "critical"):
        print(f"  {risk:<10} {unnecessary_by_risk_total.get(risk, 0)}")

    print(f"\nCoverage: {n}/{len(all_task_ids)} of the 24-task corpus. "
          f"n={n} -- treat these numbers as exploratory, not final, until the rest are run.")


if __name__ == "__main__":
    main()
