from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from trustedsql_gnn.data.v2 import prepare_v2_release
from trustedsql_gnn.paths import GNNPaths
from trustedsql_gnn.provenance import promote_checkpoint
from trustedsql_gnn.taxonomy import IntentTaxonomy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TrustedSQL intent-GNN lifecycle CLI")
    parser.add_argument("--project-root", default=os.environ.get("TRUSTEDSQL_PROJECT_ROOT"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--run-id", default=None)
    train = sub.add_parser("train")
    train.add_argument("--run-id", default=None)
    train.add_argument("--device", default="cuda")
    train.add_argument("--epochs", type=int, default=None)
    train.add_argument("--seed", type=int, default=None)
    train.add_argument("--sampling-mode", choices=["shuffle", "family_micro_balanced"], default="shuffle")
    train.add_argument("--allow-hash-encoder", action="store_true")
    train.add_argument("--smoke-samples", type=int, default=None)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--run-id", default=None)
    evaluate.add_argument("--checkpoint", default=None)
    evaluate.add_argument("--device", default="cpu")
    promote = sub.add_parser("promote")
    promote.add_argument("--checkpoint", required=True)
    promote.add_argument("--confirmed-by", required=True)
    promote.add_argument("--evaluation-report", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.project_root).resolve() if args.project_root else Path(__file__).resolve().parents[2]
    paths = GNNPaths.from_project_root(root)
    if args.command == "inspect":
        payload = inspect_dataset(paths.training_data_dir)
    elif args.command == "prepare":
        run_dir = _run_dir(paths, args.run_id)
        payload = prepare_release(paths, run_dir / "prepared")
    elif args.command == "train":
        from trustedsql_gnn.training.runner import TrainingRunner

        run_dir = _run_dir(paths, args.run_id)
        release_dir = run_dir / "prepared"
        if not (release_dir / "intent_samples.jsonl").exists():
            prepare_release(paths, release_dir)
        runner = TrainingRunner(
            root=root,
            release_dir=release_dir,
            output_dir=run_dir,
            device=args.device,
            allow_hash_encoder=args.allow_hash_encoder,
        )
        payload = runner.train(
            epochs=args.epochs,
            seed=args.seed,
            checkpoint_name="candidate.pt",
            report_name="training_report.json",
            sampling_mode=args.sampling_mode,
            sample_limit=args.smoke_samples,
        )
    elif args.command == "evaluate":
        from trustedsql_gnn.training.runner import TrainingRunner

        run_dir = _run_dir(paths, args.run_id)
        release_dir = run_dir / "prepared"
        if not (release_dir / "intent_samples.jsonl").exists():
            prepare_release(paths, release_dir)
        runner = TrainingRunner(
            root=root,
            release_dir=release_dir,
            output_dir=run_dir,
            device=args.device,
        )
        payload = runner.evaluate_checkpoint(args.checkpoint or paths.checkpoint_path)
    elif args.command == "promote":
        payload = promote_checkpoint(
            project_root=root,
            checkpoint_path=args.checkpoint,
            confirmed_by=args.confirmed_by,
            evaluation_report=args.evaluation_report,
        )
    else:
        raise AssertionError(args.command)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def inspect_dataset(data_dir: Path) -> dict[str, Any]:
    files = {
        "train": "train.jsonl",
        "validation": "validation.jsonl",
        "test": "test.jsonl",
    }
    splits: dict[str, Any] = {}
    for name, filename in files.items():
        rows = [
            json.loads(line)
            for line in (data_dir / filename).read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
        splits[name] = {
            "count": len(rows),
            "category_counts": dict(Counter(row.get("category", "UNKNOWN") for row in rows)),
        }
    return {"data_dir": str(data_dir), "splits": splits, "total": sum(v["count"] for v in splits.values())}


def prepare_release(paths: GNNPaths, output_dir: Path) -> dict[str, Any]:
    taxonomy = IntentTaxonomy.load(paths.config_dir / "intent_taxonomy_v1.json")
    concept_payload = json.loads(
        (paths.config_dir / "concept_catalog_v1.json").read_text(encoding="utf-8")
    )
    return prepare_v2_release(
        split_dir=paths.training_data_dir,
        output_dir=output_dir,
        taxonomy=taxonomy,
        known_concepts=set(concept_payload["concepts"]),
    )


def _run_dir(paths: GNNPaths, run_id: str | None) -> Path:
    selected = run_id or f"gnn_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    path = paths.training_output_dir / selected
    path.mkdir(parents=True, exist_ok=True)
    return path


if __name__ == "__main__":
    raise SystemExit(main())
