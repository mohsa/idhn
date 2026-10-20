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


def test_command_disables_other_provider_side_capabilities_by_default():
    """Week 6 follow-up to the web_search fix: `codex features list`
    (CLI 0.160.0) confirms `apps`, `browser_use`, `browser_use_external`,
    `browser_use_full_cdp_access`, and `computer_use` are ALL `stable`
    and enabled (`true`) by default -- the same pattern that made
    web_search a real gap, not a hypothetical one. None of these are in
    web_search's own config namespace (a separate top-level key), which
    is why they needed their own fix. `--strict-config` accepts all five
    names (confirmed: no "unrecognized field" error) and a real Codex
    run with all five disabled still completes a normal task
    successfully -- see the commit introducing this test for the
    reproduction.
    """
    command = CodexAdapter().command("do the task")

    config_args = [command[i + 1] for i, arg in enumerate(command) if arg == "-c"]
    for feature in (
        "apps",
        "browser_use",
        "browser_use_external",
        "browser_use_full_cdp_access",
        "computer_use",
    ):
        assert f"features.{feature}=false" in config_args, f"expected features.{feature}=false in {config_args!r}"


def test_command_still_runs_non_interactively_with_expected_flags():
    command = CodexAdapter().command("do the task")

    assert command[:2] == ["codex", "exec"]
    assert "--json" in command
    assert "--skip-git-repo-check" in command
    assert command[-1] == "do the task"
