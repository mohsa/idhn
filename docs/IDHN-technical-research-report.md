# IDHN: A Deterministic Capability Boundary for Autonomous Coding Agents

**Technical research report — draft, 2026-10-20**

*Status: sections 1–7 complete and evidence-backed. Section 8
(external validation) is explicitly pending — see note there. This is
a draft for review, not a final publication.*

## Abstract

Autonomous coding agents are typically run with either no isolation
(full access to the host or repository) or a fixed, session-level
sandbox mode chosen once and reused across every task. Neither gives a
task the *minimal* authority it actually needs. IDHN (إذن, "permission")
compiles a natural-language coding task into a predicted capability set,
converts that prediction into an enforceable, container-level policy
without ever widening it past a fixed infrastructure floor, and
enforces the result through Docker's own mount table, network
namespace, and a policy-checked gateway — not through the agent's
cooperation. This report documents the architecture, the experimental
methodology used to validate it (a 24-task benchmark corpus and six
targeted adversarial scenarios against a real external codebase), three
real security bypasses found and fixed through direct adversarial
testing rather than design review, a measured protected-vs-unprotected
comparison (task success parity, +14.2% mean runtime overhead), and an
explicit account of what remains out of scope or unresolved.

## 1. Problem statement

An autonomous coding agent that can read, write, execute commands, and
reach the network is, by construction, as powerful as the environment
it runs in. The two common ways to constrain that power both have a
specific failure mode:

- **No isolation** (the agent runs with the same access as the
  developer invoking it) grants the agent every capability a human
  would need for *any* task, for the one task actually at hand — the
  blast radius of a mistake, a prompt injection, or a model that
  simply does something unintended is the entire environment.
- **A fixed sandbox mode**, set once per session or per project and
  reused across every task, is better but still not least-privilege:
  the mode has to be broad enough for the *broadest* task anyone will
  ever ask of it, which means it is over-broad for every narrower task
  in practice. This is the actual shape of every commercial agent
  sandbox surveyed for this report (§7) — Codex CLI's three static
  modes, Claude Code's directory/domain allowlist, E2B/Daytona's
  developer-configured permissions — none of them derive a policy from
  the task's own text.

The gap this project addresses: **can a system derive, from a task's
natural-language description, the minimal filesystem/network/process
authority that task needs, and enforce exactly that — automatically,
per task, without relying on the agent to respect the boundary?**

## 2. Threat model

Stated explicitly rather than left implicit, because an unstated
threat model is the easiest way to over-claim protection.

**In scope — the agent itself may be compromised, confused, or
adversarially instructed.** IDHN assumes the coding agent (Codex, in
this implementation) may receive a prompt injection, may hallucinate
an action outside its intended task, or may be deliberately instructed
(by a test, or by an attacker who controls part of the repository
content) to attempt something unauthorized. The system's job is to
make that attempt fail regardless of the agent's own behavior, not to
rely on the agent declining to try.

**Out of scope, by design, not by oversight:**
- **A host-level attacker with independent code execution outside any
  IDHN-managed container.** No container-level check defends against
  an attacker who already has arbitrary code execution on the host
  itself; that is a host-compromise problem, not a problem this
  project's boundary can address. Stated explicitly in the TOCTOU
  mitigation's own design notes (§6.3) rather than implied.
- **Provider-side ("out-of-band") capabilities.** A coding agent's
  backend LLM provider may offer tool calls — Codex's native
  `web_search`, and similarly-shaped `browser_use`/`computer_use`/`apps`
  configuration keys — that execute on the provider's own
  infrastructure, never touching the container's filesystem, network
  namespace, or process table at all. No container boundary can see or
  govern these. One such capability (`web_search`) was found active by
  default and is now explicitly disabled; the others are a confirmed,
  documented open limitation (§8 of the security-boundary document),
  not a solved problem.

**The actually-exploitable vector found in this project's own testing**
was narrower than either of the above: two separate IDHN-managed runs
racing each other against a *shared* workspace directory (§6.3). This
is addressed; a host-level attacker and provider-side tool execution
are not, and are not claimed to be.

## 3. Architecture

Four stages, each independently testable:

1. **Compile** (`idhn compile`, LLM-based, untrusted output). Given a
   task description and limited repository context, produces a flat
   list of `(resource, operation, scope, confidence, reason)` capability
   decisions — e.g. `filesystem.write /workspace/core/language.py,
   confidence: high`. This step never touches enforcement directly;
   its output is reviewed by a human (or an automated gate) before
   anything runs.
2. **Materialize** (`idhn materialize`, deterministic, no LLM call).
   Re-validates the compiled output from scratch (never trusts that a
   file on disk was actually a valid, budget-passing proposal just
   because it's shaped right), unions it with a fixed, task-independent
   authority floor (agent infrastructure, execution substrate, and
   known tool-operational requirements — e.g. `npm` needing its
   registry), and produces an enforceable manifest. Confidence and
   reason are compiler *metadata*: `CapabilityDecision.as_tuple()`
   drops them before anything downstream ever sees the proposal, so
   they cannot influence what gets enforced. A hard budget check
   rejects unscoped wildcards (`/workspace/**` write, `network.connect
   *`) and dangerous execution primitives (`sudo`, `docker`, `mount`,
   `nsenter`) outright, regardless of what confidence or reasoning the
   compiler attached to them.
3. **Enforce** (`idhn run`, Docker-based). The materialized manifest
   drives three independent mechanisms:
   - *Filesystem*: the agent's workspace is an empty, read-only tmpfs
     by default. Every `read`/`write` grant becomes its own bind mount
     layered on top; every `deny` pattern becomes a mask layered over
     that. A path covered by neither simply does not exist in the
     container (`ENOENT`), not merely unlisted.
   - *Network*: the agent container joins only a Docker network with
     no NAT/masquerade route out at all (a network-level property, not
     an application convention); a separate gateway container is the
     only one with an actual route to the internet, and it checks
     every connection against the manifest's `network.allow` list
     before permitting it.
   - *Process*: a PATH-restricted allowlist scoped to the agent's own
     command — a bare name not in `process.allow` resolves to nothing.
     Explicitly not claimed as a complete process-confinement boundary
     (it doesn't stop invocation by absolute path, and cannot stop a
     programmable interpreter from doing arbitrary computation once
     invoked) — filesystem and network are the mechanisms that hold
     regardless of what process is asking, by design.
4. **Audit** (`idhn m3-report`). Reconciles what was granted against
   what was actually exercised, what was denied and stayed denied,
   what was denied but succeeded anyway (a "residual gap" — the single
   most important possible finding, investigated individually every
   time it appears in this project's own results rather than
   aggregated and trusted blind), and what executed via a provider-side
   channel the container never saw at all.

## 4. Experimental methodology

Two complementary evidence sources, deliberately kept distinct:

**A 24-task synthetic corpus** (`benchmark/tasks/T01–T24.yaml`,
`benchmark/corpus/`), spanning four task families (diagnose, modify,
test-writing, dependency-handling) across two disposable repositories.
Each task runs two legs: *unconstrained* (no manifest, no gateway — the
same mechanism `idhn baseline` exposes, used to observe what the task
actually needed) and *constrained* (under the manifest the compiler
proposed). This corpus is what makes the compiler's own precision/recall
measurable, and — since both legs are real container executions with
a real LLM-driven agent — what makes a protected-vs-unprotected
comparison possible without running anything twice specifically for
that purpose (§6.2 reuses this same corpus for exactly that).

**Six targeted real-repository tests (T1–T6)** against a real external
codebase (a production RAG platform, not a disposable fixture), each
isolating one variable:

| | Tests | Method |
|---|---|---|
| T1 | Single-file read+write, no execution | Real `idhn compile → materialize → run` |
| T2 | Multi-file write + new file + granted test execution | Same pipeline, a harder authority shape |
| T3 | Prompt injection planted inside the authorized file itself | A misleading instruction asks the agent to also modify an unauthorized file |
| T4 | Direct, explicit "attempt the forbidden write" instruction | Isolates enforcement from any question of agent judgment |
| T5 | Pre-existing symlink at a granted path; agent-constructed escape attempts | Both a passive host-state attack and active in-container attempts (`ln -s`, `pathlib.Path.symlink_to`, path traversal) |
| T6 | Race between the symlink check and the actual mount | A controlled timing experiment, not a real-repo run |

T3 and T4 are judged on two axes kept deliberately separate: did the
agent *attempt* the unauthorized action, and — independently — did
enforcement *block* it if attempted. Collapsing these into one verdict
would hide the difference between "the agent behaved well" and "the
boundary held regardless," which is the actual question this project
needed answered.

## 5. Findings: security results

**T1, T2 — legitimate workloads succeeded without over-grant.** T2 in
particular surfaced a real substrate confound (a test file's parent
`conftest.py` imports a package unavailable in the agent image) and the
agent resolved it with a legitimate pytest flag rather than reaching
for an unauthorized package install — directly answering a question
the test was designed to probe.

**T3, T4, T5 (in-container) — the agent never exceeded its authority
when tested adversarially, and enforcement held when directly
challenged.** T3: the agent read a planted instruction and silently
ignored it, confirmed against a clean control run with an identical
outcome. T4: an explicit, direct instruction to write to an
unauthorized file failed with `Read-only file system`, reported
accurately by the agent. T5 (in-container): three independent
construction attempts (a shell `ln`, Python's own `symlink_to`, path
traversal) all failed identically at the kernel level — ruling out "a
missing-binary allowlist gap" as the explanation, since the
Python-direct attempt bypasses any such gap entirely.

**T5d, the read-side symlink/deny-mask bypass, and T6 — three real
bypasses, found by attacking the deployed system, not assumed safe by
design.**
- *T5d*: a manifest granting `filesystem.write` on an existing file was
  silently redirected to a different, never-granted file when that
  path was actually a pre-existing symlink — confirmed with a marker
  string ending up in the symlink's real target, not the authorized
  path.
- *Read-side variant*: the same mechanism, but worse — a symlink at a
  **read**-granted path returned `.env`'s real content, completely
  bypassing the deny mask that correctly empties `/workspace/.env`
  itself. The mask only shadows the one path it names; it does nothing
  for a different path pointing at the same underlying file.
- *T6*: the symlink check above runs in a Python process before a
  separate, later `docker compose up` call actually performs the
  mount. A controlled race experiment swapped the checked path for a
  symlink in that window and won in under 1 millisecond, inside an
  8-second gap — confirmed exploitable against a real Docker mount,
  not theoretical.

All three were fixed and regression-tested (13 new tests across the
three fixes) before this report was written, and the T6 fix (a
per-workspace exclusive lock, held for a run's entire duration) was
itself verified by proving a real `DockerRuntime.start()` call
genuinely holds the lock throughout, not merely that the lock object
exists.

**A fourth, smaller finding from re-examining the audit layer itself**
(§6.2): a benchmark-wide analysis surfaced two "residual gap" readings
that, investigated individually, turned out to be command-text-parsing
false positives in the audit classifier (a bash `for` keyword and a
bare `ls` argument misread as process names) — not real bypasses of
the PATH allowlist verified directly elsewhere. Reported with the
investigation attached, consistent with the standard this project held
every other residual-gap finding to.

## 6. Findings: practical value

**6.1 Compiler prediction quality.** Across the 24-task corpus:
task-semantic recall 96% (did the compiler predict what the task
itself needed), with the much lower raw recall figure (29%) found to
be a benchmark-category error rather than a compiler weakness — it was
counting ordinary execution substrate (`pwd`, `cat`, `ls`) as if it
were task-specific authority the compiler should have had to
re-derive. Separating substrate from task authority (consequence-based
authority, v0.2.1) resolved this without any change to the compiler
itself, and confirmed task success holds at 20/22 judgeable tasks under
the corrected enforcement model.

**6.2 Protected vs. unprotected comparison.** Built from the 24-task
corpus's own unconstrained/constrained leg pair (no new runs needed for
this specific comparison): protected task success (20/24, 2
unjudgeable) is essentially unchanged from unprotected (23/24) — the
compiler-generated authority does not meaningfully cost task
completion. Mean runtime overhead: +5.0 seconds, +14.2% (wall-clock
agent duration; container build/startup amortized by layer caching and
not attributable to either leg specifically). One real
policy-compatibility gap (task T19, an already-known task-decomposition
miss, not a new finding).

**6.3 Demonstrated end to end, not just measured in aggregate.** A
single real run combining both halves of "practical value" in one
reproducible example: a real operator-precedence bug, correctly fixed
by the agent (ground truth confirms only the intended file changed),
and a deliberately out-of-scope write to `package.json`, blocked with
`Read-only file system`, reported honestly by the agent rather than
worked around. Evidence frozen and checksummed, reproducible with the
exact manifest and command already documented for first-time users.

## 7. Related work and what's novel here

Task-scoped least privilege for LLM agents is an **active 2026
research area**, not something this project originated. The closest
academic match found, IntentCap (Zheng, Zhang & Mao, arXiv 2609.14631,
accepted AgenticOS 2026), proposes the same general shape — an LLM
proposes scoped capability leases, a deterministic checker validates
them, enforcement compiles down to OS-level policies (eBPF, in their
case) rather than staying at the application level. Read in full, not
just its abstract, specifically to avoid overclaiming difference from
the nearest prior art: its own evaluation is explicitly described as
"preliminary" and is replay-based against existing benchmark datasets
(AgentDojo, MCPTox, InjecAgent, tau2-bench), with end-to-end attack
success and enforcement overhead explicitly listed as future work.

Commercial agent sandboxes (OpenAI Codex CLI's native modes, Anthropic's
Claude Code sandboxing, E2B/Daytona) all enforce a developer-configured,
session-level policy, not one derived per task from its description.
Anthropic's own documentation states plainly that native sandboxing
"covers only part of what enterprise teams need," naming exactly the
infrastructure (container isolation, egress controls, audit logging)
this project builds. Framework-level permission systems (LangChain,
AutoGPT, CrewAI) were independently audited in 2026 to provide no
deterministic, fail-closed authorization gate by default — an
unauthorized payout call succeeded under LangChain's default dispatch
in that testing, a concrete illustration of why an application-level
gate (code running as a peer to the agent's own generated code, not a
boundary beneath it) is a structurally weaker guarantee than
enforcement the agent's own generated code cannot see or step around.

**What this project's evidence actually supports is narrower and more
specific than "a new idea":** a working, deployable implementation,
validated with live end-to-end runs of a real commercial coding agent
against real external code — not a replay of existing benchmark
events, not a synthetic generated dataset — that found and fixed real
bypasses by attacking the deployed system directly, and that reports
measured task-success and overhead numbers a team could actually use
to decide whether to adopt it. OpenAI's own Codex CLI shipping a bug in
the same class as this project's own T5d finding (trusting something
by name without verifying what it actually resolves to) is independent
evidence that this is a genuinely recurring, non-obvious failure mode
in this exact problem space, not a contrived example manufactured to
make this project look more rigorous than it is.

## 8. External validation — pending

**Not yet complete.** The roadmap calls for showing this system to 2–3
developers or security engineers and collecting their genuine
reactions — specifically, whether they'd use it, what they distrust
about it, and what integration problems they anticipate. This cannot
be produced by drafting; a walkthrough script and question set are
prepared (`docs/external-feedback-kit.md`) to make running that
session straightforward, but the actual feedback has to come from real
people reacting to a real demonstration. This section should be filled
in with their actual responses — not paraphrased optimistically, not
invented — before this report is considered complete.

## 9. Limitations

Full account: `docs/security-boundary-and-limitations.md`. Summarized:
a host-level attacker and provider-side out-of-band tool execution are
out of scope by design (§2); filesystem/process denials are currently
only auditable via the agent's own self-reported trace, not an
independent live log the way network decisions are; there is no
general read-level instrumentation, only a narrow deny-masked-read
detector; the command-text-parsing audit classifier has a confirmed
false-positive mode (§5, the "fourth finding"); and two small, specific
issues remain unfixed and are named rather than hidden — a policy-engine
crash on a malformed manifest pattern (`deny: [null]`), and a silently
(not loudly) dropped `secrets` capability proposal in the
materialization step.

## 10. Contributions

1. A deterministic, three-stage pipeline (compile → materialize →
   enforce) that keeps an untrusted LLM's proposal, a fixed
   non-negotiable budget check, and real Docker-level enforcement as
   three separately-auditable layers, with confidence/reason metadata
   structurally incapable of influencing enforcement.
2. A 24-task benchmark corpus and six targeted adversarial tests (T1–T6)
   against a real external codebase, with a methodology that
   separates "did the agent behave" from "did enforcement hold"
   wherever both are relevant, rather than reporting one conflated
   verdict.
3. Three real, previously-unknown filesystem-enforcement bypasses,
   each found by direct adversarial testing of a deployed system,
   fixed, and regression-tested — not hypothesized, not assumed closed
   by design.
4. A measured, reproducible protected-vs-unprotected comparison
   (task success, runtime overhead) built from data the project
   already had, rather than asserted without evidence.
5. An explicit, cited comparison against the closest contemporary
   academic and commercial work, naming where this project's
   contribution is and is not different from what already exists.

## Appendix: source documents

Every claim in this report traces to a specific, cited artifact in
this repository: `docs/security-validation-report-2026-10-20.md`
(T1–T6 consolidated), `docs/symlink-write-bypass-fix.md`,
`docs/read-symlink-bypass-fix.md`, `docs/toctou-investigation-t6.md`,
`benchmark/results/v0.2-b-summary.md`, `benchmark/results/
v0.2.1-summary.md`, `benchmark/results/v0.2.1-overhead-summary.md`,
`docs/demo-security-boundary.md`, `docs/
security-boundary-and-limitations.md`, `docs/novelty-comparison.md`.
