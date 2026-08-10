"""Sequence-level release packager for intent conversations."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from data_synthesis.common.io import ensure_dir, load_json, save_json


DEFAULT_SPLITS = ("train", "validation", "test")
SCHEMA_NAME = "method_v1_release_v2"


def build_release_v2_from_config(config_path: str) -> Dict[str, Any]:
    """Build a sequence-level release from a JSON configuration file."""

    config = load_json(config_path)
    base_dir = os.path.dirname(os.path.abspath(config_path))

    source_split_dir = _resolve(config.get("source_split_dir"), base_dir)
    source_conversations_jsonl = _resolve(config.get("source_conversations_jsonl"), base_dir)
    output_dir = _resolve(config["output_dir"], base_dir)
    split_names = tuple(config.get("split_names") or DEFAULT_SPLITS)

    return build_release_v2(
        output_dir=output_dir,
        source_split_dir=source_split_dir,
        source_conversations_jsonl=source_conversations_jsonl,
        split_names=split_names,
        source_name=str(config.get("source_name") or "gnn_method_v1_execution_phase7_splits"),
        schema=str(config.get("schema") or SCHEMA_NAME),
        overwrite=bool(config.get("overwrite", True)),
    )


def build_release_v2(
    *,
    output_dir: str,
    source_split_dir: Optional[str] = None,
    source_conversations_jsonl: Optional[str] = None,
    split_names: Sequence[str] = DEFAULT_SPLITS,
    source_name: str = "gnn_method_v1_execution_phase7_splits",
    schema: str = SCHEMA_NAME,
    overwrite: bool = True,
) -> Dict[str, Any]:
    """Convert conversation records into validated sequence-level samples."""

    if not source_split_dir and not source_conversations_jsonl:
        raise ValueError("Provide either source_split_dir or source_conversations_jsonl.")

    ensure_dir(output_dir)
    output_paths = {
        "samples": os.path.join(output_dir, "intent_samples.jsonl"),
        "split_manifest": os.path.join(output_dir, "split_manifest.json"),
        "summary": os.path.join(output_dir, "dataset_v2_summary.json"),
        "validation": os.path.join(output_dir, "dataset_v2_validation_report.json"),
        "override_validation": os.path.join(output_dir, "dataset_v2_override_validation_report.json"),
    }
    if not overwrite:
        existing = [path for path in output_paths.values() if os.path.exists(path)]
        if existing:
            raise FileExistsError(f"Output files already exist: {existing}")

    conversations, split_by_conversation = _load_sources(
        source_split_dir=source_split_dir,
        source_conversations_jsonl=source_conversations_jsonl,
        split_names=split_names,
    )
    samples = [_conversation_to_sample(record) for record in conversations]
    validation = validate_release_samples(samples)
    summary = build_release_summary(samples, split_by_conversation, schema=schema)
    split_manifest = {
        "source": source_name,
        "conversation_splits": split_by_conversation,
        "sample_splits": {
            sample["sample_id"]: split_by_conversation.get(sample["conversation_id"])
            for sample in samples
        },
        "split_counts": summary["split_counts"],
    }

    _write_jsonl(output_paths["samples"], samples)
    save_json(output_paths["split_manifest"], split_manifest)
    save_json(output_paths["summary"], summary)
    save_json(output_paths["validation"], validation)
    save_json(output_paths["override_validation"], {
        "valid": validation["valid"],
        "sample_count": validation["sample_count"],
        "error_count": validation["error_count"],
        "errors": validation["errors"],
    })

    return {
        "output_dir": output_dir,
        "sample_count": len(samples),
        "valid": validation["valid"],
        "errors": validation["errors"],
        "paths": output_paths,
        "summary": summary,
    }


def _load_sources(
    *,
    source_split_dir: Optional[str],
    source_conversations_jsonl: Optional[str],
    split_names: Sequence[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    conversations: List[Dict[str, Any]] = []
    split_by_conversation: Dict[str, str] = {}

    if source_split_dir:
        for split_name in split_names:
            path = os.path.join(source_split_dir, f"{split_name}.jsonl")
            if not os.path.exists(path):
                continue
            for record in _read_jsonl(path):
                cid = str(record.get("conversation_id") or "")
                if not cid:
                    raise ValueError(f"Missing conversation_id in {path}")
                conversations.append(record)
                split_by_conversation[cid] = split_name

    if source_conversations_jsonl:
        for record in _read_jsonl(source_conversations_jsonl):
            cid = str(record.get("conversation_id") or "")
            if not cid:
                raise ValueError(f"Missing conversation_id in {source_conversations_jsonl}")
            if cid not in split_by_conversation:
                split_by_conversation[cid] = "unsplit"
            if not any(str(existing.get("conversation_id")) == cid for existing in conversations):
                conversations.append(record)

    return conversations, split_by_conversation


def _conversation_to_sample(record: Dict[str, Any]) -> Dict[str, Any]:
    turns = list(record.get("turns") or [])
    if not turns:
        raise ValueError(f"Conversation has no turns: {record.get('conversation_id')}")
    current = turns[-1]
    current_turn_id = int(current.get("turn_id") or len(turns))
    labels = dict(record.get("labels") or {})
    labels["reference_targets"] = _normalize_reference_targets(
        labels.get("reference_targets") or [],
        current_turn_id=current_turn_id,
    )

    metadata = dict(record.get("generation_metadata") or {})
    metadata["extra"] = {
        **dict(metadata.get("extra") or {}),
        "dataset_schema": "conversation_v2",
        "category": record.get("category"),
        "mt_id": _mt_id(record),
        "micro_pattern_id": record.get("micro_pattern_id") or record.get("pattern_id"),
        "contrastive_pair_id": record.get("contrastive_pair_id"),
        "role": record.get("role"),
        "turn_count": len(turns),
        "scope": labels.get("scope"),
        "transition": labels.get("transition"),
        "security_transition": labels.get("security_transition"),
        "original_labels": dict(record.get("labels") or {}),
    }

    return {
        "sample_id": f"{record.get('conversation_id')}::turn_{current_turn_id}",
        "conversation_id": record.get("conversation_id"),
        "pattern_id": record.get("pattern_id") or record.get("micro_pattern_id"),
        "category": record.get("category"),
        "role": record.get("role"),
        "current_turn_id": current_turn_id,
        "history": [
            {
                "turn_id": int(turn.get("turn_id") or index),
                "text": _turn_text(turn),
                "predicted_or_gold_state": None,
                "mentions": list(turn.get("mentions") or []),
            }
            for index, turn in enumerate(turns[:-1], 1)
        ],
        "current_text": _turn_text(current),
        "current_mentions": list(current.get("mentions") or []),
        "labels": labels,
        "generation_metadata": metadata,
    }


def validate_release_samples(samples: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    errors: List[Dict[str, Any]] = []
    sample_ids = Counter(str(sample.get("sample_id")) for sample in samples)
    conversation_ids = Counter(str(sample.get("conversation_id")) for sample in samples)
    for sample in samples:
        sid = str(sample.get("sample_id") or "")
        if not sid:
            errors.append({"sample_id": sid, "error": "missing_sample_id"})
        if sample_ids[sid] > 1:
            errors.append({"sample_id": sid, "error": "duplicate_sample_id"})
        if not sample.get("conversation_id"):
            errors.append({"sample_id": sid, "error": "missing_conversation_id"})
        if conversation_ids[str(sample.get("conversation_id"))] > 1:
            errors.append({"sample_id": sid, "error": "duplicate_conversation_id"})
        if not sample.get("current_text"):
            errors.append({"sample_id": sid, "error": "missing_current_text"})
        labels = sample.get("labels") or {}
        for field in ("semantic_intent", "operation", "scope", "target_relation", "transition", "target_concepts", "security_transition"):
            if field not in labels:
                errors.append({"sample_id": sid, "error": f"missing_label:{field}"})
        current_turn_id = sample.get("current_turn_id")
        history_ids = {turn.get("turn_id") for turn in sample.get("history") or []}
        for ref in labels.get("reference_targets") or []:
            target_turn = ref.get("target_turn")
            if target_turn not in history_ids:
                errors.append({"sample_id": sid, "error": f"bad_reference_target:{target_turn}"})
            if current_turn_id is not None and target_turn is not None and int(target_turn) >= int(current_turn_id):
                errors.append({"sample_id": sid, "error": f"non_prior_reference:{target_turn}"})

    return {
        "valid": not errors,
        "sample_count": len(samples),
        "error_count": len(errors),
        "errors": errors[:100],
    }


def build_release_summary(
    samples: Sequence[Dict[str, Any]],
    split_by_conversation: Dict[str, str],
    *,
    schema: str,
) -> Dict[str, Any]:
    split_counts = Counter(
        split_by_conversation.get(str(sample.get("conversation_id")), "unsplit")
        for sample in samples
    )
    category_counts = Counter(str(sample.get("category")) for sample in samples)
    mt_counts = Counter(_mt_id(sample) for sample in samples)
    micro_pattern_counts = Counter(str(sample.get("pattern_id")) for sample in samples)
    split_category_counts: Dict[str, Dict[str, int]] = defaultdict(dict)
    split_mt_counts: Dict[str, Dict[str, int]] = defaultdict(dict)
    split_pattern_counts: Dict[str, Dict[str, int]] = defaultdict(dict)

    for split in sorted(split_counts):
        split_samples = [
            sample
            for sample in samples
            if split_by_conversation.get(str(sample.get("conversation_id")), "unsplit") == split
        ]
        split_category_counts[split] = dict(Counter(str(sample.get("category")) for sample in split_samples))
        split_mt_counts[split] = dict(Counter(_mt_id(sample) for sample in split_samples))
        split_pattern_counts[split] = dict(Counter(str(sample.get("pattern_id")) for sample in split_samples))

    return {
        "schema": schema,
        "sample_count": len(samples),
        "split_counts": dict(split_counts),
        "category_counts": dict(category_counts),
        "mt_counts": dict(mt_counts),
        "micro_pattern_count": len(micro_pattern_counts),
        "micro_pattern_counts": dict(micro_pattern_counts),
        "split_category_counts": dict(split_category_counts),
        "split_mt_counts": dict(split_mt_counts),
        "split_micro_pattern_counts": dict(split_pattern_counts),
    }


def _normalize_reference_targets(
    refs: Sequence[Dict[str, Any]],
    *,
    current_turn_id: int,
) -> List[Dict[str, Any]]:
    normalized = []
    for ref in refs:
        target_turn = ref.get("target_turn", ref.get("to_turn"))
        if target_turn is None and ref.get("from_turn") == current_turn_id:
            target_turn = max(1, current_turn_id - 1)
        if target_turn is None:
            continue
        normalized.append(
            {
                "target_turn": int(target_turn),
                "target_concept": ref.get("target_concept"),
                "surface": ref.get("surface"),
            }
        )
    return normalized


def _mt_id(record: Dict[str, Any]) -> str:
    metadata = (record.get("generation_metadata") or {}).get("extra") or {}
    if metadata.get("mt_id"):
        return str(metadata["mt_id"])
    if record.get("anchor_id"):
        return "ANCHOR"
    pattern_id = str(record.get("pattern_id") or record.get("micro_pattern_id") or "")
    if pattern_id.startswith("MT") and "_" in pattern_id:
        return pattern_id[:2] + "-" + pattern_id[2:4]
    if pattern_id.startswith("BST_"):
        return "ANCHOR"
    return str(record.get("category") or "UNKNOWN")


def _turn_text(turn: Dict[str, Any]) -> str:
    return str(turn.get("text") or turn.get("user_utterance") or turn.get("nlq") or "")


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


def _write_jsonl(path: str, rows: Iterable[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _resolve(value: Optional[str], base_dir: str) -> Optional[str]:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((Path(base_dir) / path).resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description="Build method_v1 release-v2 intent samples from conversation JSONL.")
    parser.add_argument("--config", required=True, help="Path to release_v2_final JSON config.")
    args = parser.parse_args()
    result = build_release_v2_from_config(args.config)
    print(json.dumps({
        "output_dir": result["output_dir"],
        "sample_count": result["sample_count"],
        "valid": result["valid"],
        "error_count": len(result["errors"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
