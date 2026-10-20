# Security Policy

IDHN is a security tool, currently published as an **experimental
research prototype**, not a production-hardened product. Read
`docs/security-boundary-and-limitations.md` before relying on it for
anything real -- it states plainly what's actually enforced, what's
explicitly out of scope by design (not an oversight), and what's a
known, already-documented gap.

## Reporting a vulnerability

**Please do not open a public GitHub issue for a security
vulnerability.** Use GitHub's private vulnerability reporting instead
(this repository's "Security" tab → "Report a vulnerability"), which
reaches the maintainer directly without disclosing details publicly
before a fix exists.

Include, if you can:

- The exact `idhn` command (or script) that reproduces it.
- Whether it's a bypass of something `docs/
  security-boundary-and-limitations.md` claims is enforced (a real
  finding), or something that document already states is out of scope
  (expected, not a vulnerability in IDHN's own terms -- but still worth
  reporting if the scope statement itself seems wrong).
- A concrete reproduction over a description where possible -- every
  real vulnerability found in this project's own history
  (`docs/symlink-write-bypass-fix.md`, `docs/
  read-symlink-bypass-fix.md`, `docs/toctou-investigation-t6.md`) was
  confirmed by actually reproducing it against a running system before
  being fixed, not accepted on description alone.

## What's already been found and fixed

Documented, not hidden, because a security tool's own history of
finding and fixing real bugs in itself is part of its credibility, not
something to minimize:

- **T5d**: a pre-existing symlink at a write-granted path redirected
  writes to an unauthorized file.
- **Read-side symlink bypass**: the same mechanism on a read-granted
  path, bypassing the `.env` deny mask specifically.
- **T6 (TOCTOU)**: a race between the symlink check above and the
  actual Docker mount, confirmed exploitable against a real mount in
  under 1 millisecond before being closed with a per-workspace lock.

## What's explicitly out of scope (not a vulnerability report)

- A host-level attacker with independent code execution outside any
  IDHN-managed container. No container-level check can defend against
  this by definition.
- A capability a coding agent's model provider executes on its own
  backend rather than as a process inside the container (e.g. Codex's
  `web_search` before it was disabled by default, or similar
  provider-side tools). IDHN's boundary is the container and network;
  it cannot see or govern what happens on a provider's own
  infrastructure.

## Still-open findings (not fixed yet, stated plainly)

- **Some Codex provider-side capabilities are undisabled.** `codex
  features list` surfaces ~140 feature flags; `web_search`, `apps`,
  `browser_use` (and variants), and `computer_use` are confirmed
  enabled-by-default and are now disabled in this project's own Codex
  invocation. Several others whose names suggest possible out-of-band
  execution (`in_app_browser`, `in_app_local_automation`,
  `plugins`/`plugin_sharing`/`remote_plugin`, `multi_agent`) have not
  been investigated and are **not** disabled — their actual
  in-container-vs-provider-side behavior is unconfirmed.
- **`idhn.core.policy._matches_any` crashes on a non-string manifest
  pattern** (e.g. `deny: [null]`) instead of cleanly rejecting it.
  Availability issue, not a privilege escalation.
- **A compiled `secrets` capability proposal is silently dropped**
  rather than erroring. Fails closed (nothing is granted), but silently.

Full account, including what's audited-but-not-yet-prevented and the
T6 TOCTOU mitigation's own precise threat model: `docs/
security-boundary-and-limitations.md`.

## Supported versions

This project does not yet have a formal release/support cadence.
Report against the latest commit on the default branch unless you have
a specific reason to believe an older tagged release is affected
differently.
