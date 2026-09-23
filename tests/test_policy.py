from idhn.core.manifest import Manifest
from idhn.core.models import ActionRequest, Decision, ExecutionContext, Task
from idhn.core.policy import PolicyEngine


def make_manifest(**capabilities) -> Manifest:
    return Manifest(
        version="0.1",
        task=Task(id="t", description="test"),
        capabilities=capabilities,
        name="test-policy",
    )


def make_context(delegation_chain: list[str] | None = None) -> ExecutionContext:
    return ExecutionContext(
        run_id="run-test",
        agent_id="codex",
        task_id="task-test",
        parent_action_id=None,
        delegation_chain=delegation_chain or [],
    )


DIRECT = make_context()


def test_filesystem_write_allowed_inside_scope():
    manifest = make_manifest(filesystem={"write": ["/workspace/src/**"], "deny": []})
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(
        ActionRequest(resource="filesystem", action="write", target="/workspace/src/parser.py"), DIRECT
    )
    assert decision.decision == Decision.ALLOW


def test_filesystem_write_denied_outside_scope():
    manifest = make_manifest(filesystem={"write": ["/workspace/src/**"], "deny": []})
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(
        ActionRequest(resource="filesystem", action="write", target="/etc/passwd"), DIRECT
    )
    assert decision.decision == Decision.DENY


def test_filesystem_explicit_deny_wins_over_read_allow():
    manifest = make_manifest(
        filesystem={"read": ["/workspace/**"], "deny": ["/workspace/.env"]}
    )
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(
        ActionRequest(resource="filesystem", action="read", target="/workspace/.env"), DIRECT
    )
    assert decision.decision == Decision.DENY
    assert decision.reason == "explicit_deny"


def test_network_default_deny_blocks_unlisted_domain():
    manifest = make_manifest(network={"default": "deny", "allow": ["registry.npmjs.org"]})
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(
        ActionRequest(resource="network", action="connect", target="attacker.test"), DIRECT
    )
    assert decision.decision == Decision.DENY


def test_network_allows_listed_domain():
    manifest = make_manifest(network={"default": "deny", "allow": ["registry.npmjs.org"]})
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(
        ActionRequest(resource="network", action="connect", target="registry.npmjs.org"), DIRECT
    )
    assert decision.decision == Decision.ALLOW


def test_network_wildcard_subdomain():
    manifest = make_manifest(network={"default": "deny", "allow": ["*.npmjs.org"]})
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(
        ActionRequest(resource="network", action="connect", target="registry.npmjs.org"), DIRECT
    )
    assert decision.decision == Decision.ALLOW


def test_process_not_in_allow_list_denied():
    manifest = make_manifest(process={"allow": ["python", "pytest"]})
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(ActionRequest(resource="process", action="exec", target="curl"), DIRECT)
    assert decision.decision == Decision.DENY


def test_process_allow_accepts_category_dict():
    manifest = make_manifest(
        process={
            "allow": {
                "discovery": ["pwd", "ls"],
                "development": ["python3", "node"],
            }
        }
    )
    engine = PolicyEngine(manifest)
    allowed = engine.evaluate(ActionRequest(resource="process", action="exec", target="pwd"), DIRECT)
    assert allowed.decision == Decision.ALLOW
    denied = engine.evaluate(ActionRequest(resource="process", action="exec", target="curl"), DIRECT)
    assert denied.decision == Decision.DENY


def test_git_push_denied_even_though_not_requested_via_deny_list_alone():
    manifest = make_manifest(git={"allow": ["status", "diff"], "deny": ["push"]})
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(ActionRequest(resource="git", action="run", target="push"), DIRECT)
    assert decision.decision == Decision.DENY
    assert decision.reason == "explicit_deny"


def test_secrets_default_deny():
    manifest = make_manifest(secrets={"default": "deny"})
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(
        ActionRequest(resource="secrets", action="read", target="/workspace/.env"), DIRECT
    )
    assert decision.decision == Decision.DENY


def test_unknown_resource_escalates():
    manifest = make_manifest()
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(ActionRequest(resource="gpu", action="use", target="cuda0"), DIRECT)
    assert decision.decision == Decision.ESCALATE


# -- transitive (service-mediated) network requests --------------------


def make_transitive_manifest() -> Manifest:
    return make_manifest(
        network={"default": "deny", "allow": ["registry.npmjs.org", "fetch-service"]},
        services={
            "fetch-service": {
                "actions": {
                    "fetch": {
                        "allow": [{"host": "registry.npmjs.org"}],
                        "deny": [{"host": "*"}],
                    }
                }
            }
        },
    )


def test_direct_network_request_ignores_services_section():
    manifest = make_transitive_manifest()
    engine = PolicyEngine(manifest)
    decision = engine.evaluate(
        ActionRequest(resource="network", action="connect", target="example.com"), DIRECT
    )
    assert decision.decision == Decision.DENY
    assert decision.reason == "default_deny"


def test_transitive_request_allowed_when_service_allows_host():
    manifest = make_transitive_manifest()
    engine = PolicyEngine(manifest)
    context = make_context(delegation_chain=["fetch-service"])
    decision = engine.evaluate(
        ActionRequest(resource="network", action="connect", target="registry.npmjs.org"), context
    )
    assert decision.decision == Decision.ALLOW
    assert decision.reason == "matched_service_allow_pattern"


def test_transitive_request_blocked_even_though_direct_network_would_deny_anyway():
    manifest = make_transitive_manifest()
    engine = PolicyEngine(manifest)
    context = make_context(delegation_chain=["fetch-service"])
    decision = engine.evaluate(
        ActionRequest(resource="network", action="connect", target="example.com"), context
    )
    assert decision.decision == Decision.DENY
    assert decision.reason == "transitive_capability_not_authorized"


def test_transitive_request_escalates_when_service_not_declared_in_manifest():
    manifest = make_manifest(network={"default": "deny", "allow": []})
    engine = PolicyEngine(manifest)
    context = make_context(delegation_chain=["some-other-service"])
    decision = engine.evaluate(
        ActionRequest(resource="network", action="connect", target="example.com"), context
    )
    assert decision.decision == Decision.ESCALATE
    assert "no_policy_for_service" in decision.reason
