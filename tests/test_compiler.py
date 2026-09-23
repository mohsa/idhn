from idhn.compiler.compiler import compile_task
from idhn.compiler.context import RepoContext, gather_context
from idhn.compiler.parser import CompilerOutputError, parse_capability_ir, validate_capability_ir
from idhn.compiler.prompts import build_prompt


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
    # The whole point: no coding-safe.yaml-style example leaks in.
    assert "chatgpt.com" not in prompt
    assert "registry.npmjs.org" not in prompt
    assert "fix the bug" in prompt


def test_parse_capability_ir_strips_markdown_fence():
    raw = """```yaml
filesystem:
  read:
    - "/workspace/**"
  write:
    - "/workspace/src/**"
network:
  default: deny
```"""
    data = parse_capability_ir(raw)
    assert data["filesystem"]["write"] == ["/workspace/src/**"]
    assert data["network"]["default"] == "deny"


def test_parse_capability_ir_rejects_non_mapping():
    import pytest

    with pytest.raises(CompilerOutputError):
        parse_capability_ir("- just\n- a\n- list\n")


def test_parse_capability_ir_rejects_unknown_top_level_keys():
    import pytest

    with pytest.raises(CompilerOutputError):
        parse_capability_ir("something_weird:\n  - x\n")


def test_validate_capability_ir_flags_wrong_shapes():
    errors = validate_capability_ir({"filesystem": {"write": "not-a-list"}, "network": {"default": "maybe"}})
    assert any("filesystem.write" in e for e in errors)
    assert any("network.default" in e for e in errors)


def test_validate_capability_ir_accepts_well_formed_envelope():
    errors = validate_capability_ir(
        {
            "filesystem": {"read": ["/workspace/**"], "write": ["/workspace/src/**"]},
            "process": {"allow": ["python3", "pytest"]},
            "network": {"default": "deny", "allow": []},
        }
    )
    assert errors == []


def test_compile_task_end_to_end_with_fake_completer():
    context = RepoContext(languages=["javascript/node"], file_tree=["src/parser.js"], manifest_files=["package.json"])

    def fake_complete(prompt: str) -> str:
        assert "fix the bug" in prompt
        return 'filesystem:\n  write:\n    - "/workspace/src/**"\nnetwork:\n  default: deny\n'

    result = compile_task("fix the bug", context, fake_complete)
    assert result.ok
    assert result.capabilities["filesystem"]["write"] == ["/workspace/src/**"]


def test_compile_task_surfaces_parse_errors_without_crashing():
    context = RepoContext(languages=[], file_tree=[], manifest_files=[])

    result = compile_task("fix the bug", context, lambda prompt: "not: valid: yaml: at: all:")
    assert not result.ok
    assert result.errors
