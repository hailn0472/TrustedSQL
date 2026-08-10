from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Sequence

import pandas as pd


_ILLEGAL_EXCEL_CHARS = {
    code
    for code in range(32)
    if code not in (9, 10, 13)
}


def export_human_verify_files(
    *,
    csv_path: str,
    excel_path: str,
    dataset_family: str,
    selected_items: Sequence[Dict[str, Any]],
    final_records: Sequence[Dict[str, Any]],
    rejected_items: Sequence[Dict[str, Any]],
    verify_report: Dict[str, Any],
) -> Dict[str, Any]:
    rows = _build_rows(
        dataset_family=dataset_family,
        selected_items=selected_items,
        final_records=final_records,
        rejected_items=rejected_items,
    )
    df = pd.DataFrame(rows)
    df = _sanitize_dataframe(df)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    final_df = df[df["review_status"] == "FINAL_PASS"].copy() if not df.empty else df
    rejected_df = df[df["review_status"] == "REJECTED"].copy() if not df.empty else df
    legend_df = pd.DataFrame(_legend_rows())
    verify_df = _sanitize_dataframe(pd.DataFrame(_flatten_verify_report(verify_report)))

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Review_All")
        final_df.to_excel(writer, index=False, sheet_name="Final_Pass")
        rejected_df.to_excel(writer, index=False, sheet_name="Rejected")
        verify_df.to_excel(writer, index=False, sheet_name="Verify_Summary")
        legend_df.to_excel(writer, index=False, sheet_name="Legend")
        for worksheet in writer.sheets.values():
            for column_cells in worksheet.columns:
                values = [cell.value for cell in column_cells if cell.value is not None]
                max_len = max((len(str(value)) for value in values), default=8)
                worksheet.column_dimensions[column_cells[0].column_letter].width = min(max_len + 2, 80)
            worksheet.freeze_panes = "A2"

    return {
        "csv_path": csv_path,
        "excel_path": excel_path,
        "row_count": len(rows),
        "final_pass_count": len(selected_items),
        "rejected_count": len(rejected_items),
    }


def _sanitize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.map(_sanitize_excel_value)


def _sanitize_excel_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return "".join(
        char if ord(char) not in _ILLEGAL_EXCEL_CHARS else "\ufffd"
        for char in value
    )


def _build_rows(
    *,
    dataset_family: str,
    selected_items: Sequence[Dict[str, Any]],
    final_records: Sequence[Dict[str, Any]],
    rejected_items: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item, final_record in zip(selected_items, final_records):
        rows.append(_row_from_item(dataset_family, item, review_status="FINAL_PASS", final_record=final_record))
    for item in rejected_items:
        rows.append(_row_from_item(dataset_family, item, review_status="REJECTED", final_record=None))
    return rows


def _row_from_item(
    dataset_family: str,
    item: Dict[str, Any],
    *,
    review_status: str,
    final_record: Dict[str, Any] | None,
) -> Dict[str, Any]:
    record = final_record or item.get("canonical") or {}
    raw = item.get("raw") or {}
    metadata = item.get("metadata") or {}
    label_report = item.get("label_report") or {}
    evidence = label_report.get("evidence") if isinstance(label_report.get("evidence"), dict) else {}
    tags = record.get("attack_tags") or raw.get("attack_tags") or metadata.get("attack_tags") or {}
    target_condition = metadata.get("target_condition") or raw.get("target_condition") or {}
    compiled_target = metadata.get("compiled_target") or record.get("security_boundary") or {}
    user_context = metadata.get("user_context") or {}
    optimized = user_context.get("optimized_context") if isinstance(user_context, dict) else {}

    row: Dict[str, Any] = {
        "dataset_family": dataset_family,
        "review_status": review_status,
        "release_reject_reason": item.get("release_reject_reason"),
        "duplicate_similarity": item.get("duplicate_similarity"),
        "original_id": item.get("id"),
        "final_id": record.get("id"),
        "slot_id": metadata.get("slot_id"),
        "target_condition_id": target_condition.get("target_condition_id"),
        "turn_type": record.get("turn_type"),
        "primary_type": record.get("primary_type"),
        "role": record.get("role"),
        "user_context_id": record.get("user_context_id"),
        "seq_label": record.get("seq_label"),
        "injection_type": _compact_json(tags.get("injection_type")),
        "rbac_violation": _compact_json(tags.get("rbac_violation")),
        "violated_policies": _compact_json(tags.get("violated_policies")),
        "mt_pattern": _compact_json(tags.get("mt_pattern")),
        "target_scope": compiled_target.get("scope_type") or target_condition.get("scope"),
        "target_tables": _compact_json(compiled_target.get("target_tables") or target_condition.get("target_tables")),
        "target_columns": _compact_json(compiled_target.get("target_columns") or target_condition.get("target_columns")),
        "target_policy_refs": _compact_json(
            [compiled_target.get("policy_ref")]
            if compiled_target.get("policy_ref")
            else target_condition.get("policy_refs")
        ),
        "primary_violation": compiled_target.get("primary_violation"),
        "secondary_violations": _compact_json(compiled_target.get("secondary_violations")),
        "allowed_subject": compiled_target.get("allowed_subject"),
        "forbidden_subject": compiled_target.get("forbidden_subject"),
        "target_condition_text": target_condition.get("condition_text"),
        "target_relevance_rules": _compact_json(target_condition.get("relevance_rules")),
        "generation_reason": raw.get("generation_reason"),
        "target_relevance_claim": raw.get("target_relevance_claim"),
        "label_pass": label_report.get("pass"),
        "label_confidence": label_report.get("confidence"),
        "matches_slot": label_report.get("matches_slot"),
        "target_relevant": label_report.get("target_relevant"),
        "policy_aligned": label_report.get("policy_aligned"),
        "label_reject_reasons": _compact_json(label_report.get("reject_reasons")),
        "evidence_slot_alignment": evidence.get("slot_alignment"),
        "evidence_target_alignment": evidence.get("target_alignment"),
        "evidence_policy_alignment": evidence.get("policy_alignment"),
        "user_identity": _compact_json((optimized or {}).get("identity") or user_context.get("profile")),
        "user_rbac_policy_signal": _compact_json((optimized or {}).get("rbac_policy_signal")),
        "user_entity_counts": _compact_json((optimized or {}).get("entity_counts")),
        "turn_count": len(record.get("turns") or []),
        "all_nlq": _join_turn_field(record, "nlq"),
        "all_sql_gt": _join_turn_field(record, "sql_gt"),
    }

    turns = record.get("turns") or []
    for index, turn in enumerate(turns, start=1):
        row[f"turn_{index}_label"] = turn.get("turn_label")
        row[f"turn_{index}_nlq"] = turn.get("nlq")
        row[f"turn_{index}_sql_gt"] = turn.get("sql_gt")
    return row


def _join_turn_field(record: Dict[str, Any], field: str) -> str:
    values = []
    for turn in record.get("turns") or []:
        value = turn.get(field)
        values.append("" if value is None else str(value))
    return "\n---\n".join(values)


def _flatten_verify_report(verify_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = [
        {"metric": "ok", "value": verify_report.get("ok")},
        {"metric": "selected_total", "value": verify_report.get("selected_total")},
        {"metric": "rejected_total", "value": verify_report.get("rejected_total")},
        {"metric": "duplicate_threshold", "value": verify_report.get("duplicate_threshold")},
        {"metric": "expected_counts", "value": _compact_json(verify_report.get("expected_counts"))},
        {"metric": "selected_counts", "value": _compact_json(verify_report.get("selected_counts"))},
        {"metric": "missing_counts", "value": _compact_json(verify_report.get("missing_counts"))},
    ]
    validation = verify_report.get("canonical_validation") or {}
    rows.append({"metric": "canonical_validation_ok", "value": validation.get("ok")})
    rows.append({"metric": "canonical_validation_errors", "value": _compact_json(validation.get("errors"))})
    return rows


def _legend_rows() -> List[Dict[str, str]]:
    return [
        {"column": "review_status", "meaning": "FINAL_PASS means released to Final; REJECTED means kept only for audit."},
        {"column": "release_reject_reason", "meaning": "Deterministic release-gate reason, e.g. label_reject, duplicate_near, quota_already_filled."},
        {"column": "target_condition_text", "meaning": "Target contract the sample should match."},
        {"column": "generation_reason", "meaning": "Generator's raw rationale; not trusted as label."},
        {"column": "target_relevance_claim", "meaning": "Generator's raw claim that the sample targets the target condition."},
        {"column": "matches_slot", "meaning": "LLM verifier says candidate matches intended slot/primary_type."},
        {"column": "target_relevant", "meaning": "LLM verifier says NLQ/turns actually target the target condition."},
        {"column": "policy_aligned", "meaning": "LLM verifier says policy behavior is correct."},
        {"column": "label_confidence", "meaning": "Verifier confidence; release default threshold is 0.75."},
        {"column": "all_nlq/all_sql_gt", "meaning": "All turns joined for fast manual review."},
    ]


def _compact_json(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
