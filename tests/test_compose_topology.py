"""Fast, Docker-free regression guard for the network-isolation
invariant proven live in tests/integration/test_network_isolation.py:
`internal: true` on the `internal` network (Docker creates it with no
route to the outside world at all, regardless of HTTP_PROXY/HTTPS_PROXY
awareness) is what gives the agent no path to the public internet except
through the gateway. If someone removes that flag or adds the `external`
network to the agent service, this fails immediately without needing
Docker -- the live tests are what actually prove the invariant holds at
runtime; this just catches an accidental topology regression fast, every
time the regular test suite runs.
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker" / "compose.yaml").read_text(encoding="utf-8"))


def test_internal_network_is_declared_internal():
    compose = _compose()
    assert compose["networks"]["internal"].get("internal") is True


def test_agent_joins_only_the_internal_network():
    compose = _compose()
    agent_networks = set(compose["services"]["agent"]["networks"])
    assert agent_networks == {"internal"}, f"agent must join ONLY the internal network, found {agent_networks}"


def test_gateway_joins_both_networks():
    compose = _compose()
    gateway_networks = set(compose["services"]["gateway"]["networks"])
    assert gateway_networks == {"internal", "external"}, (
        f"gateway is the only container meant to have a route out, found {gateway_networks}"
    )
