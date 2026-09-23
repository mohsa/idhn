"""Append-only(-ish) JSONL event writer.

No database, no external telemetry system: an events.jsonl per run is
the entire audit trail for v0.1. Both the network gateway and (later)
the runtime write through this so every audit consumer sees one format.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from idhn.core.models import ExecutionContext, PolicyDecision
from idhn.runtime.events import RuntimeEvent


class AuditLogger:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.run_dir / "events.jsonl"
        self._lock = threading.Lock()

    def log_event(self, event: RuntimeEvent) -> None:
        line = json.dumps(event.to_dict(), sort_keys=True)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def log_decision(self, decision: PolicyDecision, context: ExecutionContext) -> None:
        self.log_event(RuntimeEvent.from_policy_decision(decision, context))
