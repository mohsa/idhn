#!/bin/bash
set -euo pipefail

cd /workspace

echo "[idhn] agent container ready, workspace=/workspace" >&2
echo "[idhn] task: ${IDHN_TASK:-<none>}" >&2

if [ -z "${IDHN_AGENT_COMMAND:-}" ]; then
  echo "[idhn] IDHN_AGENT_COMMAND not set, dropping to shell" >&2
  exec bash
fi

exec bash -c "$IDHN_AGENT_COMMAND"
