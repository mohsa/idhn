# `idhn compile` → `idhn materialize` → `idhn validate` → `idhn run`

The missing bridge between the capability compiler (v0.2-A/B/v0.2.1 --
proposes a Capability IR, never wired into enforcement) and `idhn run`
(requires a normal enforceable manifest). No LLM call in the bridge
itself -- `idhn materialize` is pure, deterministic data transformation,
built entirely out of already-existing, already-tested pieces.

## The workflow

```
idhn compile --workspace <repo> "<task>"
    -> runs/<compile-id>/compiled-envelope.yaml   (Capability IR, untrusted)
    -> runs/<compile-id>/compile-meta.json        (task text, new)

  [ inspect the envelope -- confidence/reason per decision ]

idhn materialize <compile-id>
    -> runs/<compile-id>/materialized-manifest.yaml  (enforceable manifest)

  [ inspect the manifest -- this is the human review point ]

idhn validate runs/<compile-id>/materialized-manifest.yaml

idhn run --policy runs/<compile-id>/materialized-manifest.yaml \
  --workspace <repo> "<same task>"
```

`idhn materialize` never runs Codex. That's deliberate, not a missing
feature -- this dogfood phase wants a human to actually look at the
generated manifest before anything is enforced against a real
repository, not an automatic compile-to-run pipeline.

## What materialization does

`idhn/compiler/materialize.py`'s `materialize()` function:

1. **Re-validates the envelope from scratch** -- re-parses every
   decision's `resource`/`operation`/`scope`/`confidence` against the
   same rules `idhn.compiler.parser.parse_capability_ir` already
   enforces at compile time (`ALLOWED_RESOURCES`, `ALLOWED_CONFIDENCE`),
   then re-runs `validate_capability_ir`. The file on disk is never
   trusted just because it's shaped right -- `idhn compile`'s own CLI
   command writes `compiled-envelope.yaml` to disk *before* checking
   `result.budget_violations` (so a human can see what the compiler
   produced even when it gets rejected), which means a file existing at
   that path is not proof it was ever a valid, budget-passing proposal.
2. **Re-runs the budget check** -- `idhn.compiler.budget.check_budget`,
   the same deterministic rejection of unscoped-wildcard proposals and
   forbidden process primitives (`sudo`/`docker`/`mount`/`nsenter`) used
   at compile time. A budget-violating envelope cannot reach enforcement
   by materializing it after the fact, even if it somehow got past the
   first check (e.g. a future caller that skips `idhn compile`'s own
   exit-on-violation).
3. **Drops confidence/reason** -- `idhn.compiler.parser.CapabilityDecision
   .as_tuple()` already reduces a decision to just `(resource, operation,
   scope)` for exactly this reason (it's what v0.2-B/v0.2.1's authority-set
   math has always used); materialization reuses that, so confidence and
   reason text are never read by anything that decides what gets
   enforced -- only by a human looking at the saved envelope.
4. **Unions with the fixed authority floor** --
   `idhn.audit.authority_sets.infrastructure_authority()` (agent
   infrastructure + execution substrate + tool authority, exactly the
   v0.2.1 three-source floor, unchanged) unioned with the compiler's
   predicted capabilities, via `to_manifest_capabilities` -- the *same*
   function `benchmark/run_task.py`/`reinterpret.py`/`ablate.py` already
   use to constrain a benchmark task's execution leg by exactly what the
   compiler proposed. Materialization is that same operation, now
   reachable from a normal CLI command instead of only from inside the
   benchmark harness.
5. **Validates the result one more time** -- the constructed manifest
   dict is run through `idhn.core.manifest.validate()`, the same check
   every hand-written `policies/examples/*.yaml` is held to. A
   materialized manifest is never trusted more than a hand-written one.

Any failure at any of these steps raises `MaterializeError` and produces
**nothing** -- there's no partial or best-effort manifest; the CLI
command exits 1 and writes no output file.

## Why confidence/reason can't affect enforcement, verified concretely

Two envelopes identical except for confidence and reason text produce
byte-for-byte identical `capabilities` dicts
(`tests/test_materialize.py::test_confidence_and_reason_do_not_affect_
enforcement`). There's no code path between "parse the envelope" and
"build the manifest" that ever reads either field for anything but
display.

## Why deny/default-deny semantics can't be touched by the compiler

Not just preserved by convention -- **structurally impossible for the
compiler to propose otherwise**. `idhn.compiler.parser`'s schema only
allows `operation` to be `"read"`/`"write"` for `filesystem` and
`"connect"` for `network` (`validate_capability_ir`); there is no `deny`
operation a compiled decision can ever carry. `to_manifest_capabilities`
hardcodes the deny list (`.env`, `.github/**`, `home/**`, `root/**`) and
`network.default: deny` unconditionally, regardless of what's in the
capability set passed to it. The compiler cannot add an exception to a
deny pattern, cannot flip `network.default` to `allow`, and cannot touch
`secrets.default` (always `deny`) -- these are infrastructure decisions,
not something task authority ever reaches.

## Zero task network capability does not mean zero network access

If the compiled IR contains no `network` capability at all, the
materialized manifest's `network.allow` still contains the fixed
agent-infrastructure hosts (`chatgpt.com` family -- Codex needs these to
run at all) and, if any substrate tool needing network access is in the
fixed substrate floor (e.g. `npm` → `registry.npmjs.org`, `pip` →
`pypi.org`/`files.pythonhosted.org` -- see `idhn.audit.authority_sets.
TOOL_AUTHORITY_REQUIREMENTS`), those hosts too. **This is not invented
task network access** -- it's the same fixed, task-independent floor
every v0.2-B/v0.2.1 benchmark task already got, present or absent
independent of what the task asked for. `network.default` stays `deny`,
and no host the compiled IR never named and that isn't part of that
fixed floor ever appears.

## Acceptance example: `compile-20261004-340a00`

A real dogfood compile run (against an external repository, not
`lab/repos/toy-app` -- not modified by this work, per instruction):

```
idhn materialize compile-20261004-340a00 \
  --task "Fix bounded_gap_subsequence_match in modules/evaluation/matcher.py and add regression tests in tests/unit/test_eval_matcher.py"
```

`--task` was required here because this run predates `compile-meta.json`
(added by this change -- see below); the original task text was never
saved anywhere recoverable, and reconstructing it by scraping the
one-shot compile prompt out of `codex-events.jsonl` would be exactly the
kind of fragile text-parsing this project has repeatedly rejected
elsewhere (`idhn.audit.codex_events`'s own docstring). Every `idhn
compile` run from now on writes `compile-meta.json` (`{"task": ...,
"workspace": ...}`) up front, so `idhn materialize` needs `--task` only
for pre-existing runs like this one.

Result (`runs/compile-20261004-340a00/materialized-manifest.yaml`),
confirmed with `idhn validate`:

```yaml
capabilities:
  filesystem:
    write:
    - /workspace/modules/evaluation/matcher.py      # exact scope, unchanged
    - /workspace/tests/unit/test_eval_matcher.py    # exact scope, unchanged
    read: [...]
    deny: [/workspace/.env, /workspace/.github/**, /home/**, /root/**]
  network:
    default: deny
    allow: [chatgpt.com, '*.chatgpt.com', '*.oaiusercontent.com',
            registry.npmjs.org, pypi.org, files.pythonhosted.org]
            # all infrastructure/tool-floor hosts -- the compiled IR
            # proposed ZERO network capabilities, and none appear here
            # beyond the fixed floor
  process:
    allow: [bash, cat, codex, find, git, ls, nl, node, npm, pip, pwd,
            pytest, python, python3, rg, sha256sum]
            # "python" (task-predicted, high confidence) alongside the
            # fixed substrate floor
```

`idhn validate runs/compile-20261004-340a00/materialized-manifest.yaml`
→ `OK`.

## What this does NOT change

- `idhn/adapters/codex.py` (`web_search="disabled"` by default) --
  untouched. Materialization produces a manifest; the adapter still
  builds the `codex exec` command the same way regardless of which
  manifest it's run under.
- `idhn/enforcement/filesystem.py`, `idhn/enforcement/process.py`,
  `idhn/network/proxy.py`, `idhn/core/policy.py` -- untouched.
  Enforcement consumes whatever manifest it's given, materialized or
  hand-written, through the exact same code path.
- `docker/compose.yaml` and the whole container/network topology --
  untouched.
- No escalation, no automatic recompilation, no approval UI, no adaptive
  authority. `idhn materialize` is a single deterministic conversion
  step with a human looking at the output before `idhn run` is ever
  invoked.

## Known limitations / things this surfaced

- **`secrets` capabilities are silently dropped.** `idhn.compiler.
  parser.ALLOWED_RESOURCES` includes `"secrets"`, so a compiled decision
  naming it would pass structural validation -- but `to_manifest_
  capabilities`'s bucket-selection `if/elif` chain has no branch for it
  (falls into `else: continue`), so it's silently ignored rather than
  rejected or surfaced. `secrets.default` stays `deny` regardless (no
  weakening), but a compiler that genuinely needed to propose a secrets
  grant would have it vanish without any error -- pre-existing behavior
  in `to_manifest_capabilities` (unrelated to this change), not
  introduced here, and out of scope for this narrow bridge; flagged for
  whoever next touches secrets capability support.
- **`idhn compile`'s existing write-then-check ordering is unchanged.**
  `compiled-envelope.yaml` is still written to disk (and printed) before
  budget violations are checked -- this predates materialization and
  wasn't touched, since `idhn materialize` already re-checks budget
  independently regardless of what's on disk, making the ordering
  harmless rather than something that needed fixing to satisfy "the
  compiler remains untrusted."
- **Task ID is synthetic** (`materialized-<compile-run-id>`), not
  something the compiler or task text provides -- there's no natural
  task-id source anywhere upstream of this bridge. Fine for a manifest
  that's about to be inspected by a human before use; would need
  reconsidering if this pipeline were ever made fully automatic.
- **No provenance split in the output manifest** between
  task-predicted and infrastructure-floor capabilities -- the manifest
  schema itself has no room for it (enforcement doesn't need it, and
  adding it would be a schema change well outside "smallest deterministic
  bridge"). The CLI command's own stdout *does* report both counts
  separately as a partial mitigation for the human-review point this
  phase explicitly wants, but the saved YAML is a flat, undifferentiated
  union, same as every other manifest.
