import json

from idhn.audit.filesystem_diff import diff, snapshot
from idhn.audit.m3_report import build_m3_report
from idhn.core.manifest import Manifest
from idhn.core.models import Task


def test_snapshot_hashes_every_file_relative_to_workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text("one", encoding="utf-8")
    (tmp_path / "b.txt").write_text("two", encoding="utf-8")

    snap = snapshot(tmp_path)

    assert set(snap) == {"src/a.js", "b.txt"}
    # Same content -> same hash, computed independently.
    assert snap["src/a.js"] == snapshot(tmp_path)["src/a.js"]


def test_snapshot_skips_dot_git(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")

    snap = snapshot(tmp_path)

    assert set(snap) == {"README.md"}


def test_snapshot_of_missing_directory_is_empty(tmp_path):
    assert snapshot(tmp_path / "does-not-exist") == {}


def test_diff_detects_modified_added_and_removed_files():
    before = {"src/a.js": "hash1", "src/b.js": "hash2"}
    after = {"src/a.js": "hash1-changed", "src/c.js": "hash3"}

    changed = diff(before, after)

    assert changed == {"src/a.js", "src/b.js", "src/c.js"}


def test_diff_is_empty_when_nothing_changed():
    snap = {"src/a.js": "hash1"}
    assert diff(snap, dict(snap)) == set()


def _manifest(tmp_path) -> Manifest:
    return Manifest(
        version="0.1",
        task=Task(id="demo", description="fix the bug"),
        capabilities={
            "filesystem": {"read": ["/workspace/**"], "write": ["/workspace/src/**"]},
            "process": {"allow": ["node"]},
            "network": {"allow": []},
            "git": {"allow": []},
        },
    )


def test_build_m3_report_counts_a_write_the_agent_trace_alone_would_miss(tmp_path):
    """Regression test for the real bug this was built to fix: Codex
    edited src/parser.js via an inline `node <<'NODE' ... fs.writeFileSync
    NODE` heredoc inside a command_execution item, which
    idhn.audit.codex_events has no way to recognize as a filesystem write
    (it only understands the structured file_change tool) -- so the
    agent-trace-only count was 0 despite a real, enforcement-allowed
    change. DockerRuntime.start's ground-truth snapshot diff is what
    should catch it instead.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    # Only a command_execution item -- no file_change event at all, the
    # exact shape of the real bug (an inline heredoc is opaque shell text).
    (run_dir / "codex-events.jsonl").write_text(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "/bin/bash -lc \"node <<'NODE'\nfs.writeFileSync(...)\nNODE\"",
                    "exit_code": 0,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    # What idhn.runtime.docker.DockerRuntime.start would have written.
    (run_dir / "observed-filesystem-writes.json").write_text(
        json.dumps(["src/parser.js"]), encoding="utf-8"
    )

    report = build_m3_report(run_dir, _manifest(tmp_path))

    assert report["filesystem_writes"] == 0  # the trace genuinely saw no file_change
    assert report["filesystem_unique_files_written"] == 1  # ground truth still catches it
    assert report["filesystem_writes_ground_truth"] is True


def test_build_m3_report_falls_back_to_trace_when_no_ground_truth_file(tmp_path):
    """A run from before this fix existed (no observed-filesystem-writes.json)
    still gets a count -- the old, known-lower-bound one -- rather than
    silently reporting zero or crashing.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "codex-events.jsonl").write_text(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "file_change", "changes": [{"path": "src/parser.js", "kind": "update"}], "status": "completed"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_m3_report(run_dir, _manifest(tmp_path))

    assert report["filesystem_unique_files_written"] == 1
    assert report["filesystem_writes_ground_truth"] is False
