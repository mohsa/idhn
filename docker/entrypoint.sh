#!/bin/bash
set -euo pipefail

cd /workspace

echo "[idhn] agent container ready, workspace=/workspace" >&2
echo "[idhn] task: ${IDHN_TASK:-<none>}" >&2

# Process enforcement (idhn/enforcement/process.py): populate the shim
# directory with symlinks ONLY for declared-allowed binaries, resolved
# against the image's real PATH now, before anything restricts it.
# IDHN_AGENT_COMMAND (built by idhn/runtime/docker.py) sets
# PATH=/opt/idhn/bin on just the agent's own invocation, so a bare
# command name it wasn't granted resolves to nothing here -- "command
# not found", not a policy check that had to remember to run.
if [ -n "${IDHN_ALLOWED_BINARIES:-}" ]; then
  IFS=',' read -ra _idhn_bins <<< "$IDHN_ALLOWED_BINARIES"
  for _bin in "${_idhn_bins[@]}"; do
    [ -z "$_bin" ] && continue
    _real="$(command -v "$_bin" 2>/dev/null || true)"
    if [ -n "$_real" ]; then
      ln -sf "$_real" "/opt/idhn/bin/$_bin"
    else
      echo "[idhn] warning: allowed binary not found on PATH, no shim created: $_bin" >&2
    fi
  done

  # Codex invokes every command as `/bin/bash -lc "..."` -- a LOGIN
  # shell. A login shell sources /etc/profile, which unconditionally
  # resets PATH to the system default, silently discarding the
  # PATH=/opt/idhn/bin we set as an env-var prefix on codex's own
  # invocation (found by actually running this: `findmnt`, never
  # granted, still worked, because PATH had already been reset back to
  # /usr/bin:/bin:... by the time codex's child bash got there). Bash
  # reads ~/.bash_profile right after /etc/profile in a login shell, so
  # writing our restriction there makes it win instead of /etc/profile's
  # unconditional assignment -- an env var alone can't do that.
  echo 'export PATH=/opt/idhn/bin' > "$HOME/.bash_profile"
  echo "[idhn] process enforcement: shimmed ${IDHN_ALLOWED_BINARIES}" >&2
else
  echo "[idhn] IDHN_ALLOWED_BINARIES not set -- process enforcement inactive for this run" >&2
fi

if [ -z "${IDHN_AGENT_COMMAND:-}" ]; then
  echo "[idhn] IDHN_AGENT_COMMAND not set, dropping to shell" >&2
  exec bash
fi

exec bash -c "$IDHN_AGENT_COMMAND"
