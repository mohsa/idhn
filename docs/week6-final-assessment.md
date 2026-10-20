# Week 6 final assessment

Reviews all evidence gathered this week (and the filesystem-hardening
work it builds on), prioritizes what's actually unresolved, and lays
out the evidence for a publish/pilot/continue-hardening decision. The
decision itself belongs to the person deciding whether to spend
reputation and time on this, not to this document — what follows is
the case, not the verdict.

## Checklist against the roadmap's own Week 6 items

- [x] Novelty comparison with existing solutions completed —
  `docs/novelty-comparison.md`.
- [x] T1–T6 security results documented —
  `docs/security-validation-report-2026-10-20.md`, plus individual
  writeups for every fix (T5d, the read-symlink bypass, T6).
- [x] Threat model and known limitations written —
  `docs/security-boundary-and-limitations.md`.
- [x] Benchmark results consolidated —
  `benchmark/results/v0.2.1-overhead-summary.md`, plus the prior
  `v0.2-b-summary.md`/`v0.2.1-summary.md` it builds on.
- [ ] **Feedback collected from 2–3 external reviewers — not done.**
  `docs/external-feedback-kit.md` is prepared; the actual session
  hasn't happened. This is the one item that structurally requires a
  person other than the one running this assessment.
- [x] Technical paper or research report drafted —
  `docs/IDHN-technical-research-report.md` (its own §8 marks external
  validation pending, consistent with the line above).
- [x] Open-source preview or pilot readiness assessed — this document.

**One real item incomplete, and it's named rather than worked around.**
Everything else on the list has real, cited evidence behind it.

## Prioritized unresolved risks

Ranked by a combination of exploitability and how much it would matter
if exploited — not alphabetically, not by discovery order.

**1. Provider-side out-of-band tool execution beyond `web_search`
(open).** `browser_use`, `computer_use`, and `apps` are confirmed to
exist as real Codex configuration keys of the same shape as
`web_search`, but their disable syntax was not determined. If any of
these is active by default the way `web_search` was, it's a complete
bypass of every container-level control this project built — not a
partial one. **Highest priority to close before any claim of
production-readiness**, because unlike the filesystem bugs below, this
isn't "a boundary with a crack in it," it's "a door that might not
exist at all for this specific capability."

**2. Filesystem/process audit evidence depends on the agent's own
trace (known, structural, not a bug).** The *enforcement* doesn't
depend on this — a kernel-level `EROFS` happens regardless of what the
agent reports — but *proving from the audit trail alone* that a block
happened currently requires trusting the agent didn't hide or
misreport a command's output. Network is the one resource with a live,
independent check before the action; filesystem/process are
reconciled after the fact from self-reported exit codes. Not urgent to
fix (would require either live syscall interception or restructuring
how commands are captured), but worth stating as a real limit on what
"the audit trail proves" means today.

**3. The two small, narrow, named-but-unfixed bugs.** A manifest
containing `deny: [null]` crashes the policy engine instead of being
cleanly rejected; a compiled `secrets` capability proposal is silently
dropped rather than erroring. Neither is exploitable in the sense of
granting unintended authority — the first is a crash (availability,
not confidentiality/integrity), the second fails closed (the secret
grant simply never materializes, doesn't materialize as something
broader). Low priority relative to 1 and 2, but cheap to fix and
there's no good reason to carry them into a release candidate
unaddressed.

**4. The symlink/TOCTOU line of fixes (T5d, read-symlink, T6) — closed,
listed here only to state why it's not still a top risk.** All three
were found by direct adversarial testing, fixed, and regression-tested,
with the fix for each verified against a real Docker run, not just a
unit test. The one caveat already stated plainly in that work: the T6
lock defends against two IDHN-managed runs racing each other, not
against a host-level attacker with independent code execution — which
is out of this project's threat model by definition, not an oversight.

## What the evidence actually supports

- **Security**: three real bypasses found by attacking the deployed
  system, all fixed and regression-tested; the filesystem and network
  boundary held under every adversarial test this project ran against
  it (T3, T4, T5-in-container), including techniques specifically
  chosen to rule out shallow explanations (Python-direct symlink
  creation, not just a missing `ln` binary).
- **Practicality**: task success parity between protected and
  unprotected runs (20/24 vs. 23/24), a specific measured overhead
  (+14.2% mean), and one real, already-understood policy-compatibility
  gap (T19) rather than an unexplained one.
- **Positioning**: a working, live-validated system in a research area
  that is active but where the closest academic match's own evaluation
  is explicitly preliminary and replay-based — a real, specific,
  citable difference, not an unsupported novelty claim.
- **What's still missing**: real external reactions (item 5 above, not
  done), and risk #1 above (provider-side tool coverage beyond
  `web_search`), which is the one gap serious enough to actually change
  a release-readiness recommendation if left unaddressed.

## Recommendation

Three options, as the roadmap frames them, with the evidence for each:

**Publish as a research prototype, with risk #1 closed first or stated
as an explicit, prominent limitation.** The security and practicality
evidence is real and reproducible; the novelty positioning is honest
about what's shared with contemporary work and what isn't. This is
consistent with the roadmap's own standing guidance — written before
this week's work closed T6 — that an unresolved serious vulnerability
should mean "experimental prototype only, clearly documented
limitations," not "don't release." T6 is now resolved. Risk #1
(`browser_use`/`computer_use`/`apps`) is the one item in the same
category of severity that isn't yet resolved, so the same logic applies
to it specifically: either determine and apply the disable syntax for
those capabilities before calling this release-candidate-ready, or
publish with that gap stated as prominently as `web_search`'s fix is
documented — not buried in a limitations appendix.

**A private pilot is not yet evidenced one way or the other** — nothing
in this week's work speaks to whether a specific team's workflow fits
IDHN's model (single-agent, single-repo, Docker-based), which is
exactly what the still-pending external-feedback session would surface.
Recommend not deciding pilot-readiness until that session happens;
deciding it now would be filling a gap with assumption rather than
evidence, which this project has specifically avoided doing at every
other decision point this week.

**Continuing to harden before any release** is the conservative option
and is defensible if risk #1 turns out to be a deep rabbit hole (if
Codex's own config surface for `browser_use`/`computer_use`/`apps`
resists the same `--strict-config`-driven investigation that resolved
`web_search`). Given `web_search`'s resolution took a bounded, concrete
investigation (not an open-ended one), this is plausible but unproven
either way — worth timeboxing rather than treating as a blocking
unknown of unknown size.

**This document's own recommendation, given the above:** close risk #1
(or make a clearly time-boxed attempt and document the outcome either
way), run the external-feedback session already prepared for, and then
publish as an explicitly-labeled research prototype — not a production
tool, not a finished product — with `docs/security-boundary-and-
limitations.md` linked prominently, not buried. That sequencing closes
the one open item serious enough to matter, gets the one piece of
evidence (real human reactions) this week couldn't produce on its own,
and matches what the actual evidence supports rather than either
overclaiming readiness or under-crediting three weeks of real,
adversarially-validated hardening work.
