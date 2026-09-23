from idhn.enforcement.filesystem import EMPTY_FILE_STUB, build_mount_plan

CAPABILITIES = {
    "filesystem": {
        "read": ["/workspace/**"],
        "write": ["/workspace/src/**", "/workspace/tests/**"],
        "deny": ["/workspace/.env", "/workspace/.github/**"],
    }
}


def test_base_workspace_mount_is_read_only():
    plan = build_mount_plan(CAPABILITIES, "/host/toy-app")
    base = plan[0]
    assert base.container_path == "/workspace"
    assert base.mode == "ro"
    assert base.host_path == "/host/toy-app"


def test_write_patterns_become_readwrite_bind_mounts():
    plan = build_mount_plan(CAPABILITIES, "/host/toy-app")
    by_path = {m.container_path: m for m in plan}
    assert by_path["/workspace/src"].mode == "rw"
    assert by_path["/workspace/src"].host_path == "/host/toy-app/src"
    assert by_path["/workspace/tests"].mode == "rw"


def test_deny_file_pattern_masked_with_empty_stub():
    plan = build_mount_plan(CAPABILITIES, "/host/toy-app")
    by_path = {m.container_path: m for m in plan}
    env_mount = by_path["/workspace/.env"]
    assert env_mount.mode == "ro"
    assert env_mount.kind == "bind"
    assert env_mount.host_path == EMPTY_FILE_STUB


def test_deny_directory_pattern_masked_with_tmpfs():
    plan = build_mount_plan(CAPABILITIES, "/host/toy-app")
    by_path = {m.container_path: m for m in plan}
    github_mount = by_path["/workspace/.github"]
    assert github_mount.kind == "tmpfs"
    assert github_mount.mode == "ro"
    assert github_mount.host_path is None


def test_patterns_outside_workspace_are_ignored():
    capabilities = {"filesystem": {"deny": ["/home/**", "/root/**"]}}
    plan = build_mount_plan(capabilities, "/host/toy-app")
    # Only the base workspace mount -- nothing generated for non-workspace patterns.
    assert len(plan) == 1


def test_wildcard_mid_pattern_is_ignored_not_guessed():
    capabilities = {"filesystem": {"write": ["/workspace/*.json"]}}
    plan = build_mount_plan(capabilities, "/host/toy-app")
    assert len(plan) == 1
