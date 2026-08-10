from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from data_synthesis.common.io import parse_model_json
from data_synthesis.common.policy_guard import build_role_policy_context, validate_benign_turn_policy
from data_synthesis.common.summary import export_dataset_summary_excel

from .spec import BenignJob, make_slot_key, scaled_primary_counts, scaled_slot_counts


class BenignValidationError(ValueError):
    """Raised when a generated Benign record cannot be canonicalized."""


def validate_raw_sequence(parsed: Dict[str, Any], job: BenignJob) -> None:
    turns = parsed.get("turns")
    if not isinstance(turns, list):
        raise BenignValidationError("Missing or invalid `turns` array.")
    if len(turns) != job.turn_count:
        raise BenignValidationError(f"Expected {job.turn_count} turn(s), got {len(turns)}.")

    for expected_turn_id, turn in enumerate(turns, start=1):
        if not isinstance(turn, dict):
            raise BenignValidationError(f"Turn {expected_turn_id} must be a JSON object.")
        if turn.get("turn_id") != expected_turn_id:
            raise BenignValidationError(f"Turn {expected_turn_id} has invalid turn_id.")
        if not str(turn.get("nlq", "")).strip():
            raise BenignValidationError(f"Turn {expected_turn_id} is missing `nlq`.")
        if "sql" in turn:
            raise BenignValidationError(f"Turn {expected_turn_id} must not contain `sql`; use `sql_gt`.")
        sql_gt = turn.get("sql_gt")
        if sql_gt is None or not str(sql_gt).strip():
            raise BenignValidationError(f"Turn {expected_turn_id} must have non-null `sql_gt`.")
        if str(turn.get("turn_label", "")).upper().strip() != "BENIGN":
            raise BenignValidationError(f"Turn {expected_turn_id} must be labelled BENIGN.")
        try:
            validate_benign_turn_policy(turn, job, expected_turn_id)
        except ValueError as exc:
            raise BenignValidationError(str(exc)) from exc


def parse_and_validate(output: str, job: BenignJob) -> Dict[str, Any]:
    parsed = parse_model_json(output)
    validate_raw_sequence(parsed, job)
    return parsed


def canonicalize_sequence(parsed: Dict[str, Any], job: BenignJob) -> Dict[str, Any]:
    validate_raw_sequence(parsed, job)
    return {
        "id": job.sequence_id,
        "turn_type": job.turn_type,
        "primary_type": "BENIGN",
        "role": job.role,
        "user_context_id": job.user_context_id,
        "turns": [
            {
                "turn_id": turn_id,
                "nlq": str(turn.get("nlq", "")).strip(),
                "sql_gt": str(turn.get("sql_gt", "")).strip(),
                "turn_label": "BENIGN",
            }
            for turn_id, turn in enumerate(parsed["turns"], start=1)
        ],
        "seq_label": "BENIGN",
        "attack_tags": job.attack_tags,
    }


def build_raw_audit_record(parsed: Dict[str, Any], raw_output: str, job: BenignJob) -> Dict[str, Any]:
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
    expected_primary_counts: Optional[Dict[str, int]] = None,
    expected_slot_counts: Optional[Dict[str, int]] = None,
    policy_index: Optional[Any] = None,
) -> Dict[str, Any]:
    errors: List[str] = []
    primary_counts = Counter()
    slot_counts = Counter()
    role_counts = Counter()
    turn_type_counts = Counter()
    forbidden_keys = {"cf_pair_id", "attack_type", "malicious_cot"}

    for index, record in enumerate(records, start=1):
        record_id = record.get("id", f"<index {index}>")
        present_forbidden = forbidden_keys.intersection(record)
        if present_forbidden:
            errors.append(f"{record_id}: forbidden top-level keys present: {sorted(present_forbidden)}")

        turn_type = record.get("turn_type")
        if turn_type not in {"single", "multi"}:
            errors.append(f"{record_id}: turn_type must be single or multi.")
        expected_prefix = "ST-" if turn_type == "single" else "MT-"
        if isinstance(record_id, str) and turn_type in {"single", "multi"} and not record_id.startswith(expected_prefix):
            errors.append(f"{record_id}: id must start with {expected_prefix}.")

        if record.get("primary_type") != "BENIGN":
            errors.append(f"{record_id}: primary_type must be BENIGN.")
        if record.get("seq_label") != "BENIGN":
            errors.append(f"{record_id}: seq_label must be BENIGN.")

        attack_tags = record.get("attack_tags")
        if not isinstance(attack_tags, dict):
            errors.append(f"{record_id}: missing attack_tags object.")
        else:
            for key in ("injection_type", "rbac_violation", "violated_policies", "mt_pattern"):
                if attack_tags.get(key) is not None:
                    errors.append(f"{record_id}: attack_tags.{key} must be null for Benign records.")

        role = record.get("role")
        primary_counts[record.get("primary_type")] += 1
        role_counts[role] += 1
        turn_type_counts[turn_type] += 1
        if turn_type in {"single", "multi"} and role:
            slot_counts[make_slot_key(str(turn_type), str(role))] += 1

        turns = record.get("turns")
        if not isinstance(turns, list):
            errors.append(f"{record_id}: turns must be an array.")
            continue
        if turn_type == "single" and len(turns) != 1:
            errors.append(f"{record_id}: single records must contain exactly one turn.")
        if turn_type == "multi" and len(turns) < 2:
            errors.append(f"{record_id}: multi records must contain at least two turns.")

        for expected_turn_id, turn in enumerate(turns, start=1):
            if "sql" in turn:
                errors.append(f"{record_id}: turn {expected_turn_id} must not contain `sql`.")
            for required_key in ("turn_id", "nlq", "sql_gt", "turn_label"):
                if required_key not in turn:
                    errors.append(f"{record_id}: turn {expected_turn_id} missing {required_key}.")
            if turn.get("turn_id") != expected_turn_id:
                errors.append(f"{record_id}: turn {expected_turn_id} has invalid turn_id.")
            if turn.get("turn_label") != "BENIGN":
                errors.append(f"{record_id}: turn {expected_turn_id} must be BENIGN.")
            if not str(turn.get("nlq", "")).strip():
                errors.append(f"{record_id}: turn {expected_turn_id} nlq must be non-empty.")
            if turn.get("sql_gt") is None or not str(turn.get("sql_gt", "")).strip():
                errors.append(f"{record_id}: turn {expected_turn_id} sql_gt must be non-empty.")
            if policy_index is not None:
                policy_error = _validate_canonical_benign_policy(record, turn, expected_turn_id, policy_index)
                if policy_error:
                    errors.append(f"{record_id}: {policy_error}")

    expected_primary_counts = expected_primary_counts or scaled_primary_counts()
    expected_slot_counts = expected_slot_counts or scaled_slot_counts()
    for primary_type, expected in expected_primary_counts.items():
        actual = primary_counts.get(primary_type, 0)
        if actual != expected:
            errors.append(f"{primary_type}: expected {expected} records, got {actual}.")
    for slot_key, expected in expected_slot_counts.items():
        actual = slot_counts.get(slot_key, 0)
        if actual != expected:
            errors.append(f"{slot_key}: expected {expected} records, got {actual}.")

    return {
        "ok": not errors,
        "errors": errors,
        "total_records": len(records),
        "primary_counts": dict(primary_counts),
        "slot_counts": dict(slot_counts),
        "turn_type_counts": dict(turn_type_counts),
        "role_counts": dict(role_counts),
    }


def _validate_canonical_benign_policy(
    record: Dict[str, Any],
    turn: Dict[str, Any],
    turn_index: int,
    policy_index: Any,
) -> Optional[str]:
    class _PolicyJob:
        pass

    job = _PolicyJob()
    job.role = record.get("role")
    job.user_context_id = record.get("user_context_id")
    job.policy_context = build_role_policy_context(
        policy_index,
        role=str(job.role),
        user_context_id=str(job.user_context_id),
    )
    try:
        validate_benign_turn_policy(turn, job, turn_index)
    except ValueError as exc:
        return str(exc)
    return None


def export_summary_excel(
    records: Sequence[Dict[str, Any]],
    output_path: str,
    *,
    expected_primary_counts: Optional[Dict[str, int]] = None,
    expected_slot_counts: Optional[Dict[str, int]] = None,
) -> None:
    expected_slot_counts = expected_slot_counts or scaled_slot_counts()
    slot_counts = Counter(make_slot_key(record["turn_type"], record["role"]) for record in records)
    turn_type_counts = Counter(record["turn_type"] for record in records)
    role_turn_rows = Counter((record["turn_type"], record["role"]) for record in records)

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
    turn_type_df = pd.DataFrame(
        [{"turn_type": turn_type, "count": count} for turn_type, count in sorted(turn_type_counts.items())]
    )
    role_turn_df = pd.DataFrame(
        [
            {"turn_type": turn_type, "role": role, "count": count}
            for (turn_type, role), count in sorted(role_turn_rows.items())
        ]
    )
    export_dataset_summary_excel(
        records,
        output_path,
        expected_counts=expected_primary_counts or scaled_primary_counts(),
        group_field="primary_type",
        group_sheet_name="Benign_Counts",
        extra_sheets={
            "Benign_Slots": slot_df,
            "Turn_Type_Summary": turn_type_df,
            "Role_Turn_Type": role_turn_df,
        },
    )
