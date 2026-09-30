import json

from idhn.audit.m3_report import build_m3_report, deny_masked_read_attempts
from idhn.core.manifest import Manifest
from idhn.core.models import Task


def _manifest(deny: list[str]) -> Manifest:
    return Manifest(
        version="0.1",
        task=Task(id="demo", description="fix the bug"),
        capabilities={
            "filesystem": {"read": ["/workspace/**"], "write": ["/workspace/src/**"], "deny": deny},
            "process": {"allow": ["cat", "rg", "ls"]},
            "network": {"allow": []},
            "git": {"allow": []},
        },
    )


def _write_command_execution(run_dir, command: str, exit_code: int = 0) -> None:
    (run_dir / "codex-events.jsonl").write_text(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "command_execution", "command": f"/bin/bash -lc {command!r}", "exit_code": exit_code},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_detects_a_direct_read_of_a_denied_file(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_command_execution(run_dir, "cat .env")

    attempts = deny_masked_read_attempts(run_dir, _manifest(["/workspace/.env"]))

    assert len(attempts) == 1
    assert attempts[0]["path"] == ".env"
    assert attempts[0]["matched_argument"] == ".env"
    assert attempts[0]["succeeded"] is True


def test_detects_a_read_of_a_denied_directory_via_absolute_path(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_command_execution(run_dir, "rg -n . /workspace/.github")

    attempts = deny_masked_read_attempts(run_dir, _manifest(["/workspace/.github/**"]))

    assert len(attempts) == 1
    assert attempts[0]["path"] == ".github"


def test_real_trace_shape_rg_search_across_multiple_roots_including_denied_dir(tmp_path):
    # The exact command observed in a real dogfood run: rg searching
    # several directories at once, one of which (.github) is deny-masked.
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_command_execution(run_dir, "rg -n . src tests .github")

    attempts = deny_masked_read_attempts(run_dir, _manifest(["/workspace/.env", "/workspace/.github/**"]))

    assert len(attempts) == 1
    assert attempts[0]["path"] == ".github"


def test_does_not_false_positive_when_path_only_appears_in_output_not_command(tmp_path):
    # `ls -la` lists .env in its OUTPUT (not captured here at all -- only
    # the command text is inspected) -- that's not an access attempt on
    # its content, and must not be reported as one.
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_command_execution(run_dir, "ls -la")

    attempts = deny_masked_read_attempts(run_dir, _manifest(["/workspace/.env"]))

    assert attempts == []


def test_no_false_positive_for_unrelated_filename_prefix(tmp_path):
    # ".environment" starts with ".env" as a string but is not the same
    # path -- token matching must be exact/boundary-aware, not a prefix
    # scan of raw text.
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_command_execution(run_dir, "cat .environment")

    attempts = deny_masked_read_attempts(run_dir, _manifest(["/workspace/.env"]))

    assert attempts == []


def test_empty_deny_list_or_no_manifest_returns_no_attempts(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_command_execution(run_dir, "cat .env")

    assert deny_masked_read_attempts(run_dir, _manifest([])) == []
    assert deny_masked_read_attempts(run_dir, None) == []


def test_build_m3_report_surfaces_masked_reads_separately_from_residual_gaps(tmp_path):
    """A masked read looks like a successful command (empty content,
    exit 0) -- that must NOT be misclassified as a residual gap (which
    means "denied by policy but a real enforcement hole"), since the
    mask working as designed is the opposite finding.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_command_execution(run_dir, "cat .env", exit_code=0)

    report = build_m3_report(run_dir, _manifest(["/workspace/.env"]))

    assert len(report["deny_masked_read_attempts"]) == 1
    assert report["deny_masked_read_attempts"][0]["path"] == ".env"
    assert report["residual_gaps"] == []
