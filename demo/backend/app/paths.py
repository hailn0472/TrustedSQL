"""Filesystem boundaries for the TrustedSQL-only demo."""

from pathlib import Path
from typing import Union

PathLike = Union[str, Path]


class IsolationBoundaryError(ValueError):
    """Raised when a path crosses the demo's filesystem boundary."""


def _repository_root(repository_root: PathLike) -> Path:
    return Path(repository_root).expanduser().resolve()


def _confined_path(base: Path, requested: PathLike, *, label: str) -> Path:
    candidate = Path(requested)
    if candidate.is_absolute():
        raise IsolationBoundaryError(f"{label} must be relative to its boundary")

    resolved = (base / candidate).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise IsolationBoundaryError(
            f"{label} must remain under {base}"
        ) from exc
    return resolved


def demo_root(repository_root: PathLike) -> Path:
    """Return the canonical root owned by this demo."""

    root = _repository_root(repository_root)
    boundary = (root / "demo").resolve()
    try:
        boundary.relative_to(root)
    except ValueError as exc:
        raise IsolationBoundaryError(
            f"demo root must remain under {root}"
        ) from exc
    return boundary


def _runs_root(repository_root: PathLike) -> Path:
    root = _repository_root(repository_root)
    demo_boundary = demo_root(root)
    boundary = (root / "demo" / "runs").resolve()
    try:
        boundary.relative_to(demo_boundary)
    except ValueError as exc:
        raise IsolationBoundaryError(
            f"runs root must remain under {demo_boundary}"
        ) from exc
    return boundary


def run_path(repository_root: PathLike, relative_path: PathLike) -> Path:
    """Resolve a generated config or artifact strictly below ``demo/runs``."""

    return _confined_path(_runs_root(repository_root), relative_path, label="run path")


def parent_resource_path(repository_root: PathLike, relative_path: PathLike) -> Path:
    """Resolve a parent resource for read-only use, never as demo output."""

    root = _repository_root(repository_root)
    resource = _confined_path(root, relative_path, label="parent resource path")
    try:
        resource.relative_to(demo_root(root))
    except ValueError:
        return resource
    raise IsolationBoundaryError("parent resources cannot be selected from demo/")


def output_path(repository_root: PathLike, relative_path: PathLike) -> Path:
    """Resolve an output path, allowing only generated files below ``demo/runs``."""

    return run_path(repository_root, relative_path)
