# T6: TOCTOU race against the T5d symlink check (investigation)

**Status: investigated, confirmed exploitable, mitigation chosen but
NOT yet implemented** — this is the Week 1 deliverable (establish
whether the race affects a real Docker mount, document the threat
assumptions, choose a mitigation); implementation is a separate step.

## The race

`write_filesystem_override`'s T5d symlink check
(`docs/symlink-write-bypass-fix.md`) runs once, early, in the host-side
Python process. The actual bind mount happens much later, in a
separate `docker compose up` subprocess call. Between those two
moments, nothing stops the checked path from being swapped.

**Experiment** (`docs/evidence/t6-toctou/race_experiment.py`): a
manifest grants `filesystem.write: /workspace/allowed.txt`, where
`allowed.txt` starts as an ordinary file (passes the T5d check). A
watcher thread polls for `compose.filesystem.yaml` to appear — the
signal that the check already passed — and immediately replaces
`allowed.txt` with a symlink to a different file, `secret.txt`, never
granted by the manifest. The agent then runs the exact command the
manifest's task describes: write a marker string to
`/workspace/allowed.txt`.

**Result, run for real against Docker:**

```
total start() duration: 8.46s
time from override-file-appearing to swap completing: 0.2ms
container exit code: 0
target ('secret.txt') content: 'RACE_WIN'
```

The write succeeded cleanly (exit 0) and landed, verbatim, in
`secret.txt` — the file the T5d check was specifically designed to
keep unreachable. The swap won the race in under half a millisecond,
inside an **8+ second** window dominated by ordinary container-creation
overhead. This is not a tight, hard-to-win race requiring precise
timing; it's a wide-open window a simple polling loop wins reliably.

## Threat model — who can actually win this race

Winning requires writing to the exact host path IDHN is about to mount,
at the exact moment between that path's T5d check and the later
`docker compose up` call — i.e. a **separate, already-running process**
with write access to that path, racing a **specific, in-flight** `idhn
run` invocation. Within a single run, this is structurally impossible
to trigger from the agent being tested: the agent doesn't exist as a
process until every mount for that run is already set up (atomic at
container creation), so it cannot race its own mount setup.

That leaves two realistic categories:

1. **A concurrent IDHN-managed run sharing the same `workspace_dir`.**
   Nothing in `idhn run`/`DockerRuntime` currently prevents two
   separate invocations (same or different manifests) from targeting
   the same host workspace directory at the same time. If one run's
   agent has write access anywhere under that tree, it can plant a
   symlink timed against another, independently-started run's own
   check-to-mount window.
2. **A host-level process outside any IDHN container with independent
   write access to the workspace.** This is explicitly **out of
   IDHN's threat model** — IDHN's job is to contain an agent running
   *inside* its own managed container, not to defend against an
   attacker who already has arbitrary code execution on the host
   itself. If that's a live threat, the problem is host compromise, not
   anything a filesystem-mount check inside IDHN can fix.

**Conclusion: the one threat IDHN should actually close is (1) —
concurrent IDHN-managed runs against a shared workspace.** This is a
real gap, not a contrived one: nothing today stops a user (or an
orchestration layer built on top of `idhn run`) from launching two runs
against the same repo clone at once, and this investigation shows that
if they do, one run's agent can redirect another's write grant.

## Mitigations considered

**A. Re-check immediately before `docker compose up`.** Shrinks the
window from seconds to milliseconds, but doesn't close it — the
experiment above won in 0.2ms, well under what a second, later check
would still leave open. Rejected as a real fix; still useful as cheap
defense-in-depth alongside a real fix.

**B. Open with `O_NOFOLLOW`, bind-mount via `/proc/self/fd/N`.** The
standard Linux technique for exactly this class of bug — but it only
works when the same process that validates the path also performs the
mount. Here, validation happens in IDHN's Python process, while the
actual mount is performed by the Docker daemon (a separate process,
running inside Docker Desktop's own VM) after being handed a path
*string* in `compose.filesystem.yaml` — the daemon never sees, and
can't use, a file descriptor from IDHN's process. Rejected: doesn't
cross the docker-compose-to-dockerd boundary.

**C. Copy-in/copy-out instead of a live bind mount, for single-file
write grants.** Validate the path, copy its content into a location
only IDHN controls, bind-mount the copy, and write the result back to
the real path (with one final check immediately before the write-back).
Closes the race for the duration of the container's run, at the cost of
losing "the container writes the real file directly" semantics and
needing careful handling for directory-shaped write grants. A real
architectural change — Week 2 scope if chosen, not a smallest-fix.

**D. Serialize access to a given `workspace_dir` with an exclusive
lock, held from the symlink check through the container actually
starting.** Directly closes the one realistic vector identified above
(two IDHN-managed runs racing each other against the same workspace) —
a second `idhn run` targeting the same workspace simply waits. Doesn't
protect against a host-level attacker outside IDHN's model (nothing
would, by definition — see "out of IDHN's threat model" above). Small,
localized change: one lock acquired around the existing
`write_filesystem_override` → `docker compose up` sequence in
`DockerRuntime.start()`.

## Recommendation

**D, with A as cheap defense-in-depth alongside it.** D is the smallest
change that closes the actual, demonstrated vector (concurrent
IDHN-managed runs), matches the threat model precisely (it does nothing
for, and claims nothing against, a host-level attacker, which is
honest rather than over-claiming), and needs no change to the
mount/copy semantics every other fix this project has made assumes. C
is a stronger mitigation worth keeping in mind if the threat model ever
expands to include a non-IDHN-managed writer with legitimate concurrent
access to the same workspace, but that's not the vector this
investigation found, so building it now would be solving a problem
that hasn't been demonstrated.

**Not yet implemented.** Per the roadmap's own split (Week 1: establish
+ choose; Week 2: implement + test), this document is the Week 1
deliverable. Implementing D — and its own regression tests (two
concurrent runs against one workspace; confirm the lock actually
serializes them; confirm the race no longer wins once serialized) — is
the next concrete step.
