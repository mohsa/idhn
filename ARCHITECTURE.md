# IDHN architecture

For the what/why/quickstart, see `README.md`. This is the deeper,
developer-facing reference: how the pieces fit together, where
enforcement actually happens, and where to look if you're extending
this rather than just running it.

## The four-stage pipeline

```
task (natural language)
    |
    v
COMPILE (idhn/compiler/, LLM-based, untrusted output)
    |  -> a flat list of (resource, operation, scope, confidence, reason)
    |     capability decisions
    v
MATERIALIZE (idhn/compiler/materialize.py, deterministic, no LLM call)
    |  -> re-validates from scratch, unions with a fixed infrastructure
    |     floor, rejects unscoped wildcards/dangerous primitives outright
    |  -> an enforceable manifest (policies/*.yaml shape)
    v
ENFORCE (idhn/runtime/, idhn/enforcement/, idhn/network/ -- Docker)
    |  -> filesystem: mount table.  network: gateway + no route out.
    |  -> process: PATH-restricted shim.
    v
AUDIT (idhn/audit/)
    -> reconciles granted vs. exercised vs. denied-and-stayed-denied
       vs. denied-but-succeeded-anyway (a real finding, not a gap to
       paper over)
```

Each stage is independently testable and the untrusted stage (compile)
can never widen what the deterministic stages allow: confidence and
`reason` are compiler metadata (`CapabilityDecision.as_tuple()` drops
them before anything downstream sees a proposal), and materialize's own
budget check rejects unscoped wildcards and dangerous execution
primitives (`sudo`, `docker`, `mount`, `nsenter`) regardless of what
confidence or reasoning the compiler attached to them.

## Module map

- `idhn/core/` -- the capability model, manifest loader
  (`core/manifest.py`), and the deterministic policy engine
  (`core/policy.py`, `PolicyEngine.evaluate()` -- table-driven,
  ALLOW/DENY/ESCALATE, no LLM in the loop). Knows nothing about Codex
  or Docker.
- `idhn/adapters/` -- one file per agent (`codex.py` today). Turns a
  task description into the command line that runs that agent
  non-interactively. `idhn/adapters/codex.py`'s own docstring explains
  a real, non-obvious design decision in detail: why it runs Codex with
  `--sandbox danger-full-access` instead of Codex's own `workspace-write`
  mode (IDHN's container enforcement replaces it; the two conflict under
  `cap_drop: ALL`), and what provider-side capabilities it disables by
  default and why (`web_search`, `browser_use`, `computer_use`, `apps`
  -- see `docs/web-search-out-of-band-capability.md`).
- `idhn/compiler/` -- the untrusted LLM proposal step (`compiler.py`,
  `parser.py`, `budget.py`) and the deterministic bridge from that
  proposal to an enforceable manifest (`materialize.py` -- no LLM call,
  pure data transformation, see `docs/compile-materialize-run.md`).
- `idhn/runtime/` -- `docker.py` (`DockerRuntime`, the only module that
  knows Docker Compose is being used -- swap it for another backend
  without touching `core/`, `adapters/`, or `enforcement/`),
  `workspace_lock.py` (the T6 TOCTOU mitigation -- serializes runs per
  workspace, see `docs/toctou-investigation-t6.md`).
- `idhn/enforcement/` -- turns manifest capability sections into real
  mechanisms: `filesystem.py` (mount-plan builder: empty read-only tmpfs
  base, every `read`/`write` grant a layered bind mount, every `deny` a
  mask on top, plus the symlink checks from `docs/
  symlink-write-bypass-fix.md`/`docs/read-symlink-bypass-fix.md`) and
  `process.py` (PATH-restricted binary allowlist, scoped to the agent's
  own command only -- see its own docstring for exactly what this
  mechanism does and does not guarantee).
- `idhn/network/` -- `proxy.py` (the gateway: a policy-checked HTTP
  CONNECT forward proxy, the only route any container has to the
  internet) and `policy.py`/`callers.py` (which service is asking,
  direct vs. transitive).
- `idhn/audit/` -- reconciliation and reporting:
  `authority_sets.py` (the three non-task authority sources -- agent
  infrastructure, execution substrate, tool requirements -- unioned
  with what the compiler predicted), `m3_report.py`/`codex_events.py`
  (parses an agent's own trace for capability usage, deny-masked read
  attempts, and out-of-band/provider-side items), `filesystem_diff.py`
  (ground-truth content-hash diffing, independent of what an agent
  self-reports).

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

The agent container joins only the `internal` network defined in
`docker/compose.yaml` -- a Docker network property (`internal: true`,
no NAT/masquerade route out at all), not an application-level
convention like a proxy environment variable. It has no route to the
internet other than through the gateway, which checks every outbound
connection against the manifest's `network` capability
(`idhn/network/proxy.py`) *before* opening a tunnel -- the one resource
with a live, pre-action check; filesystem and process are reconciled
after the fact from the agent's own reported exit codes (see `docs/
security-boundary-and-limitations.md`'s audit-gap section for exactly
what that distinction means for what's provable from the audit trail
alone). The agent container never receives `/var/run/docker.sock`, a
host filesystem mount beyond the one task-scoped workspace directory,
or any real credentials beyond a single read-only `auth.json` file --
see `lab/README.md` for the full account of what it does and doesn't
get.

## Extending this

**A new agent** (Claude, Gemini, ...): add `idhn/adapters/<name>.py`
with a `command(self, task: str) -> list[str]` method, matching
`CodexAdapter`'s shape. Nothing in `idhn/core/`, `idhn/enforcement/`,
or `idhn/runtime/` should need to change -- if it does, that's a sign
the new adapter needs something architecturally new, worth raising as
its own design question rather than special-casing.

**A new runtime** (not Docker): implement `idhn/runtime/runtime.py`'s
`Runtime` abstract base (`start`/`stop`/`events`) the way `DockerRuntime`
does. The policy engine and manifest model have zero Docker-specific
assumptions; `idhn/runtime/docker.py` is the only place that should
need Docker Compose knowledge.

**A new resource type** beyond filesystem/network/process/git/secrets:
needs a new `_evaluate_<resource>` method on `PolicyEngine`
(`idhn/core/policy.py`) and a corresponding enforcement mechanism under
`idhn/enforcement/` or `idhn/network/` -- the policy engine's own
`evaluate()` dispatches by resource name via `getattr`, so an
unimplemented resource currently escalates rather than silently
allowing or denying (`_escalate(request, f"no_policy_for_resource:
{request.resource}")`).
