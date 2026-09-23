# IDHN v0.1 -- Interchangeable Deterministic Host Network

A proof of concept for putting a deterministic, auditable policy
boundary between a coding agent (first: Codex) and the resources it can
touch -- filesystem, network, processes, git, secrets.

IDHN is **not** a Codex wrapper. The policy engine (`idhn/core/`) knows
nothing about Codex or Docker; both are interchangeable adapters
(`idhn/adapters/`, `idhn/runtime/`). Swapping in Claude or Gemini as the
agent, or a different sandbox as the runtime, should never require
touching `core/`.

```
                 IDHN
                  |
          +-------+--------+
          |                |
     CONTROL PLANE     AUDIT PLANE
          |                ^
          v                |
     POLICY ENGINE ----- events
          |
          v
     RUNTIME ADAPTER
          |
        Docker
          |
          v
        CODEX
          |
          v
   Controlled resources
```

## How a request is decided

```
ActionRequest
     |
     v
Policy Engine (idhn/core/policy.py)
     |
 +---+--------+
 v   v        v
ALLOW DENY  ESCALATE
```

Deterministic, table-driven, no LLM in the loop. See
[idhn/core/policy.py](idhn/core/policy.py).

## Docker topology

```
                         HOST
                           |
              +------------+-------------+
              |                          |
         IDHN Controller             Audit log (runs/<id>/events.jsonl)
              |
              v
       +--------------+
       | Agent Docker |
       |              |
       |    CODEX     |
       |              |
       | /workspace   |
       +-------+------+
               |
               | isolated Docker network (no direct internet)
               v
       +--------------+
       | IDHN Gateway |
       |              |
       | network      |
       | enforcement  |
       +-------+------+
               |
               v
            Internet
```

The agent container joins only the `internal` Docker network defined in
[docker/compose.yaml](docker/compose.yaml) -- it has no route to the
internet other than through the gateway, which checks every outbound
connection against the manifest's `network` capability
(`idhn/network/proxy.py`) before opening a tunnel. The agent container
also never receives `/var/run/docker.sock`, a host filesystem mount, or
any real credentials -- see [lab/README.md](lab/README.md) for what it
does get.

## Setup

Requires Docker, Node/npm (for the Codex CLI, installed at image build
time), and Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate   # or your usual venv flow
pip install -e ".[dev]"
idhn init                                            # creates .env from .env.example
```

Fill in `OPENAI_API_KEY` in `.env` (or export it) -- the agent container
needs it to authenticate `codex exec`.

## Running the M0 experiment

M0's goal: Codex fixes one intentionally broken test entirely inside the
disposable Docker environment, with zero access to anything outside
`lab/repos/toy-app`.

```bash
idhn validate policies/examples/coding-safe.yaml

idhn run \
    --policy policies/examples/coding-safe.yaml \
    --agent codex \
    "Fix the parser bug and run the tests"

idhn report <run-id>   # printed at the end of `idhn run`
```

What this does:

1. Loads and validates the manifest (`policies/examples/coding-safe.yaml`).
2. Builds and starts the `agent` + `gateway` containers via
   `docker compose` ([docker/compose.yaml](docker/compose.yaml)), mounting
   `lab/repos/toy-app` read-write at `/workspace` in the agent container
   and nothing else.
3. Runs `codex exec --full-auto "<task>"` inside the agent container.
   `lab/repos/toy-app/tests/parser.test.js` has one failing test (a
   shunting-yard operator-precedence bug in
   [src/parser.js](lab/repos/toy-app/src/parser.js)) -- Codex's job is to
   find and fix it, then confirm `npm test` passes.
4. The gateway logs every outbound connection attempt as an allow/deny
   event to `runs/<run-id>/events.jsonl`.
5. `idhn report <run-id>` summarizes allowed/blocked actions.

You can run the toy test suite directly (no Docker, no agent) to see the
bug and confirm the fix works the same way Codex will be expected to:

```bash
cd lab/repos/toy-app
node --test "tests/*.test.js"    # 1 of 4 tests fails
# fix src/parser.js, then:
node --test "tests/*.test.js"    # all 4 pass
```

## Repository layout

- `idhn/core/` -- capability model, manifest loader, deterministic policy
  engine, risk classification. No Codex or Docker knowledge.
- `idhn/runtime/` -- runtime interface + the Docker backend
  (`docker.py`). Swappable.
- `idhn/adapters/` -- per-agent command builders. `codex.py` is the only
  one implemented in v0.1.
- `idhn/network/` -- the gateway proxy and its policy resolution.
- `idhn/audit/` -- JSONL event logging + report rendering. Files only,
  no database.
- `idhn/compiler/` -- **not implemented**. Task -> capability compilation
  is downstream of M0-M2.
- `policies/` -- manifest examples.
- `docker/` -- the two-container topology (agent, gateway).
- `lab/` -- disposable toy app + fake secrets for attack testing.
- `benchmark/` -- task/attack scenario definitions; the runner that
  executes them is not implemented yet.

## Known limitations of v0.1

- **Network enforcement is real**: the agent container has no direct
  internet route, and the gateway checks every domain against policy.
- **Filesystem enforcement is coarse-grained**: v0.1 relies on *not
  mounting* anything sensitive into the agent container, plus the policy
  engine's filesystem allow/deny rules being available for audit and for
  M1's benchmark runner to check against. There is no live syscall-level
  interception (e.g. FUSE/seccomp) blocking a specific `read()` inside
  `/workspace` yet -- that's M1 work.
- `compiler/`, `benchmark/runner.py`, and `lab/services/vulnerable-proxy`
  are intentionally stubs. So are multi-agent support, a capability
  graph, dynamic permission learning, and everything else on the "not
  yet" list below.

## What we deliberately do not build yet

LLM capability generation, dynamic permission learning, a capability
graph, multi-agent support, Kubernetes/AWS, MCP, an enterprise
dashboard, user accounts, billing, compliance reports, cryptographic
receipts, or sophisticated risk scoring. All of that is downstream of
proving the enforcement boundary itself works.

## Milestones

- **M0 -- Sandbox**: Codex solves a toy coding task inside the disposable
  Docker environment. *(current target)*
- **M1 -- Enforcement**: IDHN policy allows normal actions and reliably
  prevents deliberately forbidden filesystem/network actions.
- **M2 -- Transitive enforcement**: Codex has access to an allowed
  internal service but cannot use it to obtain a forbidden external
  capability.

Only after M0 -> M1 -> M2 work does adaptive/learned authorization become
worth building.
