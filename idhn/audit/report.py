"""Build and render an IDHN execution report from a run's audit trail."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def _read_events(run_dir: Path) -> list[dict[str, Any]]:
    events_path = run_dir / "events.jsonl"
    if not events_path.exists():
        return []
    events = []
    with events_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def build_report(run_dir: Path, run_id: str) -> dict[str, Any]:
    run_dir = Path(run_dir)
    events = _read_events(run_dir)

    tallies: dict[str, Counter] = {
        "filesystem_read": Counter(),
        "filesystem_write": Counter(),
        "network": Counter(),
        "process": Counter(),
        "git": Counter(),
        "secrets": Counter(),
    }
    blocked: list[dict[str, Any]] = []
    escalations = 0

    for event in events:
        resource = event.get("resource")
        action = event.get("action")
        decision = event.get("decision")
        key = f"{resource}_{action}" if resource == "filesystem" else resource
        if key in tallies:
            tallies[key][decision] += 1
        if decision == "deny":
            blocked.append(event)
        elif decision == "escalate":
            escalations += 1

    task_description = None
    manifest_path = run_dir / "manifest.yaml"
    if manifest_path.exists():
        import yaml

        with manifest_path.open("r", encoding="utf-8") as f:
            manifest_data = yaml.safe_load(f) or {}
        task_description = (manifest_data.get("task") or {}).get("description")

    return {
        "run_id": run_id,
        "task": task_description,
        "tallies": {k: dict(v) for k, v in tallies.items()},
        "blocked": blocked,
        "escalations": escalations,
        "event_count": len(events),
    }


def render_text_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("IDHN EXECUTION REPORT")
    lines.append("-" * 40)
    lines.append("")
    lines.append("Run")
    lines.append(report["run_id"])
    lines.append("")
    if report.get("task"):
        lines.append("Task")
        lines.append(report["task"])
        lines.append("")

    lines.append("CAPABILITIES")
    lines.append("")

    def section(title: str, key: str) -> None:
        counts = report["tallies"].get(key, {})
        lines.append(title)
        lines.append(f"Allowed  {counts.get('allow', 0)}")
        lines.append(f"Blocked  {counts.get('deny', 0)}")
        lines.append("")

    section("Filesystem reads", "filesystem_read")
    section("Filesystem writes", "filesystem_write")
    section("Network", "network")

    lines.append("BLOCKED ACTIONS")
    lines.append("")
    if report["blocked"]:
        for event in report["blocked"]:
            ts = (event.get("timestamp") or "")[11:16]
            lines.append(f"{ts}  {event.get('action', '').upper():<8} {event.get('target', '')}")
    else:
        lines.append("none")
    lines.append("")

    lines.append("ESCALATIONS")
    lines.append("")
    lines.append(str(report["escalations"]))

    return "\n".join(lines)
