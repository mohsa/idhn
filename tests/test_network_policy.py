from idhn.core.manifest import Manifest
from idhn.core.models import Decision, Task
from idhn.core.policy import PolicyEngine
from idhn.network.policy import NetworkPolicy


def make_engine(**network) -> PolicyEngine:
    manifest = Manifest(
        version="0.1",
        task=Task(id="t", description="test"),
        capabilities={"network": network},
        name="test-policy",
    )
    return PolicyEngine(manifest)


def test_allowed_domain_passes_through_network_policy():
    engine = make_engine(default="deny", allow=["registry.npmjs.org"])
    policy = NetworkPolicy(engine, agent="codex")
    decision = policy.check("registry.npmjs.org")
    assert decision.decision == Decision.ALLOW
    assert decision.request.resource == "network"
    assert decision.request.agent == "codex"


def test_denied_domain_blocked_by_network_policy():
    engine = make_engine(default="deny", allow=["registry.npmjs.org"])
    policy = NetworkPolicy(engine, agent="codex")
    decision = policy.check("attacker.test")
    assert decision.decision == Decision.DENY
