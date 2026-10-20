# Running IDHN on Windows / WSL2

IDHN's own development and testing happened on Windows with Docker
Desktop (WSL2 backend). Everything in this repo works there, but three
real, concrete pitfalls were hit along the way -- documented here so
the next person doesn't have to rediscover them.

## 1. Creating symlinks requires elevated privilege or Developer Mode

Four tests in `tests/test_runtime_symlinks.py` create real symlinks to
verify IDHN's symlink-bypass protections (`docs/
symlink-write-bypass-fix.md`, `docs/read-symlink-bypass-fix.md`).
`Path.symlink_to()` on native Windows Python raises
`OSError: [WinError 1314] A required privilege is not held by the
client` unless either:

- Windows Developer Mode is enabled (Settings → Privacy & Security →
  For developers), or
- the process runs elevated (as Administrator).

This is a genuine Windows platform limitation, not a code gap -- the
underlying check (`Path.is_symlink()`) is ordinary, cross-platform
Python with no Windows-specific behavior. If you hit this, either
enable Developer Mode, or run the affected tests from inside WSL2
instead (see below) -- both are confirmed working.

## 2. Bash's `/tmp` and native Windows Python's `/tmp` are NOT the same path

If you write a script that creates files under `/tmp/...` and run it
with native Windows Python (`.venv\Scripts\python.exe`, not a WSL/MSYS
Python), Windows resolves `/tmp/...` as a *drive-relative* path --
effectively `C:\tmp\...` on whatever the current drive is -- not Git
Bash's own `/tmp` (which Git Bash maps to a real temp directory under
`C:\Users\<you>\AppData\Local\Temp\...`). The two tools silently
disagree about where "/tmp" is. If a script needs to be created by one
tool (e.g. Git Bash's `cp`) and consumed by native Windows Python,
either use an explicit absolute Windows path, or a path relative to a
known shared working directory -- not a bare `/tmp/...` path assumed
to mean the same thing in both places.

## 3. Docker Desktop (WSL2 backend, containerd snapshotter) cannot create a NEW single-file bind mount

Confirmed directly while investigating the T6 TOCTOU mitigation (`docs/
toctou-investigation-t6.md`): Docker Desktop on this platform can
auto-create a bind-mount target when the mount *source* is a
directory, but fails container creation outright
(`make mountpoint "...": read-only file system`) when the source is a
single file and that exact target path doesn't already exist in the
image -- even when the target file is pre-baked (empty) into the image
at build time. This isn't specific to any one fix in this project; it's
a platform constraint that shaped how `idhn.runtime.docker
.gateway_manifest_mount` is built (it stages externally-sourced
manifests into the already-mounted `/run` directory instead of
bind-mounting them in place). If you're extending IDHN's own mount
logic, prefer a directory-type mount or writing into an already-mounted
directory over introducing a brand-new single-file bind mount.

## 4. WSL2 is the practical way to run anything needing real, unprivileged symlink creation

This repo's own Linux-side verification throughout development used
WSL2 directly against the Windows-side repo files (not a separate
clone) via:

```bash
wsl.exe -e bash -c "cd /mnt/c/Users/<you>/path/to/idhn && python -m pytest tests/test_runtime_symlinks.py -v"
```

A venv with this repo's dependencies installed under WSL2's own Python
(not the Windows `.venv`) is what makes this work -- `pip install -e
".[dev]"` inside a WSL2 shell, same as the Windows instructions in
"Setup", just from a Linux Python interpreter. `IDHN_CODEX_AUTH_PATH`
may need to be set explicitly if your `codex login` credential lives
on the Windows side and you're running from WSL2 (`/mnt/c/Users/<you>
/.codex/auth.json`), since WSL2's own `Path.home()` resolves to its own
Linux home directory, not the Windows one.

## What works without any of this

Everything else in this repo -- `idhn run`, `idhn compile`, the full
Docker Compose topology, the gateway, and every test except the four
symlink-creation ones above -- runs correctly from native Windows
Python with Docker Desktop, no WSL2 shell-out required. These notes are
about the narrow set of things that specifically need real,
unprivileged symlink creation or that touched a Docker Desktop platform
quirk directly, not a statement that Windows support is partial.
