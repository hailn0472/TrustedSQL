from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from data_synthesis.common.io import parse_model_json
from data_synthesis.common.policy_guard import validate_pi_target_policy
from data_synthesis.common.summary import export_dataset_summary_excel

from .spec import PI_TYPES, SingleturnPIJob, make_slot_key, scaled_pi_counts, scaled_slot_counts


class SingleturnPIValidationError(ValueError):
    """Raised when a generated Singleturn PI record cannot be canonicalized."""


def validate_raw_sequence(parsed: Dict[str, Any], job: SingleturnPIJob) -> None:
    turns = parsed.get("turns")
    if not isinstance(turns, list):
        raise SingleturnPIValidationError("Missing or invalid `turns` array.")
    if len(turns) != 1:
        raise SingleturnPIValidationError(f"Singleturn PI records must contain exactly 1 turn, got {len(turns)}.")

    turn = turns[0]
    if not isinstance(turn, dict):
        raise SingleturnPIValidationError("Turn 1 must be a JSON object.")
    if not str(turn.get("nlq", "")).strip():
        raise SingleturnPIValidationError("Turn 1 is missing `nlq`.")
    if "sql" in turn:
        raise SingleturnPIValidationError("Turn 1 must not contain `sql`; use `sql_gt`.")
    if turn.get("sql_gt") is not None:
        raise SingleturnPIValidationError("Turn 1 must use sql_gt = null.")
    if str(turn.get("turn_label", "")).upper().strip() != "MALICIOUS":
        raise SingleturnPIValidationError("Turn 1 must be labelled MALICIOUS.")
    try:
        validate_pi_target_policy(turn, job)
    except ValueError as exc:
        raise SingleturnPIValidationError(str(exc)) from exc


def parse_and_validate(output: str, job: SingleturnPIJob) -> Dict[str, Any]:
    parsed = parse_model_json(output)
    validate_raw_sequence(parsed, job)
    return parsed


def canonicalize_sequence(parsed: Dict[str, Any], job: SingleturnPIJob) -> Dict[str, Any]:
    validate_raw_sequence(parsed, job)
    turn = parsed["turns"][0]
    return {
        "id": job.sequence_id,
        "turn_type": "single",
        "primary_type": job.primary_type,
        "role": job.role,
        "user_context_id": job.user_context_id,
        "turns": [
            {
                "turn_id": 1,
                "nlq": str(turn.get("nlq", "")).strip(),
                "sql_gt": None,
                "turn_label": "MALICIOUS",
            }
        ],
        "seq_label": "MALICIOUS",
        "attack_tags": job.attack_tags,
    }


def build_raw_audit_record(parsed: Dict[str, Any], raw_output: str, job: SingleturnPIJob) -> Dict[str, Any]:
    return {
        **job.to_metadata(),
        "generation_reason": str(parsed.get("generation_reason", "")).strip(),
        "target_relevance_claim": str(parsed.get("target_relevance_claim", "")).strip(),
        "turns": parsed.get("turns", []),
        "raw_model_output": raw_output,
    }


def validate_canonical_dataset(
    records: Sequence[Dict[str, Any]],
    *,
    expected_pi_counts: Optional[Dict[str, int]] = None,
    expected_slot_counts: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    errors: List[str] = []
    pi_counts = Counter()
    slot_counts = Counter()
    role_counts = Counter()
    forbidden_keys = {"cf_pair_id", "attack_type", "malicious_cot"}

    for index, record in enumerate(records, start=1):
        record_id = record.get("id", f"<index {index}>")
        present_forbidden = forbidden_keys.intersection(record)
        if present_forbidden:
            errors.append(f"{record_id}: forbidden top-level keys present: {sorted(present_forbidden)}")

        if record.get("turn_type") != "single":
            errors.append(f"{record_id}: turn_type must be single.")
        primary_type = record.get("primary_type")
        if primary_type not in PI_TYPES:
            errors.append(f"{record_id}: primary_type must be one of {sorted(PI_TYPES)}.")
        if record.get("seq_label") != "MALICIOUS":
            errors.append(f"{record_id}: seq_label must be MALICIOUS.")

        attack_tags = record.get("attack_tags")
        if not isinstance(attack_tags, dict):
            errors.append(f"{record_id}: missing attack_tags object.")
            continue
        if attack_tags.get("injection_type") != primary_type:
            errors.append(f"{record_id}: attack_tags.injection_type must match primary_type.")
        if attack_tags.get("mt_pattern") is not None:
            errors.append(f"{record_id}: attack_tags.mt_pattern must be null.")

        rbac = attack_tags.get("rbac_violation")
        rbac_value = _single_tag_value(rbac)
        if rbac_value not in {None, "RB-01", "RB-02", "RB-03"}:
            errors.append(f"{record_id}: invalid rbac_violation {rbac!r}.")
        pi_counts[primary_type] += 1
        slot_counts[make_slot_key(str(primary_type), rbac_value)] += 1
        role_counts[record.get("role")] += 1

        turns = record.get("turns")
        if not isinstance(turns, list) or len(turns) != 1:
            errors.append(f"{record_id}: turns must contain exactly one turn.")
            continue
        turn = turns[0]
        if "sql" in turn:
            errors.append(f"{record_id}: turn must not contain `sql`.")
        for required_key in ("turn_id", "nlq", "sql_gt", "turn_label"):
            if required_key not in turn:
                errors.append(f"{record_id}: turn missing {required_key}.")
        if turn.get("turn_id") != 1:
            errors.append(f"{record_id}: turn_id must be 1.")
        if turn.get("turn_label") != "MALICIOUS":
            errors.append(f"{record_id}: turn_label must be MALICIOUS.")
        if turn.get("sql_gt") is not None:
            errors.append(f"{record_id}: sql_gt must be null.")
        if not str(turn.get("nlq", "")).strip():
            errors.append(f"{record_id}: nlq must be non-empty.")

    expected_pi_counts = expected_pi_counts or scaled_pi_counts()
    expected_slot_counts = expected_slot_counts or scaled_slot_counts()
    for pi_code, expected in expected_pi_counts.items():
        actual = pi_counts.get(pi_code, 0)
        if actual != expected:
            errors.append(f"{pi_code}: expected {expected} records, got {actual}.")
    for slot_key, expected in expected_slot_counts.items():
        actual = slot_counts.get(slot_key, 0)
        if actual != expected:
            errors.append(f"{slot_key}: expected {expected} records, got {actual}.")

    return {
        "ok": not errors,
        "errors": errors,
        "total_records": len(records),
        "pi_counts": dict(pi_counts),
        "slot_counts": dict(slot_counts),
        "role_counts": dict(role_counts),
    }


def export_summary_excel(
    records: Sequence[Dict[str, Any]],
    output_path: str,
    *,
    expected_pi_counts: Optional[Dict[str, int]] = None,
    expected_slot_counts: Optional[Dict[str, int]] = None,
) -> None:
    expected_slot_counts = expected_slot_counts or scaled_slot_counts()
    slot_counts = Counter()
    for record in records:
        tags = record["attack_tags"]
        slot_counts[make_slot_key(record["primary_type"], _single_tag_value(tags.get("rbac_violation")))] += 1
    slot_df = pd.DataFrame(
        [
            {
                "slot": slot_key,
                "count": slot_counts.get(slot_key, 0),
                "expected_count": expected,
                "matches_plan": slot_counts.get(slot_key, 0) == expected,
            }
            for slot_key, expected in expected_slot_counts.items()
        ]
    )
    export_dataset_summary_excel(
        records,
        output_path,
        expected_counts=expected_pi_counts or scaled_pi_counts(),
        group_field="primary_type",
        group_sheet_name="PI_Type_Counts",
        extra_sheets={"PI_RBAC_Slots": slot_df},
    )


def _single_tag_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, list):
        return None if not value else str(value[0])
    return str(value)
