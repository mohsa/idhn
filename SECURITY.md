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

Full account, including what's audited-but-not-yet-prevented and two
small, specific, still-open findings: `docs/
security-boundary-and-limitations.md`.

## Supported versions

This project does not yet have a formal release/support cadence.
Report against the latest commit on the default branch unless you have
a specific reason to believe an older tagged release is affected
differently.
