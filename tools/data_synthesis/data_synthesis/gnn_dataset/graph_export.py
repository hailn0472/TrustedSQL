from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence, Set

from .evidence import extract_sql_references

FORBIDDEN_FEATURE_KEYS = {
    "graph_label",
    "pattern_id",
    "primary_type",
    "seq_label",
    "violated_policies",
    "rbac_violation",
    "turn_label",
}
FORBIDDEN_NODE_TYPES = {"Pattern", "PrimaryType", "Policy", "RBACViolation"}
FORBIDDEN_EDGE_TYPES = {
    "allowed_by_policy",
    "constrained_by_policy",
    "has_primary_type",
    "has_primary_violation",
    "has_rbac_violation",
    "has_secondary_violation",
    "is_malicious_reason",
    "uses_pattern",
    "violates_policy",
}


def export_graph_artifacts(
    records: Sequence[Dict[str, Any]],
    *,
    feature_path: str,
    targets_path: str,
    audit_path: str,
) -> Dict[str, Any]:
    feature_graphs = [record_to_feature_graph(record) for record in records]
    targets = [record_to_target(record) for record in records]
    audit_graphs = [record_to_audit_graph(record) for record in records]
    validation = validate_feature_target_contract(feature_graphs, targets)
    if not validation["ok"]:
        raise ValueError("Graph sanitization failed: " + "; ".join(validation["errors"][:20]))
    _write_jsonl(feature_graphs, feature_path)
    _write_jsonl(targets, targets_path)
    _write_jsonl(audit_graphs, audit_path)
    return validation


def export_graphs_jsonl(records: Sequence[Dict[str, Any]], output_path: str) -> None:
    """Backward-compatible feature-only export."""
    graphs = [record_to_feature_graph(record) for record in records]
    validation = validate_feature_target_contract(
        graphs,
        [record_to_target(record) for record in records],
    )
    if not validation["ok"]:
        raise ValueError("Graph sanitization failed: " + "; ".join(validation["errors"][:20]))
    _write_jsonl(graphs, output_path)


def record_to_feature_graph(record: Dict[str, Any]) -> Dict[str, Any]:
    graph_id = f"GRAPH-{record.get('id')}"
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, str]] = []

    def add_node(node_id: Any, node_type: str, **attrs: Any) -> None:
        key = str(node_id)
        if not key or node_type in FORBIDDEN_NODE_TYPES:
            return
        nodes.setdefault(key, {"id": key, "type": node_type})
        nodes[key].update(
            {
                name: value
                for name, value in attrs.items()
                if name not in FORBIDDEN_FEATURE_KEYS and value is not None
            }
        )

    def add_edge(source: Any, edge_type: str, target: Any) -> None:
        if source is None or target is None or edge_type in FORBIDDEN_EDGE_TYPES:
            return
        edges.append({"source": str(source), "type": edge_type, "target": str(target)})

    user_node = f"user_{record.get('role')}"
    add_node(user_node, "UserRole", role=record.get("role"))
    previous_turn = None
    for index, turn in enumerate(record.get("turns") or [], 1):
        turn_id = f"turn_{index}"
        add_node(turn_id, "Turn", nlq=turn.get("nlq"), sql_gt=turn.get("sql_gt"))
        add_edge(user_node, "asks", turn_id)
        if previous_turn:
            add_edge(previous_turn, "next_turn", turn_id)
        previous_turn = turn_id
        tables, columns = extract_sql_references(str(turn.get("sql_gt") or ""))
        for table in sorted(tables):
            node_id = f"table:{table}"
            add_node(node_id, "Table", name=table)
            add_edge(turn_id, "accesses_table", node_id)
        for column in sorted(columns):
            node_id = f"column:{column}"
            add_node(node_id, "Column", name=column)
            add_edge(turn_id, "accesses_column", node_id)

    node_ids = set(nodes)
    edges = [
        edge
        for edge in edges
        if edge["source"] in node_ids and edge["target"] in node_ids
    ]
    return {
        "graph_id": graph_id,
        "nodes": list(nodes.values()),
        "edges": edges,
    }


def record_to_target(record: Dict[str, Any]) -> Dict[str, Any]:
    tags = record.get("attack_tags") or {}
    return {
        "graph_id": f"GRAPH-{record.get('id')}",
        "source_record_id": record.get("id"),
        "graph_label": record.get("seq_label"),
        "dataset_family": record.get("dataset_family"),
        "primary_type": record.get("primary_type"),
        "pattern_id": record.get("pattern_id"),
        "rbac_violation": tags.get("rbac_violation") or [],
        "violated_policies": tags.get("violated_policies") or [],
        "protocol_assignments": record.get("protocol_assignments") or {},
    }


def record_to_audit_graph(record: Dict[str, Any]) -> Dict[str, Any]:
    evidence = record.get("graph_evidence") or {}
    return {
        **record_to_target(record),
        "nodes": evidence.get("evidence_nodes") or [],
        "edges": evidence.get("evidence_edges") or [],
        "malicious_reason_nodes": evidence.get("malicious_reason_nodes") or [],
        "malicious_reason_edges": evidence.get("malicious_reason_edges") or [],
        "security_boundary": record.get("security_boundary"),
    }


def validate_feature_target_contract(
    feature_graphs: Sequence[Dict[str, Any]],
    targets: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    errors: List[str] = []
    feature_ids = [str(graph.get("graph_id")) for graph in feature_graphs]
    target_ids = [str(target.get("graph_id")) for target in targets]
    if len(feature_ids) != len(set(feature_ids)):
        errors.append("Duplicate graph_id in feature graphs.")
    if len(target_ids) != len(set(target_ids)):
        errors.append("Duplicate graph_id in targets.")
    if set(feature_ids) != set(target_ids):
        errors.append("Feature and target graph_id sets differ.")
    for graph in feature_graphs:
        forbidden_keys = sorted(set(graph).intersection(FORBIDDEN_FEATURE_KEYS))
        if forbidden_keys:
            errors.append(f"{graph.get('graph_id')}: forbidden root keys {forbidden_keys}.")
        for node in graph.get("nodes") or []:
            if node.get("type") in FORBIDDEN_NODE_TYPES:
                errors.append(f"{graph.get('graph_id')}: forbidden node type {node.get('type')}.")
            leaked = sorted(set(node).intersection(FORBIDDEN_FEATURE_KEYS))
            if leaked:
                errors.append(f"{graph.get('graph_id')}: forbidden node keys {leaked}.")
            node_text = json.dumps(node, ensure_ascii=False)
            if _contains_label_literal(node_text):
                errors.append(f"{graph.get('graph_id')}: label literal leaked in node.")
        for edge in graph.get("edges") or []:
            if edge.get("type") in FORBIDDEN_EDGE_TYPES:
                errors.append(f"{graph.get('graph_id')}: forbidden edge {edge.get('type')}.")
            if _contains_label_literal(json.dumps(edge, ensure_ascii=False)):
                errors.append(f"{graph.get('graph_id')}: label literal leaked in edge.")
    return {
        "ok": not errors,
        "errors": errors,
        "feature_graph_count": len(feature_graphs),
        "target_count": len(targets),
    }


def _contains_label_literal(text: str) -> bool:
    lowered = text.lower()
    literals: Set[str] = {
        '"benign"',
        '"malicious"',
        '"rb-01"',
        '"rb-02"',
        '"rb-03"',
    }
    return any(literal in lowered for literal in literals)


def _write_jsonl(rows: Sequence[Dict[str, Any]], path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
