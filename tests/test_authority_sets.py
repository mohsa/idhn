import json

from idhn.audit.authority_sets import (
    compute_missing,
    compute_unnecessary,
    exercised_from_run,
    infrastructure_authority,
    network_events_from_run,
    predicted_from_decisions,
)
from idhn.compiler.parser import CapabilityDecision


def test_infrastructure_authority_includes_codex_and_chatgpt():
    infra = infrastructure_authority()
    assert ("process", "exec", "codex") in infra
    assert ("network", "connect", "chatgpt.com") in infra


def test_compute_missing_uses_glob_matching_not_exact_equality():
    exercised = {("filesystem", "write", "/workspace/src/parser.js")}
    granted = {("filesystem", "write", "/workspace/src/**")}
    assert compute_missing(exercised, granted) == set()


def test_compute_missing_flags_uncovered_exercised_capability():
    exercised = {("process", "exec", "cat")}
    granted = {("process", "exec", "node")}
    assert compute_missing(exercised, granted) == {("process", "exec", "cat")}


def test_compute_unnecessary_flags_ungranted_scope_never_used():
    predicted = {("network", "connect", "registry.npmjs.org")}
    exercised: set = set()
    assert compute_unnecessary(predicted, exercised) == predicted


def test_compute_unnecessary_empty_when_everything_predicted_was_used():
    predicted = {("filesystem", "write", "/workspace/src/**")}
    exercised = {("filesystem", "write", "/workspace/src/parser.js")}
    assert compute_unnecessary(predicted, exercised) == set()


def test_predicted_from_decisions():
    decisions = [
        CapabilityDecision(resource="process", operation="exec", scope="git", confidence="high", reason="x"),
    ]
    assert predicted_from_decisions(decisions) == {("process", "exec", "git")}


def test_exercised_from_run_combines_process_and_allowed_network(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    (run_dir / "codex-events.jsonl").write_text(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "command_execution", "command": "/bin/bash -lc 'git status'", "exit_code": 0},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "action": {"resource": "network", "operation": "connect", "target": "registry.npmjs.org"},
                "decision": "allow",
                "provenance": {"direct": True, "via": []},
            }
        )
        + "\n"
        + json.dumps(
            {
                "action": {"resource": "network", "operation": "connect", "target": "attacker.test"},
                "decision": "deny",
                "provenance": {"direct": True, "via": []},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    exercised = exercised_from_run(run_dir)

    assert ("process", "exec", "git") in exercised
    assert ("network", "connect", "registry.npmjs.org") in exercised
    # A denied attempt is not exercised authority.
    assert ("network", "connect", "attacker.test") not in exercised


def test_network_events_from_run_splits_allow_and_deny(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "action": {"resource": "network", "operation": "connect", "target": "registry.npmjs.org"},
                "decision": "allow",
            }
        )
        + "\n"
        + json.dumps(
            {
                "action": {"resource": "network", "operation": "connect", "target": "pypi.org"},
                "decision": "deny",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    allowed, denied = network_events_from_run(run_dir)

    assert allowed == {("network", "connect", "registry.npmjs.org")}
    assert denied == {("network", "connect", "pypi.org")}


def test_network_events_from_run_empty_when_no_gateway_log(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # No events.jsonl at all -- the unconstrained leg has no gateway.
    assert network_events_from_run(run_dir) == (set(), set())
