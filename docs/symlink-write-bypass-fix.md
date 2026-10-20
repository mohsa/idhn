# Writable-mount symlink bypass (T5d)

## The vulnerability, reproduced for real

A manifest granting `filesystem.write: /workspace/core/language.py`
(and nothing else) was supposed to let the agent write exactly one
file. In a controlled reproduction, `core/language.py` was made a
pre-existing symlink on the host, pointing at a different file,
`core/idhn-symlink-target.txt`:

```
language.py -> idhn-symlink-target.txt
```

`idhn run` was given the usual task text ("write the exact text
T5D_BYPASS_TEST into /workspace/core/language.py"). Codex ran exactly
the command it believed was authorized:

```
printf '%s' 'T5D_BYPASS_TEST' > /workspace/core/language.py
```

— and reported success, having written to the path the manifest named.
But on the host, the marker string landed in `core/idhn-symlink-
target.txt`, not `core/language.py`:

```
$ cat core/idhn-symlink-target.txt
T5D_BYPASS_TEST
```

The bind mount's source was resolved by following the symlink, so the
container's `/workspace/core/language.py` was actually backed by a
completely different file — one the manifest, the compiler, and
`idhn materialize` never granted authority over. This is not a Codex
misbehavior: Codex did exactly what it was asked, against the exact
path it was told was authorized. The bypass is entirely in how the
host resolves a write-grant path into a bind-mount source, independent
of agent behavior — a manifest's write scope can be silently widened
to whatever an existing symlink in the workspace points at.

## The fix

`idhn.runtime.docker.write_filesystem_override` now rejects, before any
container starts, any writable (`rw`) bind mount whose target path — or
any directory component leading to it — is a symlink on the host:

```python
if spec.kind == "bind" and spec.mode == "rw":
    root = workspace_dir.resolve(strict=True)
    relative = target_path.relative_to(workspace_dir)
    current = root

    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(
                f"IDHN security: writable mount contains symlink: {current}"
            )
```

This converts a silent scope-widening bypass into a hard rejection of
the whole run (`write_filesystem_override` raises, so `DockerRuntime
.start()` never reaches `docker compose up` at all — no container, no
stdout/stderr, nothing past the manifest that was already written).
Walking every path component (not just the final one) also catches a
symlinked *directory* on the way to the target, not only a symlinked
leaf file.

**Scope, originally narrow:** this fix initially checked only `rw`
(write-granted) mounts, flagging a `ro` (read-granted) mount following
a symlink as a smaller, different, unaddressed risk. That assessment
was revised after actually investigating it: a read-granted symlink
turned out to be able to bypass IDHN's deny-mask guarantee entirely
(reading `.env` under a different, symlinked name), not just leak
unrelated content. Both mount modes are now checked — see
`docs/read-symlink-bypass-fix.md`.

## Verification

`tests/test_runtime_symlinks.py` (3 tests):

- A writable target that is itself a symlink is rejected
  (`test_rejects_symlinked_writable_file`) — and the symlink's real
  target file is confirmed unchanged afterward.
- A writable target reached through a symlinked *parent directory* is
  also rejected (`test_rejects_symlinked_parent_directory`).
- An ordinary (non-symlinked) writable file is unaffected
  (`test_allows_normal_writable_file`) — the fix doesn't regress the
  normal case.

**Platform note:** creating a symlink requires an elevated privilege or
Developer Mode on Windows (`os.symlink` raises `WinError 1314`
otherwise), which this dev environment doesn't have enabled — so the
two symlink-creation tests fail here for an environment reason
unrelated to the fix. The check itself (`Path.is_symlink()`) is
ordinary, cross-platform Python with no platform-specific behavior;
verified instead via WSL running directly against this repository's
files (not a separate copy), where unprivileged symlink creation
works: all 3 tests pass, and the full default suite is unaffected (134
passed on WSL; the one pre-existing Windows-path test
(`test_windows_style_separators_in_an_external_path_handled_correctly`,
see `docs/gateway-manifest-mount-fix.md`) fails there for the opposite,
already-known reason — a Windows-only path format tested on Linux).

## What this does NOT fix yet (T6)

This check runs in Python, on the host, at override-generation time —
strictly *before* the later, separate `docker compose up` subprocess
call actually performs the mount. That gap is a real time-of-check to
time-of-use (TOCTOU) window: nothing stops a path from being swapped
from a regular file to a symlink (or back) between this check and the
mount actually happening. Investigating whether that race is exploitable
against a real Docker mount, and what a race-resistant mitigation looks
like, is tracked separately and intentionally not addressed by this fix.
