"""Regression tests for run-20261005-64e5af: `idhn run --policy <path>`
was accepted and validated by the CLI, but the gateway crashed trying to
load `/policies/<path>` regardless of where <path> actually was, because
the old mechanism (_policy_relpath) fell back to the host's absolute
path for anything outside policies/, which still got /policies/
prepended by compose.yaml's own template. See
idhn.runtime.docker.gateway_manifest_mount's docstring for the full
root-cause writeup, including why an external manifest is COPIED into
run_dir rather than bind-mounted in place (a real Docker Desktop/WSL2
limitation, found by actually running it -- not a stylistic choice).
"""
from pathlib import Path

import yaml

from idhn.core.manifest import Manifest
from idhn.core.models import Task
from idhn.runtime.docker import (
    GATEWAY_STAGED_MANIFEST_NAME,
    REPO_ROOT,
    gateway_manifest_mount,
    write_filesystem_override,
)


def _manifest(source_path: Path | None) -> Manifest:
    return Manifest(
        version="0.1",
        task=Task(id="t", description="d"),
        capabilities={},
        source_path=source_path,
    )


def test_existing_policies_examples_manifest_unchanged(tmp_path):
    """The exact case docker/compose.yaml's base mount already covers --
    must produce the same relative path the old mechanism did, and must
    not touch run_dir at all (no staging needed).
    """
    path = REPO_ROOT / "policies" / "examples" / "coding-safe.yaml"
    container_path = gateway_manifest_mount(_manifest(path), tmp_path)

    assert container_path == "/policies/examples/coding-safe.yaml"
    assert list(tmp_path.iterdir()) == []


def test_materialized_manifest_under_runs_gets_staged_into_run_dir(tmp_path):
    """The exact failure from run-20261005-64e5af."""
    source_dir = tmp_path / "compile-20261004-340a00"
    source_dir.mkdir()
    source = source_dir / "materialized-manifest.yaml"
    source.write_text("version: '0.1'\nmarker: original\n", encoding="utf-8")

    run_dir = tmp_path / "run"
    container_path = gateway_manifest_mount(_manifest(source), run_dir)

    assert container_path == f"/run/{GATEWAY_STAGED_MANIFEST_NAME}"
    staged = run_dir / GATEWAY_STAGED_MANIFEST_NAME
    assert staged.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_staged_copy_is_read_only(tmp_path):
    source = tmp_path / "policy.yaml"
    source.write_text("version: '0.1'", encoding="utf-8")
    run_dir = tmp_path / "run"

    gateway_manifest_mount(_manifest(source), run_dir)

    staged = run_dir / GATEWAY_STAGED_MANIFEST_NAME
    assert not (staged.stat().st_mode & 0o222), "staged manifest copy must not be host-writable"


def test_original_file_is_never_modified(tmp_path):
    source = tmp_path / "policy.yaml"
    original_content = "version: '0.1'\n"
    source.write_text(original_content, encoding="utf-8")
    run_dir = tmp_path / "run"

    gateway_manifest_mount(_manifest(source), run_dir)

    assert source.read_text(encoding="utf-8") == original_content
    assert source.stat().st_mode & 0o200, "original host file must remain writable (untouched)"


def test_absolute_external_path_outside_the_repo_entirely(tmp_path):
    external_dir = tmp_path / "some-other-project" / "config"
    external_dir.mkdir(parents=True)
    source = external_dir / "policy.yaml"
    source.write_text("version: '0.1'", encoding="utf-8")
    run_dir = tmp_path / "run"

    container_path = gateway_manifest_mount(_manifest(source), run_dir)

    assert container_path == f"/run/{GATEWAY_STAGED_MANIFEST_NAME}"
    assert (run_dir / GATEWAY_STAGED_MANIFEST_NAME).exists()


def test_windows_style_separators_in_an_external_path_handled_correctly(tmp_path):
    """Path() normalizes '/' and '\\\\' the same way on Windows -- confirm
    a source path built with a literal backslash still resolves and
    copies correctly rather than erroring on a malformed path.
    """
    external_dir = tmp_path / "windows-style" / "nested"
    external_dir.mkdir(parents=True)
    source = external_dir / "materialized-manifest.yaml"
    source.write_text("version: '0.1'", encoding="utf-8")
    source_with_backslash = Path(str(external_dir) + "\\materialized-manifest.yaml")
    run_dir = tmp_path / "run"

    container_path = gateway_manifest_mount(_manifest(source_with_backslash), run_dir)

    assert container_path == f"/run/{GATEWAY_STAGED_MANIFEST_NAME}"
    assert "\\" not in container_path
    assert (run_dir / GATEWAY_STAGED_MANIFEST_NAME).exists()


def test_no_source_path_falls_back_to_the_documented_default(tmp_path):
    container_path = gateway_manifest_mount(_manifest(None), tmp_path)

    assert container_path == "/policies/default.yaml"
    assert list(tmp_path.iterdir()) == []


def test_nested_policies_subdirectory_relative_path_is_posix_separated(tmp_path):
    """A manifest two levels deep under policies/ (not just the
    examples/ convention) still produces a clean POSIX relative path."""
    path = REPO_ROOT / "policies" / "examples" / "network-isolation-test.yaml"
    container_path = gateway_manifest_mount(_manifest(path), tmp_path)

    assert container_path == "/policies/examples/network-isolation-test.yaml"
    assert list(tmp_path.iterdir()) == []


def test_override_file_never_adds_a_gateway_key(tmp_path):
    """Preserve existing behavior for policies under policies/, and for
    an external manifest too: the generated override must not touch the
    gateway service at all in either case -- an external manifest is
    staged into run_dir (already mounted at /run), not given a new
    Compose volume.
    """
    workspace_dir = REPO_ROOT / "lab" / "repos" / "toy-app"
    dummy_auth = tmp_path / "auth.json"
    dummy_auth.write_text("{}", encoding="utf-8")

    for manifest in (
        _manifest(REPO_ROOT / "policies" / "examples" / "coding-safe.yaml"),
        _manifest(tmp_path / "compile-run" / "materialized-manifest.yaml"),
    ):
        if manifest.source_path is not None and not manifest.source_path.parent.exists():
            manifest.source_path.parent.mkdir(parents=True)
            manifest.source_path.write_text("version: '0.1'", encoding="utf-8")

        run_dir = tmp_path / manifest.source_path.stem
        run_dir.mkdir()
        override_path = write_filesystem_override(manifest, run_dir, str(dummy_auth), workspace_dir)
        override = yaml.safe_load(override_path.read_text(encoding="utf-8"))

        assert "gateway" not in override["services"]
