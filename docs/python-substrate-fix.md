# `python` missing from the agent image (`run-20261005-fd5119`)

## What actually happened -- this was not a task/authority failure

The task's own validation step was `python -m pytest
tests/unit/test_eval_matcher.py -q`. The compiler correctly inferred
process authority for **both** `python` and `python3`, and `idhn
materialize` correctly carried both into the manifest. Codex:

- Changed exactly the two authorized files
  (`observed-filesystem-writes.json`: `modules/evaluation/matcher.py`,
  `tests/unit/test_eval_matcher.py` -- nothing else).
- Noticed `python` wasn't available, adapted with
  `python() { python3 "$@"; }`, and validated its own fix:
  **27 passed, 1 warning** (`codex-events.jsonl`).
- Exited 0 (`codex-exit-code`).

The real bug was external to Codex entirely: IDHN's own
`--before-test`/`--after-test` harness runs the literal command text the
operator gave it (here, the task's own `python -m pytest ...`), with no
shell function to fall back on, against an agent image that only ever
installed `python3`. Both hooks failed outright:

```
bash: line 1: python: command not found
```

Because the *after*-test phase runs last in the generated agent script
and its exit status becomes the whole script's exit status, the overall
agent container exited 127 — which read as a top-level run failure (and
reported task completion UNKNOWN) despite the actual coding task having
fully succeeded and validated itself correctly inside Codex's own run.

## The fix

`docker/Dockerfile.agent` now installs `python-is-python3` (Debian's
own package for a real `/usr/bin/python -> python3` symlink), alongside
the existing `python3`/`python3-pip`. Not a shell alias, not a rewrite
of any test command — the exact thing requirement #2 asked for: a
normal executable that resolves because it's actually present in the
image, the same way `python3` already was.

This fixes it for every run uniformly:

- **Codex's own (PATH-restricted) invocation**: `enforcement/process.py`
  builds `/opt/idhn/bin` by resolving each granted binary name against
  the image's real `PATH` at container start
  (`entrypoint.sh`'s `command -v "$_bin"`). Before this fix, `command -v
  python` found nothing, so no shim was created even though the manifest
  granted it (the `[idhn] warning: allowed binary not found on PATH, no
  shim created: python` line in the original run's log). Now it
  resolves, so a `python` grant actually does something.
- **IDHN's own `--before-test`/`--after-test` harness**: these run with
  the harness's own unrestricted `PATH` (deliberately exempt from
  process.allow — see `build_agent_script`'s docstring), so they just
  need the binary to exist in the image at all, which it now does.

## Why this doesn't weaken process enforcement

The symlink only means the *binary* exists in the image, same as
`python3` always has. It does **not** change who can reach it:

- Codex's own command still only sees `/opt/idhn/bin` on its `PATH`
  (`restrict_path=True`, scoped to just that one command). A manifest
  that doesn't grant `python` still produces no
  `/opt/idhn/bin/python` shim, so a bare `python` invocation inside
  Codex's own restricted command still fails with "command not found" —
  verified directly
  (`tests/integration/test_python_substrate.py::test_python_execution_
  still_denied_without_python_authority`), not just assumed.
- This is exactly the same model `python3` (and every other substrate
  binary) was already under — adding `python` as a second name for the
  same interpreter doesn't introduce a new enforcement path, it fixes a
  gap in an existing one.

## What this deliberately does NOT change

- `idhn/compiler/*`, `idhn/audit/authority_sets.py` (`PROCESS_SUBSTRATE_
  BINARIES` still lists only `python3`, unchanged) — the compiler
  already correctly predicts `python` per-task when a task's own text
  calls for it (as it did here); there was never a need to add it to
  the fixed, task-independent substrate floor, and doing so would widen
  what every manifest implicitly grants regardless of task, which isn't
  what this bug needed.
- The materialized manifest schema or `materialize()` — untouched. This
  was never a manifest-content problem; the manifest was already
  correct.
- No rewriting of task text, before/after-test commands, or anything
  specific to this task, this repository, or `pytest` — the fix is one
  image-level package install that makes a standard Unix convention
  (`python` resolving to whatever Python 3 is installed) actually hold,
  the same way it does on most real developer machines.

## Verification

`tests/integration/test_python_substrate.py` (4 Docker tests, `pytest -m
docker`), run directly against the real agent image (no Codex
invocation needed — the agent's own command is always PATH-restricted
the same way regardless of what it is):

- `python --version` resolves when granted.
- `python3 --version` still resolves when granted (unaffected).
- `--before-test`/`--after-test` hooks can use `python -m pytest ...` —
  the literal shape of the original failing command.
- `python` is still unreachable by Codex's own restricted invocation
  when process authority for it isn't granted — the symlink doesn't
  create a bypass.
