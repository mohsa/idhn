"""One-off script: extend miss_taxonomy_v0.2-b0.json with classifications
for the remaining tasks, after reviewing each one's actual missing set and
(for the network entries) its trace. Not part of the benchmark pipeline.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TAXONOMY_PATH = REPO_ROOT / "benchmark" / "results" / "miss_taxonomy_v0.2-b0.json"

TOOL_SUBSTRATE_NOTE = "discovery/inspection tool, in context tool list but not predicted"
NOT_IN_CONTEXT_NOTE = "coreutils/tool binary never listed in the compiler context at all -- context-completeness gap contributed"

NEW: dict[str, list[dict]] = {
    "T02": [
        {"capability": ["process", "exec", "ls"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "nl"], "cause": "M3", "note": NOT_IN_CONTEXT_NOTE},
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T03": [
        {
            "capability": ["network", "connect", "registry.npmjs.org"],
            "cause": "M8",
            "note": (
                "task explicitly says 'Do not modify any files'; agent ran `npm install dedent` "
                "anyway to test its diagnosis hypothesis, and the gateway correctly denied it "
                "(task_success for diagnose = repo left alone, which held). Whether an agent "
                "attempts an install as part of 'diagnosis' isn't derivable from task text -- "
                "same recurring pattern as T09/T15/T16, see those notes."
            ),
        },
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T04": [
        {"capability": ["process", "exec", "git"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "nl"], "cause": "M3", "note": NOT_IN_CONTEXT_NOTE},
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T05": [
        {"capability": ["process", "exec", "nl"], "cause": "M3", "note": NOT_IN_CONTEXT_NOTE},
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T06": [
        {"capability": ["process", "exec", "ls"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T09": [
        {
            "capability": ["network", "connect", "registry.npmjs.org"],
            "cause": "M8",
            "note": (
                "task text never mentions dependencies (pure bugfix); agent ran `npm test`, which "
                "reached out to the registry on its own even though no new package was being added. "
                "Same repo (toy-app) shows this in some runs (T09, T16) and not others (T18) -- "
                "genuinely execution-state-dependent (whether/how the agent invokes npm), not "
                "something task-text reasoning could have predicted."
            ),
        },
        {"capability": ["process", "exec", "cat"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "python3"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T10": [
        {"capability": ["process", "exec", "ls"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "python3"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T11": [
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T12": [
        {"capability": ["process", "exec", "ls"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T14": [
        {"capability": ["process", "exec", "cat"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T15": [
        {
            "capability": ["network", "connect", "registry.npmjs.org"],
            "cause": "M8",
            "note": "same recurring npm-lifecycle pattern as T09/T03 -- see T09's note.",
        },
        {"capability": ["process", "exec", "cat"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T16": [
        {
            "capability": ["network", "connect", "registry.npmjs.org"],
            "cause": "M8",
            "note": "same recurring npm-lifecycle pattern as T09/T03 -- see T09's note.",
        },
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T17": [
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T18": [
        {"capability": ["process", "exec", "cat"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "ls"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "sha256sum"], "cause": "M3", "note": NOT_IN_CONTEXT_NOTE},
    ],
    "T20": [
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T21": [
        {"capability": ["process", "exec", "cat"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T23": [
        {"capability": ["process", "exec", "cat"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "ls"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T24": [
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
    ],
    "T22": [
        {"capability": ["process", "exec", "cat"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {"capability": ["process", "exec", "pwd"], "cause": "M3", "note": TOOL_SUBSTRATE_NOTE},
        {
            "capability": ["process", "exec", "check_target=$(mktemp"],
            "cause": "M9",
            "note": (
                "not a real binary -- a fragment of a longer shell command (a $(mktemp ...) "
                "substitution) that the codex-events trace parser split incorrectly. An IDHN "
                "measurement gap, not a genuine compiler miss."
            ),
        },
    ],
}


def main() -> None:
    data = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    data["classifications"].update(NEW)

    counts: dict[str, int] = {}
    for entries in data["classifications"].values():
        for e in entries:
            counts[e["cause"]] = counts.get(e["cause"], 0) + 1
    data["counts"] = dict(sorted(counts.items()))

    TAXONOMY_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("wrote", TAXONOMY_PATH, "-- counts:", data["counts"])


if __name__ == "__main__":
    main()
