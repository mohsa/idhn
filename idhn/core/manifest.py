"""Load and validate IDHN policy manifests (YAML)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from idhn.core.models import Task

SUPPORTED_VERSIONS = {"0.1"}
REQUIRED_TOP_LEVEL_KEYS = ("version", "task", "capabilities")


class ManifestError(Exception):
    """Raised when a manifest fails to load or validate."""


@dataclass
class Manifest:
    version: str
    task: Task
    capabilities: dict[str, Any]
    limits: dict[str, Any] = field(default_factory=dict)
    name: str = "default"
    source_path: Path | None = None


def validate(data: dict[str, Any]) -> list[str]:
    """Return human-readable validation errors. Empty list means valid."""
    errors: list[str] = []

    if not isinstance(data, dict):
        return ["manifest root must be a mapping"]

    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in data:
            errors.append(f"missing required top-level key: {key}")

    version = data.get("version")
    if version is not None and str(version) not in SUPPORTED_VERSIONS:
        errors.append(
            f"unsupported manifest version: {version!r} (supported: {sorted(SUPPORTED_VERSIONS)})"
        )

    task = data.get("task")
    if task is not None:
        if not isinstance(task, dict):
            errors.append("task must be a mapping")
        else:
            if "id" not in task:
                errors.append("task.id is required")
            if "description" not in task:
                errors.append("task.description is required")

    capabilities = data.get("capabilities")
    if capabilities is not None and not isinstance(capabilities, dict):
        errors.append("capabilities must be a mapping")

    limits = data.get("limits")
    if limits is not None and not isinstance(limits, dict):
        errors.append("limits must be a mapping")

    return errors


def load_manifest(path: str | Path) -> Manifest:
    """Load a YAML manifest from disk and validate it."""
    path = Path(path)
    if not path.exists():
        raise ManifestError(f"manifest not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    errors = validate(data)
    if errors:
        raise ManifestError(
            f"invalid manifest {path}:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    task_data = data["task"]
    task = Task(id=str(task_data["id"]), description=str(task_data["description"]))

    return Manifest(
        version=str(data["version"]),
        task=task,
        capabilities=data.get("capabilities", {}) or {},
        limits=data.get("limits", {}) or {},
        name=path.stem,
        source_path=path,
    )
