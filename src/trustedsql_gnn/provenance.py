from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from trustedsql_gnn.paths import GNNPaths


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def canonical_json_sha256(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).hexdigest().upper()


def promote_checkpoint(
    *,
    project_root: str | Path,
    checkpoint_path: str | Path,
    confirmed_by: str,
    evaluation_report: str | Path | None = None,
) -> dict[str, Any]:
    import torch

    paths = GNNPaths.from_project_root(project_root)
    source = Path(checkpoint_path).resolve()
    destination = paths.checkpoint_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source != destination:
        shutil.copy2(source, destination)

    checkpoint = torch.load(destination, map_location="cpu", weights_only=False)
    data_manifest_path = paths.training_data_dir / "dataset_manifest.json"
    data_manifest = (
        json.loads(data_manifest_path.read_text(encoding="utf-8-sig"))
        if data_manifest_path.exists()
        else None
    )
    encoder_manifest_path = destination.parent / "encoder_manifest.json"
    encoder_manifest = json.loads(encoder_manifest_path.read_text(encoding="utf-8-sig"))
    origin_config_path = destination.parent / "training_config.origin.json"
    report = None
    if evaluation_report:
        report = json.loads(Path(evaluation_report).read_text(encoding="utf-8-sig"))

    config_files = [
        "training_config.json",
        "graph_config.json",
        "intent_taxonomy_v1.json",
        "concept_catalog_v1.json",
        "legacy_intent_mapping_v1.json",
    ]
    manifest = {
        "schema_version": "trustedsql_intent_gnn_model_v1",
        "model_id": "trustedsql-intent-gnn-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "checkpoint": {
            "path": "artifacts/models/intent_gnn/v1/best.pt",
            "sha256": sha256_file(destination),
            "best_epoch": checkpoint.get("best_epoch", "not_recorded"),
            "model_config": checkpoint.get("model_config", {}),
        },
        "source": {
            "training_git_commit": "not_recorded",
            "manifest_generation_git_commit": _git_commit(paths.project_root),
            "training_author_confirmation": confirmed_by,
            "training_history": "not_recorded",
        },
        "training_data": _training_data_manifest(data_manifest_path, data_manifest),
        "configuration_hashes": {
            name: sha256_file(paths.config_dir / name) for name in config_files
        },
        "checkpoint_origin": {
            "training_config_path": "artifacts/models/intent_gnn/v1/training_config.origin.json",
            "training_config_file_sha256": sha256_file(origin_config_path),
            "training_config_canonical_json_sha256": canonical_json_sha256(origin_config_path),
            "note": "Semantic JSON snapshot supplied with the confirmed checkpoint; line endings are not provenance-bearing.",
        },
        "encoder": encoder_manifest,
        "metrics": {
            "training_recorded_validation": checkpoint.get(
                "validation_metrics", "not_recorded"
            ),
            "post_migration_evaluation": (
                {
                    "report_path": _relative_or_not_recorded(
                        Path(evaluation_report), paths.project_root
                    ),
                    "report_sha256": sha256_file(Path(evaluation_report)),
                    "validation": report.get("validation_metrics", "not_recorded"),
                    "test": report.get("test_metrics", "not_recorded"),
                    "note": report.get("note", "not_recorded"),
                }
                if report
                else "not_recorded"
            ),
        },
        "provenance_note": (
            "The training author confirmed this checkpoint belongs to best_final. "
            "Unavailable training-history fields are explicitly marked not_recorded."
        ),
    }
    output_path = destination.parent / "model_manifest.json"
    output_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def _training_data_manifest(path: Path, data_manifest: dict[str, Any] | None) -> dict[str, Any]:
    if data_manifest is None:
        return {
            "manifest_path": "not_included_runtime_only",
            "manifest_sha256": "not_included_runtime_only",
            "version": "not_included_runtime_only",
            "split_hashes": {},
        }
    return {
        "manifest_path": "data/training/intent_gnn/v1/dataset_manifest.json",
        "manifest_sha256": sha256_file(path),
        "version": data_manifest.get("version"),
        "split_hashes": {
            name: payload.get("sha256")
            for name, payload in data_manifest.get("splits", {}).items()
        },
    }


def _git_commit(project_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "not_recorded"


def _relative_or_not_recorded(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return "not_recorded"
