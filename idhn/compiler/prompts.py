"""Prompt construction for the capability compiler.

Deliberately does NOT include any hand-written IDHN manifest (e.g.
coding-safe.yaml) as a few-shot example -- only a generic schema
illustration -- so a generated envelope reflects what the model infers
from the task itself, not a policy it copied.
"""
from __future__ import annotations

from idhn.compiler.context import RepoContext

SCHEMA_DESCRIPTION = """\
Emit ONLY a YAML list (no prose, no markdown code fences) of individual
capability decisions, in this shape:

- capability:
    resource: filesystem
    operation: write
    scope: /workspace/src/parser.js
  confidence: high
  reason: "Task explicitly requires fixing implementation and parser.js
    is the relevant implementation file."

- capability:
    resource: network
    operation: connect
    scope: registry.npmjs.org
  confidence: low
  reason: "Dependency installation may be required but is not explicit."

Rules:
- resource is one of: filesystem, process, network, git.
- operation is "read" or "write" for filesystem, "connect" for network,
  "exec" for process, a git subcommand for git.
- scope is specific: a glob rooted at /workspace for filesystem, a
  binary name for process, a hostname for network. Do not use unscoped
  wildcards like "/workspace/**" for write, or "*" for network, unless
  the task gives you no way to be more specific -- and say so in reason
  if you do.
- confidence is exactly one of: high, medium, low. Use it honestly: low
  confidence is a valid, useful answer, not a failure. A capability you
  reason might plausibly be needed but the task doesn't require
  explicitly should be low confidence, not omitted and not granted at
  high confidence to be safe.
- reason is one sentence explaining why, in terms of the task.
- One list item per distinct (resource, operation, scope) capability.
  Grant the smallest set that could plausibly let an agent complete the
  task -- do not pad the list with capabilities "just in case".
"""


def build_prompt(task: str, context: RepoContext) -> str:
    tree_preview = "\n".join(f"  {p}" for p in context.file_tree) or "  (empty)"
    return f"""\
You are proposing an initial capability envelope for a coding agent,
BEFORE it has run. You do not have live access to the repository --
only the summary below. The agent will only get what you grant here:
if you under-grant, it will hit a wall it can't get past; if you
over-grant, you've defeated the point of this exercise.

TASK
{task}

REPOSITORY CONTEXT
languages: {', '.join(context.languages) or 'unknown'}
manifest files present: {', '.join(context.manifest_files) or 'none'}
available process tools: {', '.join(context.available_tools)}
file tree (partial):
{tree_preview}

{SCHEMA_DESCRIPTION}
"""
