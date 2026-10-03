"""Verify IDHN's resistance to direct network egress that bypasses
HTTP_PROXY/HTTPS_PROXY -- prompted by dogfood run run-20261003-867c08,
where Codex's Node.js `https.get` ignored the configured proxy entirely
and failed with `getaddrinfo EAI_AGAIN example.com` instead of reaching
the gateway. The IDHN report showed `Network Blocked 0` because the
request never reached the gateway's policy check at all -- which is
EXACTLY the desired outcome (no usable direct path to the internet), but
"the report shows nothing" and "the agent is properly contained" look
identical from inside a single run. These tests tell them apart
mechanically, against the real Docker topology, not by reasoning about
docker/compose.yaml.

Desired invariant:
    Agent external traffic -> IDHN gateway -> policy decision -> external network
There must be no usable path:
    Agent -> direct public internet

Mechanism (see docker/compose.yaml and idhn/network/callers.py):
the `internal` network is declared `internal: true` -- Docker creates it
with NO route to the outside world at all (no NAT/masquerade rule), so a
container on it has nowhere to send a packet addressed to the public
internet regardless of whether it's proxy-aware. `agent` joins ONLY
`internal`; `gateway` joins both `internal` and `external`, so it's the
only container with an actual route out. HTTP_PROXY/HTTPS_PROXY are a
convenience the agent's own tooling may or may not honor (Node's native
https module doesn't, by default -- the dogfood run's own finding); the
network topology itself is what makes bypassing them useless, not an
assumption about which HTTP clients respect proxy env vars.

Each test brings up the FULL real compose topology (gateway included)
via idhn.runtime.docker.DockerRuntime.start -- the same code path a real
`idhn run` uses, not a hand-rolled `docker compose run` -- because the
gateway identifies its caller by resolving the Docker-DNS hostname
"agent" to an IP (idhn.network.callers.CallerRegistry), which only the
compose SERVICE (`up`) gets as a network alias; an ad hoc `docker
compose run` container doesn't, and gets rejected as an
"unrecognized caller" for a reason that has nothing to do with the
invariant under test -- found by trying the simpler approach first and
hitting exactly that failure.

Requires Docker and real internet access to example.com/wikipedia.org/
1.1.1.1 (consistent with how M1 was originally verified live against
registry.npmjs.org/example.com -- not a new requirement). Marked
`docker` and excluded from the default `pytest` run (see pyproject.toml)
-- run explicitly with `pytest -m docker tests/integration`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from idhn.core.manifest import load_manifest  # noqa: E402
from idhn.runtime.docker import DockerRuntime  # noqa: E402

pytestmark = pytest.mark.docker

POLICY_PATH = REPO_ROOT / "policies" / "examples" / "network-isolation-test.yaml"
WORKSPACE_DIR = REPO_ROOT / "lab" / "repos" / "toy-app"

# Direct-egress probes: the agent trying to reach the real internet
# WITHOUT going through HTTP_PROXY/HTTPS_PROXY. Three different mechanisms
# an agent (or a library it uses) could use, per the request:
_DNS_PROBE = "import socket; print(socket.getaddrinfo('example.com', 443))"
_RAW_TCP_PROBE = (
    "import socket; s = socket.create_connection(('1.1.1.1', 443), timeout=5); print('CONNECTED')"
)
# http.client (unlike urllib.request) never consults HTTP_PROXY/HTTPS_PROXY
# -- a stand-in for "a client that ignores proxy environment variables",
# the exact shape of the real dogfood finding (Node's native https module).
_PROXY_IGNORING_HTTPS_PROBE = (
    "import http.client\n"
    "c = http.client.HTTPSConnection('example.com', timeout=5)\n"
    "c.request('GET', '/')\n"
    "r = c.getresponse()\n"
    "print('STATUS', r.status)\n"
)
# urllib.request DOES consult HTTP_PROXY/HTTPS_PROXY by default -- the
# proxy-AWARE case, for contrast.
_PROXY_AWARE_PROBE_TEMPLATE = (
    "import urllib.request\n"
    "r = urllib.request.urlopen('https://{host}', timeout=15)\n"
    "print('STATUS', r.status)\n"
)


def _run_probe(probe_code: str, run_dir: Path, execution_id: str) -> dict:
    """Run `probe_code` as the "agent command" through the real
    DockerRuntime.start -- full compose topology (gateway included), real
    mount/process/network enforcement from the manifest at POLICY_PATH.
    Returns the container exit code, the probe's own exit code, its
    stdout/stderr, and whatever the gateway logged to events.jsonl (empty
    if the gateway never saw anything at all).
    """
    manifest = load_manifest(POLICY_PATH)
    runtime = DockerRuntime()
    container_exit_code = runtime.start(
        manifest,
        ["python3", "-c", probe_code],
        run_dir,
        execution_id,
        workspace_dir=WORKSPACE_DIR,
    )
    probe_exit_path = run_dir / "codex-exit-code"
    probe_exit = int(probe_exit_path.read_text().strip()) if probe_exit_path.exists() else None
    stdout = (run_dir / "codex-events.jsonl").read_text(encoding="utf-8") if (run_dir / "codex-events.jsonl").exists() else ""
    stderr = (run_dir / "codex-stderr.log").read_text(encoding="utf-8") if (run_dir / "codex-stderr.log").exists() else ""
    events_path = run_dir / "events.jsonl"
    events = []
    if events_path.exists():
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {
        "container_exit_code": container_exit_code,
        "probe_exit_code": probe_exit,
        "stdout": stdout,
        "stderr": stderr,
        "events": events,
        "events_file_exists": events_path.exists(),
    }


def test_direct_dns_resolution_of_public_hostname_fails(tmp_path):
    result = _run_probe(_DNS_PROBE, tmp_path, "net-iso-dns")

    assert result["probe_exit_code"] != 0, f"direct DNS resolution should fail; stdout={result['stdout']!r}"
    assert "gaierror" in result["stderr"] or "Temporary failure" in result["stderr"]
    # The request never got far enough to even form an HTTP CONNECT --
    # the gateway never saw it, and has nothing to say about it either
    # way. See the module docstring's observability distinction.
    assert not result["events_file_exists"], "gateway should have logged nothing -- DNS never resolved"


def test_direct_tcp_connection_to_external_ip_fails(tmp_path):
    # Bypasses DNS entirely -- a hardcoded public IP (Cloudflare's
    # 1.1.1.1), so this can't be explained away as "just a DNS problem".
    result = _run_probe(_RAW_TCP_PROBE, tmp_path, "net-iso-raw-tcp")

    assert result["probe_exit_code"] != 0, f"direct raw TCP connect should fail; stdout={result['stdout']!r}"
    assert "CONNECTED" not in result["stdout"]
    assert "unreachable" in result["stderr"].lower() or "timed out" in result["stderr"].lower()
    assert not result["events_file_exists"], "gateway should have logged nothing -- the packet had no route out"


def test_proxy_ignoring_https_client_cannot_reach_internet(tmp_path):
    # http.client never looks at HTTP_PROXY/HTTPS_PROXY -- the same shape
    # as Node's native https module in the real dogfood run.
    result = _run_probe(_PROXY_IGNORING_HTTPS_PROBE, tmp_path, "net-iso-proxy-ignoring")

    assert result["probe_exit_code"] != 0, f"proxy-ignoring HTTPS should fail; stdout={result['stdout']!r}"
    assert "STATUS" not in result["stdout"]
    assert not result["events_file_exists"], "gateway should have logged nothing -- it never reached the proxy"


def test_proxy_aware_traffic_to_allowed_host_succeeds_through_gateway(tmp_path):
    probe = _PROXY_AWARE_PROBE_TEMPLATE.format(host="example.com")
    result = _run_probe(probe, tmp_path, "net-iso-allowed")

    assert result["probe_exit_code"] == 0, f"allowed host should succeed; stderr={result['stderr']!r}"
    assert "STATUS 200" in result["stdout"]
    allow_events = [
        e for e in result["events"]
        if e["action"]["target"] == "example.com" and e["decision"] == "allow"
    ]
    assert allow_events, f"gateway should have logged an allow decision for example.com; saw {result['events']!r}"


def test_proxy_aware_traffic_to_forbidden_host_is_rejected_by_gateway(tmp_path):
    probe = _PROXY_AWARE_PROBE_TEMPLATE.format(host="wikipedia.org")
    result = _run_probe(probe, tmp_path, "net-iso-denied")

    assert result["probe_exit_code"] != 0, f"forbidden host should be rejected; stdout={result['stdout']!r}"
    assert "403" in result["stderr"]
    deny_events = [
        e for e in result["events"]
        if e["action"]["target"] == "wikipedia.org" and e["decision"] == "deny"
    ]
    assert deny_events, f"gateway should have logged a deny decision for wikipedia.org; saw {result['events']!r}"
    assert deny_events[0]["reason"] == "default_deny"
