"""Runtime adapter interface.

An IDHN Runtime is responsible for starting/stopping an agent's sandbox
and surfacing whatever enforcement signals it can. `docker.py` is the
first implementation; it should be swappable later (gVisor, Firecracker,
...) without core/ or adapters/ changing.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

from idhn.core.manifest import Manifest
from idhn.runtime.events import RuntimeEvent


class Runtime(ABC):
    @abstractmethod
    def start(self, manifest: Manifest, command: list[str], run_dir: Path, execution_id: str) -> int:
        """Start the sandbox, run `command` inside it to completion, return its exit code."""

    @abstractmethod
    def stop(self) -> None:
        """Tear down the sandbox, if still running."""

    @abstractmethod
    def events(self) -> Iterator[RuntimeEvent]:
        """Yield runtime-observed events (e.g. from the network gateway's audit log)."""
