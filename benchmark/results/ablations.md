# v0.2-B causal capability ablations

Six tasks, chosen to represent distinct authority shapes (one each:
diagnosis, single-file modification, multi-file modification, test
creation, dependency addition, network/dependency-behavior). Each starts
from a task that already succeeded under its generated envelope (I union
P, frozen in `benchmark/runs/<id>.json`), removes exactly one capability
believed necessary, and reruns just the constrained leg
(`benchmark/ablate.py`) to check whether it now fails. This is how
"exercised" gets told apart from "necessary" with real ground truth,
rather than assumed from the compiler's own reasoning.

Full per-task before/after test counts and traces are in
`benchmark/results/ablations/<task>.json` and `runs/bench-ablate-<task>/`.

| task | family     | ablated capability                            | baseline | ablated | verdict |
|------|------------|------------------------------------------------|----------|---------|---------|
| T08  | modify (single-file) | filesystem.write /workspace/src/dedupe.py       | PASS     | FAIL    | confirmed necessary |
| T09  | modify (multi-file)  | filesystem.write /workspace/src/parser.js       | PASS     | FAIL    | confirmed necessary |
| T13  | test                  | filesystem.write /workspace/tests/parser.test.js| PASS     | FAIL    | confirmed necessary |
| T20  | dependency (network)  | network.connect registry.npmjs.org              | PASS     | FAIL    | confirmed necessary |
| T21  | dependency (addition) | filesystem.write /workspace/package.json        | PASS     | PASS*   | confirmed necessary (see note) |
| T01  | diagnose               | filesystem.read /workspace/src/**               | PASS     | PASS    | not enforced (see note) |

4 of 6 give a clean, mechanically-visible PASS -> FAIL. The other two are
more informative than a clean result would have been:

## T21 -- the mechanical metric is blind, the ground truth isn't

T21's dependency-family success check is "didn't regress" (test failure
count non-increasing) because the task explicitly never touches tests
("confirm `npm install` succeeds. Don't change any other files"). Removing
`package.json` write doesn't move that number either way, so
`task_succeeded()` reads PASS on both runs. Direct inspection tells a
different story: after the ablated run, `package.json` is byte-for-byte
identical to HEAD (`git diff` empty) and the trace has explicit
EROFS/read-only/Blocked evidence of the write attempt failing -- the
devDependency was never added. The capability was genuinely necessary;
the automated proxy metric just isn't shaped to see it. Any future
compiler-version comparison on the dependency family should check the
target file's content, not only test counts, or it will silently miss
exactly this kind of regression.

## T01 -- a real enforcement gap, not a compiler finding

Both the baseline and the ablated run produced the same correct,
specific diagnosis (`src/parser.js:29`, the precedence bug in `toRPN`),
despite the manifest granting no `filesystem.read` for `src/**` in the
ablated run. Checked directly against the enforcement code, not assumed:
`idhn.enforcement.filesystem.build_mount_plan` only ever builds mounts
from `capabilities.filesystem.write` and `.deny` -- there is no loop over
`.read` at all. The base workspace mount is read-only for the entire
tree regardless of what's declared as "read," so removing a read grant
has no mechanical effect; only an explicit `deny` pattern masks a path,
and only a `write` grant changes what's writable. `idhn.core.policy.
PolicyEngine.decide()` does check `read` requests against the declared
allowlist, but nothing routes Codex's local file reads through that
engine, so the check is audit-shaped, not preventive.

This means IDHN's current filesystem model enforces two of three
declared axes at the OS level (write, deny) and the third (read-allow)
only on paper. It's a real gap in the "consequence authority" story --
an agent can read anything not explicitly deny-masked, whether or not it
was granted -- and worth weighing against the process-execution-authority
vs. consequence-authority idea already flagged as something to watch
(not touched here, per the freeze-and-observe instruction: this is a
finding to carry into that decision, not something fixed as part of the
v0.2-B run).

## Takeaway

Where the outcome is genuinely test-count-shaped (single/multi-file
modification, test creation, network-gated dependency behavior), the
ablation is a clean, mechanical PASS -> FAIL and confirms the compiler's
predicted capability was load-bearing, not just exercised. Where the
outcome isn't test-count-shaped (file-content-only dependency tasks,
diagnosis), the mechanical proxy under- or mis-reports, and the two
outlier results here are both real findings in their own right rather
than failed experiments.
