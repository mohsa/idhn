from idhn.adapters.codex import CodexAdapter


def test_command_disables_native_web_search_by_default():
    """Regression guard for a real containment gap: Codex's native
    web_search/open_page tool executes on Codex's own backend, never
    touching the container's network namespace or the IDHN gateway, so
    no network policy can see or govern it (see
    docs/web-search-out-of-band-capability.md). `-c web_search="disabled"`
    is Codex's own documented way to remove the tool from the model's
    toolset entirely, confirmed by real reproduction, not merely
    discouraging its use.
    """
    command = CodexAdapter().command("do the task")

    assert "-c" in command
    config_args = [command[i + 1] for i, arg in enumerate(command) if arg == "-c"]
    assert 'web_search="disabled"' in config_args


def test_command_still_runs_non_interactively_with_expected_flags():
    command = CodexAdapter().command("do the task")

    assert command[:2] == ["codex", "exec"]
    assert "--json" in command
    assert "--skip-git-repo-check" in command
    assert command[-1] == "do the task"
