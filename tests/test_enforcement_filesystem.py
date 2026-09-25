from idhn.enforcement.filesystem import EMPTY_FILE_STUB, build_mount_plan

CAPABILITIES = {
    "filesystem": {
        "read": ["/workspace/**"],
        "write": ["/workspace/src/**", "/workspace/tests/**"],
        "deny": ["/workspace/.env", "/workspace/.github/**"],
    }
}


def test_base_workspace_mount_is_an_empty_tmpfs():
    # Default deny: the base is empty, not the real repo -- everything
    # visible comes from an explicit read/write mount layered on top.
    plan = build_mount_plan(CAPABILITIES, "/host/toy-app")
    base = plan[0]
    assert base.container_path == "/workspace"
    assert base.mode == "ro"
    assert base.kind == "tmpfs"
    assert base.host_path is None


def test_broad_read_pattern_mounts_the_real_repo_readonly():
    # "/workspace/**" is the ordinary "read the whole repo" grant --
    # _relpath returns rel="" for it, which has to resolve to the real
    # workspace root, not a malformed "/workspace/" path.
    plan = build_mount_plan(CAPABILITIES, "/host/toy-app")
    read_mounts = [m for m in plan if m.container_path == "/workspace" and m.kind == "bind"]
    assert len(read_mounts) == 1
    assert read_mounts[0].mode == "ro"
    assert read_mounts[0].host_path == "/host/toy-app"


def test_write_patterns_become_readwrite_bind_mounts():
    plan = build_mount_plan(CAPABILITIES, "/host/toy-app")
    by_path = {m.container_path: m for m in plan if m.mode == "rw"}
    assert by_path["/workspace/src"].mode == "rw"
    assert by_path["/workspace/src"].host_path == "/host/toy-app/src"
    assert by_path["/workspace/tests"].mode == "rw"


def test_deny_file_pattern_masked_with_empty_stub():
    plan = build_mount_plan(CAPABILITIES, "/host/toy-app")
    env_mounts = [m for m in plan if m.container_path == "/workspace/.env"]
    assert len(env_mounts) == 1
    env_mount = env_mounts[0]
    assert env_mount.mode == "ro"
    assert env_mount.kind == "bind"
    assert env_mount.host_path == EMPTY_FILE_STUB


def test_deny_directory_pattern_masked_with_tmpfs():
    plan = build_mount_plan(CAPABILITIES, "/host/toy-app")
    github_mounts = [m for m in plan if m.container_path == "/workspace/.github"]
    assert len(github_mounts) == 1
    github_mount = github_mounts[0]
    assert github_mount.kind == "tmpfs"
    assert github_mount.mode == "ro"
    assert github_mount.host_path is None


def test_patterns_outside_workspace_are_ignored():
    capabilities = {"filesystem": {"deny": ["/home/**", "/root/**"]}}
    plan = build_mount_plan(capabilities, "/host/toy-app")
    # Only the base workspace mount -- nothing generated for non-workspace patterns.
    assert len(plan) == 1


def test_write_wildcard_mid_pattern_is_ignored_not_guessed():
    # Deliberately NOT widened like the read case below: this is the only
    # write pattern of this shape observed across the 24-task corpus
    # (T23's "/workspace/eslint.config.*"), and it has no directory
    # prefix at all -- widening it would mount the entire workspace
    # read-write, a worse failure mode than the one the read-side
    # widening fixes.
    capabilities = {"filesystem": {"write": ["/workspace/*.json"]}}
    plan = build_mount_plan(capabilities, "/host/toy-app")
    assert len(plan) == 1


def test_read_wildcard_confined_to_final_segment_mounts_the_directory():
    # v0.2.1: unlike write, a read pattern like "tests/*.py" is widened to
    # its containing directory rather than dropped -- under the new
    # default-deny base, dropping it makes the directory invisible (found
    # via T11: the test harness itself failed with "tests/ not found").
    capabilities = {"filesystem": {"read": ["/workspace/tests/*.py"]}}
    plan = build_mount_plan(capabilities, "/host/toy-app")
    tests_mounts = [m for m in plan if m.container_path == "/workspace/tests"]
    assert len(tests_mounts) == 1
    assert tests_mounts[0].mode == "ro"
    assert tests_mounts[0].host_path == "/host/toy-app/tests"
    assert tests_mounts[0].is_dir is True


def test_read_wildcard_with_no_directory_prefix_mounts_the_whole_repo():
    capabilities = {"filesystem": {"read": ["/workspace/*.json"]}}
    plan = build_mount_plan(capabilities, "/host/toy-app")
    root_mounts = [m for m in plan if m.container_path == "/workspace" and m.kind == "bind"]
    assert len(root_mounts) == 1
    assert root_mounts[0].mode == "ro"
    assert root_mounts[0].host_path == "/host/toy-app"


def test_read_recursive_subtree_glob_mounts_the_directory():
    # "src/**/*.js" (explicit recursive-subtree glob, T18's actual
    # predicted shape) widens the same way as a final-segment glob --
    # dropping it made a real source file invisible ("Cannot find module
    # '../src/parser'") even though the compiler clearly intended to
    # grant it.
    capabilities = {"filesystem": {"read": ["/workspace/src/**/*.js"]}}
    plan = build_mount_plan(capabilities, "/host/toy-app")
    src_mounts = [m for m in plan if m.container_path == "/workspace/src"]
    assert len(src_mounts) == 1
    assert src_mounts[0].mode == "ro"
    assert src_mounts[0].is_dir is True


def test_read_wildcard_outside_final_segment_stays_unsupported():
    capabilities = {"filesystem": {"read": ["/workspace/*/config.json"]}}
    plan = build_mount_plan(capabilities, "/host/toy-app")
    assert len(plan) == 1  # only the base tmpfs -- genuinely ambiguous, not guessed


def test_path_granted_neither_read_nor_write_has_no_mount_at_all():
    # The actual "no access" behavior the read/write/deny matrix requires:
    # a path outside every declared pattern isn't reachable through any
    # mount, not merely omitted from an allowlist that's checked
    # elsewhere.
    capabilities = {"filesystem": {"write": ["/workspace/src/**"]}}
    plan = build_mount_plan(capabilities, "/host/toy-app")
    assert not any(m.container_path.startswith("/workspace/private") for m in plan)
    assert not any(m.container_path.startswith("/workspace/README") for m in plan)


def test_read_write_deny_matrix():
    # The exact matrix from the v0.2.1 request:
    #   /workspace/**            READ
    #   /workspace/src/**        WRITE
    #   /workspace/private/**    DENY
    # expected:
    #   README.md            R yes  W no
    #   src/parser.js        R yes  W yes
    #   private/canary.txt   R no   W no
    capabilities = {
        "filesystem": {
            "read": ["/workspace/**"],
            "write": ["/workspace/src/**"],
            "deny": ["/workspace/private/**"],
        }
    }
    plan = build_mount_plan(capabilities, "/host/toy-app")

    # README.md: covered only by the broad read mount -- readable, not writable.
    read_root = [m for m in plan if m.container_path == "/workspace" and m.kind == "bind"]
    assert len(read_root) == 1 and read_root[0].mode == "ro"
    assert not any(m.container_path == "/workspace/README.md" for m in plan)

    # src/parser.js: the write mount on /workspace/src nests inside (and
    # therefore overrides, per mount stacking) the broad read mount --
    # readable AND writable.
    write_mounts = [m for m in plan if m.container_path == "/workspace/src"]
    assert len(write_mounts) == 1 and write_mounts[0].mode == "rw"

    # private/canary.txt: the deny mask nests inside (and overrides) the
    # broad read mount -- neither readable nor writable.
    deny_mounts = [m for m in plan if m.container_path == "/workspace/private"]
    assert len(deny_mounts) == 1
    assert deny_mounts[0].kind == "tmpfs" and deny_mounts[0].mode == "ro"

    # Mount order matters (last-applied wins for an overlapping path):
    # write and deny must come after the broad read mount they override.
    order = [m.container_path for m in plan]
    assert order.index("/workspace") < order.index("/workspace/src")
    assert order.index("/workspace") < order.index("/workspace/private")


def test_read_pattern_targeting_nonexistent_path_still_produces_a_plan_entry():
    # build_mount_plan itself is pure/IO-free -- it has no way to know
    # whether a host path exists, so it still emits a MountSpec for any
    # syntactically valid read pattern. Skipping a read target that
    # doesn't exist on the host is idhn.runtime.docker.write_filesystem_
    # override's job (it's the one doing host filesystem I/O), not this
    # module's.
    capabilities = {"filesystem": {"read": ["/workspace/does-not-exist.txt"]}}
    plan = build_mount_plan(capabilities, "/host/toy-app")
    assert any(m.container_path == "/workspace/does-not-exist.txt" and m.mode == "ro" for m in plan)
