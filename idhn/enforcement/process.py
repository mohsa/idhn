"""Process enforcement: turn `capabilities.process` into a PATH-restricted
binary allowlist.

Mechanism: idhn/runtime/docker.py builds a per-run directory
(/opt/idhn/bin) containing symlinks only to the returned binaries,
resolved against the image's real PATH at container start, and runs
Codex's own invocation with PATH set to only that directory (see
build_agent_script in idhn/runtime/docker.py -- the restriction is
scoped to Codex's own command, not IDHN's before/after test-harness
commands around it).

This is a real, live boundary for the common case -- a bare command name
Codex's generated shell script wants to run either resolves inside
/opt/idhn/bin or fails with "command not found". It is NOT a complete
process-confinement boundary, and isn't trying to be:

- It doesn't stop invocation by absolute path of some other binary still
  present in the image (e.g. /usr/bin/curl even if "curl" was never
  granted). Shrinking what's actually installed in docker/Dockerfile.agent
  is the mitigation for that, not this list.
- It can't stop `bash -c "..."`, `python -c "..."`, `node -e "..."` etc.
  from running arbitrary computation once invoked -- those are
  programmable execution environments, not single-purpose tools, and no
  binary allowlist closes that gap. What actually constrains the
  consequences of that arbitrary computation is the filesystem and
  network boundaries (enforcement/filesystem.py, idhn/network/), which
  hold regardless of what process is doing the writing or connecting.

Process execution + filesystem boundary + network boundary is the real
containment story here, not process execution alone.
"""
from __future__ import annotations

from typing import Any

from idhn.core.policy import flatten_category_allowlist

# Required for the agent to run at all -- not a task-granted capability,
# same idea as "chatgpt.com" in the network manifests. `codex` is the npm-
# installed CLI itself; `node` is what its own launcher script needs.
AGENT_INFRASTRUCTURE_BINARIES = ("codex", "node")


def allowed_binaries(capabilities: dict[str, Any]) -> list[str]:
    """The full set of binary names Codex's PATH should resolve: the
    manifest's declared process.allow, plus the fixed infrastructure set
    needed for Codex itself to run.
    """
    proc = capabilities.get("process", {}) or {}
    declared = flatten_category_allowlist(proc.get("allow", []))
    return sorted(set(declared) | set(AGENT_INFRASTRUCTURE_BINARIES))
