from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from trustedsql.config import load_config
from benchmark_eval.pipeline import evaluate_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TrustedSQL automatic evaluator")
    parser.add_argument("--project-root", default=os.environ.get("TRUSTEDSQL_PROJECT_ROOT"))
    sub = parser.add_subparsers(dest="command", required=True)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--run-id", required=True)
    evaluate.add_argument("--config-dir", default="configs")
    evaluate.add_argument("--datasets-file", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.project_root).resolve() if args.project_root else Path(__file__).resolve().parents[2]
    config_dir = (root / args.config_dir).resolve() if not Path(args.config_dir).is_absolute() else Path(args.config_dir)
    datasets_file = None
    if args.datasets_file:
        datasets_file = Path(args.datasets_file)
        if not datasets_file.is_absolute():
            datasets_file = root / datasets_file
    config = load_config(config_dir, datasets_file=datasets_file, project_root=root)
    metrics = evaluate_run(config, args.run_id)
    _record_invocation(config.output_dir / args.run_id, root)
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    return 0


def _record_invocation(run_dir: Path, evaluation_root: Path) -> None:
    manifest_path = run_dir / "run_manifest.json"
    legacy_snapshot_path = run_dir / "run_config_snapshot.json"
    if manifest_path.exists():
        snapshot = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        protocol = snapshot.get("protocol") or "architecture-ablation-v1"
    elif legacy_snapshot_path.exists():
        snapshot = json.loads(legacy_snapshot_path.read_text(encoding="utf-8-sig"))
        protocol = snapshot.get("protocol")
    else:
        raise RuntimeError(f"Runtime manifest is required under: {run_dir}")
    event = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "phase": "automatic_evaluation",
        "evaluation_project_root": str(evaluation_root),
        "run_manifest_validated": True,
        "protocol": protocol,
    }
    path = run_dir / "evaluation_invocations.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())

