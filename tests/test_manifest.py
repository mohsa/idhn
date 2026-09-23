from pathlib import Path

import pytest

from idhn.core.manifest import ManifestError, load_manifest, validate

REPO_ROOT = Path(__file__).resolve().parents[1]
CODING_SAFE = REPO_ROOT / "policies" / "examples" / "coding-safe.yaml"


def test_load_coding_safe_manifest():
    manifest = load_manifest(CODING_SAFE)
    assert manifest.version == "0.1"
    assert manifest.task.id == "demo-001"
    assert "filesystem" in manifest.capabilities
    assert manifest.limits["cpu"] == 2


def test_missing_file_raises():
    with pytest.raises(ManifestError):
        load_manifest(REPO_ROOT / "policies" / "does-not-exist.yaml")


def test_validate_rejects_missing_keys():
    errors = validate({"version": "0.1"})
    assert any("task" in e for e in errors)
    assert any("capabilities" in e for e in errors)


def test_validate_rejects_bad_version():
    errors = validate(
        {"version": "9.9", "task": {"id": "x", "description": "y"}, "capabilities": {}}
    )
    assert any("unsupported manifest version" in e for e in errors)


def test_validate_accepts_minimal_valid_manifest():
    errors = validate(
        {"version": "0.1", "task": {"id": "x", "description": "y"}, "capabilities": {}}
    )
    assert errors == []
