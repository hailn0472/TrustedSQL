from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

from data_synthesis.common.io import parse_model_json
from data_synthesis.common.summary import export_dataset_summary_excel

from data_synthesis.common.policy_guard import build_role_policy_context, validate_benign_turn_policy
from .spec import GenerationJob, PATTERNS, scaled_pattern_counts


class MultiturnValidationError(ValueError):
    """Raised when a generated Multiturn record cannot be canonicalized."""


def _extract_sql(turn: Dict[str, Any]) -> str:
    sql = turn.get("sql")
    if sql is None:
        sql = turn.get("sql_gt")
    return "" if sql is None else str(sql).strip()


def validate_raw_sequence(parsed: Dict[str, Any], job: GenerationJob) -> None:
    turns = parsed.get("turns")
    if not isinstance(turns, list):
        raise MultiturnValidationError("Missing or invalid `turns` array.")
    if len(turns) < 2:
        raise MultiturnValidationError("A Multiturn sequence must contain at least 2 turns.")
    if len(turns) != job.turn_count:
        raise MultiturnValidationError(
            f"Expected {job.turn_count} turns for {job.pattern.code}, got {len(turns)}."
        )

    malicious_count = 0
    for index, turn in enumerate(turns, start=1):
        if not isinstance(turn, dict):
            raise MultiturnValidationError(f"Turn {index} must be a JSON object.")
        nlq = str(turn.get("nlq", "")).strip()
        if not nlq:
            raise MultiturnValidationError(f"Turn {index} is missing `nlq`.")

        label = str(turn.get("turn_label", "")).upper().strip()
        is_final = index == len(turns)
        sql = _extract_sql(turn)
        if (
            label == "BENIGN"
            and not sql
            and not is_final
            and job.pattern.allow_intermediate_malicious
        ):
            label = "MALICIOUS"
            turn["turn_label"] = "MALICIOUS"

        if label == "MALICIOUS":
            malicious_count += 1
        elif label != "BENIGN":
            raise MultiturnValidationError(
                f"Turn {index} label must be BENIGN or MALICIOUS, got {label or '<empty>'}."
            )

        if label == "BENIGN" and not sql:
            raise MultiturnValidationError(f"Turn {index} is missing sql_gt for a BENIGN turn.")
        if label == "BENIGN":
            try:
                validate_benign_turn_policy(turn, job, index)
            except ValueError as exc:
                raise MultiturnValidationError(str(exc)) from exc

        if is_final and label != "MALICIOUS":
            raise MultiturnValidationError("The final turn must be labelled MALICIOUS.")
        if not is_final and label == "MALICIOUS" and not job.pattern.allow_intermediate_malicious:
            raise MultiturnValidationError(
                f"{job.pattern.code} only allows the final turn to be MALICIOUS."
            )

    if malicious_count < 1:
        raise MultiturnValidationError("A Multiturn sequence must have at least one MALICIOUS turn.")
    if malicious_count != 1 and not job.pattern.allow_intermediate_malicious:
        raise MultiturnValidationError("This pattern must have exactly one final MALICIOUS turn.")


def canonicalize_sequence(parsed: Dict[str, Any], job: GenerationJob) -> Dict[str, Any]:
    validate_raw_sequence(parsed, job)

    canonical_turns: List[Dict[str, Any]] = []
    turns = parsed["turns"]
    for index, turn in enumerate(turns, start=1):
        label = str(turn.get("turn_label", "")).upper().strip()
        canonical_turns.append(
            {
                "turn_id": index,
                "nlq": str(turn.get("nlq", "")).strip(),
                "sql_gt": None if label == "MALICIOUS" else _extract_sql(turn),
                "turn_label": label,
            }
        )

    return {
        "id": job.sequence_id,
        "turn_type": "multi",
        "role": job.role,
        "user_context_id": job.user_context_id,
        "turns": canonical_turns,
        "seq_label": "MALICIOUS",
        "attack_tags": job.attack_tags,
    }


def build_raw_audit_record(parsed: Dict[str, Any], raw_output: str, job: GenerationJob) -> Dict[str, Any]:
    return {
        **job.to_metadata(),
        "generation_reason": str(parsed.get("generation_reason", "")).strip(),
        "target_relevance_claim": str(parsed.get("target_relevance_claim", "")).strip(),
        "malicious_cot": str(parsed.get("malicious_cot", "")).strip(),
        "turns": parsed.get("turns", []),
        "raw_model_output": raw_output,
    }


def validate_canonical_dataset(
    records: Sequence[Dict[str, Any]],
    *,
    expected_counts: Optional[Dict[str, int]] = None,
    policy_index: Optional[Any] = None,
) -> Dict[str, Any]:
    errors: List[str] = []
    counts = Counter()
    turn_counts = Counter()

    for index, record in enumerate(records, start=1):
        record_id = record.get("id", f"<index {index}>")
        forbidden_keys = {"cf_pair_id", "attack_type", "malicious_cot"}
        present_forbidden = forbidden_keys.intersection(record)
        if present_forbidden:
            errors.append(f"{record_id}: forbidden top-level keys present: {sorted(present_forbidden)}")

        if record.get("turn_type") != "multi":
            errors.append(f"{record_id}: turn_type must be multi.")
        if record.get("seq_label") != "MALICIOUS":
            errors.append(f"{record_id}: seq_label must be MALICIOUS.")

        attack_tags = record.get("attack_tags")
        if not isinstance(attack_tags, dict):
            errors.append(f"{record_id}: missing attack_tags object.")
            continue
        mt_pattern = attack_tags.get("mt_pattern")
        if mt_pattern not in PATTERNS:
            errors.append(f"{record_id}: invalid mt_pattern {mt_pattern!r}.")
        else:
            counts[mt_pattern] += 1

        turns = record.get("turns")
        if not isinstance(turns, list) or len(turns) < 2:
            errors.append(f"{record_id}: turns must contain at least 2 turns.")
            continue
        turn_counts[len(turns)] += 1

        malicious_count = 0
        for turn_index, turn in enumerate(turns, start=1):
            if "sql" in turn:
                errors.append(f"{record_id}: turn {turn_index} must not contain `sql`.")
            for required_key in ("turn_id", "nlq", "sql_gt", "turn_label"):
                if required_key not in turn:
                    errors.append(f"{record_id}: turn {turn_index} missing {required_key}.")
            label = turn.get("turn_label")
            if label == "MALICIOUS":
                malicious_count += 1
            elif label != "BENIGN":
                errors.append(f"{record_id}: turn {turn_index} label must be BENIGN or MALICIOUS.")

            is_final = turn_index == len(turns)
            if is_final and label != "MALICIOUS":
                errors.append(f"{record_id}: final turn must be MALICIOUS.")
            if (
                not is_final
                and label == "MALICIOUS"
                and mt_pattern in PATTERNS
                and not PATTERNS[mt_pattern].allow_intermediate_malicious
            ):
                errors.append(f"{record_id}: only MT-08 allows intermediate MALICIOUS turns.")
            if label == "MALICIOUS" and turn.get("sql_gt") is not None:
                errors.append(f"{record_id}: malicious turn {turn_index} sql_gt must be null.")
            if label == "BENIGN" and not turn.get("sql_gt"):
                errors.append(f"{record_id}: benign turn {turn_index} must have sql_gt.")
            if label == "BENIGN" and policy_index is not None:
                policy_error = _validate_canonical_benign_policy(record, turn, turn_index, policy_index)
                if policy_error:
                    errors.append(f"{record_id}: {policy_error}")
        if malicious_count < 1:
            errors.append(f"{record_id}: at least one turn must be MALICIOUS.")
        if (
            malicious_count != 1
            and mt_pattern in PATTERNS
            and not PATTERNS[mt_pattern].allow_intermediate_malicious
        ):
            errors.append(f"{record_id}: this pattern must have exactly one MALICIOUS turn.")

    expected_counts = expected_counts or scaled_pattern_counts()
    for pattern_code, expected in expected_counts.items():
        actual = counts.get(pattern_code, 0)
        if actual != expected:
            errors.append(f"{pattern_code}: expected {expected} records, got {actual}.")

    return {
        "ok": not errors,
        "errors": errors,
        "total_records": len(records),
        "pattern_counts": dict(counts),
        "turn_count_distribution": dict(turn_counts),
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
    expected_counts: Optional[Dict[str, int]] = None,
) -> None:
    export_dataset_summary_excel(
        records,
        output_path,
        expected_counts=expected_counts or scaled_pattern_counts(),
        group_field="mt_pattern",
        group_sheet_name="MT_Pattern_Counts",
    )
