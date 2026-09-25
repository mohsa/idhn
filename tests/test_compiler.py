import pytest

from idhn.compiler.budget import check_budget
from idhn.compiler.compiler import compile_task
from idhn.compiler.context import RepoContext, gather_context
from idhn.compiler.parser import CompilerOutputError, parse_capability_ir, validate_capability_ir
from idhn.compiler.prompts import build_prompt

DECISION_YAML = """\
- capability:
    resource: filesystem
    operation: write
    scope: /workspace/src/parser.js
  confidence: high
  reason: "Task explicitly requires fixing implementation."

- capability:
    resource: network
    operation: connect
    scope: registry.npmjs.org
  confidence: low
  reason: "Dependency installation may be required but is not explicit."
"""


def test_gather_context_detects_language_and_file_tree(tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "parser.js").write_text("", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("", encoding="utf-8")

    context = gather_context(tmp_path)

    assert context.languages == ["javascript/node"]
    assert "package.json" in context.manifest_files
    assert "src/parser.js" in context.file_tree
    assert not any(".git" in f for f in context.file_tree)


def test_build_prompt_never_includes_a_hand_written_manifest():
    context = RepoContext(languages=["javascript/node"], file_tree=["src/parser.js"], manifest_files=["package.json"])
    prompt = build_prompt("fix the bug", context)
    assert "coding-safe" not in prompt
    assert "fix the bug" in prompt


def test_parse_capability_ir_strips_markdown_fence():
    decisions = parse_capability_ir(f"```yaml\n{DECISION_YAML}```")
    assert len(decisions) == 2
    assert decisions[0].resource == "filesystem"
    assert decisions[0].scope == "/workspace/src/parser.js"
    assert decisions[0].confidence == "high"
    assert decisions[1].confidence == "low"


def test_parse_capability_ir_rejects_non_list():
    with pytest.raises(CompilerOutputError):
        parse_capability_ir("just_a_key: value\n")


def test_parse_capability_ir_rejects_unknown_resource():
    with pytest.raises(CompilerOutputError):
        parse_capability_ir(
            "- capability:\n    resource: gpu\n    operation: use\n    scope: cuda0\n  confidence: high\n  reason: x\n"
        )


def test_parse_capability_ir_rejects_bad_confidence():
    with pytest.raises(CompilerOutputError):
        parse_capability_ir(
            "- capability:\n    resource: process\n    operation: exec\n    scope: git\n  confidence: 0.9\n  reason: x\n"
        )


def test_validate_capability_ir_flags_bad_filesystem_operation():
    decisions = parse_capability_ir(
        "- capability:\n    resource: filesystem\n    operation: delete\n    scope: /workspace/x\n  confidence: high\n  reason: x\n"
    )
    errors = validate_capability_ir(decisions)
    assert any("filesystem operation" in e for e in errors)


def test_check_budget_rejects_unscoped_filesystem_write():
    decisions = parse_capability_ir(
        "- capability:\n    resource: filesystem\n    operation: write\n    scope: /workspace/**\n  confidence: high\n  reason: to be safe\n"
    )
    violations = check_budget(decisions)
    assert violations
    assert "forbidden outright" in violations[0]


def test_check_budget_rejects_unscoped_network():
    decisions = parse_capability_ir(
        "- capability:\n    resource: network\n    operation: connect\n    scope: '*'\n  confidence: low\n  reason: might need internet\n"
    )
    violations = check_budget(decisions)
    assert violations


def test_check_budget_allows_scoped_grants():
    decisions = parse_capability_ir(DECISION_YAML)
    assert check_budget(decisions) == []


def test_check_budget_rejects_forbidden_execution_primitives():
    decisions = parse_capability_ir(
        "- capability:\n    resource: process\n    operation: exec\n    scope: sudo\n  confidence: high\n  reason: just in case\n"
    )
    violations = check_budget(decisions)
    assert violations
    assert "forbidden outright" in violations[0]


def test_check_budget_allows_ordinary_process_grants():
    decisions = parse_capability_ir(
        "- capability:\n    resource: process\n    operation: exec\n    scope: python3\n  confidence: high\n  reason: run the tests\n"
    )
    assert check_budget(decisions) == []


def test_compile_task_end_to_end_with_fake_completer():
    context = RepoContext(languages=["javascript/node"], file_tree=["src/parser.js"], manifest_files=["package.json"])

    def fake_complete(prompt: str) -> str:
        assert "fix the bug" in prompt
        return DECISION_YAML

    result = compile_task("fix the bug", context, fake_complete)
    assert result.ok
    assert len(result.decisions) == 2


def test_compile_task_surfaces_parse_errors_without_crashing():
    context = RepoContext(languages=[], file_tree=[], manifest_files=[])
    result = compile_task("fix the bug", context, lambda prompt: "not: valid: yaml: at: all:")
    assert not result.ok
    assert result.errors


def test_compile_task_surfaces_budget_violations():
    context = RepoContext(languages=[], file_tree=[], manifest_files=[])
    unscoped = "- capability:\n    resource: filesystem\n    operation: write\n    scope: /workspace/**\n  confidence: high\n  reason: safe\n"
    result = compile_task("fix the bug", context, lambda prompt: unscoped)
    assert not result.ok
    assert result.budget_violations
