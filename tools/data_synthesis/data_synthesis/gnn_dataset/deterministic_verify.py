from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

from .evidence import DYNAMIC_EDGE_TYPES, extract_sql_references
from .pattern_slots import GNNJob
from .policy_compiler import CompiledPolicyBundle, INTERNAL_TABLES
from .sql_compiler import compile_turn_sql, validate_nlq_sql_alignment


def validate_parsed_candidate(
    parsed: Dict[str, Any],
    job: GNNJob,
    policy_bundle: CompiledPolicyBundle,
) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    matrix = policy_bundle.role_access.get(job.role) or {}
    errors.extend(_validate_variation_alignment(parsed, job))
    for index, turn in enumerate(parsed.get("turn_contents") or [], 1):
        spec = job.pattern.turn_blueprint[index - 1]
        turn_target = job.turn_policy_targets[index - 1]
        sql = compile_turn_sql(job, index)
        if not sql:
            continue
        errors.extend(validate_nlq_sql_alignment(job, index, str(turn.get("nlq") or "")))
        tables, columns = extract_sql_references(str(sql))
        internal = sorted(tables.intersection(INTERNAL_TABLES))
        if internal:
            errors.append(f"turn_{index}: internal tables referenced: {internal}")
        unknown_tables = sorted(tables.difference(policy_bundle.tables))
        if unknown_tables:
            errors.append(f"turn_{index}: unknown tables: {unknown_tables}")
        if spec.get("turn_label") == "BENIGN":
            denied_tables = sorted(table for table in tables if table not in matrix)
            if denied_tables:
                errors.append(f"turn_{index}: BENIGN uses denied tables: {denied_tables}")
            allowed_columns = {
                column
                for table in tables
                for column in matrix.get(table, [])
            }
            denied_columns = sorted(column for column in columns if column not in allowed_columns)
            if denied_columns:
                errors.append(f"turn_{index}: BENIGN uses denied columns: {denied_columns}")
            scoped_target_used = bool(tables.intersection(turn_target.target_tables))
            if (
                scoped_target_used
                and turn_target.row_filter
                and "@user_id" not in str(sql)
                and not re.search(r"\buser_id\s*=\s*current_user\b", str(sql), re.I)
            ):
                errors.append(
                    f"turn_{index}: BENIGN scoped SQL lacks authenticated-user binding."
                )
            literal_ids = {
                str(job.user_context_id),
                str(
                    ((job.user_context or {}).get("optimized_context") or {})
                    .get("identity", {})
                    .get("user_id", "")
                ),
            }
            for literal_id in {value for value in literal_ids if value}:
                if turn_target.row_filter and re.search(
                    rf"(?:user_id|student_id|lecturer_id)\s*=\s*['\"]?{re.escape(literal_id)}['\"]?",
                    str(sql),
                    re.I,
                ):
                    errors.append(f"turn_{index}: BENIGN SQL hard-codes the authenticated user ID.")
                    break
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "slot_id": job.slot_id,
        "primary_violation": job.target.primary_violation,
        "possible_secondary_violations": job.target.possible_secondary_violations,
    }


def _validate_variation_alignment(parsed: Dict[str, Any], job: GNNJob) -> List[str]:
    required_patterns = {
        "BENIGN_ENROLLED_CLASSMATE_IDENTITY_ROSTER",
        "BENIGN_PUBLIC_COURSE_CATALOG_LOOKUP",
        "BENIGN_SELF_SCOPE_ACADEMIC_LOOKUP",
        "MT-02_PROGRESSIVE_PERSONAL_INFO_TARGETING",
        "MT-04_AGGREGATE_DIFFERENCE_INFERENCE",
        "MT-05_EXTREME_PEER_ATTRIBUTE_LEAKAGE",
        "MT-06_ATTENTION_REDIRECTION_TO_PRIVATE_DATA",
        "MT-07_CRESCENDO_SCOPE_ESCALATION",
        "RB-03_ROW_SCOPE_OTHER_SUBJECT",
    }
    if job.pattern_id not in required_patterns:
        return []
    variation = (job.protocol_assignments or {}).get("variation_plan") or {}
    anchor = variation.get("entity_anchor") or {}
    value = str(anchor.get("value") or "").strip()
    if not value:
        return []
    nlq_text = " ".join(
        str(turn.get("nlq") or "")
        for turn in parsed.get("turn_contents") or []
    ).lower()
    if value.lower() not in nlq_text:
        return [
            f"variation target mismatch: required concrete anchor {value!r} is absent from NLQ"
        ]
    return []


def validate_record_evidence(record: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    evidence = record.get("graph_evidence") or {}
    nodes = evidence.get("evidence_nodes") or []
    edges = evidence.get("evidence_edges") or []
    node_ids = {
        str(node.get("id"))
        for node in nodes
        if isinstance(node, dict) and node.get("id") is not None
    }
    edge_ids = {
        str(edge.get("id"))
        for edge in edges
        if isinstance(edge, dict) and edge.get("id") is not None
    }
    for edge in edges:
        if not isinstance(edge, dict):
            errors.append("graph edge is not an object")
            continue
        if edge.get("edge") not in DYNAMIC_EDGE_TYPES:
            errors.append(f"invalid dynamic edge type {edge.get('edge')!r}")
        if str(edge.get("source")) not in node_ids or str(edge.get("target")) not in node_ids:
            errors.append(f"edge endpoint missing for {edge.get('id')}")
    malicious_nodes = set(map(str, evidence.get("malicious_reason_nodes") or []))
    malicious_edges = set(map(str, evidence.get("malicious_reason_edges") or []))
    if not malicious_nodes.issubset(node_ids):
        errors.append("malicious_reason_nodes contains unknown node ids")
    if not malicious_edges.issubset(edge_ids):
        errors.append("malicious_reason_edges contains unknown edge ids")
    if record.get("seq_label") == "BENIGN" and (malicious_nodes or malicious_edges):
        errors.append("BENIGN record contains malicious evidence")
    if record.get("seq_label") == "MALICIOUS" and (not malicious_nodes or not malicious_edges):
        errors.append("MALICIOUS record lacks malicious evidence")
    return errors
