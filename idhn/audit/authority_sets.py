"""Authority-set arithmetic for v0.2-B: I (infrastructure), P
(compiler-predicted), E (exercised), M (missing), U (unnecessary).

The reason this exists instead of just diffing against one hand-written
manifest (what v0.2-A did): the compiler should be judged on whether it
predicted what the TASK needed, not penalized for not predicting
infrastructure Codex needs regardless of task (chatgpt.com, its own
launcher binaries) -- that's a separate, fixed set, not something task
reasoning should have to rediscover every time. Keeping I separate from
P is what makes "M = E - (I union P)" a meaningful signal about the
compiler rather than a repeat of the same infrastructure-blindness
finding from v0.2-A's single data point.

As of v0.2.1, I is three composable sources, not one undifferentiated
set (agent_infrastructure_authority, substrate_authority, tool_authority
below) -- v0.2-B's 24-task run found "infrastructure" had been doing
double duty, conflating "what Codex needs to run at all" with "what
execution substrate ANY task needs" (pwd/cat/ls/...) and "what a
substrate tool itself needs" (npm's registry) -- three genuinely
different, still all task-independent, things. infrastructure_authority()
stays as their union so nothing downstream needs to change.

A capability is a (resource, operation, scope) tuple. Set membership
for comparison purposes is glob-aware (a granted "/workspace/src/**"
covers an exercised "/workspace/src/parser.js"), not exact string
equality -- see `covers`.
"""
from __future__ import annotations

import json
from fnmatch import fnmatch
from pathlib import Path

from idhn.audit.codex_events import parse_codex_events
from idhn.enforcement.process import AGENT_INFRASTRUCTURE_BINARIES

Capability = tuple[str, str, str]

# Fixed, not task-conditioned -- what Codex itself needs to run at all,
# independent of what any task asks for or which tools it invokes. See
# idhn.enforcement.process (process side) and the M3 finding that
# motivated adding chatgpt.com to every example manifest (network side).
AGENT_INFRASTRUCTURE_NETWORK_HOSTS = ["chatgpt.com", "*.chatgpt.com", "*.oaiusercontent.com"]

# Backward-compatible alias -- INFRASTRUCTURE_NETWORK_HOSTS was the name
# before v0.2.1 split "infrastructure" into agent/substrate/tool sources.
INFRASTRUCTURE_NETWORK_HOSTS = AGENT_INFRASTRUCTURE_NETWORK_HOSTS

# The execution substrate: ordinary shell/discovery/interpreter binaries
# treated as a fixed floor of HOW the agent operates, not WHAT it's
# authorized to do -- v0.2-B's 24-task run found the compiler being
# scored on predicting these (14% substrate recall, dragging a 96%
# task-semantic-recall compiler down to a 29% raw one) was measuring the
# wrong layer entirely: idhn.enforcement.filesystem/network already gate
# the actual CONSEQUENCE of running any given tool regardless of whether
# the tool itself was "granted" (see idhn.enforcement.process's own
# docstring). idhn.compiler.context.AVAILABLE_TOOLS's 10, plus `bash`
# (the shell every one of Codex's own commands runs through, and which
# has never once shown up as a missing/predicted capability across 24
# tasks + 6 ablations -- it's reached some other way that never touches
# this allowlist, but belongs here conceptually regardless), plus `nl`
# and `sha256sum` (T01/T18's context-completeness gaps -- coreutils
# discovery tools of the exact same kind, just never listed), plus `pip`
# (T24's real, exercised install tool -- distinct from `python3`, which
# is the interpreter, not the package manager). Deliberately
# a superset of AVAILABLE_TOOLS, not identical to it: this list has to be
# COMPLETE against what the corpus actually exercises for "tool-substrate
# recall" to genuinely read N/A rather than a residual few percent that
# misrepresents this fix as partial.
PROCESS_SUBSTRATE_BINARIES = (
    "pwd", "ls", "find", "cat", "rg", "python3", "pytest", "node", "npm", "git",
    "bash", "nl", "sha256sum", "pip",
)

# Known operational network requirements of specific substrate tools --
# e.g. `npm` reaching its registry even on tasks that never mention
# dependencies (v0.2-B's M8 finding: 4 of the taxonomy's 55 misses, all
# `network.connect: registry.npmjs.org`, all on tasks whose text never
# mentions a dependency, driven by the agent's own choice to run `npm
# test`/`npm install` as part of unrelated work). This is the concrete
# form of "EffectiveGrant = ToolRequirement (subject to whatever else
# denies it)", not "npm implies network *": only the specific hosts a
# tool is actually known to need, and still filtered by an explicit
# `deny` pattern at enforcement time the same way any other granted
# capability is -- idhn.core.policy.PolicyEngine.decide() checks `deny`
# before allow, unconditionally, regardless of a capability's source.
# There's no separate "organizational ceiling" object to build; that
# deny-first precedence already IS the ceiling.
TOOL_AUTHORITY_REQUIREMENTS: dict[str, set[Capability]] = {
    "npm": {("network", "connect", "registry.npmjs.org")},
    "pip": {
        ("network", "connect", "pypi.org"),
        ("network", "connect", "files.pythonhosted.org"),
    },
}


def agent_infrastructure_authority() -> set[Capability]:
    """Agent authority: what Codex itself needs to run at all."""
    items: set[Capability] = {("process", "exec", binary) for binary in AGENT_INFRASTRUCTURE_BINARIES}
    items |= {("network", "connect", host) for host in AGENT_INFRASTRUCTURE_NETWORK_HOSTS}
    return items


def substrate_authority() -> set[Capability]:
    """Execution-substrate authority: ordinary binaries the agent may
    invoke regardless of task, distinct from what consequence running
    them is allowed to have (that's filesystem/network, still
    task-scoped via P).
    """
    return {("process", "exec", binary) for binary in PROCESS_SUBSTRATE_BINARIES}


def tool_authority() -> set[Capability]:
    """Tool authority: known operational requirements of substrate tools
    (only for tools actually in the substrate floor, so this stays
    coupled to what's actually grantable rather than every possible
    tool's every possible host).
    """
    items: set[Capability] = set()
    for tool in PROCESS_SUBSTRATE_BINARIES:
        items |= TOOL_AUTHORITY_REQUIREMENTS.get(tool, set())
    return items


def infrastructure_authority() -> set[Capability]:
    """I: the fixed floor, task-independent authority from all three
    non-task sources -- agent, substrate, and tool. Kept as one function
    (rather than requiring every caller to union three) so run_task.py,
    summarize.py, and ablate.py need no changes; the sources are exposed
    separately above for anyone who wants to reason about them apart.
    """
    return agent_infrastructure_authority() | substrate_authority() | tool_authority()


def capability_class(capability: Capability) -> str:
    """"infrastructure" | "tool_substrate" | "task_semantic".

    Not a fourth axis alongside I/P/E/M/U -- a way of slicing them,
    because the aggregate recall number conflates two very different
    kinds of miss. Task-semantic capabilities are the actual consequence
    of doing the task -- which files get written, which hosts get
    reached, which git state changes -- and are exactly what a capability
    compiler should be judged on predicting. Tool-substrate capabilities
    (process.exec for cat/ls/pwd/rg/...) are how the agent looked around
    to figure out what to do; a compiler that doesn't grant `pwd` isn't
    making the same kind of mistake as one that doesn't grant
    `filesystem.write` on the file that needed fixing, and pretending
    otherwise buries the signal that actually matters (see M3.5's
    enforcement.process docstring: process execution is real, but weak,
    containment -- the filesystem/network boundaries are what actually
    constrain the consequences of running any given tool). Any
    non-infrastructure `process` capability is tool_substrate, regardless
    of which specific binary; anything else (filesystem, network, git)
    is task_semantic.
    """
    if any(covers(g, capability) for g in infrastructure_authority()):
        return "infrastructure"
    resource, _operation, _scope = capability
    if resource == "process":
        return "tool_substrate"
    return "task_semantic"


def covers(granted: Capability, used: Capability) -> bool:
    g_resource, g_operation, g_scope = granted
    u_resource, u_operation, u_target = used
    if g_resource != u_resource or g_operation != u_operation:
        return False
    if g_resource == "process":
        return g_scope == u_target  # exact binary name, no glob semantics
    return fnmatch(u_target, g_scope)


def compute_missing(exercised: set[Capability], granted: set[Capability]) -> set[Capability]:
    """M = E - (I union P): exercised authority no granted capability covers."""
    return {e for e in exercised if not any(covers(g, e) for g in granted)}


def compute_unnecessary(predicted: set[Capability], exercised: set[Capability]) -> set[Capability]:
    """U = P - E: predicted grants that never covered anything exercised.

    Not automatically "wrong" -- a legitimate grant simply might not be
    exercised in one particular run (e.g. a deny/read grant for a file
    that turned out not to need touching). It's the signal worth
    inspecting, especially when it's a broad or high-risk scope; see
    idhn.core.risk for the classification used to prioritize which U
    entries matter.
    """
    return {p for p in predicted if not any(covers(p, e) for e in exercised)}


def exercised_from_run(run_dir: Path) -> set[Capability]:
    """E for one run: process/filesystem usage from Codex's own --json
    trace (idhn.audit.codex_events -- same lower-bound caveat as
    everywhere else it's used: an inline-edit write can go unrecorded),
    plus every network target the gateway actually allowed through
    (events.jsonl). A denied network attempt is NOT exercised authority
    -- it's evidence of an attempt, which is a different question
    (what the compiler predicted vs. what got blocked) than what
    authority the run actually used.
    """
    run_dir = Path(run_dir)
    exercised: set[Capability] = set()

    for record in parse_codex_events(run_dir / "codex-events.jsonl"):
        if record.get("succeeded") is False:
            continue
        exercised.add((record["resource"], record["operation"], record["target"]))

    events_path = run_dir / "events.jsonl"
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            action = event.get("action", {}) or {}
            if action.get("resource") == "network" and event.get("decision") == "allow":
                exercised.add(("network", "connect", action.get("target", "")))

    return exercised


def network_events_from_run(run_dir: Path) -> tuple[set[Capability], set[Capability]]:
    """(allowed, denied) network capabilities from one run's events.jsonl
    (the gateway's own log -- only exists for runs that go through it).

    Split out from exercised_from_run because of a real gap: the
    unconstrained leg of a v0.2-B task (idhn.runtime.docker.run_baseline)
    has no gateway at all -- there's nothing IN THE RUN ITSELF for it to
    isolate against -- so it can never show network authority as
    "exercised", which would make every network prediction look
    unnecessary regardless of reality (found by actually running T07 and
    seeing an empty exercised network set despite the task clearly using
    the network). The fix isn't to give the unconstrained leg a gateway;
    it's to also pull network signal from the CONSTRAINED leg
    (idhn.runtime.docker.DockerRuntime, run under I union P), where a
    deny event is direct, confirmed evidence of a missing capability --
    stronger than the usual "wasn't observed" absence-of-evidence M
    computation, not weaker.
    """
    run_dir = Path(run_dir)
    allowed: set[Capability] = set()
    denied: set[Capability] = set()

    events_path = run_dir / "events.jsonl"
    if not events_path.exists():
        return allowed, denied

    for line in events_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        event = json.loads(line)
        action = event.get("action", {}) or {}
        if action.get("resource") != "network":
            continue
        capability = ("network", "connect", action.get("target", ""))
        if event.get("decision") == "allow":
            allowed.add(capability)
        elif event.get("decision") == "deny":
            denied.add(capability)

    return allowed, denied


def predicted_from_decisions(decisions) -> set[Capability]:
    """P from a list of idhn.compiler.parser.CapabilityDecision."""
    return {d.as_tuple() for d in decisions}


def to_manifest_capabilities(capabilities: set[Capability]) -> dict:
    """Collapse a capability set (e.g. I union P) into the nested
    filesystem/process/network shape idhn.core.manifest.Manifest and
    idhn.enforcement expect, so "run the task actually constrained by
    what the compiler proposed" can reuse the real enforcement path
    (idhn.runtime.docker.DockerRuntime, same as every other real run in
    this codebase) instead of a separate simulated one.
    """
    result: dict = {
        "filesystem": {"read": [], "write": [], "deny": ["/workspace/.env", "/workspace/.github/**", "/home/**", "/root/**"]},
        "process": {"allow": []},
        "network": {"default": "deny", "allow": []},
        "git": {"allow": ["status", "diff"], "deny": ["push"]},
        "secrets": {"default": "deny"},
    }
    for resource, operation, scope in sorted(capabilities):
        if resource == "filesystem" and operation in ("read", "write"):
            bucket = result["filesystem"][operation]
        elif resource == "process":
            bucket = result["process"]["allow"]
        elif resource == "network":
            bucket = result["network"]["allow"]
        elif resource == "git":
            bucket = result["git"]["allow"]
        else:
            continue
        if scope not in bucket:
            bucket.append(scope)
    return result
