"""Build and render the M3 experiment report.

Distinct from idhn/audit/report.py's generic per-run report: M3 asks a
specific question ("did the agent do useful work, and how much authority
did it actually use getting there?") that needs task-completion and
before/after test counts, not just an allow/deny tally.

Two data sources, kept honestly separate:
  - events.jsonl: gateway-observed, policy-DECIDED network events. Live
    enforcement, real ALLOW/DENY.
  - codex-events.jsonl: Codex's own --json tool-call stream, parsed by
    idhn.audit.codex_events. Observed only -- v0.1 has no live
    interception for filesystem/process, so these numbers describe what
    Codex did, not what IDHN allowed or blocked.

CapabilityUsage (granted vs. exercised vs. unused) is deliberately just
counters over sets here, per the design note that spawned it: don't
build a model around this before there's a reason to.
"""
from __future__ import annotations

import json
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from idhn.audit.codex_events import parse_codex_events
from idhn.core.manifest import Manifest
from idhn.core.models import ActionRequest, Decision, ExecutionContext
from idhn.core.policy import PolicyEngine

# Node's test runner uses the "spec" reporter (ℹ pass N) on a TTY but
# falls back to TAP (# pass N) once stdout is redirected to a file, which
# is always true for us -- match both so this doesn't silently break
# depending on how someone's Node happens to detect its stdout.
_TEST_SUMMARY_RE = re.compile(r"^(?:ℹ|#) (pass|fail) (\d+)", re.MULTILINE)


def _parse_test_counts(log_path: Path) -> tuple[int, int] | None:
    """Return (passed, failed) parsed from a `node --test` log, or None."""
    if not log_path.exists():
        return None
    text = log_path.read_text(encoding="utf-8", errors="replace")
    counts = dict(_TEST_SUMMARY_RE.findall(text))
    if not counts:
        return None
    return int(counts.get("pass", 0)), int(counts.get("fail", 0))


def _read_int(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _read_network_events(run_dir: Path) -> list[dict[str, Any]]:
    events_path = run_dir / "events.jsonl"
    if not events_path.exists():
        return []
    events = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def _granted_capabilities(capabilities: dict[str, Any]) -> set[tuple[str, str, str]]:
    """Every individual allow-pattern in the manifest is one granted unit."""
    granted: set[tuple[str, str, str]] = set()
    fs = capabilities.get("filesystem", {}) or {}
    for pattern in fs.get("read", []) or []:
        granted.add(("filesystem", "read", pattern))
    for pattern in fs.get("write", []) or []:
        granted.add(("filesystem", "write", pattern))
    net = capabilities.get("network", {}) or {}
    for pattern in net.get("allow", []) or []:
        granted.add(("network", "connect", pattern))
    proc = capabilities.get("process", {}) or {}
    for pattern in proc.get("allow", []) or []:
        granted.add(("process", "exec", pattern))
    git = capabilities.get("git", {}) or {}
    for pattern in git.get("allow", []) or []:
        granted.add(("git", "run", pattern))
    return granted


def _exercised_capabilities(
    granted: set[tuple[str, str, str]], usage_records: list[dict[str, str]]
) -> set[tuple[str, str, str]]:
    exercised: set[tuple[str, str, str]] = set()
    for record in usage_records:
        for grant in granted:
            resource, operation, pattern = grant
            if record["resource"] == resource and record["operation"] == operation and fnmatch(record["target"], pattern):
                exercised.add(grant)
    return exercised


def _shadow_violations(manifest: Manifest, usage_records: list[dict[str, str]]) -> list[dict[str, str]]:
    """Re-check observed filesystem/process usage against the SAME policy
    engine the gateway uses for network -- but after the fact, since v0.1
    doesn't live-block these resources. This exists so an agent exceeding
    its declared filesystem/process authority shows up somewhere instead
    of silently not counting as a violation just because nothing stopped
    it in real time. A non-empty result here is a real finding: it means
    the manifest under-declared what the agent actually needed (or the
    agent overreached), not that IDHN caught and blocked anything.
    """
    engine = PolicyEngine(manifest)
    context = ExecutionContext(run_id="shadow-check", agent_id="codex", task_id=manifest.task.id)
    violations = []
    for record in usage_records:
        if record["resource"] not in ("filesystem", "process"):
            continue
        request = ActionRequest(resource=record["resource"], action=record["operation"], target=record["target"])
        decision = engine.evaluate(request, context)
        if decision.decision != Decision.ALLOW:
            violations.append({**record, "reason": decision.reason})
    return violations


def build_m3_report(run_dir: Path, manifest: Manifest | None) -> dict[str, Any]:
    run_dir = Path(run_dir)
    events = _read_network_events(run_dir)
    codex_usage = parse_codex_events(run_dir / "codex-events.jsonl")

    before = _parse_test_counts(run_dir / "before-tests.log")
    after = _parse_test_counts(run_dir / "after-tests.log")

    direct_allowed = direct_denied = transitive_denied = escalations = 0
    network_usage: list[dict[str, str]] = []
    for event in events:
        action = event.get("action", {}) or {}
        if action.get("resource") != "network":
            continue
        decision = event.get("decision")
        direct = (event.get("provenance", {}) or {}).get("direct", True)
        if decision == "allow":
            network_usage.append(
                {"resource": "network", "operation": action.get("operation", ""), "target": action.get("target", "")}
            )
            if direct:
                direct_allowed += 1
        elif decision == "deny":
            if direct:
                direct_denied += 1
            else:
                transitive_denied += 1
        elif decision == "escalate":
            escalations += 1

    fs_writes = [r for r in codex_usage if r["resource"] == "filesystem"]
    process_execs = [r for r in codex_usage if r["resource"] == "process"]

    start_ts = _read_int(run_dir / "codex-start.ts")
    end_ts = _read_int(run_dir / "codex-end.ts")
    duration_seconds = (end_ts - start_ts) if start_ts is not None and end_ts is not None else None

    task_completion: bool | None = None
    if after is not None:
        _, after_failed = after
        task_completion = after_failed == 0

    granted = _granted_capabilities(manifest.capabilities) if manifest is not None else set()
    exercised = _exercised_capabilities(granted, network_usage + codex_usage)
    shadow_violations = _shadow_violations(manifest, codex_usage) if manifest is not None else []

    return {
        "run_id": run_dir.name,
        "task_completion": task_completion,
        "tests_before": before,
        "tests_after": after,
        "duration_seconds": duration_seconds,
        "filesystem_writes": len(fs_writes),
        "filesystem_unique_files_written": len({r["target"] for r in fs_writes}),
        "process_executions": len(process_execs),
        "network_direct_allowed": direct_allowed,
        "network_direct_denied": direct_denied,
        "network_transitive_denied": transitive_denied,
        "capabilities_granted": len(granted),
        "capabilities_exercised": len(exercised),
        "capabilities_unused": len(granted) - len(exercised),
        "escalations": escalations,
        "policy_violations": direct_denied + transitive_denied,
        "shadow_violations": shadow_violations,
        "has_manifest": manifest is not None,
    }


def _fmt_ratio(counts: tuple[int, int] | None) -> str:
    if counts is None:
        return "n/a"
    passed, failed = counts
    return f"{passed}/{passed + failed}"


def _fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "n/a"
    minutes, secs = divmod(max(seconds, 0), 60)
    return f"{minutes:02d}:{secs:02d}"


def render_m3_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("IDHN M3 RESULT")
    lines.append("-" * 34)
    lines.append("")

    completion = report["task_completion"]
    completion_label = "PASS" if completion is True else "FAIL" if completion is False else "UNKNOWN (no after-test log)"
    lines.append(f"Task completion          {completion_label}")
    lines.append(f"Tests before              {_fmt_ratio(report['tests_before'])}")
    lines.append(f"Tests after               {_fmt_ratio(report['tests_after'])}")
    lines.append("")
    lines.append(f"Agent duration            {_fmt_duration(report['duration_seconds'])}")
    lines.append("")

    lines.append("Filesystem")
    lines.append("  reads                   not observed in v0.1 (no read-level instrumentation yet)")
    lines.append(f"  writes                  {report['filesystem_writes']}")
    lines.append(f"  unique files written    {report['filesystem_unique_files_written']}")
    lines.append("")

    lines.append("Processes")
    lines.append(f"  executions              {report['process_executions']}")
    lines.append("")

    lines.append("Network")
    lines.append(f"  direct allowed          {report['network_direct_allowed']}")
    lines.append(f"  direct denied           {report['network_direct_denied']}")
    lines.append(f"  transitive denied       {report['network_transitive_denied']}")
    lines.append("")

    lines.append("Capabilities")
    if report["has_manifest"]:
        lines.append(f"  granted                 {report['capabilities_granted']}")
        lines.append(f"  exercised               {report['capabilities_exercised']}")
        lines.append(f"  unused                  {report['capabilities_unused']}")
    else:
        lines.append("  n/a (no policy applied -- this is a baseline run)")
    lines.append("")

    lines.append(f"Escalations               {report['escalations']}")
    lines.append("")
    lines.append(f"Policy violations         {report['policy_violations']}  (live-enforced: network only)")

    shadow = report.get("shadow_violations") or []
    lines.append("")
    lines.append(f"Unenforced overreach      {len(shadow)}  (filesystem/process: observed, not blocked in v0.1)")
    for v in shadow:
        lines.append(f"  {v['resource']}.{v['operation']:<6} {v['target']}  ({v['reason']})")

    return "\n".join(lines)
