import pytest

from idhn.runtime.events import RuntimeEvent
from idhn.runtime.runtime import Runtime


def test_runtime_is_abstract():
    with pytest.raises(TypeError):
        Runtime()


def test_runtime_event_serializes_to_dict_with_timestamp():
    event = RuntimeEvent(
        execution_id="run-001",
        agent="codex",
        event="capability_request",
        resource="network",
        action="connect",
        target="registry.npmjs.org",
        decision="allow",
        reason="matched_allow_pattern",
        policy="coding-safe",
    )
    data = event.to_dict()
    assert data["execution_id"] == "run-001"
    assert data["timestamp"].endswith("Z")
