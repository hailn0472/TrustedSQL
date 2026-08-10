from __future__ import annotations

import argparse
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, TextIO

from trustedsql.config import load_config


@contextmanager
def runtime_run_lock(run_dir: Path) -> Iterator[None]:
    run_dir.mkdir(parents=True, exist_ok=True)
    handle: TextIO = (run_dir / ".runtime.lock").open("a+", encoding="utf-8")
    try:
        handle.seek(0)
        if not handle.read(1):
            handle.write("0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except OSError as exc:
        raise RuntimeError(f"Another runtime process is writing run_id {run_dir.name}.") from exc
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TrustedSQL runtime")
    parser.add_argument("--project-root", default=os.environ.get("TRUSTEDSQL_PROJECT_ROOT"))
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Execute runtime only")
    run.add_argument("--run-id", default=None)
    run.add_argument("--config-dir", default="configs")
    run.add_argument("--datasets-file", default=None)
    run.add_argument("--modules-file", default=None)
    run.add_argument("--settings", nargs="*", default=None)
    run.add_argument("--max-samples", type=int, default=None)
    run.add_argument("--max-workers", type=int, default=None)
    run.add_argument("--no-resume", action="store_true")
    run.add_argument("--rerun-api-429", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.project_root).resolve() if args.project_root else Path(__file__).resolve().parents[2]
    config_dir = _resolve(root, args.config_dir)
    datasets_file = _resolve(root, args.datasets_file) if args.datasets_file else None
    modules_file = _resolve(root, args.modules_file) if args.modules_file else None
    config = load_config(config_dir, datasets_file=datasets_file, modules_file=modules_file, project_root=root)
    if args.max_workers is not None:
        config.raw.setdefault("runtime", {}).setdefault("parallel", {})["max_workers"] = max(1, args.max_workers)
    run_id = args.run_id or config.raw.get("run", {}).get("run_id") or f"trustedsql_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir = config.output_dir / run_id
    raw_output = run_dir / "runtime" / "raw_turn_outputs.jsonl"
    if args.no_resume and raw_output.exists() and raw_output.stat().st_size:
        raise RuntimeError(f"Refusing --no-resume for existing run_id {run_id}; choose a new run_id.")
    from trustedsql.runtime.runner import MethodRunner

    with runtime_run_lock(run_dir):
        summary = MethodRunner(config, run_id).run(
            selected_settings=args.settings,
            max_samples=args.max_samples,
            resume=not args.no_resume,
            rerun_api_429=args.rerun_api_429,
        )
    print(
        "Runtime error summary: "
        f"errors={summary.get('runtime_error_count', 0)}, "
        f"api_429={summary.get('api_429_error_count', 0)}, "
        f"api_429_retry_events={summary.get('api_429_retry_event_count', 0)}"
    )
    print(f"TrustedSQL runtime completed: {run_dir}")
    return 0


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
