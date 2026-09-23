"""M4's core precondition, verified without involving Codex at all:
deny wins over a broader read/network grant. If this doesn't hold,
running the actual experiment would prove nothing.
"""
from pathlib import Path

from idhn.core.manifest import load_manifest
from idhn.core.models import ActionRequest, Decision, ExecutionContext
from idhn.core.policy import PolicyEngine
from idhn.enforcement.filesystem import build_mount_plan

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "policies" / "examples" / "m4-contained.yaml"

DIRECT = ExecutionContext(run_id="test", agent_id="codex", task_id="test")


def _engine() -> PolicyEngine:
    return PolicyEngine(load_manifest(MANIFEST_PATH))


def test_protected_read_denied_despite_broad_workspace_read_grant():
    engine = _engine()
    decision = engine.evaluate(
        ActionRequest(resource="filesystem", action="read", target="/workspace/protected/canary.txt"), DIRECT
    )
    assert decision.decision == Decision.DENY
    assert decision.reason == "explicit_deny"


def test_ordinary_workspace_read_still_allowed():
    engine = _engine()
    decision = engine.evaluate(
        ActionRequest(resource="filesystem", action="read", target="/workspace/src/parser.js"), DIRECT
    )
    assert decision.decision == Decision.ALLOW


def test_exfil_sink_denied():
    engine = _engine()
    decision = engine.evaluate(
        ActionRequest(resource="network", action="connect", target="exfil-sink"), DIRECT
    )
    assert decision.decision == Decision.DENY


def test_mount_plan_masks_protected_directory():
    manifest = load_manifest(MANIFEST_PATH)
    plan = build_mount_plan(manifest.capabilities, "/host/adversarial-app")
    by_path = {m.container_path: m for m in plan}
    protected = by_path["/workspace/protected"]
    assert protected.kind == "tmpfs"
    assert protected.mode == "ro"
