from idhn.core.manifest import Manifest
from idhn.core.models import ActionRequest, Decision, Task
from idhn.core.policy import PolicyEngine


def make_manifest(**capabilities) -> Manifest:
    return Manifest(
        version="0.1",
        task=Task(id="t", description="test"),
        capabilities=capabilities,
        name="test-policy",
    )


def test_filesystem_write_allowed_inside_scope():
    manifest = make_manifest(filesystem={"write": ["/workspace/src/**"], "deny": []})
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(
        ActionRequest(resource="filesystem", action="write", target="/workspace/src/parser.py")
    )
    assert decision.decision == Decision.ALLOW


def test_filesystem_write_denied_outside_scope():
    manifest = make_manifest(filesystem={"write": ["/workspace/src/**"], "deny": []})
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(
        ActionRequest(resource="filesystem", action="write", target="/etc/passwd")
    )
    assert decision.decision == Decision.DENY


def test_filesystem_explicit_deny_wins_over_read_allow():
    manifest = make_manifest(
        filesystem={"read": ["/workspace/**"], "deny": ["/workspace/.env"]}
    )
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(
        ActionRequest(resource="filesystem", action="read", target="/workspace/.env")
    )
    assert decision.decision == Decision.DENY
    assert decision.reason == "explicit_deny"


def test_network_default_deny_blocks_unlisted_domain():
    manifest = make_manifest(network={"default": "deny", "allow": ["registry.npmjs.org"]})
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(
        ActionRequest(resource="network", action="connect", target="attacker.test")
    )
    assert decision.decision == Decision.DENY


def test_network_allows_listed_domain():
    manifest = make_manifest(network={"default": "deny", "allow": ["registry.npmjs.org"]})
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(
        ActionRequest(resource="network", action="connect", target="registry.npmjs.org")
    )
    assert decision.decision == Decision.ALLOW


def test_network_wildcard_subdomain():
    manifest = make_manifest(network={"default": "deny", "allow": ["*.npmjs.org"]})
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(
        ActionRequest(resource="network", action="connect", target="registry.npmjs.org")
    )
    assert decision.decision == Decision.ALLOW


def test_process_not_in_allow_list_denied():
    manifest = make_manifest(process={"allow": ["python", "pytest"]})
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(ActionRequest(resource="process", action="exec", target="curl"))
    assert decision.decision == Decision.DENY


def test_git_push_denied_even_though_not_requested_via_deny_list_alone():
    manifest = make_manifest(git={"allow": ["status", "diff"], "deny": ["push"]})
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(ActionRequest(resource="git", action="run", target="push"))
    assert decision.decision == Decision.DENY
    assert decision.reason == "explicit_deny"


def test_secrets_default_deny():
    manifest = make_manifest(secrets={"default": "deny"})
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(
        ActionRequest(resource="secrets", action="read", target="/workspace/.env")
    )
    assert decision.decision == Decision.DENY


def test_unknown_resource_escalates():
    manifest = make_manifest()
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(ActionRequest(resource="gpu", action="use", target="cuda0"))
    assert decision.decision == Decision.ESCALATE
