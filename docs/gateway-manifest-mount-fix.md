# Gateway manifest loading: fixing `idhn run --policy <path outside policies/>`

## The failure (`run-20261005-64e5af`)

```
idhn run --policy runs/compile-20261004-340a00/materialized-manifest.yaml \
  --workspace <repo> "<task>"
```

was accepted and validated by the CLI, but the gateway container crashed
on startup:

```
idhn.core.manifest.ManifestError: manifest not found: /policies/runs/compile-20261004-340a00/materialized-manifest.yaml
```

`docker/compose.yaml`'s gateway service only ever bind-mounted the host's
whole `policies/` directory at `/policies`, and the old mechanism for
telling the gateway which file to load inside it
(`DockerRuntime._policy_relpath`) fell back to the manifest's absolute
host path for anything outside `policies/` — which then still got
`/policies/` prepended by `compose.yaml`'s own
`IDHN_MANIFEST_PATH: /policies/${IDHN_POLICY_FILE}` template, producing
a nonsensical path regardless of host OS. A manifest produced by `idhn
materialize` (which writes to `runs/<compile-id>/`, not `policies/`) was
therefore never loadable by the gateway, even though `idhn validate` and
`idhn run`'s own CLI-side checks accepted it fine.

## The fix

`idhn.runtime.docker.gateway_manifest_mount(manifest, run_dir)`:

- For a manifest already under `policies/`: returns the same
  `/policies/<relative path>` the old mechanism did. Untouched behavior.
- For a manifest anywhere else: **copies it into `run_dir`** (fixed
  name, `gateway-manifest.yaml`) and points the gateway at
  `/run/gateway-manifest.yaml`. `run_dir` is already bind-mounted
  read-write into the gateway at `/run` (for its own `events.jsonl`), so
  this needs **no new Compose volume** and exposes nothing the gateway
  didn't already have access to — never the manifest's containing
  directory (which could be `runs/`, ephemeral and gitignored, or
  anywhere else on the host).

`write_filesystem_override` no longer needs a gateway-specific override
block at all; the whole mechanism collapsed into one function plus a
plain file copy. `DockerRuntime.start()` calls it once, for the
`IDHN_MANIFEST_PATH` environment variable.

The copy is `chmod`'d read-only on the host as defense in depth, but the
real protection is structural: the gateway process only reads this path
once at startup, and the original host file is never written by
anything in this path.

## Why a copy, not a bind mount onto the original file

The first version of this fix bind-mounted the manifest file **in
place** (read-only) at a container path derived from its own filename.
This does not work reliably: Docker Desktop (WSL2 backend, containerd
snapshotter) can auto-create a bind-mount target when the mount source
is a *directory*, but fails container creation outright when the source
is a single *file* and that exact target path doesn't already exist in
the image —

```
OCI runtime create failed: ... error mounting ".../materialized-manifest.yaml"
to rootfs at "/policies/materialized-manifest.yaml": create mountpoint for
.../materialized-manifest.yaml mount: make mountpoint
"/policies/materialized-manifest.yaml": read-only file system
```

Pre-baking an empty placeholder file at that exact path into the image
(so the bind mount would only need to *replace* an existing mountpoint,
not create one) was tried next and **still failed with the identical
error** — confirmed by actually rebuilding the image and re-running it.
This isn't about auto-vivification of a missing parent directory; this
Docker setup cannot create *any* new file-type bind mount at
container-init time, full stop. Directory-type mounts (what `/policies`
and `/run` already are) have no such problem, which is why reusing the
existing `/run` mount via a copy sidesteps the bug entirely rather than
fighting it.

One consequence: the gateway's own `Manifest.name` (`idhn.core.manifest
.load_manifest`'s `path.stem`) comes out as `"gateway-manifest"` rather
than the real manifest's own name, for every run using an external
manifest — this shows up as the `policy` field in `events.jsonl`. This
is fine: every audit event the gateway logs already carries `run_id`
(`idhn.network.proxy`, from `IDHN_RUN_ID`) independently of `policy`,
and `run_id` is what actually disambiguates one run from another.

## Verification

- `tests/test_gateway_manifest_mount.py` (9 unit tests): in-`policies/`
  behavior unchanged; a manifest under `runs/<compile-id>/` gets staged
  correctly (the exact failing scenario); an absolute external path
  outside the repo; Windows-style separators in the source path; no
  `source_path` falls back to the documented default; a manifest nested
  two levels under `policies/`; the staged copy is read-only; the
  original file is never modified; the generated Compose override never
  adds a `gateway` key in either case (no new volume needed).
- `tests/integration/test_materialized_manifest_gateway.py` (1 Docker
  test, `pytest -m docker`): builds a real materialized manifest via
  `idhn.compiler.materialize.materialize()`, writes it outside
  `policies/`, and runs it through the real two-container topology end
  to end — asserts the gateway starts without `ManifestError`, logs real
  decisions (`events.jsonl` non-empty) tagged with the correct `policy`
  and `run_id`, and the container exits 0. This is the actual proof (not
  just a unit-level guarantee) that an externally-sourced manifest can
  start the gateway and have the agent reach it.
- While building the Docker integration test, confirmed directly (not
  just by inference) that IDHN's read-only filesystem enforcement is
  intact: a manifest granting `filesystem.read: ["/workspace/**"]` with
  no `write` grant produces a container where a plain `echo > file`
  write attempt fails with `Read-only file system` at the kernel level,
  exactly as `idhn/enforcement/filesystem.py` documents.

## The misleading top-level exit 137

The CLI's `idhn run` prints `done (exit code {N})`, where `N` is
`docker compose up --exit-code-from agent`'s return code. In
`run-20261005-64e5af`, that was 137 (128+9, SIGKILL) — which looked like
a separate, meaningful failure of the agent itself.

It wasn't. The actual sequence, read directly from that run's
`stderr.log`:

1. The gateway crashed on startup (`ManifestError`, exit 1).
2. Compose's `--abort-on-container-exit` (used so the overall command
   exits once the agent is done) treats *any* container exiting as the
   signal to tear the whole stack down — "Compose Stopping Aborting on
   container exit...".
3. The agent container was starting at that exact moment (its own log
   shows it had only just printed `[idhn] process enforcement: shimmed
   ...` — before Codex itself ever ran). Compose's teardown SIGTERM'd it
   and then SIGKILL'd it once it didn't exit in time, which Docker
   reports as exit code 137.

137 is a side effect of the *agent* being killed mid-startup by Compose's
own abort sequence, triggered by the *gateway's* real failure — not an
independent signal about anything the agent did. No exit-code semantics
were changed: inspecting the gateway's own exit status specifically (to
report something like "gateway exited 1 before the agent could run"
instead of the agent's incidental 137) would be a real improvement, but
it's not a small one — it needs querying per-container exit status
after `docker compose up` returns, not just the one `--exit-code-from`
value already in hand — so it's left as a documented limitation rather
than attempted here.

## What this does NOT change

- Policy semantics, enforcement, or the compiler/materializer output —
  untouched. This is purely about which file the gateway process loads
  from disk.
- Behavior for any manifest already under `policies/` — byte-for-byte
  identical to before.
- `docker/compose.yaml`'s base volume/network definitions — untouched;
  the fix reuses the `/run` mount that was already there.
