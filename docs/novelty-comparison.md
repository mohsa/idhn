# Novelty comparison: IDHN vs. existing agent sandboxing and permission approaches

Week 6 roadmap deliverable (Monday): compare IDHN with Docker sandboxing,
agent permission frameworks, and other task-scoped security approaches,
and assess whether the difference is technically meaningful. Grounded
in real sources (cited throughout), not assumed — and honest about
where this research is incomplete.

**Headline finding, stated up front:** task-scoped least-privilege for
LLM agents is an active 2026 research area with real, contemporaneous
prior art (see "Academic prior art" below). IDHN is **not** the first
system to propose this idea. What's defensible is narrower and more
specific: IDHN is a working, adversarially-tested, empirically-measured
*implementation* of this idea, enforced at the container/kernel level
against a real commercial coding agent (Codex) running against a real
external codebase — not a claim to have invented task-scoped
capability compilation.

## Commercial agent sandboxes

**OpenAI Codex CLI's own sandbox** ([docs](https://developers.openai.com/codex/concepts/sandboxing.md),
[third-party security writeup](https://codex.danielvaughan.com/2026/07/28/week-of-sandbox-escapes-pillar-security-codex-cli-cursor-gemini-docker-socket-git-hooks-defence/)):
Seatbelt on macOS, Landlock + bubblewrap on Linux. Three *static* modes
(read-only / workspace-write / danger-full-access) — not task-specific,
not generated from the task description. `workspace-write` turns
network off entirely by default (coarse on/off, not a host allowlist).

This is the sandbox IDHN's own `idhn/adapters/codex.py` explicitly
**disables** (`--sandbox danger-full-access`), not layers on top of —
`workspace-write`'s bubblewrap requirement conflicts with running under
`cap_drop: ALL` in IDHN's own container (documented in that file).
IDHN replaces Codex's native enforcement layer with its own
container/gateway boundary rather than stacking both.

A real, relevant data point: OpenAI's own safe-command allowlist
trusted `git show` *by name*, without checking that its actual
arguments weren't read-only — patched in v0.95.0 after a bug-bounty
report. This is the same bug *class* as IDHN's own T5d/read-symlink
findings (trusting something by identity — a command name, a file
path — without verifying what it actually resolves to or does).
Independent discovery of the same bug class in both a major vendor's
shipped product and in IDHN is evidence this is a genuinely
recurring, non-obvious failure mode in this problem space, not a
contrived example. OpenAI also classified Codex CLI's Docker-socket
exposure as "informational" and explicitly chose not to patch it —
a residual risk accepted by design, worth noting as the kind of
tradeoff every system in this space has to make somewhere.

**Anthropic's Claude Code sandboxing** ([engineering post](https://anthropic.com/engineering/claude-code-sandboxing),
[secure-deployment guide](https://code.claude.com/docs/en/agent-sdk/secure-deployment)):
structurally the closest match to IDHN found in this research.
Filesystem isolation (bubblewrap/Seatbelt, blocks writes outside the
working directory) plus network isolation via an **external proxy**
enforcing domain restrictions — the same two-layer shape as IDHN's
container + gateway. Anthropic's own stated reasoning for needing
both layers together ("without network isolation, a compromised agent
could exfiltrate sensitive files... without filesystem isolation, a
compromised agent could easily escape the sandbox and gain network
access") is the same reasoning behind IDHN's M2 transitive-escape and
M4 compromised-agent containment tests.

Two concrete differences: (1) Anthropic's own design goal is reducing
permission-prompt friction in an **interactive, human-supervised**
session (reported 84% reduction in prompts) — a different target than
IDHN's unattended/autonomous-run model, where there's no human to
prompt. (2) Anthropic's own documentation states plainly that native
sandboxing "covers only part of what enterprise teams need" and that
container isolation, network egress controls, credential scoping, and
audit logging "require infrastructure decisions that go beyond a
single CLI flag" — i.e., Anthropic's own guidance says the gap IDHN
fills is real and unaddressed by their shipped tool, not something
IDHN is inventing a need for.

**E2B / Daytona** ([E2B docs](https://e2b.dev/docs/use-cases/coding-agents),
[2026 comparison](https://developersdigest.tech/blog/ai-agent-code-sandbox-comparison-2026)):
cloud sandbox infrastructure — full isolated VM/container per run.
Isolation is the product; permissions (which directories, which
domains) are configured by the developer once and reused, not derived
per-task from a task description. No compiler step analogous to
`idhn compile`.

## Framework-level "permission" systems (LangChain, AutoGPT, CrewAI)

A June 2026 arXiv audit ([2606.28679](https://arxiv.org/abs/2606.28679))
found that LangChain, LlamaIndex, and the Stripe Agent Toolkit all
provide *some* capability gating, but **none provides a deterministic,
fail-closed, per-call authorization gate by default** — their testing
reproduced an unauthorized payout call succeeding under LangChain's
default dispatch. AutoGPT and CrewAI have no published, shared security
API at all; any permission check is a wrapper the integrator builds
themselves ([Arcjet's survey](https://arcjet.com/learn/runtime-security-langchain-autogpt-crewai)).

This is the sharpest, most concrete differentiator found: these are
**application-level** gates — Python code living in the same trust
domain as the agent's own generated code. A sufficiently capable or
simply confused agent with access to an arbitrary code-execution tool
can route around an application-level check the way any other piece of
its own logic can be routed around, because the check and the agent's
code run as peers, not as a boundary the agent's generated code sits
below. IDHN's enforcement (Docker mount table, network namespace,
kernel-level `EROFS`) sits *underneath* whatever the agent executes —
demonstrated directly in this project by every symlink-write/TOCTOU
bypass attempt that failed at the kernel level regardless of which
tool or language was used to attempt it (`docs/
dogfood-t5-in-container-result.md`: `ln -s`, Python's
`pathlib.Path.symlink_to`, and shell path traversal all failed
identically).

## OS-level isolation technologies (gVisor, seccomp/AppArmor, microVMs)

Surveyed for completeness, not as competitors — these are a
**different, complementary layer**. Consensus from current comparisons
([example](https://adamtheautomator.com/containers-vs-gvisor-vs-microvms-azure-ai-agent/)):
plain containers with seccomp/AppArmor are considered the weakest
option for genuinely untrusted code (shared host kernel — a kernel
exploit compromises everything); gVisor interposes a user-space kernel
(costs I/O performance, some compatibility gaps); microVMs (Firecracker,
Kata) give the strongest boundary via real hardware virtualization, at
higher resource cost. None of these address *what gets granted*, only
*how strongly a boundary is enforced once something is granted* — IDHN
currently runs on plain Docker containers (not gVisor or microVMs), so
adopting one of these as IDHN's container runtime would be a real,
available hardening option, orthogonal to everything IDHN's compiler/
policy-engine work does. Worth flagging as a legitimate avenue for a
"Week 2, continued" hardening pass, not something this project has
evaluated.

## Academic prior art (2026, active research area)

Four close papers found via search: IntentCap's full paper was read
(the closest match); Dynamic Capability Scoping's abstract was read
directly; the other two only via secondary summaries — flagged where
that matters.

- **IntentCap** ([arXiv 2609.14631](https://arxiv.org/abs/2609.14631),
  [full HTML](https://arxiv.org/html/2609.14631v1), Zheng/Zhang/Mao,
  Sep 2026, accepted at AgenticOS 2026, 3 pages) — the full paper was
  read, not just the abstract, since it's the closest match found.
  Argues agent capabilities should be scoped to current task intent,
  not sandbox/session lifetime — composes capabilities from user
  intent, workflow instructions, tool schemas, and runtime environment;
  an LLM generates short-lived leases, validated by a deterministic
  checker before any side effect commits, which only narrows the
  user's authorized authority, never widens it. **Structurally close
  to IDHN's own compiler (LLM) + policy engine (deterministic check) +
  fixed infrastructure floor (never widened) split.**

  **Enforcement does go below the agent process**, same as IDHN, just
  via a different mechanism: "boundary adapters intercept MCP calls"
  at the tool/API level, and accepted leases additionally compile to
  "file, exec, and network policies enforced via eBPF" — a real
  OS-level enforcement layer, not merely an application-level check.
  This means IDHN's "enforcement sits below the agent" property is
  **not unique to IDHN** — IntentCap shares the same design principle,
  via eBPF rather than Docker's mount table/network namespaces/kernel
  `EROFS`.

  **Where IDHN's evidence differs concretely:** IntentCap's own
  evaluation is explicitly described as "preliminary" and is
  **replay-based** — about 3,746 security-sensitive events replayed
  from existing benchmark datasets (AgentDojo, MCPTox, InjecAgent,
  tau2-bench), 38 checks across five enforcement points, 24
  hand-labeled leases — not live, end-to-end runs of a real agent
  against a real codebase. The authors explicitly list **"end-to-end
  attack success, enforcement latency, and adaptive attacks" as future
  work** — i.e., they have not yet done what this project's T5d/T6/
  read-symlink work and Week 4 benchmark did: find a real bypass by
  actually attacking the live system, fix it, and measure real
  overhead on real runs. That specific gap — live adversarial
  validation and measured runtime cost on a real deployed agent,
  versus a preliminary replay evaluation — is the honest, precise
  difference, not "enforces below the agent," which both systems do.
- **Dynamic Capability Scoping** ([arXiv 2607.22445](https://arxiv.org/abs/2607.22445),
  Noyan, Jul 2026). Abstract confirmed directly. "A credential that
  does not exist in an agent's context cannot be misused" — the same
  default-deny philosophy as IDHN. Evaluated against a **released
  synthetic dataset** (600 prompts, 15-permission taxonomy) and a
  three-source architecture (role-based ceilings, task-context
  classifier, policy-derived combination prohibitions), reporting a
  reduction in ceiling violations (46 to 3) through iterating the
  dataset and policy together. **This is a methodologically different
  kind of evidence than IDHN's**: a classification/violation-rate
  metric on synthetic, generated prompts, not real adversarial
  bypasses found by actually attacking a real deployed system running
  a real commercial agent against real external code.
- **Task-Conditioned Least-Privilege Learning** (arXiv 2608.18351,
  secondary summary only, not independently verified) — reportedly
  trains a 4B-parameter model via post-training to select task-specific
  authority for terminal/MCP agents, reporting 98.48% "safe success"
  vs. 64.36% for a base policy. A fundamentally different technical
  approach (a trained selector model) than IDHN's (a prompted LLM
  compiler plus deterministic budget/ceiling checks, no model
  training).
- **PCAS** (Palumbo et al., 2026) — described secondhand as "a policy
  compiler... based on a policy specification" using Datalog. Closest
  name-match to "capability compiler," but I could not locate or read
  its primary source; treat this entry as unverified.

## What's actually defensible as IDHN's contribution

Not "inventing task-scoped least privilege for agents" — that ship has
sailed, with multiple 2026 papers pursuing closely related ideas. What
this project has that the closest prior art (as researched, with the
stated gaps above) does not clearly have:

1. **Validated with live, end-to-end runs of a real commercial agent**
   (Codex) against real external code, not a replay of existing
   benchmark datasets (IntentCap's own stated evaluation method) or a
   synthetic generated dataset (Dynamic Capability Scoping's). The
   underlying enforcement principle (push the boundary below the
   agent's own process, via eBPF or a container's kernel-level
   mounts/namespaces) is shared with IntentCap, not unique to IDHN.
2. **Adversarial bypasses found by actually attacking the deployed
   system**, not by measuring a classifier's accuracy: T5d (a real
   symlink redirected a write to an unauthorized file, confirmed with
   a marker string), the read-side deny-mask bypass (`.env` content
   returned under a different name), and T6 (a real race won in under
   1ms against a real `docker compose up` call) — each reproduced,
   fixed, and regression-tested, not hypothesized.
3. **Measured practical cost**, not just a safety metric: Week 4's
   protected-vs-unprotected comparison (`benchmark/results/
   v0.2.1-overhead-summary.md`) reports task success parity (20/24 vs.
   23/24) and a specific, measured runtime overhead (+14.2% mean) —
   the kind of number a team actually deciding whether to adopt
   something needs, which none of the sources surveyed above report in
   comparable form.
4. **An honestly-scoped threat model**, stated rather than implied —
   `docs/security-boundary-and-limitations.md` names what's out of
   scope (a host-level attacker, provider-side out-of-band tool calls)
   as explicitly as what's in scope, which is not a given in this
   space (the Codex CLI Docker-socket finding above is an example of a
   vendor choosing not to state or close a gap).

## Answering the roadmap's central question

**"Why should someone use IDHN rather than simply running an AI agent
inside Docker?"** Plain Docker gives you a filesystem/network/process
boundary if you configure it carefully — but "configure it carefully"
in practice means either wide-open (most real usage, per the
framework-level audit above) or one static policy reused across every
task (every commercial sandbox surveyed above, and IntentCap's design
principle notwithstanding, its own evaluation hasn't yet been run this
way either), because nothing *validated end-to-end against live runs*
generates and enforces a minimal, task-specific policy automatically.
IDHN's compiler does that generation (96% task-semantic recall,
independently measured across 24 real tasks), its enforcement sits
below the agent's own execution layer rather than beside it (a
property shared with IntentCap's eBPF-based design, not unique to
IDHN, but demonstrated here specifically by every bypass attempt in
this project failing at the kernel level regardless of technique), and
both the safety and the cost of that are measured on live runs, not
assumed or only replayed. That is a genuine, demonstrated difference
from plain Docker and from the closest academic prior art's current
evaluation state — not a claim that the underlying idea of task-scoped
agent permissions, or even enforcement below the agent process, is new
to IDHN.

## What this research did not establish (be honest about the gap)

IntentCap's full paper was read directly, resolving the main open
question from the first pass of this research (its enforcement does go
below the agent, via eBPF). What remains unverified: **PCAS**'s primary
source was never located (only a secondhand description), so its
actual architecture and evaluation are unconfirmed — if a precise
"capability compiler" prior-art comparison is needed for the research
report, finding and reading PCAS directly is the highest remaining
value next step. Also unverified: **Task-Conditioned Least-Privilege
Learning**'s reported 98.48%/64.36% figures come from a secondary
summary, not the primary paper — worth confirming before citing those
specific numbers in anything meant for external review.
