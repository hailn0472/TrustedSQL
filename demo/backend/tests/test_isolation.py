from pathlib import Path

import pytest

from demo.backend.app.paths import (
    IsolationBoundaryError,
    demo_root,
    output_path,
    parent_resource_path,
    run_path,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_demo_root_resolves_inside_repository_demo_directory():
    assert demo_root(REPOSITORY_ROOT) == (REPOSITORY_ROOT / "demo").resolve()


def test_run_paths_are_confined_to_demo_runs():
    path = run_path(REPOSITORY_ROOT, "2026-08-23/config.json")

    assert path == (REPOSITORY_ROOT / "demo/runs/2026-08-23/config.json").resolve()
    assert path.is_relative_to((REPOSITORY_ROOT / "demo/runs").resolve())


def test_run_paths_reject_traversal_and_absolute_paths():
    with pytest.raises(IsolationBoundaryError):
        run_path(REPOSITORY_ROOT, "../outside.json")

    with pytest.raises(IsolationBoundaryError):
        run_path(REPOSITORY_ROOT, "/tmp/outside.json")


def test_run_paths_reject_symlinked_runs_boundary(tmp_path):
    repository_root = tmp_path / "repository"
    (repository_root / "demo").mkdir(parents=True)
    external_runs = tmp_path / "external-runs"
    external_runs.mkdir()
    (repository_root / "demo" / "runs").symlink_to(
        external_runs, target_is_directory=True
    )

    with pytest.raises(IsolationBoundaryError):
        run_path(repository_root, "generated.json")


def test_parent_resources_are_read_only_and_cannot_be_output_paths():
    source = parent_resource_path(REPOSITORY_ROOT, "configs/example.yaml")

    assert source == (REPOSITORY_ROOT / "configs/example.yaml").resolve()

    with pytest.raises(IsolationBoundaryError):
        output_path(REPOSITORY_ROOT, source)

    with pytest.raises(IsolationBoundaryError):
        parent_resource_path(REPOSITORY_ROOT, "demo/runs/generated.json")
