from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from data_synthesis.common.io import parse_model_json
from data_synthesis.common.summary import export_dataset_summary_excel

from .deterministic_verify import validate_parsed_candidate, validate_record_evidence
from .evidence import DYNAMIC_EDGE_TYPES, build_dynamic_graph_evidence
from .pattern_loader import PatternBank
from .pattern_slots import GNNJob
from .policy_compiler import INTERNAL_TABLES
from .sql_compiler import compile_turn_sql


RAW_KEYS = {
    "turn_contents",
    "generation_reason",
    "target_relevance_claim",
    "evidence_alignment_claim",
    "hard_negative_family",
}


def parse_and_validate(output: str, job: GNNJob) -> Dict[str, Any]:
    parsed = parse_model_json(output)
    extra = set(parsed).difference(RAW_KEYS)
    missing = RAW_KEYS.difference(parsed)
    if missing:
        raise ValueError(f"Missing root keys: {sorted(missing)}")
    if extra:
        raise ValueError(f"Unexpected root keys: {sorted(extra)}")
    turns = parsed.get("turn_contents")
    if not isinstance(turns, list):
        raise ValueError("turns must be an array.")
    blueprint = job.pattern.turn_blueprint
    if len(turns) != len(blueprint):
        raise ValueError(f"Expected {len(blueprint)} turns, got {len(turns)}.")
    for index, (turn, spec) in enumerate(zip(turns, blueprint), 1):
        _validate_turn_content(turn, spec, index)
    for field in ("generation_reason", "target_relevance_claim", "evidence_alignment_claim"):
        value = parsed.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string.")
    hard_negative = parsed.get("hard_negative_family")
    if hard_negative is not None and not isinstance(hard_negative, str):
        raise ValueError("hard_negative_family must be a string or null.")
    deterministic = validate_parsed_candidate(parsed, job, job.policy_bundle)
    if not deterministic["ok"]:
        raise ValueError("Deterministic policy validation failed: " + "; ".join(deterministic["errors"]))
    parsed["_deterministic_validation"] = deterministic
    return parsed


def canonicalize_sequence(parsed: Dict[str, Any], job: GNNJob) -> Dict[str, Any]:
    turns = []
    for index, turn in enumerate(parsed["turn_contents"], 1):
        sql_gt = compile_turn_sql(job, index)
        turns.append(
            {
                "turn_id": index,
                "nlq": str(turn["nlq"]).strip(),
                "sql_gt": sql_gt,
                "turn_label": job.pattern.turn_blueprint[index - 1]["turn_label"],
            }
        )
    graph_evidence = build_dynamic_graph_evidence(turns, job)
    detected_secondary = graph_evidence.get("detected_secondary_violations") or []
    security_boundary = job.target.to_dict()
    security_boundary["secondary_violations"] = detected_secondary
    attack_tags = dict(job.attack_tags)
    attack_tags["secondary_rbac_violations"] = detected_secondary
    rbac = [job.target.primary_violation] if job.target.primary_violation else []
    rbac.extend(item for item in detected_secondary if item not in rbac)
    attack_tags["rbac_violation"] = rbac or None
    return {
        "id": job.sequence_id,
        "dataset_family": job.dataset_family,
        "pattern_id": job.pattern_id,
        "turn_type": job.turn_type,
        "primary_type": job.primary_type,
        "role": job.role,
        "user_context_id": job.user_context_id,
        "turns": turns,
        "seq_label": job.seq_label,
        "attack_tags": attack_tags,
        "security_boundary": security_boundary,
        "graph_evidence": graph_evidence,
        "hard_negative_family": parsed.get("hard_negative_family"),
        "generation_validation_snapshot": job.pattern.generation_validation,
        "pattern_metadata": {
            "identity": job.pattern.identity,
            "turn_blueprint": job.pattern.turn_blueprint,
            "hard_negatives": job.pattern.hard_negatives,
        },
        "slot_id": job.slot_id,
        "protocol_assignments": job.protocol_assignments or {},
    }


def build_raw_audit_record(parsed: Dict[str, Any], raw_output: str, job: GNNJob) -> Dict[str, Any]:
    return {
        "id": job.sequence_id,
        "dataset_family": job.dataset_family,
        "pattern_id": job.pattern_id,
        "primary_type": job.primary_type,
        "role": job.role,
        "user_context_id": job.user_context_id,
        "metadata": job.to_metadata(),
        "parsed": parsed,
        "raw_model_output": raw_output,
    }


def validate_canonical_dataset(
    records: Sequence[Dict[str, Any]],
    *,
    pattern_bank: PatternBank,
    expected_pattern_counts: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    errors: List[str] = []
    pattern_counts = Counter()
    family_counts = Counter()
    primary_counts = Counter()
    graph_edge_types = set(pattern_bank.controlled_vocab.get("edge_types") or []).union(
        DYNAMIC_EDGE_TYPES
    )
    patterns_by_id = {pattern.pattern_id: pattern for pattern in pattern_bank.active_patterns}

    for record in records:
        record_id = str(record.get("id"))
        pattern_id = record.get("pattern_id")
        pattern = patterns_by_id.get(str(pattern_id))
        if not pattern:
            errors.append(f"{record_id}: unknown pattern_id {pattern_id!r}.")
            continue
        pattern_counts[pattern.pattern_id] += 1
        family_counts[record.get("dataset_family")] += 1
        primary_counts[record.get("primary_type")] += 1
        if record.get("dataset_family") != pattern.dataset_family:
            errors.append(f"{record_id}: dataset_family mismatch.")
        if record.get("primary_type") != pattern.primary_type:
            errors.append(f"{record_id}: primary_type mismatch.")
        if record.get("seq_label") != pattern.seq_label:
            errors.append(f"{record_id}: seq_label mismatch.")
        security = record.get("security_boundary") or {}
        if security.get("role") != record.get("role"):
            errors.append(f"{record_id}: compiled security role mismatch.")
        if security.get("primary_violation") != pattern.primary_violation:
            errors.append(f"{record_id}: compiled primary violation mismatch.")
        possible_secondary = set(security.get("possible_secondary_violations") or [])
        actual_secondary = set(security.get("secondary_violations") or [])
        if not actual_secondary.issubset(possible_secondary):
            errors.append(f"{record_id}: unsupported secondary violation asserted.")
        internal_targets = sorted(set(security.get("target_tables") or []).intersection(INTERNAL_TABLES))
        if internal_targets:
            errors.append(f"{record_id}: internal target tables are forbidden: {internal_targets}.")
        turns = record.get("turns") or []
        if len(turns) != len(pattern.turn_blueprint):
            errors.append(f"{record_id}: turn count mismatch.")
            continue
        for index, (turn, spec) in enumerate(zip(turns, pattern.turn_blueprint), 1):
            _validate_canonical_turn(record_id, turn, spec, index, errors)
        evidence = record.get("graph_evidence") or {}
        for field in ("evidence_nodes", "evidence_edges", "malicious_reason_nodes", "malicious_reason_edges"):
            if field not in evidence:
                errors.append(f"{record_id}: graph_evidence missing {field}.")
        for edge in evidence.get("evidence_edges") or []:
            if isinstance(edge, dict) and edge.get("edge") not in graph_edge_types:
                errors.append(f"{record_id}: invalid graph edge type {edge.get('edge')!r}.")
        malicious_nodes = evidence.get("malicious_reason_nodes") or []
        malicious_edges = evidence.get("malicious_reason_edges") or []
        if pattern.primary_type == "BENIGN":
            if malicious_nodes or malicious_edges:
                errors.append(f"{record_id}: BENIGN record has malicious reason evidence.")
        elif not malicious_nodes or not malicious_edges:
            errors.append(f"{record_id}: malicious record missing malicious reason evidence.")
        errors.extend(f"{record_id}: {error}" for error in validate_record_evidence(record))

    if expected_pattern_counts:
        for pattern_id, expected in expected_pattern_counts.items():
            actual = pattern_counts.get(pattern_id, 0)
            if actual != expected:
                errors.append(f"{pattern_id}: expected {expected} records, got {actual}.")

    return {
        "ok": not errors,
        "total_records": len(records),
        "errors": errors,
        "pattern_counts": dict(pattern_counts),
        "family_counts": dict(family_counts),
        "primary_counts": dict(primary_counts),
        "expected_pattern_counts": expected_pattern_counts or {},
    }


def export_summary_excel(
    records: Sequence[Dict[str, Any]],
    output_path: str,
    *,
    expected_pattern_counts: Optional[Dict[str, int]] = None,
) -> None:
    extra = {
        "Family_Counts": pd.DataFrame(
            [{"dataset_family": key, "count": value} for key, value in sorted(Counter(r.get("dataset_family") for r in records).items())]
        ),
        "Pattern_Counts": pd.DataFrame(
            [
                {
                    "pattern_id": key,
                    "count": Counter(r.get("pattern_id") for r in records).get(key, 0),
                    "expected_count": value,
                    "matches_plan": Counter(r.get("pattern_id") for r in records).get(key, 0) == value,
                }
                for key, value in (expected_pattern_counts or {}).items()
            ]
        ),
    }
    export_dataset_summary_excel(
        records,
        output_path,
        expected_counts=expected_pattern_counts,
        group_field="pattern_id",
        group_sheet_name="GNN_Patterns",
        extra_sheets=extra,
    )


def _validate_turn_content(turn: Any, spec: Dict[str, Any], index: int) -> None:
    if not isinstance(turn, dict):
        raise ValueError(f"turn {index} must be an object.")
    allowed = {"nlq"}
    extra = set(turn).difference(allowed)
    missing = allowed.difference(turn)
    if missing:
        raise ValueError(f"turn {index} missing keys: {sorted(missing)}")
    if extra:
        raise ValueError(f"turn {index} unexpected keys: {sorted(extra)}")
    if not isinstance(turn.get("nlq"), str) or not turn.get("nlq").strip():
        raise ValueError(f"turn {index} nlq must be a non-empty string.")


def _validate_canonical_turn(
    record_id: str,
    turn: Dict[str, Any],
    spec: Dict[str, Any],
    index: int,
    errors: List[str],
) -> None:
    if turn.get("turn_id") != index:
        errors.append(f"{record_id}: turn {index} has wrong turn_id.")
    if turn.get("turn_label") != spec.get("turn_label"):
        errors.append(f"{record_id}: turn {index} label mismatch.")
    if not isinstance(turn.get("nlq"), str) or not turn.get("nlq").strip():
        errors.append(f"{record_id}: turn {index} nlq is empty.")
    try:
        _validate_sql_policy(turn.get("sql_gt"), spec.get("sql_gt_policy"), f"{record_id} turn {index}")
    except ValueError as exc:
        errors.append(str(exc))


def _validate_sql_policy(sql_gt: Any, policy: str, label: str) -> None:
    if policy == "null_for_unsafe":
        if sql_gt is not None:
            raise ValueError(f"{label}: sql_gt must be null for null_for_unsafe.")
        return
    if policy == "optional" and sql_gt is None:
        return
    if not isinstance(sql_gt, str) or not sql_gt.strip():
        raise ValueError(f"{label}: sql_gt must be a SELECT string.")
    sql = sql_gt.strip()
    if not re.match(r"(?is)^select\b", sql):
        raise ValueError(f"{label}: sql_gt must start with SELECT.")
    forbidden = re.search(r"(?is)\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|call|execute)\b", sql)
    if forbidden:
        raise ValueError(f"{label}: sql_gt contains forbidden statement {forbidden.group(1)}.")
