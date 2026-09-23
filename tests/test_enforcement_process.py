from idhn.enforcement.process import AGENT_INFRASTRUCTURE_BINARIES, allowed_binaries


def test_flat_list_allow():
    capabilities = {"process": {"allow": ["git", "python3"]}}
    result = allowed_binaries(capabilities)
    assert "git" in result
    assert "python3" in result


def test_categorized_allow_is_flattened():
    capabilities = {
        "process": {
            "allow": {
                "discovery": ["pwd", "ls"],
                "development": ["python3", "node"],
            }
        }
    }
    result = allowed_binaries(capabilities)
    for binary in ("pwd", "ls", "python3", "node"):
        assert binary in result


def test_infrastructure_binaries_always_included():
    capabilities = {"process": {"allow": ["git"]}}
    result = allowed_binaries(capabilities)
    for binary in AGENT_INFRASTRUCTURE_BINARIES:
        assert binary in result


def test_no_duplicates():
    capabilities = {"process": {"allow": ["codex", "node"]}}
    result = allowed_binaries(capabilities)
    assert len(result) == len(set(result))
