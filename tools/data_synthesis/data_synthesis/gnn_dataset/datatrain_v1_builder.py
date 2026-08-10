"""Verifier and packager for the promoted frozen DataTrain v1 corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from data_synthesis.common.io import ensure_dir, load_json, save_json


SPLIT_FILES = {
    "train": "train.jsonl",
    "validation": "validation.jsonl",
    "test": "test.jsonl",
}

DEFAULT_FINAL_ID_PREFIXES = ("FULLMT-", "ANCHOR-", "0107-", "AUGMT-")


def build_datatrain_v1_from_config(config_path: str) -> Dict[str, Any]:
    """Rebuild the frozen package from a JSON configuration file."""

    config = load_json(config_path)
    base_dir = os.path.dirname(os.path.abspath(config_path))
    source_package_dir = _resolve(config["source_package_dir"], base_dir)
    output_dir = _resolve(config["output_dir"], base_dir)
    return build_datatrain_v1(
        source_package_dir=source_package_dir,
        output_dir=output_dir,
        overwrite=bool(config.get("overwrite", True)),
        expected_split_counts=config.get("expected_split_counts"),
        expected_category_counts=config.get("expected_category_counts"),
        expected_conversation_id_prefixes=config.get("expected_conversation_id_prefixes"),
    )


def build_datatrain_v1(
    *,
    source_package_dir: str,
    output_dir: str,
    overwrite: bool,
    expected_split_counts: Optional[Dict[str, int]] = None,
    expected_category_counts: Optional[Dict[str, Dict[str, int]]] = None,
    expected_conversation_id_prefixes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Copy, validate, and manifest an already promoted DataTrain package.

    This function does not synthesize new conversations. It preserves source
    split files, verifies identifiers and schemas, and records file hashes.
    """

    if not os.path.isdir(source_package_dir):
        raise FileNotFoundError(source_package_dir)
    if os.path.exists(output_dir):
        if not overwrite:
            raise FileExistsError(output_dir)
        shutil.rmtree(output_dir)
    ensure_dir(output_dir)

    for split, file_name in SPLIT_FILES.items():
        source = os.path.join(source_package_dir, file_name)
        if not os.path.exists(source):
            raise FileNotFoundError(source)
        shutil.copy2(source, os.path.join(output_dir, file_name))

    for file_name in (
        "README.md",
        "dataset_manifest.json",
        "dataset_manifest_0107_augmented_v3.source.json",
        "split_summary.json",
    ):
        source = os.path.join(source_package_dir, file_name)
        if os.path.exists(source):
            shutil.copy2(source, os.path.join(output_dir, file_name))

    report_src = os.path.join(source_package_dir, "validation_reports")
    if os.path.isdir(report_src):
        shutil.copytree(report_src, os.path.join(output_dir, "validation_reports"))

    validation = validate_package(
        output_dir,
        expected_conversation_id_prefixes=expected_conversation_id_prefixes
        or DEFAULT_FINAL_ID_PREFIXES,
    )
    if expected_split_counts and validation["split_counts"] != expected_split_counts:
        raise ValueError(f"Split counts mismatch: {validation['split_counts']} != {expected_split_counts}")
    if expected_category_counts and validation["category_counts"] != expected_category_counts:
        raise ValueError("Category counts mismatch.")

    generated_manifest = {
        "dataset_name": "intent_gnn_model_development_v1_rebuilt",
        "provenance": {
            "package_role": "frozen_promoted_gnn_training_package",
            "source_release": "intent_gnn_model_development_v1",
            "source_id_families": list(
                expected_conversation_id_prefixes or DEFAULT_FINAL_ID_PREFIXES
            ),
            "note": (
                "This builder verifies and repackages the promoted model-development corpus. "
                "It is intentionally separate from execution_v2.py, whose EXEC-* "
                "rows are a procedural development branch rather than the active "
                "FULLMT/ANCHOR/0107/AUGMT package."
            ),
        },
        "source_package_dir": source_package_dir,
        "output_dir": output_dir,
        "split_counts": validation["split_counts"],
        "category_counts": validation["category_counts"],
        "id_family_counts": validation["id_family_counts"],
        "files": validation["files"],
        "valid": validation["valid"],
    }
    save_json(os.path.join(output_dir, "rebuild_validation_report.json"), validation)
    save_json(os.path.join(output_dir, "rebuild_manifest.json"), generated_manifest)

    return {
        "output_dir": output_dir,
        "sample_count": sum(validation["split_counts"].values()),
        "valid": validation["valid"],
        "split_counts": validation["split_counts"],
        "category_counts": validation["category_counts"],
    }

def validate_package(
    package_dir: str,
    *,
    expected_conversation_id_prefixes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    split_counts: Dict[str, int] = {}
    category_counts: Dict[str, Dict[str, int]] = {}
    id_family_counts: Dict[str, Dict[str, int]] = {}
    files: Dict[str, Dict[str, Any]] = {}
    conversation_ids: Counter[str] = Counter()
    errors: List[Dict[str, Any]] = []
    expected_prefixes = tuple(expected_conversation_id_prefixes or ())

    for split, file_name in SPLIT_FILES.items():
        path = os.path.join(package_dir, file_name)
        rows = list(_read_jsonl(path))
        split_counts[split] = len(rows)
        category_counts[split] = dict(Counter(str(row.get("category")) for row in rows))
        id_family_counts[split] = dict(Counter(_id_family(str(row.get("conversation_id") or "")) for row in rows))
        files[file_name] = {
            "sha256": _sha256(path),
            "count": len(rows),
        }
        for row in rows:
            cid = str(row.get("conversation_id") or "")
            if not cid:
                errors.append({"split": split, "error": "missing_conversation_id"})
            elif expected_prefixes and not cid.startswith(expected_prefixes):
                errors.append(
                    {
                        "split": split,
                        "conversation_id": cid,
                        "error": "unexpected_conversation_id_family",
                        "expected_prefixes": list(expected_prefixes),
                    }
                )
            conversation_ids[cid] += 1
            _validate_record(row, split, errors)

    duplicate_ids = sorted(cid for cid, count in conversation_ids.items() if cid and count > 1)
    for cid in duplicate_ids[:100]:
        errors.append({"conversation_id": cid, "error": "duplicate_conversation_id"})

    return {
        "valid": not errors,
        "error_count": len(errors),
        "errors": errors[:200],
        "duplicate_conversation_ids": duplicate_ids,
        "split_counts": split_counts,
        "category_counts": category_counts,
        "id_family_counts": id_family_counts,
        "files": files,
    }


def _id_family(conversation_id: str) -> str:
    if conversation_id.startswith("FULLMT-"):
        return "FULLMT"
    if conversation_id.startswith("ANCHOR-"):
        return "ANCHOR"
    if conversation_id.startswith("0107-"):
        return "0107"
    if conversation_id.startswith("AUGMT-"):
        return "AUGMT"
    if conversation_id.startswith("EXEC-"):
        return "EXEC"
    return "OTHER"


def _validate_record(row: Dict[str, Any], split: str, errors: List[Dict[str, Any]]) -> None:
    cid = str(row.get("conversation_id") or "")
    for field in ("category", "role", "turns", "labels", "entity_seed"):
        if field not in row:
            errors.append({"split": split, "conversation_id": cid, "error": f"missing_field:{field}"})
    labels = row.get("labels") or {}
    for field in (
        "semantic_intent",
        "operation",
        "scope",
        "target_relation",
        "transition",
        "target_concepts",
        "security_transition",
        "reference_targets",
    ):
        if field not in labels:
            errors.append({"split": split, "conversation_id": cid, "error": f"missing_label:{field}"})
    turns = row.get("turns") or []
    if not turns:
        errors.append({"split": split, "conversation_id": cid, "error": "missing_turns"})
        return
    for turn in turns:
        if not str(turn.get("user_utterance") or turn.get("text") or turn.get("nlq") or ""):
            errors.append({"split": split, "conversation_id": cid, "error": "empty_turn_text"})


def _read_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _resolve(value: str, base_dir: str) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((Path(base_dir) / path).resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild a Datatrain_Final/v1-style package.")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    result = build_datatrain_v1_from_config(args.config)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
