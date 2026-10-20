# Contributing to IDHN

IDHN is a research prototype, published as an experimental security
tool (see `docs/security-boundary-and-limitations.md` for exactly what
that means in practice). It's currently maintained by one person. This
file sets realistic expectations rather than a formal process that
doesn't exist yet.

## Before you open an issue or PR

Read `README.md`'s Quickstart and `docs/security-boundary-and-
limitations.md` first — a lot of "is this a bug?" questions are
already answered there (what's enforced, what's explicitly out of
scope, and what's a known, already-documented gap).

## Reporting a security finding

**Please don't open a public issue for a security vulnerability.** See
`SECURITY.md` for how to report one privately instead. This project's
own history includes three real bypasses found by directly attacking a
running system (`docs/symlink-write-bypass-fix.md`, `docs/
read-symlink-bypass-fix.md`, `docs/toctou-investigation-t6.md`) — that
kind of report, with a concrete reproduction, is exactly the most
valuable contribution this project can receive.

## Reporting a bug

Open an issue with: what you ran (the exact `idhn` command), what you
expected, what happened instead, and the relevant lines from
`runs/<run-id>/` if the problem is enforcement-related (`codex-
events.jsonl`, `events.jsonl`, or `idhn m3-report <run-id>`'s output).
A reproduction that doesn't depend on a private repository or a
specific OpenAI account is far more useful than a description alone.

## Proposing a change

1. Open an issue first for anything beyond a small, obvious fix --
   this project has a strong bias toward verifying claims against a
   real, running system rather than reasoning from the code alone (see
   almost any `docs/*.md` file for what that looks like in practice),
   and a change that looks right by inspection has been wrong before.
2. Run the full default suite (`pytest`) and, if your change touches
   `idhn/enforcement/`, `idhn/runtime/`, or `idhn/network/`, the Docker
   integration suite too (`pytest -m docker tests/integration` --
   requires Docker and a `codex login` credential).
3. If you're fixing or hardening an enforcement mechanism, a
   regression test that would have failed before your fix is expected,
   not optional -- every fix in this project's history has one.
4. Keep the change's own commit message explaining *why*, not just
   *what* -- this project's commit history is written to be read
   later, not just skimmed once.

## What this project is not yet set up for

No CI pipeline runs automatically on PRs yet. No formal maintainer
rotation or response-time commitment exists. Large, speculative
features (a new agent adapter, a new runtime backend) are welcome to
discuss in an issue first, since the architecture
(`idhn/core/`/`idhn/adapters/`/`idhn/runtime/` kept deliberately
separate) is meant to support them, but hasn't been exercised with a
second adapter or runtime yet.
