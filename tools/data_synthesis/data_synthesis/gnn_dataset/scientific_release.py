from __future__ import annotations

import json
import os
import shutil
from collections import Counter
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from data_synthesis.common.io import ensure_dir, load_json, save_json

from .graph_export import record_to_feature_graph, record_to_target


def export_metric_protocol(
    records: Sequence[Dict[str, Any]],
    *,
    protocol: str,
    status: Dict[str, Any],
    output_dir: str,
) -> Dict[str, Any]:
    protocol_dir = os.path.join(output_dir, "protocols", protocol)
    ensure_dir(protocol_dir)
    if status.get("status") != "READY":
        marker = {
            "protocol": protocol,
            "status": "UNAVAILABLE",
            "reason": status.get("reason"),
        }
        save_json(os.path.join(protocol_dir, "UNAVAILABLE.json"), marker)
        return marker

    counts = Counter()
    for split in ("train", "validation", "test"):
        selected = [
            record
            for record in records
            if ((record.get("protocol_assignments") or {}).get(protocol) or {}).get("split") == split
        ]
        counts[split] = len(selected)
        _write_jsonl(
            [record_to_feature_graph(record) for record in selected],
            os.path.join(protocol_dir, f"{split}_graphs.jsonl"),
        )
        _write_jsonl(
            [record_to_target(record) for record in selected],
            os.path.join(protocol_dir, f"{split}_targets.jsonl"),
        )
        _write_jsonl(
            [
                {
                    "id": record.get("id"),
                    "graph_id": f"GRAPH-{record.get('id')}",
                    "protocol_assignment": (record.get("protocol_assignments") or {}).get(protocol),
                }
                for record in selected
            ],
            os.path.join(protocol_dir, f"{split}_index.jsonl"),
        )
    payload = {
        "protocol": protocol,
        "status": "READY",
        "counts": dict(counts),
        "directory": protocol_dir,
    }
    save_json(os.path.join(protocol_dir, "manifest.json"), payload)
    return payload


def create_human_external_package(
    *,
    output_dir: str,
    policy_manifest: Dict[str, Any],
    policy_source_dir: Optional[str] = None,
    user_contexts: Optional[Sequence[Dict[str, Any]]] = None,
    minimum_records: int = 200,
) -> Dict[str, Any]:
    package_dir = os.path.join(output_dir, "protocols", "human_external")
    ensure_dir(package_dir)
    authoring_columns = [
        "external_id",
        "role",
        "user_context_id",
        "turns_json",
        "annotator_1_label",
        "annotator_1_primary_type",
        "annotator_1_reason",
        "annotator_2_label",
        "annotator_2_primary_type",
        "annotator_2_reason",
        "adjudicated_label",
        "adjudicated_primary_type",
        "adjudication_reason",
    ]
    authoring_path = os.path.join(package_dir, "Human_External_Authoring.xlsx")
    with pd.ExcelWriter(authoring_path, engine="openpyxl") as writer:
        pd.DataFrame(columns=authoring_columns).to_excel(
            writer,
            index=False,
            sheet_name="Independent_Annotations",
        )
        pd.DataFrame(
            [
                {
                    "rule": "Blind authoring",
                    "instruction": "Do not inspect Gemini-generated records before writing or labeling.",
                },
                {
                    "rule": "Two annotators",
                    "instruction": "Annotators label independently before adjudication.",
                },
                {
                    "rule": "Minimum size",
                    "instruction": f"At least {minimum_records} fully adjudicated records are required.",
                },
                {
                    "rule": "No training use",
                    "instruction": "This protocol is evaluation-only and must not be used for prompt tuning or training.",
                },
            ]
        ).to_excel(writer, index=False, sheet_name="Rubric")
    adjudication_path = os.path.join(package_dir, "Human_External_Adjudication.xlsx")
    with pd.ExcelWriter(adjudication_path, engine="openpyxl") as writer:
        pd.DataFrame(
            columns=[
                "external_id",
                "annotator_1_label",
                "annotator_2_label",
                "adjudicated_label",
                "adjudicated_primary_type",
                "adjudication_reason",
                "adjudicator_id",
            ]
        ).to_excel(writer, index=False, sheet_name="Adjudication")
    schema = {
        "type": "array",
        "minItems": minimum_records,
        "required_record_fields": authoring_columns,
        "allowed_labels": ["BENIGN", "MALICIOUS"],
        "notes": "Both independent annotations and adjudication are mandatory.",
    }
    save_json(os.path.join(package_dir, "Human_External_Import_Schema.json"), schema)
    save_json(os.path.join(package_dir, "Policy_Context_Manifest.json"), policy_manifest)
    save_json(
        os.path.join(package_dir, "Blind_User_Context_Package.json"),
        {"contexts": list(user_contexts or [])},
    )
    if policy_source_dir:
        for filename in ("ddl_upgrade.txt", "policy_index.json", "role_access_matrix.json"):
            source = os.path.join(policy_source_dir, filename)
            if os.path.exists(source):
                shutil.copy2(source, os.path.join(package_dir, filename))
    status = {
        "protocol": "human_external",
        "status": "UNAVAILABLE",
        "reason": "awaiting_independent_human_authored_import",
        "minimum_records": minimum_records,
        "required_annotators": 2,
        "package_dir": package_dir,
        "authoring_workbook": authoring_path,
        "adjudication_workbook": adjudication_path,
    }
    save_json(os.path.join(package_dir, "UNAVAILABLE.json"), status)
    return status


def load_human_external_rows(path: str) -> List[Dict[str, Any]]:
    return _load_human_rows(path)


def validate_human_external_import(
    path: str,
    *,
    synthetic_records: Sequence[Dict[str, Any]],
    minimum_records: int = 200,
) -> Dict[str, Any]:
    rows = _load_human_rows(path)
    errors: List[str] = []
    if len(rows) < minimum_records:
        errors.append(f"requires at least {minimum_records} records, got {len(rows)}")
    agreements = 0
    disagreements = 0
    label_pairs: List[tuple[str, str]] = []
    synthetic_texts = [_record_text(record) for record in synthetic_records]
    duplicate_hits: List[str] = []
    for index, row in enumerate(rows, 1):
        record_id = str(row.get("external_id") or f"row_{index}")
        label_1 = str(row.get("annotator_1_label") or "").upper()
        label_2 = str(row.get("annotator_2_label") or "").upper()
        adjudicated = str(row.get("adjudicated_label") or "").upper()
        if label_1 not in {"BENIGN", "MALICIOUS"} or label_2 not in {"BENIGN", "MALICIOUS"}:
            errors.append(f"{record_id}: both independent labels are required")
            continue
        if adjudicated not in {"BENIGN", "MALICIOUS"}:
            errors.append(f"{record_id}: adjudicated_label is required")
        label_pairs.append((label_1, label_2))
        if label_1 == label_2:
            agreements += 1
        else:
            disagreements += 1
            if not str(row.get("adjudication_reason") or "").strip():
                errors.append(f"{record_id}: disagreement requires adjudication_reason")
        text = _human_row_text(row)
        if any(SequenceMatcher(None, text, candidate).ratio() >= 0.96 for candidate in synthetic_texts):
            duplicate_hits.append(record_id)
    if duplicate_hits:
        errors.append(f"semantic duplicates with synthetic dataset: {duplicate_hits[:20]}")
    agreement = agreements / len(label_pairs) if label_pairs else 0.0
    kappa = _cohen_kappa(label_pairs)
    return {
        "status": "READY" if not errors else "UNAVAILABLE",
        "ok": not errors,
        "errors": errors,
        "record_count": len(rows),
        "agreement_count": agreements,
        "disagreement_count": disagreements,
        "raw_agreement": agreement,
        "cohen_kappa": kappa,
        "duplicate_hits": duplicate_hits,
    }


def _load_human_rows(path: str) -> List[Dict[str, Any]]:
    if path.lower().endswith(".json"):
        data = load_json(path)
        return data if isinstance(data, list) else list(data.get("records") or [])
    return pd.read_excel(path, sheet_name="Independent_Annotations").fillna("").to_dict("records")


def _cohen_kappa(pairs: Sequence[tuple[str, str]]) -> float:
    if not pairs:
        return 0.0
    observed = sum(1 for left, right in pairs if left == right) / len(pairs)
    left_counts = Counter(left for left, _ in pairs)
    right_counts = Counter(right for _, right in pairs)
    expected = sum(
        (left_counts[label] / len(pairs)) * (right_counts[label] / len(pairs))
        for label in {"BENIGN", "MALICIOUS"}
    )
    return (observed - expected) / (1.0 - expected) if expected < 1.0 else 1.0


def _record_text(record: Dict[str, Any]) -> str:
    return " ".join(
        str(turn.get("nlq") or "")
        for turn in record.get("turns") or []
    ).lower().strip()


def _human_row_text(row: Dict[str, Any]) -> str:
    value = row.get("turns_json")
    try:
        turns = json.loads(value) if isinstance(value, str) else value
        return " ".join(str(turn.get("nlq") or "") for turn in turns or []).lower().strip()
    except (TypeError, ValueError, json.JSONDecodeError):
        return str(value or "").lower().strip()


def _write_jsonl(rows: Sequence[Dict[str, Any]], path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
