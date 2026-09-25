"""Aggregate benchmark/runs-v0.2.1/*.json -- the SAME 24 tasks, the SAME
frozen compiler-v0.2-b0 predicted decisions, reinterpreted through the
v0.2.1 corrected authority model (mechanical filesystem.read enforcement,
execution substrate + tool authority folded into the fixed floor). See
benchmark/reinterpret.py and benchmark/results/v0.2.1-summary.md.

Reuses idhn.audit.authority_sets.capability_class -- unchanged code, but
its answer for a given capability now differs from the v0.2-b0 run,
because infrastructure_authority() (which capability_class checks first)
is now three sources instead of one. That's the entire point: same
classification logic, corrected authority model underneath it.

Usage: python benchmark/summarize_v0.2.1.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from idhn.audit.authority_sets import capability_class, covers  # noqa: E402
from idhn.core.models import ActionRequest  # noqa: E402
from idhn.core.risk import classify  # noqa: E402

V021_RUNS_DIR = REPO_ROOT / "benchmark" / "runs-v0.2.1"
B0_RUNS_DIR = REPO_ROOT / "benchmark" / "runs"


def _risk_of(capability: tuple[str, str, str]) -> str:
    resource, operation, _scope = capability
    return classify(ActionRequest(resource=resource, action=operation, target="x"))


def _recall_for(exercised_subset: set, predicted: set) -> float | None:
    if not exercised_subset:
        return None
    covered = {e for e in exercised_subset if any(covers(p, e) for p in predicted)}
    return len(covered) / len(exercised_subset)


def task_metrics(record: dict) -> dict:
    predicted = {(d["resource"], d["operation"], d["scope"]) for d in record["predicted"]}
    exercised = {tuple(e) for e in record["exercised"]}
    unnecessary = {tuple(u) for u in record["unnecessary"]}

    semantic_exercised = {e for e in exercised if capability_class(e) == "task_semantic"}
    substrate_exercised = {e for e in exercised if capability_class(e) == "tool_substrate"}
    semantic_recall = _recall_for(semantic_exercised, predicted)
    substrate_recall = _recall_for(substrate_exercised, predicted)

    unnecessary_by_risk = Counter(_risk_of(u) for u in unnecessary)

    return {
        "semantic_recall": semantic_recall,
        "substrate_recall": substrate_recall,
        "substrate_exercised_count": len(substrate_exercised),
        "unnecessary_by_risk": dict(unnecessary_by_risk),
    }


def main() -> None:
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(V021_RUNS_DIR.glob("*.json"))]
    print(f"v0.2.1 SUMMARY -- {len(records)}/24 tasks reinterpreted under the corrected authority model")
    print("=" * 70)

    print(f"\n{'task':<6} {'family':<11} {'success':<9} {'sem.recall':<11} {'sub.recall':<11} {'missing':<8} {'unnecessary'}")
    total_success = 0
    success_judged = 0
    total_semantic_recall: list[float] = []
    total_substrate_recall: list[float] = []
    substrate_exercised_total = 0
    unnecessary_by_risk_total: Counter = Counter()

    for r in records:
        m = task_metrics(r)
        success = r["task_succeeded"]
        if success is not None:
            success_judged += 1
            total_success += int(bool(success))
        if m["semantic_recall"] is not None:
            total_semantic_recall.append(m["semantic_recall"])
        if m["substrate_recall"] is not None:
            total_substrate_recall.append(m["substrate_recall"])
        substrate_exercised_total += m["substrate_exercised_count"]
        for risk, count in m["unnecessary_by_risk"].items():
            unnecessary_by_risk_total[risk] += count

        semantic_str = f"{m['semantic_recall']:.0%}" if m["semantic_recall"] is not None else "n/a"
        substrate_str = f"{m['substrate_recall']:.0%}" if m["substrate_recall"] is not None else "n/a"
        success_str = "n/a" if success is None else str(bool(success))
        print(
            f"{r['task_id']:<6} {r['family']:<11} {success_str:<9} "
            f"{semantic_str:<11} {substrate_str:<11} {len(r['missing']):<8} {len(r['unnecessary'])}"
        )

    success_rate = (total_success / success_judged) if success_judged else None
    avg_semantic_recall = sum(total_semantic_recall) / len(total_semantic_recall) if total_semantic_recall else None
    avg_substrate_recall = sum(total_substrate_recall) / len(total_substrate_recall) if total_substrate_recall else None
    high_risk_unnecessary = unnecessary_by_risk_total.get("high", 0) + unnecessary_by_risk_total.get("critical", 0)

    print("\n" + "=" * 70)
    print("compiler-v0.2-b0, reinterpreted under v0.2.1 authority semantics")
    print("=" * 70)
    print(f"Task-semantic recall        {f'{avg_semantic_recall:.0%}' if avg_semantic_recall is not None else 'n/a'}")
    print(f"Tool-substrate recall       {f'{avg_substrate_recall:.0%}' if avg_substrate_recall is not None else 'n/a'}  "
          f"(non-infra process.exec capabilities actually exercised: {substrate_exercised_total})")
    print(f"Unnecessary high-risk grants {high_risk_unnecessary}")
    print(f"Task success                {total_success}/{success_judged}"
          + (f"  ({(len(records) - success_judged)} unjudgeable -- test harness itself failed to produce parseable output)"
             if success_judged < len(records) else ""))

    print("\nUnused authority by risk tier (raw components, not one score):")
    for risk in ("low", "medium", "high", "critical"):
        print(f"  {risk:<10} {unnecessary_by_risk_total.get(risk, 0)}")


if __name__ == "__main__":
    main()
