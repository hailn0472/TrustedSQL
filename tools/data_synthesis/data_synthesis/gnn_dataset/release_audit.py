from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from data_synthesis.common.io import save_json

from .policy_compiler import CompiledPolicyBundle


def build_release_coverage(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    dimensions = {
        "dataset_family": Counter(str(record.get("dataset_family")) for record in records),
        "pattern_id": Counter(str(record.get("pattern_id")) for record in records),
        "primary_type": Counter(str(record.get("primary_type")) for record in records),
        "role": Counter(str(record.get("role")) for record in records),
        "policy_ref": Counter(
            str((record.get("security_boundary") or {}).get("policy_ref"))
            for record in records
        ),
        "scope_type": Counter(
            str((record.get("security_boundary") or {}).get("scope_type"))
            for record in records
        ),
        "primary_violation": Counter(
            str((record.get("security_boundary") or {}).get("primary_violation"))
            for record in records
        ),
        "target_table": Counter(
            str(table)
            for record in records
            for table in (record.get("security_boundary") or {}).get("target_tables") or []
        ),
    }
    return {
        "total_records": len(records),
        **{
            name: dict(sorted(counter.items()))
            for name, counter in dimensions.items()
        },
    }


def create_group_aware_splits(
    records: Sequence[Dict[str, Any]],
    *,
    seed: int = 20260606,
) -> Dict[str, Any]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(record.get("user_context_id"))].append(record)

    target_ratios = {"train": 0.8, "validation": 0.1, "test": 0.1}
    assignments: Dict[str, str] = {}
    counts = {name: 0 for name in target_ratios}
    total = max(len(records), 1)
    ordered_groups = sorted(
        groups.items(),
        key=lambda item: (
            -len(item[1]),
            _stable_hash(f"{seed}:{item[0]}"),
        ),
    )
    bootstrap_order = ["train", "test", "validation"]
    for group_index, (group_id, group_records) in enumerate(ordered_groups):
        if group_index < len(bootstrap_order):
            split = bootstrap_order[group_index]
        else:
            split = min(
                target_ratios,
                key=lambda name: (
                    counts[name] / total - target_ratios[name],
                    counts[name],
                    name,
                ),
            )
        assignments[group_id] = split
        counts[split] += len(group_records)

    split_records = {
        split: [
            record
            for record in records
            if assignments.get(str(record.get("user_context_id"))) == split
        ]
        for split in target_ratios
    }
    user_sets = {
        split: {str(record.get("user_context_id")) for record in values}
        for split, values in split_records.items()
    }
    overlaps = {
        f"{left}_{right}": sorted(user_sets[left].intersection(user_sets[right]))
        for left, right in (("train", "validation"), ("train", "test"), ("validation", "test"))
    }
    warnings: List[str] = []
    empty = [split for split, values in split_records.items() if not values]
    if empty:
        warnings.append(
            "Insufficient distinct authenticated user contexts for three non-empty "
            f"user-disjoint splits; empty splits: {empty}."
        )
    return {
        "strategy": "authenticated_user_group_holdout",
        "seed": seed,
        "target_ratios": target_ratios,
        "group_count": len(groups),
        "assignments": assignments,
        "counts": counts,
        "user_context_overlaps": overlaps,
        "ok": not any(overlaps.values()),
        "warnings": warnings,
        "splits": {
            split: [record.get("id") for record in values]
            for split, values in split_records.items()
        },
    }


def export_split_jsonl(
    records: Sequence[Dict[str, Any]],
    split_report: Dict[str, Any],
    output_dir: str,
) -> Dict[str, str]:
    by_id = {str(record.get("id")): record for record in records}
    paths: Dict[str, str] = {}
    for split, ids in (split_report.get("splits") or {}).items():
        path = os.path.join(output_dir, f"GNN_{split.title()}.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for record_id in ids:
                handle.write(json.dumps(by_id[str(record_id)], ensure_ascii=False) + "\n")
        paths[split] = path
    return paths


def export_stratified_human_review(
    records: Sequence[Dict[str, Any]],
    *,
    csv_path: str,
    xlsx_path: str,
    ratio: float = 0.2,
) -> Dict[str, Any]:
    by_pattern: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_pattern[str(record.get("pattern_id"))].append(record)
    selected: List[Dict[str, Any]] = []
    for pattern_id in sorted(by_pattern):
        group = sorted(by_pattern[pattern_id], key=lambda record: str(record.get("id")))
        take = min(len(group), max(1, int(math.ceil(len(group) * ratio))))
        selected.extend(group[:take])

    rows = [_human_review_row(record) for record in selected]
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["record_id"])
        writer.writeheader()
        writer.writerows(rows)
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="Stratified_Review")
        pd.DataFrame(
            [
                {"field": "human_decision", "instruction": "PASS or REJECT"},
                {"field": "human_primary_type", "instruction": "Confirmed primary type"},
                {"field": "human_notes", "instruction": "Reviewer evidence and correction notes"},
                {"field": "review_scope", "instruction": "20% per pattern, minimum one record"},
            ]
        ).to_excel(writer, index=False, sheet_name="Instructions")
    return {
        "ratio": ratio,
        "selected_count": len(selected),
        "total_records": len(records),
        "pattern_count": len(by_pattern),
        "csv_path": csv_path,
        "xlsx_path": xlsx_path,
    }


def write_release_manifest(
    path: str,
    *,
    policy_bundle: CompiledPolicyBundle,
    pattern_bank_path: str,
    prompt_path: str,
    model: str,
    seed: int,
    final_records: Sequence[Dict[str, Any]],
    split_report: Dict[str, Any],
    protocol_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = {
        "pipeline": "gnn_policy_grounded_v2",
        "model": model,
        "seed": seed,
        "record_count": len(final_records),
        "policy_compiler": policy_bundle.manifest_fragment(),
        "source_hashes_sha256": {
            "pattern_bank": _file_hash(pattern_bank_path),
            "prompts": _file_hash(prompt_path),
        },
        "split_strategy": {
            key: split_report.get(key)
            for key in (
                "strategy",
                "seed",
                "target_ratios",
                "group_count",
                "counts",
                "user_context_overlaps",
                "warnings",
            )
        },
        "scientific_protocols": protocol_status or {},
    }
    save_json(path, payload)
    return payload


def _human_review_row(record: Dict[str, Any]) -> Dict[str, Any]:
    boundary = record.get("security_boundary") or {}
    return {
        "record_id": record.get("id"),
        "dataset_family": record.get("dataset_family"),
        "pattern_id": record.get("pattern_id"),
        "primary_type": record.get("primary_type"),
        "role": record.get("role"),
        "user_context_id": record.get("user_context_id"),
        "policy_ref": boundary.get("policy_ref"),
        "scope_type": boundary.get("scope_type"),
        "primary_violation": boundary.get("primary_violation"),
        "secondary_violations": json.dumps(boundary.get("secondary_violations") or [], ensure_ascii=False),
        "target_tables": json.dumps(boundary.get("target_tables") or [], ensure_ascii=False),
        "target_columns": json.dumps(boundary.get("target_columns") or [], ensure_ascii=False),
        "forbidden_subject": boundary.get("forbidden_subject"),
        "turns": json.dumps(record.get("turns") or [], ensure_ascii=False),
        "malicious_reason_nodes": json.dumps(
            (record.get("graph_evidence") or {}).get("malicious_reason_nodes") or [],
            ensure_ascii=False,
        ),
        "human_decision": "",
        "human_primary_type": "",
        "human_notes": "",
    }


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_hash(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
