"""Reproduces run-20261005-64e5af end to end and proves the fix: a
materialized manifest living under `runs/<compile-id>/materialized-
manifest.yaml` (i.e. NOT under policies/) must make the gateway load
THAT EXACT manifest, not crash trying to find it under /policies/, and
not silently fall back to some other policy. See
idhn.runtime.docker.gateway_manifest_mount's docstring and
docs/gateway-manifest-mount-fix.md for the root cause and fix.

Requires Docker and a real Codex credential. Marked `docker`, excluded
from the default `pytest` run -- same convention as every other file in
this directory.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from idhn.adapters.codex import CodexAdapter  # noqa: E402
from idhn.compiler.materialize import materialize  # noqa: E402
from idhn.core.manifest import load_manifest  # noqa: E402
from idhn.runtime.docker import DockerRuntime  # noqa: E402

pytestmark = pytest.mark.docker

WORKSPACE_DIR = REPO_ROOT / "lab" / "repos" / "toy-app"


def test_materialized_manifest_outside_policies_starts_gateway_and_reaches_agent(tmp_path):
    # A minimal real Capability IR -- enough for Codex to actually run
    # (read the repo, exec node) without needing to touch src/tests at
    # all, keeping this test about the gateway/manifest-mount fix, not
    # about task completion.
    envelope = [
        {"capability": {"resource": "filesystem", "operation": "read", "scope": "/workspace/**"}, "confidence": "high", "reason": "test"},
        {"capability": {"resource": "process", "operation": "exec", "scope": "node"}, "confidence": "high", "reason": "test"},
    ]
    result = materialize(envelope, task_id="materialized-gateway-test", task_description="Say hello. Do not modify any files.")

    # Deliberately OUTSIDE policies/ -- the exact shape of the original
    # bug (runs/<compile-id>/materialized-manifest.yaml), just under a
    # tmp_path instead of a real runs/ directory so this test doesn't
    # depend on any specific prior compile run existing on disk.
    manifest_dir = tmp_path / "compile-materialized-gateway-test"
    manifest_dir.mkdir()
    manifest_path = manifest_dir / "materialized-manifest.yaml"
    with manifest_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(result.manifest_data, f, sort_keys=False)

    manifest = load_manifest(manifest_path)
    assert manifest.name == "materialized-manifest"  # host-side name, from the CLI's own load -- unrelated to what the gateway sees

    command = CodexAdapter().command("Say hello. Do not modify any files.")
    run_dir = tmp_path / "run"
    runtime = DockerRuntime()
    container_exit_code = runtime.start(manifest, command, run_dir, "materialized-gateway-test", workspace_dir=WORKSPACE_DIR)

    stderr = (run_dir / "stderr.log").read_text(encoding="utf-8") if (run_dir / "stderr.log").exists() else ""
    assert "ManifestError" not in stderr, f"gateway failed to load the materialized manifest: {stderr}"
    assert "manifest not found" not in stderr

    events_path = run_dir / "events.jsonl"
    assert events_path.exists(), "gateway produced no events.jsonl at all -- it never started successfully"
    events = [__import__("json").loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert events, "gateway logged no decisions -- the agent never reached it"
    # The gateway's own Manifest.name comes from the fixed name the
    # externally-sourced manifest gets staged under in run_dir (see
    # gateway_manifest_mount's docstring on why the filename isn't
    # preserved) -- "gateway-manifest", not "default" (compose.yaml's
    # bare-default name) and not "coding-safe" (what a silent fallback to
    # the base compose.yaml mount would produce). run_id is what actually
    # disambiguates this run from another.
    assert all(e["policy"] == "gateway-manifest" for e in events), [e["policy"] for e in events]
    assert all(e["run_id"] == "materialized-gateway-test" for e in events), [e["run_id"] for e in events]

    staged = run_dir / "gateway-manifest.yaml"
    assert staged.exists(), "gateway_manifest_mount should have staged a copy into run_dir"
    assert yaml.safe_load(staged.read_text(encoding="utf-8")) == result.manifest_data

    assert container_exit_code == 0
