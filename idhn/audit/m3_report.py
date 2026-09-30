"""Build and render the M3 experiment report.

Distinct from idhn/audit/report.py's generic per-run report: M3 asks a
specific question ("did the agent do useful work, and how much authority
did it actually use getting there?") that needs task-completion and
before/after test counts, not just an allow/deny tally.

Two data sources, kept honestly separate:
  - events.jsonl: gateway-observed, policy-DECIDED network events, from
    idhn/network/proxy.py. Enforcement happens BEFORE the connection is
    allowed through -- a denied request never reaches its target.
  - codex-events.jsonl: Codex's own --json tool-call stream, parsed by
    idhn.audit.codex_events. Filesystem/process enforcement (mount
    layout + PATH restriction, see enforcement/filesystem.py and
    enforcement/process.py) happens as a structural property of the
    container Codex is already running in, not a check IDHN runs before
    each attempt -- so this module re-evaluates each observed action
    against policy AFTER the fact and cross-checks it against whether the
    action actually succeeded (exit code / completion status) to tell a
    correctly-blocked attempt from a real enforcement gap. See
    _process_overreach.

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
from idhn.enforcement.process import allowed_binaries as enforced_process_allowlist

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


def _read_observed_writes(run_dir: Path) -> list[str] | None:
    """Ground-truth changed-file list from idhn.audit.filesystem_diff
    (idhn.runtime.docker.DockerRuntime.start writes this), or None for a
    run that predates it / didn't go through that runtime. See
    build_m3_report for how this and the codex-events-derived write
    count are combined -- and why they're allowed to disagree.
    """
    path = run_dir / "observed-filesystem-writes.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, list) else None


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
    # Use the same flattening + infra-inclusion enforcement actually uses
    # (categories -> flat set, plus codex/node) so this count can't drift
    # from what's really on the agent's PATH.
    for binary in enforced_process_allowlist(capabilities):
        granted.add(("process", "exec", binary))
    git = capabilities.get("git", {}) or {}
    for pattern in git.get("allow", []) or []:
        granted.add(("git", "run", pattern))
    return granted


def _exercised_capabilities(
    granted: set[tuple[str, str, str]], usage_records: list[dict[str, Any]]
) -> set[tuple[str, str, str]]:
    exercised: set[tuple[str, str, str]] = set()
    for record in usage_records:
        # A record that we know failed (succeeded is False) wasn't a
        # successful use of the capability, even if it matches a grant --
        # don't count an attempt as "exercised". succeeded is True or None
        # (no signal, e.g. every network record, which we only ever see
        # once the gateway has already allowed it through) both count.
        if record.get("succeeded") is False:
            continue
        for grant in granted:
            resource, operation, pattern = grant
            if record["resource"] == resource and record["operation"] == operation and fnmatch(record["target"], pattern):
                exercised.add(grant)
    return exercised


def _process_overreach(manifest: Manifest, usage_records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Re-check observed filesystem/process usage against the SAME policy
    engine the gateway uses for network -- but after the fact for these
    two resources, since only network is live-checked before the action
    happens (see enforcement/filesystem.py and enforcement/process.py for
    what v0.1's real, live mechanisms are and their limits).

    Splits denied-by-policy records into what actually happened, using
    codex_events.py's exit_code-derived `succeeded` field -- this
    distinction matters now that filesystem/process enforcement is
    partially real:
      - blocked: policy denies it AND it failed. The mechanism worked --
        this is the success story, not a problem.
      - residual_gap: policy denies it AND it succeeded anyway. A real
        finding: the mechanism has a hole (e.g. the documented
        absolute-path bypass of PATH restriction) or the manifest is
        wrong about what's actually reachable.
      - unknown: policy denies it and we have no success/failure signal
        for it. Reported rather than hidden -- treat as "needs a look",
        not as either of the above.
    """
    engine = PolicyEngine(manifest)
    context = ExecutionContext(run_id="shadow-check", agent_id="codex", task_id=manifest.task.id)
    result: dict[str, list[dict[str, Any]]] = {"blocked": [], "residual_gap": [], "unknown": []}
    for record in usage_records:
        if record["resource"] not in ("filesystem", "process"):
            continue
        request = ActionRequest(resource=record["resource"], action=record["operation"], target=record["target"])
        decision = engine.evaluate(request, context)
        if decision.decision == Decision.ALLOW:
            continue
        entry = {**record, "reason": decision.reason}
        succeeded = record.get("succeeded")
        if succeeded is False:
            result["blocked"].append(entry)
        elif succeeded is True:
            result["residual_gap"].append(entry)
        else:
            result["unknown"].append(entry)
    return result


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

    # Ground truth (idhn.audit.filesystem_diff, via DockerRuntime.start)
    # subsumes fs_writes' file list whenever it's available: a real
    # content change shows up here regardless of whether Codex reported
    # it through file_change or an opaque shell command, whereas
    # fs_writes only ever sees the former. Not unioned as a heuristic --
    # every file file_change reports is, definitionally, also a real
    # content change, so ground truth is a superset, not a second guess.
    # None means this run predates the observer (no known-changed-files
    # signal at all), so unique_files_written falls back to the
    # trace-derived set alone, same as before this existed.
    observed_writes = _read_observed_writes(run_dir)
    if observed_writes is not None:
        unique_files_written = set(observed_writes)
    else:
        unique_files_written = {r["target"] for r in fs_writes}

    start_ts = _read_int(run_dir / "codex-start.ts")
    end_ts = _read_int(run_dir / "codex-end.ts")
    duration_seconds = (end_ts - start_ts) if start_ts is not None and end_ts is not None else None

    task_completion: bool | None = None
    if after is not None:
        _, after_failed = after
        task_completion = after_failed == 0

    granted = _granted_capabilities(manifest.capabilities) if manifest is not None else set()
    exercised = _exercised_capabilities(granted, network_usage + codex_usage)
    overreach = _process_overreach(manifest, codex_usage) if manifest is not None else {"blocked": [], "residual_gap": [], "unknown": []}

    return {
        "run_id": run_dir.name,
        "task_completion": task_completion,
        "tests_before": before,
        "tests_after": after,
        "duration_seconds": duration_seconds,
        "filesystem_writes": len(fs_writes),
        "filesystem_unique_files_written": len(unique_files_written),
        "filesystem_writes_ground_truth": observed_writes is not None,
        "process_executions": len(process_execs),
        "network_direct_allowed": direct_allowed,
        "network_direct_denied": direct_denied,
        "network_transitive_denied": transitive_denied,
        "capabilities_granted": len(granted),
        "capabilities_exercised": len(exercised),
        "capabilities_unused": len(granted) - len(exercised),
        "escalations": escalations,
        "policy_violations": direct_denied + transitive_denied,
        "blocked_attempts": overreach["blocked"],
        "residual_gaps": overreach["residual_gap"],
        "unknown_outcome_attempts": overreach["unknown"],
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
    lines.append(f"  writes (agent trace)    {report['filesystem_writes']}")
    ground_truth_note = "ground truth: workspace content-hash diff" if report["filesystem_writes_ground_truth"] else "from agent trace only -- this run predates ground-truth observation"
    lines.append(f"  unique files written    {report['filesystem_unique_files_written']}  ({ground_truth_note})")
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

    blocked = report.get("blocked_attempts") or []
    residual = report.get("residual_gaps") or []
    unknown = report.get("unknown_outcome_attempts") or []

    lines.append("Denied and stayed denied")
    lines.append(f"  network (live pre-check)          {report['policy_violations']}")
    lines.append(f"  filesystem/process (blocked mid-attempt)  {len(blocked)}")
    lines.append("")

    lines.append(f"Residual gaps             {len(residual)}  (denied by policy but succeeded anyway -- a real finding)")
    for v in residual:
        lines.append(f"  {v['resource']}.{v['operation']:<6} {v['target']}  ({v['reason']})")

    if unknown:
        lines.append("")
        lines.append(f"Unknown outcome           {len(unknown)}  (denied by policy, no success/failure signal available)")
        for v in unknown:
            lines.append(f"  {v['resource']}.{v['operation']:<6} {v['target']}  ({v['reason']})")

    return "\n".join(lines)
