from idhn.core.manifest import Manifest
from idhn.core.models import Decision, ExecutionContext, Task
from idhn.core.policy import PolicyEngine
from idhn.network.policy import NetworkPolicy


def make_engine(**capabilities) -> PolicyEngine:
    manifest = Manifest(
        version="0.1",
        task=Task(id="t", description="test"),
        capabilities=capabilities,
        name="test-policy",
    )
    return PolicyEngine(manifest)


def make_context(delegation_chain: list[str] | None = None) -> ExecutionContext:
    return ExecutionContext(
        run_id="run-test",
        agent_id="codex",
        task_id="task-test",
        delegation_chain=delegation_chain or [],
    )


def test_allowed_domain_passes_through_network_policy():
    engine = make_engine(network={"default": "deny", "allow": ["registry.npmjs.org"]})
    policy = NetworkPolicy(engine)
    decision = policy.check("registry.npmjs.org", make_context())
    assert decision.decision == Decision.ALLOW
    assert decision.request.resource == "network"
    assert decision.request.agent == "codex"


def test_denied_domain_blocked_by_network_policy():
    engine = make_engine(network={"default": "deny", "allow": ["registry.npmjs.org"]})
    policy = NetworkPolicy(engine)
    decision = policy.check("attacker.test", make_context())
    assert decision.decision == Decision.DENY


def test_service_mediated_request_uses_services_capability_not_network():
    engine = make_engine(
        network={"default": "deny", "allow": ["fetch-service"]},
        services={
            "fetch-service": {
                "actions": {"fetch": {"allow": [{"host": "registry.npmjs.org"}], "deny": [{"host": "*"}]}}
            }
        },
    )
    policy = NetworkPolicy(engine)

    allowed = policy.check("registry.npmjs.org", make_context(delegation_chain=["fetch-service"]))
    assert allowed.decision == Decision.ALLOW

    blocked = policy.check("example.com", make_context(delegation_chain=["fetch-service"]))
    assert blocked.decision == Decision.DENY
    assert blocked.reason == "transitive_capability_not_authorized"
