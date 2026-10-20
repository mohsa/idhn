# Read-granted symlink bypass, including a deny-mask bypass

Found while reviewing the T5d fix's own documented limitation
(`docs/symlink-write-bypass-fix.md`'s "Scope, deliberately narrow"
section flagged read-granted mounts as an unaddressed, "smaller,
different risk" -- investigated directly rather than left as an
assumption, per the roadmap's Week 2 item to review symlink handling
more broadly).

## The vulnerability, reproduced for real

**Scenario 1 — plain read redirect.** A manifest granting ONLY
`filesystem.read: /workspace/allowed-read.txt`, where that file was
actually a pre-existing symlink to a completely different,
never-granted file:

```
allowed-read.txt -> never-granted-secret.txt
```

`cat /workspace/allowed-read.txt` returned
`never-granted-secret.txt`'s content, not an empty or missing file —
the read-only bind mount followed the symlink exactly the way the
write-side one did in T5d.

**Scenario 2 — a real deny-mask bypass, more serious.** Same mechanism,
but `allowed-read.txt` was made a symlink to `.env` instead:

```
allowed-read.txt -> .env
```

with a manifest also correctly denying `/workspace/.env` (the
unconditional deny-mask every manifest carries). Reading `.env`
directly still correctly returned nothing (the mask works, as every
prior test in this project confirms). But `cat
/workspace/allowed-read.txt` returned `.env`'s REAL, unmasked content.
The deny mask only shadows the one container path it names
(`/workspace/.env`); it does nothing for a *different* container path
that happens to point at the same underlying host file via a symlink.
This defeats IDHN's one unconditional guarantee — that `.env` and the
other deny-listed paths are never readable — without touching the
manifest, the compiler, or the deny list itself at all.

## The fix

`write_filesystem_override`'s symlink check (T5d) now also covers
read-granted (`ro`) bind mounts, not just writable (`rw`) ones:

```python
is_real_grant = spec.mode == "rw" or (spec.mode == "ro" and spec.host_path != EMPTY_FILE_STUB)
if spec.kind == "bind" and is_real_grant:
    ...  # same symlink walk as the T5d fix
```

**Deny masks are deliberately exempt** (`host_path == EMPTY_FILE_STUB`):
a deny mask substitutes a fixed empty file regardless of what the
container path points to on the host, so whether `.env` itself happens
to be a symlink (a real pattern some repos use, e.g. `.env ->
.env.production`) is irrelevant to what actually gets mounted there.
Rejecting that case would be a false positive — confirmed directly: a
deny-masked `.env` that is itself a symlink to a real file still
produces an empty read, with no rejection of the run (`tests
/test_runtime_symlinks.py::test_deny_mask_is_exempt_even_if_dotenv_
itself_is_a_symlink`).

## Verification

`tests/test_runtime_symlinks.py` now has 6 tests (3 new): a read-granted
symlink is rejected and its real target's content is confirmed
untouched; an ordinary (non-symlinked) read-granted file is unaffected;
and a deny mask on a symlinked `.env` still applies cleanly rather than
being incorrectly rejected. All three new scenarios were first
reproduced live against real Docker (`docs/evidence/t6-toctou/
read_symlink_experiment.py`) both before the fix (confirming the
bypass, including the real `.env` content coming back) and after
(confirming a clean rejection for scenarios 1-2, and a clean, correctly
masked pass-through for scenario 3). Full default suite and Docker
integration suite both clean on Windows and via WSL.

## What this does NOT change

- The write-side T5d fix, the T6 TOCTOU lock, or any other enforcement
  mechanism — this only extends the EXISTING symlink check's condition
  to cover one more mount mode.
- Deny-mask semantics — a deny mask still unconditionally masks its
  named path regardless of host state, exactly as before; this fix only
  makes sure nothing ELSE can read the same underlying file under a
  different name.
- The manifest schema, compiler, or materializer — untouched, as with
  every other fix in this filesystem-hardening line of work.
