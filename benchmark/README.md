# benchmark/

v0.2's evaluation harness for the capability compiler (`idhn compile`,
see `idhn/compiler/`). Structure:

- `corpus/` -- toy repos the compiler and agent run against, grouped by
  language (`python/`, `javascript/`, `git/`, `mixed/`). Not populated
  yet -- `lab/repos/toy-app` is the only corpus member so far.
- `tasks/` -- one YAML per benchmark task (task text + which corpus repo
  it targets). Not populated yet -- 20-30 tasks across the corpus is
  v0.2-B scope, not built in this pass.
- `expected/` -- deliberately **not** hand-labeled "required
  capabilities" ahead of time. M3 already showed why: the hand-written
  `coding-safe.yaml` and, so far, the compiler too, both missed real
  requirements (`chatgpt.com`, `cat`/`ls`/`pwd`/`rg`) that only became
  visible from an actual execution trace. Required capabilities get
  established by running the task, not by guessing before anyone runs
  it -- see `runs/*/codex-events.jsonl` for how that ground truth is
  captured today.
- `results/` -- one record per (task, compiler version, run) with:
  task text, environment metadata, generated initial envelope,
  exercised capabilities, blocked capabilities, escalations, final
  grants, task success, runtime, agent/model, policy/compiler version.
  `results/compile-m3-task.md` is the first real one.

See the top-level README's "v0.2: adaptive authority" section for the
architecture this feeds (compiler -> deterministic guard -> runtime)
and why capability *precision* and *recall* are the metrics, not raw
grant count.
