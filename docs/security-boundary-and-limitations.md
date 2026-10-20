# IDHN security boundary and known limitations

Consolidates what IDHN actually enforces, what it explicitly does not
and cannot, and every known gap found during this project's security
work — in one place, for external review. Everything here cites the
specific finding or doc it comes from; none of this is new reasoning,
only the consolidation is new (the Week 5 roadmap deliverable:
"document the security boundary and known limitations").

## What's actually enforced (mechanically, not by convention)

**Filesystem.** `enforcement/filesystem.py`: the workspace base is an
empty, read-only tmpfs — default-deny, not "everything except what's
masked." Every `read`/`write` grant is its own bind mount layered on
top; `deny` masks shadow both. A path covered by neither simply doesn't
exist in the container (`ENOENT`), not merely unlisted. Verified
directly against a real container, not assumed (`docs/
demo-security-boundary.md`: an unauthorized write to `package.json`
failed with `Read-only file system` at the kernel level).

Two bypasses were found and fixed by directly testing the mount logic,
not by design review:
- **T5d** (`docs/symlink-write-bypass-fix.md`): a pre-existing symlink
  at a write-granted path redirected writes to an unauthorized file.
  Fixed — any writable mount whose path (or a path component) is a
  symlink is now rejected before any container starts.
- **Read-granted symlink / deny-mask bypass** (`docs/
  read-symlink-bypass-fix.md`): the same mechanism, but worse — a
  symlink at a read-granted path returned `.env`'s real content,
  bypassing the deny mask entirely. Same fix, extended to read grants
  (deny masks themselves correctly exempted, since they substitute
  fixed content regardless of host state).
- **T6 TOCTOU** (`docs/toctou-investigation-t6.md`): the symlink check
  above runs in Python before `docker compose up` actually mounts
  anything — a real, demonstrated race window (won in <1ms inside an
  8+ second gap). Closed with a per-workspace exclusive lock held for
  a run's entire duration, so two runs against the same workspace can
  never be alive at the same time.

**Network.** `docker/compose.yaml`'s `internal` network has no
NAT/masquerade route out at all — not a proxy-env-var convention, a
Docker network property. The agent only joins `internal`; the gateway
is the one container with an actual route out, and it checks every
connection against the manifest before allowing it. Verified directly
(`docs/network-isolation-verification.md`): direct DNS resolution
fails, a raw TCP connect to a hardcoded IP fails (`Network is
unreachable`, ruling out "just DNS"), a proxy-ignoring HTTPS client
fails the same way, and proxy-aware traffic is correctly allowed or
denied per the manifest.

**Process.** `enforcement/process.py`: a PATH-restricted binary
allowlist, scoped to the agent's own invocation. A bare command name
not in `process.allow` resolves to nothing (`command not found`).
Deliberately not described as a complete process-confinement boundary
(see "What's NOT enforced" below) — it's the first line, not the whole
wall; filesystem and network hold regardless of what process is doing
the asking.

**Concurrency.** `idhn.runtime.workspace_lock`: one exclusive lock per
resolved workspace path, held for a run's entire lifetime, closing the
one concurrency-based attack vector this project found (see T6 above).

## What's explicitly out of scope — not gaps, boundaries

**A host-level attacker with independent code execution outside any
IDHN-managed container.** No mount-time check, lock, or gateway policy
defends against this by definition — IDHN's job is containing an agent
running *inside* its own managed container, not securing the host
itself. Stated explicitly in `docs/toctou-investigation-t6.md`'s threat
model rather than left implicit, because it would be easy to
over-claim the lock as general protection it isn't.

**Provider-side ("out-of-band") capabilities.** Codex's native
`web_search` tool executed on OpenAI's own backend, not as a process in
the container — no amount of Docker network isolation or gateway
policy can see or govern it, the same way a firewall around a
contractor's laptop doesn't stop them from looking something up
themselves (`docs/web-search-out-of-band-capability.md`). Fixed for
`web_search`, and — a follow-up investigation found via `codex features
list` that they were enabled by default in the same way — also for
`apps`, `browser_use`, `browser_use_external`,
`browser_use_full_cdp_access`, and `computer_use` (disabled by default
in `idhn.adapters.codex.CodexAdapter.command()`, confirmed via
`--strict-config` and a real completed task run, not guessed).

**Still open:** several other Codex feature flags whose names suggest
possible out-of-band execution (`in_app_browser`,
`in_app_local_automation`, `plugins`/`plugin_sharing`/`remote_plugin`,
`multi_agent`) were seen in the same `codex features list` output
(~140 total flags) but their actual behavior — in-container vs.
provider-side — was not investigated, and they are **not** disabled by
default. Left open deliberately rather than guessed at: most of the
~140 flags are core functionality Codex needs to run at all
(`shell_tool`, `unified_exec`), so disabling broadly without confirming
each one's behavior risks breaking Codex outright for an unconfirmed
security benefit. `idhn.audit.codex_events.out_of_band_items` still
flags any of these (or anything else) as an unrecognized item type if
one is ever actually exercised in a real run — detection, not
prevention, for this specific set.

## Known gaps in what can currently be audited (enforcement unaffected)

**Filesystem/process denials are visible only in the agent's own
command trace**, not in an independent, live log the way network
decisions are (the gateway checks every connection *before* allowing
it; nothing analogous exists for filesystem/process, which are
re-checked against the policy *after the fact* from the agent's own
reported exit codes). If an agent hid or misreported a command's real
output, these blocks wouldn't show up in the audit trail — the
enforcement itself would still hold (the kernel doesn't care what an
agent claims happened), but *proving* it from the audit trail alone
currently depends on trusting that trace. First identified in T1
(`docs/dogfood-t1-rag-safe-basename.md`), restated in the Week 5 demo
(`docs/demo-security-boundary.md`) since it applies there too.

**No general read-level instrumentation.** v0.1 has no audit signal at
all for an *ordinary, allowed* read — only deny-masked reads are
specifically detected (`idhn.audit.m3_report.deny_masked_read_attempts`,
a narrow, bounded heuristic matching a manifest's own small deny list,
not a general "did the agent read this" detector).

**The `_process_overreach` audit classifier parses command text, not
live syscalls, and can misclassify.** Confirmed by Week 4's benchmark
comparison (`benchmark/results/v0.2.1-overhead-summary.md`): a bash
`for` keyword and a bare `ls` argument were both misidentified as
process-exec targets never granted — investigated and confirmed as
parser false positives, not real bypasses (neither `for` nor a `ls`
argument is ever resolved via the real PATH-allowlist mechanism this
project verified directly elsewhere). The risk this creates is
asymmetric: a false positive in the "blocked" (good) count just
overstates safety slightly; a false positive in the "residual gap"
(bad) count would understate a real failure, which is why every
residual-gap finding in this project has been individually
investigated before being reported, not aggregated and trusted blind.

## Known, currently unfixed findings (narrow scope, flagged not silenced)

**`idhn.core.policy._matches_any` crashes on a non-string pattern.**
`fnmatch(value, pattern)` is called with no type check on `pattern`; a
manifest containing e.g. `deny: [null]` crashes `PolicyEngine.evaluate()`
with an unhandled `TypeError` instead of a clean rejection. Found during
an earlier task-selection pass in this project, confirmed by direct
reproduction, but never assigned or fixed — noted here so it isn't
lost, not because it's been addressed.

**`secrets` capability proposals are silently dropped, not rejected.**
`idhn.audit.authority_sets.to_manifest_capabilities`'s bucket-selection
has no branch for `resource == "secrets"`, so a compiled decision
naming it falls through and vanishes with no error.
`secrets.default: deny` stays fixed regardless (no widening), but a
compiler that genuinely proposed a secrets grant would have it
disappear without any error — pre-existing, unrelated to any specific
fix, documented in `docs/compile-materialize-run.md` and flagged there
for whoever next touches secrets capability support.

**Tool-substrate compiler recall and two known task misses.** The
compiler's task-semantic recall is strong (96%, `benchmark/results/
v0.2-b-summary.md`); two of 24 corpus tasks still fail for reasons
unrelated to enforcement — T19 (a task-decomposition miss: the compiler
under-predicted what the task actually needed) and T24 (a container-
image/PEP-668 substrate limitation, not an authority problem). Neither
is new; both are restated here because an external reviewer judging
"does this actually work" should see the two real failures alongside
the fixes, not just the fixes.

## Summary

Three real security bypasses were found by directly testing the
mechanisms (not assumed safe by design), reproduced with a concrete
artifact in each case, fixed, and regression-tested. The boundaries
that remain are stated explicitly rather than implied: a host-level
attacker and provider-side out-of-band tool execution are out of
IDHN's model by definition, not gaps in it; the audit trail for
filesystem/process denials depends on trusting the agent's own trace,
which the enforcement itself does not; and two small, narrow,
already-known issues (a policy-engine crash on malformed input, a
silently-dropped secrets capability) remain unfixed and are named here
rather than left for someone else to rediscover.
