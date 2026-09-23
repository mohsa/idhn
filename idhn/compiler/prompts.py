"""Prompt construction for the capability compiler.

Deliberately does NOT include any hand-written IDHN manifest (e.g.
coding-safe.yaml) as a few-shot example -- only the schema shape --
so a generated envelope reflects what the model infers from the task
itself, not a policy it copied. This is the whole point of the v0.2-A
experiment: does the compiler independently converge on something close
to what a task actually needs.
"""
from __future__ import annotations

from idhn.compiler.context import RepoContext

SCHEMA_DESCRIPTION = """\
Emit ONLY a YAML document (no prose, no markdown code fences) with this
shape:

filesystem:
  read: [<glob patterns rooted at /workspace>]
  write: [<glob patterns rooted at /workspace>]
process:
  allow: [<binary names, only from the available tools list below>]
network:
  default: deny
  allow: [<hostnames, only if the task genuinely requires network access>]

Grant the SMALLEST set of capabilities that could plausibly let an
agent complete the task. Do not grant filesystem.write beyond what the
fix would plausibly touch. Do not grant network access unless the task
clearly requires it (e.g. installing a package). Do not use unscoped
wildcards like "*" or "/**" for anything you haven't reasoned about
specifically -- that defeats the purpose of granting authority at all.
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
