import pytest

from idhn.audit.authority_sets import infrastructure_authority
from idhn.compiler.materialize import MaterializeError, materialize


def _decision(resource, operation, scope, confidence="high", reason="because"):
    return {"capability": {"resource": resource, "operation": operation, "scope": scope}, "confidence": confidence, "reason": reason}


def test_filesystem_read_capabilities_translate_correctly():
    envelope = [_decision("filesystem", "read", "/workspace/src/**")]
    result = materialize(envelope, "t1", "do the thing")
    assert "/workspace/src/**" in result.manifest_data["capabilities"]["filesystem"]["read"]


def test_exact_filesystem_write_scope_is_preserved_verbatim():
    """A write grant for an exact file must remain that exact scope --
    materialization must never widen it to a directory or a glob."""
    exact_scope = "/workspace/modules/evaluation/matcher.py"
    envelope = [_decision("filesystem", "write", exact_scope)]

    result = materialize(envelope, "t1", "fix the matcher")

    writes = result.manifest_data["capabilities"]["filesystem"]["write"]
    assert writes == [exact_scope]


def test_process_capabilities_translate_correctly():
    envelope = [_decision("process", "exec", "python")]
    result = materialize(envelope, "t1", "run python")
    assert "python" in result.manifest_data["capabilities"]["process"]["allow"]


def test_zero_task_network_grants_remains_default_deny_with_only_infrastructure_hosts():
    """No network capability in the compiled IR must not invent task
    network access -- only the fixed agent-infrastructure hosts
    (chatgpt.com family) should appear, never a host the IR never named.
    """
    envelope = [_decision("filesystem", "read", "/workspace/**")]

    result = materialize(envelope, "t1", "read only task")

    network = result.manifest_data["capabilities"]["network"]
    assert network["default"] == "deny"
    infra_network_hosts = {t for (r, _op, t) in infrastructure_authority() if r == "network"}
    assert set(network["allow"]) == infra_network_hosts


def test_network_grants_if_present_translate_correctly():
    envelope = [_decision("network", "connect", "some-task-host.example")]

    result = materialize(envelope, "t1", "reach a task host")

    network = result.manifest_data["capabilities"]["network"]
    assert network["default"] == "deny"
    assert "some-task-host.example" in network["allow"]
    # Infrastructure hosts are still present alongside it -- the task
    # grant doesn't replace the fixed floor.
    infra_network_hosts = {t for (r, _op, t) in infrastructure_authority() if r == "network"}
    assert infra_network_hosts.issubset(set(network["allow"]))


@pytest.mark.parametrize(
    "bad_envelope,why",
    [
        ([{"capability": {"resource": "filesystem", "operation": "read"}, "confidence": "high", "reason": "x"}], "missing scope"),
        ([{"capability": {"resource": "gpu", "operation": "use", "scope": "cuda0"}, "confidence": "high", "reason": "x"}], "unknown resource"),
        ([{"capability": {"resource": "process", "operation": "exec", "scope": "git"}, "confidence": "maybe", "reason": "x"}], "bad confidence"),
        ("not-a-list", "wrong top-level type"),
        ([{"capability": {"resource": "filesystem", "operation": "delete", "scope": "/workspace/x"}, "confidence": "high", "reason": "x"}], "bad filesystem operation"),
    ],
)
def test_malformed_unvalidated_ir_cannot_become_an_enforceable_manifest(bad_envelope, why):
    with pytest.raises(MaterializeError):
        materialize(bad_envelope, "t1", "task", limits=None)


def test_budget_violations_cannot_become_an_enforceable_manifest():
    envelope = [_decision("filesystem", "write", "/workspace/**")]
    with pytest.raises(MaterializeError, match="budget"):
        materialize(envelope, "t1", "task")


def test_confidence_and_reason_do_not_affect_enforcement():
    """Two envelopes that differ ONLY in confidence/reason must produce
    byte-for-byte identical capabilities -- confidence/reason are
    compiler metadata, never consulted by materialization."""
    envelope_a = [_decision("filesystem", "write", "/workspace/src/a.py", confidence="high", reason="very sure")]
    envelope_b = [_decision("filesystem", "write", "/workspace/src/a.py", confidence="low", reason="just a guess, might be wrong")]

    result_a = materialize(envelope_a, "t1", "task")
    result_b = materialize(envelope_b, "t1", "task")

    assert result_a.manifest_data["capabilities"] == result_b.manifest_data["capabilities"]


def test_generated_manifest_passes_idhn_validate():
    from idhn.core.manifest import validate as validate_manifest

    envelope = [
        _decision("filesystem", "read", "/workspace/**"),
        _decision("filesystem", "write", "/workspace/src/matcher.py"),
        _decision("process", "exec", "python"),
    ]

    result = materialize(envelope, "materialized-test", "fix the matcher and add tests")

    assert validate_manifest(result.manifest_data) == []


def test_tolerates_the_flat_shape_as_well_as_the_nested_capability_wrapper():
    """Same tolerance parse_capability_ir already has for the model's
    output -- materialize's input is the compiler's already-parsed
    envelope, but shouldn't be any pickier than the compiler itself was.
    """
    flat_envelope = [{"resource": "process", "operation": "exec", "scope": "git", "confidence": "high", "reason": "x"}]
    result = materialize(flat_envelope, "t1", "task")
    assert "git" in result.manifest_data["capabilities"]["process"]["allow"]


def test_empty_envelope_still_produces_a_valid_manifest_with_only_infrastructure():
    result = materialize([], "t1", "a task needing nothing beyond infrastructure")
    assert result.predicted == set()
    assert result.manifest_data["capabilities"]["network"]["default"] == "deny"
