from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Set, Tuple

from .pattern_slots import GNNJob


DYNAMIC_EDGE_TYPES = {
    "asks",
    "has_role",
    "mentions_table",
    "mentions_column",
    "accesses_table",
    "accesses_column",
    "constrained_by_policy",
    "allowed_by_policy",
    "violates_policy",
    "targets_scope",
    "targets_subject",
    "outside_scope",
    "requests_operation",
    "is_malicious_reason",
    "has_primary_violation",
    "has_secondary_violation",
}


def build_dynamic_graph_evidence(
    turns: Sequence[Dict[str, Any]],
    job: GNNJob,
) -> Dict[str, Any]:
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    malicious_node_ids: List[str] = []
    malicious_edge_ids: List[str] = []

    def add_node(node_id: str, node_type: str, **attributes: Any) -> None:
        if not node_id:
            return
        node = nodes.setdefault(node_id, {"id": node_id, "node_type": node_type})
        node.update({key: value for key, value in attributes.items() if value is not None})

    def add_edge(source: str, edge_type: str, target: str, *, malicious: bool = False) -> None:
        if not source or not target:
            return
        edge_id = f"edge_{len(edges) + 1}"
        edges.append(
            {
                "id": edge_id,
                "source": source,
                "edge": edge_type,
                "target": target,
            }
        )
        if malicious:
            malicious_edge_ids.append(edge_id)

    add_node(job.user_context_id, "User", role=job.role)
    add_node(job.role, "Role")
    add_node(job.target.policy_ref, "Policy", title=job.target.policy_title)
    add_node(job.target.scope_type, "Scope")
    add_edge(job.user_context_id, "has_role", job.role)
    add_edge(job.role, "constrained_by_policy", job.target.policy_ref)

    for table in job.target.target_tables:
        add_node(table, "Table")
    for column in job.target.target_columns:
        add_node(column, "Column")
    for column in job.target.denied_columns:
        add_node(column, "DeniedColumn")

    detected_secondary = detect_secondary_violations(turns, job)
    for index, turn in enumerate(turns, 1):
        turn_id = f"turn_{index}"
        malicious = turn.get("turn_label") == "MALICIOUS"
        add_node(
            turn_id,
            "Turn",
            turn_label=turn.get("turn_label"),
            nlq=turn.get("nlq"),
        )
        add_edge(job.user_context_id, "asks", turn_id)
        tables, columns = extract_sql_references(str(turn.get("sql_gt") or ""))
        if malicious and not tables:
            tables = set(job.target.target_tables)
        if malicious and not columns and job.target.primary_violation == "RB-02":
            columns = set(job.target.denied_columns[:3])
        for table in sorted(tables):
            add_node(table, "Table")
            add_edge(turn_id, "accesses_table", table, malicious=malicious)
        for column in sorted(columns):
            add_node(column, "Column")
            add_edge(turn_id, "accesses_column", column, malicious=malicious)
        add_edge(turn_id, "targets_scope", job.target.scope_type, malicious=malicious)

        if malicious and job.target.primary_violation:
            violation_id = job.target.primary_violation
            add_node(violation_id, "RBACViolation", primary=True)
            add_edge(turn_id, "violates_policy", job.target.policy_ref, malicious=True)
            add_edge(turn_id, "has_primary_violation", violation_id, malicious=True)
            malicious_node_ids.extend([turn_id, job.target.policy_ref, violation_id])
            if job.target.forbidden_subject:
                add_node(job.target.forbidden_subject, "ForbiddenSubject")
                add_edge(turn_id, "targets_subject", job.target.forbidden_subject, malicious=True)
                if job.target.primary_violation == "RB-03":
                    add_edge(turn_id, "outside_scope", job.target.forbidden_subject, malicious=True)
                malicious_node_ids.append(job.target.forbidden_subject)
            for secondary in detected_secondary:
                add_node(secondary, "RBACViolation", primary=False)
                add_edge(turn_id, "has_secondary_violation", secondary, malicious=True)
                malicious_node_ids.append(secondary)
        elif not malicious:
            add_edge(turn_id, "allowed_by_policy", job.target.policy_ref)

    return {
        "mode": "dynamic_from_candidate_and_compiled_slot",
        "evidence_nodes": list(nodes.values()),
        "evidence_edges": edges,
        "malicious_reason_nodes": _dedupe(malicious_node_ids),
        "malicious_reason_edges": _dedupe(malicious_edge_ids),
        "detected_secondary_violations": detected_secondary,
    }


def detect_secondary_violations(
    turns: Sequence[Dict[str, Any]],
    job: GNNJob,
) -> List[str]:
    possible = set(job.target.possible_secondary_violations)
    detected: List[str] = []
    malicious_text = " ".join(
        str(turn.get("nlq") or "").lower()
        for turn in turns
        if turn.get("turn_label") == "MALICIOUS"
    )
    if "RB-02" in possible:
        denied_phrases = {
            column.lower().replace("_", " ")
            for column in job.target.denied_columns
        }
        if any(phrase and phrase in malicious_text for phrase in denied_phrases):
            detected.append("RB-02")
    return detected


def extract_sql_references(sql: str) -> Tuple[Set[str], Set[str]]:
    tables = {
        match.group(1).split(".")[-1].strip('"')
        for match in re.finditer(r"\b(?:from|join)\s+([A-Za-z_][\w.\"]*)", sql, re.I)
    }
    aliases = {
        match.group(2): match.group(1).split(".")[-1].strip('"')
        for match in re.finditer(
            r"\b(?:from|join)\s+([A-Za-z_][\w.\"]*)\s+(?:as\s+)?([A-Za-z_]\w*)",
            sql,
            re.I,
        )
        if match.group(2).lower() not in {"where", "join", "on", "order", "group", "limit"}
    }
    columns: Set[str] = set()
    select_match = re.search(r"\bselect\s+(.*?)\s+from\b", sql, re.I | re.S)
    if select_match:
        for qualifier, column in re.findall(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b", select_match.group(1)):
            columns.add(column)
            if qualifier in aliases:
                tables.add(aliases[qualifier])
    return tables, columns


def _dedupe(values: Sequence[str]) -> List[str]:
    result: List[str] = []
    seen: Set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
