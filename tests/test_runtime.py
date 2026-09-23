import pytest

from idhn.core.models import ActionRequest, Decision, ExecutionContext, PolicyDecision
from idhn.runtime.events import RuntimeEvent
from idhn.runtime.runtime import Runtime


def test_runtime_is_abstract():
    with pytest.raises(TypeError):
        Runtime()


def test_direct_decision_produces_direct_provenance():
    decision = PolicyDecision(
        request=ActionRequest(resource="network", action="connect", target="registry.npmjs.org", agent="codex"),
        decision=Decision.ALLOW,
        reason="matched_allow_pattern",
        policy_name="coding-safe",
    )
    context = ExecutionContext(run_id="run-001", agent_id="codex", task_id="fix-parser", delegation_chain=[])

    event = RuntimeEvent.from_policy_decision(decision, context)
    data = event.to_dict()

    assert data["run_id"] == "run-001"
    assert data["principal"] == {"type": "agent", "id": "codex"}
    assert data["action"] == {"resource": "network", "operation": "connect", "target": "registry.npmjs.org"}
    assert data["provenance"] == {"direct": True, "via": []}
    assert data["decision"] == "allow"
    assert data["timestamp"].endswith("Z")


def test_transitive_decision_records_via_chain():
    decision = PolicyDecision(
        request=ActionRequest(resource="network", action="connect", target="example.com", agent="codex"),
        decision=Decision.DENY,
        reason="transitive_capability_not_authorized",
        policy_name="transitive-escape",
    )
    context = ExecutionContext(
        run_id="run-002",
        agent_id="codex",
        task_id="fix-parser",
        delegation_chain=["fetch-service"],
    )

    event = RuntimeEvent.from_policy_decision(decision, context)
    data = event.to_dict()

    assert data["provenance"] == {"direct": False, "via": ["fetch-service"]}
    assert data["decision"] == "deny"
    assert data["reason"] == "transitive_capability_not_authorized"
    # principal stays the agent even though the connect was made by a
    # service on its behalf -- provenance is what distinguishes the path.
    assert data["principal"]["id"] == "codex"
