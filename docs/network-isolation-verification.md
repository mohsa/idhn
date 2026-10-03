# Network isolation: resistance to direct egress bypassing the gateway

Prompted by dogfood run `run-20261003-867c08`: Codex used Node's native
`https.get("https://example.com")`, which ignores `HTTP_PROXY`/
`HTTPS_PROXY` entirely (Node's built-in `http`/`https` modules never
consult them unless the caller explicitly wires in a proxy agent). The
request failed with `getaddrinfo EAI_AGAIN example.com`, and the IDHN
report showed `Network Blocked 0` because the request never reached the
gateway's policy check at all. That's the *desired* outcome, but from a
single run's report alone, "nothing happened because the agent is
properly contained" and "nothing happened because our instrumentation
didn't catch it" look identical. This doesn't assume the former -- it's
verified mechanically, against the real Docker topology, with
integration tests in `tests/integration/test_network_isolation.py`.

## Mechanism: why direct egress does not work

```
docker/compose.yaml:

networks:
  internal:
    internal: true      # <-- the entire mechanism
  external: {}

services:
  gateway:
    networks: [internal, external]   # the only container with a route out
  agent:
    networks: [internal]             # no route out, by construction
    environment:
      HTTP_PROXY:  http://gateway:8080
      HTTPS_PROXY: http://gateway:8080
```

Docker Compose's `internal: true` is not a firewall rule layered on top
of a network that otherwise has internet access -- it changes how the
network itself is built. A normal (non-internal) bridge network gets a
NAT/masquerade iptables rule so its containers can reach the host's
external interface; an `internal: true` network does not. A container
attached only to such a network has **no route to anything outside that
network's own subnet**, full stop. This holds regardless of:

- whether the process inside the container knows about `HTTP_PROXY`/
  `HTTPS_PROXY` at all (most don't, by default -- that's the whole
  dogfood finding);
- whether it tries DNS, a raw TCP connection to a hardcoded IP, or
  anything else;
- whether the gateway container is even running.

`agent` joins only `internal`. `gateway` joins both `internal` and
`external`, making it the one container with an actual route out --
every outbound connection genuinely has to pass through its code
(`idhn/network/proxy.py`, a policy-checked HTTP CONNECT forward proxy)
to reach the real internet, because nothing else in the topology can.
`HTTP_PROXY`/`HTTPS_PROXY` pointing at `gateway:8080` is a *convenience*
for proxy-aware tools, not the enforcement boundary -- the boundary is
that there is nowhere else for a packet to go.

This also explains the exact symptom in the dogfood run. Docker's
embedded DNS resolver (127.0.0.11, used by every container by default)
forwards queries it can't answer itself to an upstream resolver. On an
`internal: true` network, that forwarding attempt has no route out
either, so the query times out and the application sees `EAI_AGAIN`
("temporary failure in name resolution") -- not `NXDOMAIN`, not a
connection refusal. DNS failing is itself a symptom of the same network
isolation, not a separate or weaker protection: confirmed directly below
by also testing a hardcoded IP, which bypasses DNS completely and still
has no route out.

## What the integration tests verify

`tests/integration/test_network_isolation.py` (marked `docker`, excluded
from the default `pytest` run -- see "Running these tests" below). Each
test brings up the **full real compose topology** (gateway included)
via `idhn.runtime.docker.DockerRuntime.start` -- the same code path a
real `idhn run` uses -- and runs a probe script as the agent's command
under a dedicated policy, `policies/examples/network-isolation-test.yaml`
(`network.allow: [example.com]`, default deny otherwise).

| # | Test | What it proves |
|---|---|---|
| 1 | `test_direct_dns_resolution_of_public_hostname_fails` | `socket.getaddrinfo('example.com', 443)` fails with `gaierror` -- the exact dogfood symptom, reproduced deliberately |
| 2 | `test_direct_tcp_connection_to_external_ip_fails` | A raw TCP connect to a hardcoded public IP (`1.1.1.1:443`, Cloudflare) fails with `OSError: Network is unreachable` -- rules out "maybe it's just a DNS problem"; there is no route at the TCP layer either |
| 3 | `test_proxy_ignoring_https_client_cannot_reach_internet` | Python's `http.client.HTTPSConnection` (which, like Node's native `https`, never consults `HTTP_PROXY`/`HTTPS_PROXY`) still can't reach `example.com` -- a second, independent proxy-ignoring client, not just Node's |
| 4 | `test_proxy_aware_traffic_to_allowed_host_succeeds_through_gateway` | `urllib.request` (which DOES honor `HTTP_PROXY`/`HTTPS_PROXY`) reaches `example.com` successfully, and the gateway's own `events.jsonl` logs a real `allow` decision for it -- confirms the gateway path genuinely works, not just that direct egress is blocked |
| 5 | `test_proxy_aware_traffic_to_forbidden_host_is_rejected_by_gateway` | The same proxy-aware client requesting `wikipedia.org` (not in the policy's allow-list) gets HTTP 403 from the gateway, logged as `deny`/`default_deny` -- confirms policy enforcement still works for traffic that correctly reaches the gateway |

Plus `tests/test_compose_topology.py` (no `docker` marker, runs every
time): a fast, Docker-free static check that `docker/compose.yaml`
still declares `internal: true` and that `agent` joins only `internal`
-- catches an accidental topology regression immediately, without
needing Docker, even though it can't on its own prove the runtime
behavior the way the integration tests do.

### Why `DockerRuntime.start`, not a simpler `docker compose run`

The first implementation attempt used `docker compose run --no-deps
--entrypoint python3 agent -c "..."` directly. It failed for the wrong
reason: the gateway (`idhn/network/callers.py`) identifies its caller by
resolving the Docker-DNS hostname `"agent"` to an IP and comparing it to
the TCP peer address -- deliberately, specifically because that's not
spoofable by the agent the way a header would be. `docker compose run`
creates an ad hoc, uniquely-named container that does **not** get the
compose service's network alias (`"agent"` doesn't resolve to it at
all), so the gateway correctly rejected it as an "unrecognized caller" --
a real finding about the test harness, not about network isolation.
Using `DockerRuntime.start` (the same path a real `idhn run` takes)
brings up the actual `agent` *service*, which does get the alias, and
avoids re-implementing (and potentially subtly diverging from) IDHN's
own run orchestration.

## The observability distinction

Two things that can make a connection attempt "not show up as blocked,"
which must not be read as the same finding:

1. **Denied by the IDHN gateway**: the request reached
   `idhn/network/proxy.py`'s policy check, which evaluated it and said
   no. This is a positive, attributable decision: an entry in
   `events.jsonl` with `"decision": "deny"` and a `reason` (e.g.
   `default_deny`, `explicit_deny`), surfaced in `idhn report`/
   `idhn m3-report`. **Test 5** produces exactly this.

2. **Direct egress prevented by network isolation, before reaching the
   gateway**: the request never got far enough to reach -- or in the DNS
   case, never got far enough to even *address* -- the gateway at all.
   **There is no event. `events.jsonl` doesn't contain a denial for
   this; for tests 1-3, it doesn't exist at all**, because the gateway's
   own code never ran for that attempt. This is not a gap in the
   gateway's logging -- there is nothing for the gateway to log, because
   the gateway was never involved. The report correctly shows
   `Network Blocked 0` for a run shaped like this, for the same reason
   it would for a run that made no network attempts whatsoever: from the
   gateway's point of view, those two situations are identical, and
   distinguishing them requires looking at whether the attempt could
   have reached the gateway's network at all (an `internal: true`-network
   question), not at the gateway's own logs.

Put differently: `idhn report`'s `Network Blocked` count means "the
gateway saw this and said no." It has never meant, and cannot mean on
its own, "nothing got through" -- that second, stronger claim is what
this document's mechanism section and tests 1-3 establish independently,
by testing the network topology directly rather than inferring it from
the absence of a gateway log entry.

## Running these tests

```
# Default suite (fast, Docker-free, unaffected by any of this):
pytest

# The live network-isolation proof (requires Docker + real internet
# access to example.com/wikipedia.org/1.1.1.1 -- consistent with how
# M1 was originally verified live, not a new requirement):
pytest -m docker tests/integration
```

## Scope and limitations

- Verifies the **current** topology (`docker/compose.yaml` as checked
  in). It does not protect against someone later adding the `external`
  network to the `agent` service, removing `cap_drop: ALL`, or similar --
  `tests/test_compose_topology.py` catches the first two specifically;
  a determined enough change to the compose file or enforcement code
  could still reintroduce a bypass that these tests don't happen to
  probe for. They test the invariant as stated, not every conceivable
  variant of it.
- Depends on real internet reachability to `example.com`, `wikipedia.org`,
  and `1.1.1.1` from wherever these tests run. If none of those are
  reachable (e.g. an air-gapped CI runner), tests 2, 4, and 5 would fail
  or hang for reasons unrelated to the invariant -- acceptable for a lab
  project verified this way since M1, not something this change
  introduces.
- `fetch-service` (the M2 transitive-escape fixture) is not exercised
  here. It already joins only `internal` and routes its own outbound
  traffic through the same gateway, by the same construction -- the
  identical mechanism, already covered by this document's reasoning,
  not re-tested separately since the question ("can code inside an
  `internal`-only container reach the internet directly") doesn't change
  based on which container is asking.
- Did not change `docker/compose.yaml`, `idhn/network/proxy.py`,
  `idhn/network/callers.py`, or any enforcement code. The tests found no
  bypass, so none of this was needed; if they had, the instruction was
  to stop and document before making any architectural change.
