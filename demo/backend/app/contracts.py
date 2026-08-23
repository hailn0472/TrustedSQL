"""Typed, browser-safe contracts for TrustedSQL runtime rows."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, TypedDict, cast

from .sanitization import (
    MAX_ID_LENGTH,
    MAX_RESULT_ROWS,
    MAX_STRING_LENGTH,
    redact_error,
    safe_summary,
    sanitize_json_value,
)

ALLOWED_MODULE_IDS = ("C0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "X1")
_COMMON_ROUTE = ["chat", "orchestrator", "context_memory", "policy_engine"]
_ARTIFACT_KEYS = {
    "verdict", "reason_code", "reason_codes", "risk_score", "count", "row_count",
    "execution_ms", "timing_ms", "table", "tables", "table_name", "column",
    "columns", "column_name", "violation", "violations",
}
_AUDIT_KEYS = _ARTIFACT_KEYS | {"action", "event_type"}
_SUMMARY_KEY_NAMES = {
    "reason_code": "reasonCode", "reason_codes": "reasonCodes", "risk_score": "riskScore",
    "row_count": "rowCount", "execution_ms": "executionMs", "timing_ms": "timingMs",
    "table_name": "table", "column_name": "column", "event_type": "eventType",
}
_ARTIFACT_SCHEMAS = {
    "verdict": "string", "reason_code": "string", "reason_codes": "string_list",
    "risk_score": "number", "count": "number", "row_count": "number",
    "execution_ms": "number", "timing_ms": "number", "table": "string",
    "tables": "string_list", "table_name": "string", "column": "string",
    "columns": "string_list", "column_name": "string", "violation": "string",
    "violations": "string_list",
}
_AUDIT_SCHEMAS = {**_ARTIFACT_SCHEMAS, "action": "string", "event_type": "string"}


class ContractError(ValueError):
    """Raised when a runtime row cannot be represented safely."""


class MalformedRuntimeRow(ContractError):
    """Raised when a module or final runtime row has an invalid shape."""


class BrowserEvent(TypedDict, total=False):
    timestamp: Any
    runId: Any
    scenarioId: Any
    sampleId: Any
    sequenceId: str | int | None
    turnId: int
    moduleId: str
    stage: str
    decision: str
    artifact: dict[str, Any]
    audit: dict[str, Any]
    latencyMs: int | float | None
    error: str | None
    detail: str
    traceLines: list[str]
    gnnGraph: dict[str, Any]


class BrowserFinalResult(TypedDict, total=False):
    timestamp: Any
    runId: Any
    scenarioId: Any
    sampleId: Any
    sequenceId: str | int | None
    turnId: int
    decision: str
    detectedAt: str | None
    enforcedAt: str | None
    executed: bool
    dbTouched: bool
    columns: list[str]
    rows: list[Any]
    rawSql: str | None
    finalSql: str | None
    latencyMs: int | float | None
    error: str | None
    events: list[BrowserEvent]
    route: list[str]
    mode: str
    resultType: str


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MalformedRuntimeRow(f"{label} must be a mapping")
    return value


def _required(row: Mapping[str, Any], key: str, label: str) -> Any:
    if key not in row:
        raise MalformedRuntimeRow(f"{label} is missing {key}")
    return row[key]


def _safe_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    identity: dict[str, Any] = {}
    for source, target in (
        ("created_at", "timestamp"), ("run_id", "runId"), ("setting_id", "scenarioId"),
        ("sample_id", "sampleId"), ("sequence_id", "sequenceId"), ("turn_id", "turnId"),
    ):
        if source in row:
            identity[target] = sanitize_json_value(row[source])
    return identity


def _summary(value: Any, allowed: set[str]) -> dict[str, Any]:
    schemas = _AUDIT_SCHEMAS if allowed == _AUDIT_KEYS else _ARTIFACT_SCHEMAS
    picked = safe_summary(value, allowed, schemas)
    return {
        _SUMMARY_KEY_NAMES.get(key, key): value
        for key, value in picked.items()
    }


def _module_id(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    return value if value in ALLOWED_MODULE_IDS else "unknown"


def _safe_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", normalized) else None


def _safe_number(value: Any) -> int | float | None:
    if type(value) is int and 0 <= value <= 1_000_000:
        return value
    if type(value) is float and 0 <= value <= 1_000_000:
        return round(value, 1)
    return None


def _safe_labels(value: Any, limit: int = 4) -> list[str]:
    if not isinstance(value, list):
        return []
    labels: list[str] = []
    for item in value:
        label = _safe_label(item)
        if label:
            labels.append(label)
        if len(labels) >= limit:
            break
    return labels


_GNN_NODE_TYPES = {
    "Role",
    "UserTurn",
    "EntityMention",
    "SemanticConceptCandidate",
    "ScopeCandidate",
    "TargetCandidate",
    "ReferenceExpression",
    "PreviousSemanticState",
}


def _safe_gnn_graph(artifact: Mapping[str, Any], decision: str) -> dict[str, Any] | None:
    """Return a bounded, prompt-free view of the exact M2 runtime graph."""

    debug = artifact.get("graph_debug")
    if not isinstance(debug, Mapping):
        return None
    raw_nodes = debug.get("nodes")
    raw_edges = debug.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        return None

    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for index, raw_node in enumerate(raw_nodes[:120]):
        if not isinstance(raw_node, Mapping):
            continue
        node_id = _safe_label(raw_node.get("node_id"))
        node_type = _safe_label(raw_node.get("node_type"))
        if not node_id or node_type not in _GNN_NODE_TYPES:
            continue
        raw_turn_id = raw_node.get("turn_id")
        turn_id = raw_turn_id if type(raw_turn_id) is int and 1 <= raw_turn_id <= 20 else None
        attrs = raw_node.get("attrs") if isinstance(raw_node.get("attrs"), Mapping) else {}
        raw_label = _safe_label(raw_node.get("label"))
        # Mention and reference labels may contain user-provided identifiers.
        # Render structural labels instead; their concept remains connected by
        # the real graph edge and no raw prompt content enters the browser DTO.
        if node_type == "EntityMention":
            label = f"Mention T{turn_id or '?'}:{index + 1}"
        elif node_type == "ReferenceExpression":
            label = f"Reference T{turn_id or '?'}"
        else:
            label = raw_label or node_type
        node: dict[str, Any] = {
            "id": node_id,
            "type": node_type,
            "label": label,
        }
        if turn_id is not None:
            node["turnNumber"] = turn_id
        if attrs.get("current") is True:
            node["current"] = True
        confidence = attrs.get("confidence")
        if type(confidence) in {int, float} and 0 <= confidence <= 1:
            node["confidence"] = round(float(confidence), 3)
        nodes.append(node)
        node_ids.add(node_id)

    edges: list[dict[str, Any]] = []
    for raw_edge in raw_edges[:240]:
        if not isinstance(raw_edge, Mapping):
            continue
        source = _safe_label(raw_edge.get("source"))
        target = _safe_label(raw_edge.get("target"))
        edge_type = _safe_label(raw_edge.get("edge_type"))
        if not source or not target or not edge_type or source not in node_ids or target not in node_ids:
            continue
        # The runtime adds a reverse relation for every canonical relation.
        # The UI renders one edge to keep the graph readable while reporting
        # the full encoded edge count separately.
        if edge_type.endswith("__rev"):
            continue
        edge: dict[str, Any] = {"source": source, "target": target, "type": edge_type}
        attrs = raw_edge.get("attrs") if isinstance(raw_edge.get("attrs"), Mapping) else {}
        confidence = attrs.get("confidence")
        distance = attrs.get("distance")
        if type(confidence) in {int, float} and 0 <= confidence <= 1:
            edge["confidence"] = round(float(confidence), 3)
        if type(distance) is int and 0 <= distance <= 20:
            edge["distance"] = distance
        edges.append(edge)

    if not nodes:
        return None
    resolution = artifact.get("intent_resolution")
    resolution = resolution if isinstance(resolution, Mapping) else {}
    outputs = {
        key: label
        for key, source_key in (
            ("intent", "primary_intent"),
            ("scope", "scope"),
            ("target", "target_relation"),
            ("securityTransition", "security_transition"),
        )
        for label in [_safe_label(resolution.get(source_key))]
        if label
    }
    current_turn = debug.get("current_turn_id")
    return {
        "graphId": _safe_label(debug.get("graph_id")) or "m2-runtime-graph",
        "currentTurn": current_turn if type(current_turn) is int and 1 <= current_turn <= 20 else None,
        "nodeCount": len(raw_nodes),
        "edgeCount": len(raw_edges),
        "nodes": nodes,
        "edges": edges,
        "outputs": outputs,
        "decision": decision.upper()[:16],
    }


def _module_detail(module_id: str, decision: str, artifact: Mapping[str, Any], audit: Mapping[str, Any]) -> str:
    """Build a bounded, deterministic operator summary from trusted artifacts."""

    verdict = decision.upper()
    if module_id == "C0":
        role = _safe_label(artifact.get("role")) or "unknown-role"
        user_id = _safe_number(artifact.get("user_id"))
        history = _safe_number(artifact.get("history_count"))
        if artifact.get("security_modules_bypassed") is True:
            return f"Conversation memory ready · identity={role}#{user_id if user_id is not None else '?'} · history={history if history is not None else '?'} · security_policy=OFF"
        policy_tables = _safe_number(artifact.get("policy_index_role_tables_count"))
        return f"Context ready · identity={role}#{user_id if user_id is not None else '?'} · history={history if history is not None else '?'} · policy_tables={policy_tables if policy_tables is not None else '?'}"

    if module_id == "M1":
        hits = len(artifact.get("heuristic_hits") or []) if isinstance(artifact.get("heuristic_hits"), list) else 0
        classifier = _safe_label(artifact.get("llm_verdict")) or verdict
        action = "Prompt integrity passed" if verdict == "ALLOW" else "Prompt integrity blocked"
        return f"{action} · heuristic_hits={hits} · classifier={classifier}"

    if module_id == "M2":
        resolution = artifact.get("intent_resolution")
        resolution = resolution if isinstance(resolution, Mapping) else {}
        intent = _safe_label(resolution.get("primary_intent")) or "UNKNOWN"
        scope = _safe_label(resolution.get("scope")) or "UNKNOWN"
        target = _safe_label(resolution.get("target_relation")) or "UNKNOWN"
        transition = _safe_label(resolution.get("security_transition")) or "NONE"
        reason = _safe_label(audit.get("reason_code"))
        suffix = f" · reason={reason}" if reason else ""
        return f"Intent={intent} · scope={scope} · target={target} · security_transition={transition}{suffix}"

    if module_id == "M3":
        plan = artifact.get("access_plan")
        plan = plan if isinstance(plan, Mapping) else {}
        resources = plan.get("requested_resources")
        resources = resources if isinstance(resources, list) else []
        tables = [
            label
            for item in resources
            if isinstance(item, Mapping)
            for label in [_safe_label(item.get("table"))]
            if label
        ][:4]
        scope = _safe_label(plan.get("scope_type")) or "UNKNOWN"
        policy_count = len(plan.get("policy_refs") or []) if isinstance(plan.get("policy_refs"), list) else 0
        predicates = sum(
            len(plan.get(key) or []) if isinstance(plan.get(key), list) else 0
            for key in ("target_identity_predicates", "query_filter_predicates")
        )
        return f"Access plan · scope={scope} · tables={','.join(tables) or 'none'} · policies={policy_count} · predicates={predicates}"

    if module_id == "M4":
        contract = artifact.get("resource_contract")
        contract = contract if isinstance(contract, Mapping) else {}
        scope = _safe_label(contract.get("scope_type")) or "UNKNOWN"
        tables = _safe_number(audit.get("validated_resource_count"))
        columns = _safe_number(audit.get("validated_column_count"))
        predicates = _safe_number(audit.get("target_identity_predicates_count"))
        action = "Resource contract validated" if verdict == "ALLOW" else "Resource contract rejected"
        return f"{action} · scope={scope} · tables={tables if tables is not None else '?'} · columns={columns if columns is not None else '?'} · identity_predicates={predicates if predicates is not None else '?'}"

    if module_id == "M5":
        proof = artifact.get("scope_proof")
        proof = proof if isinstance(proof, Mapping) else {}
        contract = artifact.get("resource_contract")
        contract = contract if isinstance(contract, Mapping) else {}
        scope = _safe_label(contract.get("scope_type")) or _safe_label(audit.get("scope_type")) or "UNKNOWN"
        reason = _safe_label(proof.get("reason_code")) or "UNKNOWN"
        matches = _safe_number(proof.get("matched_count"))
        action = "Row-scope proof passed" if verdict == "ALLOW" else "Row-scope proof failed"
        return f"{action} · scope={scope} · result={reason} · matches={matches if matches is not None else '?'}"

    if module_id == "M6":
        sql_chars = _safe_number(artifact.get("raw_sql_chars"))
        history = _safe_number(artifact.get("history_turn_count"))
        bypassed = artifact.get("security_modules_bypassed") is True
        if bypassed:
            return f"Generated SQL from full schema · policy=OFF · sql_chars={sql_chars if sql_chars is not None else '?'} · history={history if history is not None else '?'}"
        guided = "yes" if artifact.get("m5_guide_included") is True else "no"
        return f"Generated policy-aware SQL · scope_guide={guided} · sql_chars={sql_chars if sql_chars is not None else '?'} · history={history if history is not None else '?'}"

    if module_id == "M7":
        analysis = artifact.get("analysis")
        analysis = analysis if isinstance(analysis, Mapping) else {}
        parser = _safe_label(analysis.get("parser_status")) or "UNKNOWN"
        tables = _safe_labels(artifact.get("tables_referenced"))
        risk_count = len(analysis.get("risks") or []) if isinstance(analysis.get("risks"), list) else 0
        select_only = "yes" if analysis.get("is_select_only") is True else "no"
        action = "SQL conformance passed" if verdict == "ALLOW" else "SQL conformance rejected"
        return f"{action} · parser={parser} · select_only={select_only} · tables={','.join(tables) or 'none'} · risks={risk_count}"

    if module_id == "X1":
        rows = _safe_number(artifact.get("row_count"))
        columns = _safe_labels(artifact.get("columns"))
        db_ms = _safe_number(artifact.get("execution_time_ms"))
        action = "Read-only query executed" if artifact.get("executed") is True else "Database execution failed"
        return f"{action} · rows={rows if rows is not None else 0} · columns={','.join(columns) or 'none'} · db_time={db_ms if db_ms is not None else '?'}ms"

    return f"Module completed · decision={verdict}"


def _module_trace(module_id: str, decision: str, artifact: Mapping[str, Any], audit: Mapping[str, Any]) -> list[str]:
    """Expose bounded operation-level evidence without leaking prompts, SQL, or row data."""

    verdict = decision.upper()
    lines: list[str]

    if module_id == "C0":
        role = _safe_label(artifact.get("role")) or "unknown-role"
        user_id = _safe_number(artifact.get("user_id"))
        history = _safe_number(artifact.get("history_count"))
        if artifact.get("security_modules_bypassed") is True:
            lines = [
                "load server-owned conversation chain -> verified",
                f"hydrate conversation memory -> {history if history is not None else '?'} prior turn(s)",
                f"bind current session identity -> {role}#{user_id if user_id is not None else '?'}",
                f"publish generation context without security policy -> {verdict}",
            ]
        else:
            policy_tables = _safe_number(artifact.get("policy_index_role_tables_count"))
            lines = [
                f"resolve authenticated identity -> {role}#{user_id if user_id is not None else '?'}",
                f"hydrate conversation memory -> {history if history is not None else '?'} prior turn(s)",
                f"index role policy surface -> {policy_tables if policy_tables is not None else '?'} table rule(s)",
                f"publish immutable runtime context -> {verdict}",
            ]
    elif module_id == "M1":
        hits = len(artifact.get("heuristic_hits") or []) if isinstance(artifact.get("heuristic_hits"), list) else 0
        classifier = _safe_label(artifact.get("llm_verdict")) or verdict
        lines = [
            f"scan prompt-integrity heuristics -> {hits} signal(s)",
            f"run semantic integrity classifier -> {classifier}",
            f"seal untrusted instruction boundary -> {verdict}",
        ]
    elif module_id == "M2":
        resolution = artifact.get("intent_resolution")
        resolution = resolution if isinstance(resolution, Mapping) else {}
        intent = _safe_label(resolution.get("primary_intent")) or "UNKNOWN"
        scope = _safe_label(resolution.get("scope")) or "UNKNOWN"
        target = _safe_label(resolution.get("target_relation")) or "UNKNOWN"
        transition = _safe_label(resolution.get("security_transition")) or "NONE"
        reason = _safe_label(audit.get("reason_code")) or "UNSPECIFIED"
        lines = [
            f"classify primary request intent -> {intent}",
            f"resolve requested access scope -> {scope}",
            f"bind target relation to session identity -> {target}",
            f"compare cross-turn security transition -> {transition}",
            f"commit intent-risk verdict -> {verdict} ({reason})",
        ]
    elif module_id == "M3":
        plan = artifact.get("access_plan")
        plan = plan if isinstance(plan, Mapping) else {}
        resources = plan.get("requested_resources")
        resources = resources if isinstance(resources, list) else []
        tables = [
            label
            for item in resources
            if isinstance(item, Mapping)
            for label in [_safe_label(item.get("table"))]
            if label
        ][:4]
        policies = len(plan.get("policy_refs") or []) if isinstance(plan.get("policy_refs"), list) else 0
        identity_predicates = len(plan.get("target_identity_predicates") or []) if isinstance(plan.get("target_identity_predicates"), list) else 0
        filters = len(plan.get("query_filter_predicates") or []) if isinstance(plan.get("query_filter_predicates"), list) else 0
        lines = [
            f"collect requested data resources -> {','.join(tables) or 'none'}",
            f"bind applicable policy references -> {policies} rule(s)",
            f"compile identity predicates -> {identity_predicates} predicate(s)",
            f"compile query filters -> {filters} predicate(s)",
            f"publish policy-scoped access plan -> {verdict}",
        ]
    elif module_id == "M4":
        contract = artifact.get("resource_contract")
        contract = contract if isinstance(contract, Mapping) else {}
        scope = _safe_label(contract.get("scope_type")) or "UNKNOWN"
        tables = _safe_number(audit.get("validated_resource_count"))
        columns = _safe_number(audit.get("validated_column_count"))
        predicates = _safe_number(audit.get("target_identity_predicates_count"))
        lines = [
            f"load role access matrix for scope -> {scope}",
            f"validate requested tables -> {tables if tables is not None else '?'} accepted",
            f"validate requested columns -> {columns if columns is not None else '?'} accepted",
            f"verify identity predicate coverage -> {predicates if predicates is not None else '?'} bound",
            f"seal resource contract -> {verdict}",
        ]
    elif module_id == "M5":
        proof = artifact.get("scope_proof")
        proof = proof if isinstance(proof, Mapping) else {}
        contract = artifact.get("resource_contract")
        contract = contract if isinstance(contract, Mapping) else {}
        scope = _safe_label(contract.get("scope_type")) or _safe_label(audit.get("scope_type")) or "UNKNOWN"
        result = _safe_label(proof.get("reason_code")) or "UNKNOWN"
        matches = _safe_number(proof.get("matched_count"))
        lines = [
            f"materialize row-scope proof obligations -> {scope}",
            "bind proof subject to authenticated session -> locked",
            f"evaluate target membership constraints -> {matches if matches is not None else '?'} match(es)",
            f"resolve proof result -> {result}",
            f"commit row-security verdict -> {verdict}",
        ]
    elif module_id == "M6":
        sql_chars = _safe_number(artifact.get("raw_sql_chars"))
        history = _safe_number(artifact.get("history_turn_count"))
        bypassed = artifact.get("security_modules_bypassed") is True
        guide = "full schema / security bypass" if bypassed else "validated M5 scope guide"
        lines = [
            f"assemble generation context -> {guide}",
            f"attach bounded conversation history -> {history if history is not None else '?'} turn(s)",
            "synthesize read-only PostgreSQL candidate -> complete",
            f"record generated statement envelope -> {sql_chars if sql_chars is not None else '?'} character(s)",
            f"publish SQL candidate -> {verdict}",
        ]
    elif module_id == "M7":
        analysis = artifact.get("analysis")
        analysis = analysis if isinstance(analysis, Mapping) else {}
        parser = _safe_label(analysis.get("parser_status")) or "UNKNOWN"
        tables = _safe_labels(artifact.get("tables_referenced"))
        risks = len(analysis.get("risks") or []) if isinstance(analysis.get("risks"), list) else 0
        select_only = "confirmed" if analysis.get("is_select_only") is True else "rejected"
        lines = [
            f"parse generated SQL syntax tree -> {parser}",
            f"enforce SELECT-only execution class -> {select_only}",
            f"inspect referenced table set -> {','.join(tables) or 'none'}",
            f"scan structural and policy risks -> {risks} finding(s)",
            f"commit SQL conformance verdict -> {verdict}",
        ]
    elif module_id == "X1":
        rows = _safe_number(artifact.get("row_count"))
        columns = _safe_labels(artifact.get("columns"))
        db_ms = _safe_number(artifact.get("execution_time_ms"))
        executed = artifact.get("executed") is True
        lines = [
            "open bounded read-only transaction -> ready",
            f"dispatch validated SQL to database -> {'executed' if executed else 'blocked'}",
            f"collect result shape -> {rows if rows is not None else 0} row(s), {len(columns)} column(s)",
            f"record database timing -> {db_ms if db_ms is not None else '?'}ms",
            f"close transaction and publish result -> {verdict}",
        ]
    else:
        lines = [f"commit module verdict -> {verdict}"]

    return [line[:160] for line in lines[:6]]


def normalize_event(row: Mapping[str, Any]) -> BrowserEvent:
    """Allowlist a raw ``module_events.jsonl`` row for browser use."""

    source = _mapping(row, "module event")
    for key in ("created_at", "run_id", "setting_id", "sequence_id", "sample_id", "turn_id", "module_id", "output"):
        _required(source, key, "module event")
    output = _mapping(source["output"], "module event output")
    for key in ("module_id", "stage", "decision", "artifact", "audit", "latency_ms", "error"):
        _required(output, key, "module event output")
    if source["module_id"] != output["module_id"]:
        raise MalformedRuntimeRow("module event module_id values must match")
    sequence_id = source["sequence_id"]
    if sequence_id is not None and (
        (type(sequence_id) is int and not 0 <= sequence_id <= 1_000_000_000)
        or (isinstance(sequence_id, str) and (not sequence_id or len(sequence_id) > MAX_ID_LENGTH))
        or (type(sequence_id) is not int and not isinstance(sequence_id, str))
    ):
        raise MalformedRuntimeRow("module event sequence_id must be a bounded string, integer, or null")
    if type(source["turn_id"]) is not int:
        raise MalformedRuntimeRow("module event turn_id must be an integer")
    if not isinstance(output["stage"], str) or not isinstance(output["decision"], str):
        raise MalformedRuntimeRow("module event stage and decision must be strings")
    event: BrowserEvent = cast(BrowserEvent, _safe_identity(source))
    event["moduleId"] = _module_id(source["module_id"])
    event["stage"] = output["stage"][:MAX_STRING_LENGTH]
    event["decision"] = output["decision"][:MAX_STRING_LENGTH]
    event["artifact"] = _summary(output["artifact"], _ARTIFACT_KEYS)
    event["audit"] = _summary(output["audit"], _AUDIT_KEYS)
    event["latencyMs"] = sanitize_json_value(output["latency_ms"])
    event["error"] = None if output["error"] is None else redact_error(output["error"])
    event["detail"] = _module_detail(
        str(source["module_id"]),
        str(output["decision"]),
        _mapping(output["artifact"], "module artifact"),
        _mapping(output["audit"], "module audit"),
    )[:MAX_STRING_LENGTH]
    event["traceLines"] = _module_trace(
        str(source["module_id"]),
        str(output["decision"]),
        _mapping(output["artifact"], "module artifact"),
        _mapping(output["audit"], "module audit"),
    )
    if source["module_id"] == "M2":
        graph = _safe_gnn_graph(
            _mapping(output["artifact"], "module artifact"),
            str(output["decision"]),
        )
        if graph is not None:
            event["gnnGraph"] = graph
    return event


def normalize_module_event(row: Mapping[str, Any]) -> BrowserEvent:
    """Descriptive alias for :func:`normalize_event`."""

    return normalize_event(row)


def normalize_events(rows: Iterable[Mapping[str, Any]]) -> list[BrowserEvent]:
    """Normalize module rows in source order; never reorder or deduplicate them."""

    if isinstance(rows, (str, bytes)):
        raise MalformedRuntimeRow("module trace must be a list of rows")
    try:
        return [normalize_event(row) for row in rows]
    except TypeError as exc:
        raise MalformedRuntimeRow("module trace must be iterable") from exc


def _result_rows(value: Any, *, required: bool) -> list[Any]:
    if value is None:
        if required:
            raise MalformedRuntimeRow("executed final result is missing execution_result_json")
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise MalformedRuntimeRow("execution_result_json is not valid JSON") from exc
    if isinstance(value, Mapping):
        if "rows" not in value:
            raise MalformedRuntimeRow("execution_result_json must contain a rows list")
        rows = value["rows"]
    elif isinstance(value, list):
        rows = value
    else:
        raise MalformedRuntimeRow("execution_result_json must be a rows list or mapping")
    if not isinstance(rows, list):
        raise MalformedRuntimeRow("execution_result_json rows must be a list")
    if any(not isinstance(row, Mapping) for row in rows):
        raise MalformedRuntimeRow("execution_result_json rows must contain mappings")
    return [sanitize_json_value(row) for row in rows[:MAX_RESULT_ROWS]]


def _columns(value: Any, *, required: bool) -> list[str]:
    if value is None:
        if required:
            raise MalformedRuntimeRow("executed final result is missing execution_columns")
        return []
    if not isinstance(value, list) or any(not isinstance(column, str) for column in value):
        raise MalformedRuntimeRow("execution_columns must be a list of strings")
    return [column[:MAX_STRING_LENGTH] for column in value[:MAX_RESULT_ROWS]]


def _detector(row: Mapping[str, Any], events: list[BrowserEvent]) -> str | None:
    blocked_at = row.get("blocked_at")
    if isinstance(blocked_at, str):
        return blocked_at if blocked_at in ALLOWED_MODULE_IDS else "unknown"
    for event in events:
        if str(event.get("decision", "")).lower() in {"deny", "error", "blocked"}:
            module_id = event.get("moduleId")
            return module_id if module_id in ALLOWED_MODULE_IDS else "unknown"
    return None


def _decision(value: Any) -> str:
    if not isinstance(value, str):
        raise MalformedRuntimeRow("final decision must be a string")
    normalized = value.upper()
    if normalized not in {"ALLOW", "DENY", "ERROR"}:
        raise MalformedRuntimeRow(f"unsupported final decision: {value}")
    return normalized


def _final_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    identity: dict[str, Any] = {}
    for key, target in (("run_id", "runId"), ("setting_id", "scenarioId"), ("sample_id", "sampleId")):
        value = _required(row, key, "final result")
        if not isinstance(value, str) or not value or len(value) > MAX_ID_LENGTH:
            raise MalformedRuntimeRow(f"final {key} must be a non-empty bounded string")
        identity[target] = value
    turn_id = _required(row, "turn_id", "final result")
    if type(turn_id) is not int or turn_id < 0 or turn_id > 1_000_000_000:
        raise MalformedRuntimeRow("final turn_id must be a bounded non-negative integer")
    identity["turnId"] = turn_id
    if "sequence_id" in row:
        sequence_id = row["sequence_id"]
        if sequence_id is not None and (
            (type(sequence_id) is int and not 0 <= sequence_id <= 1_000_000_000)
            or (isinstance(sequence_id, str) and (not sequence_id or len(sequence_id) > MAX_ID_LENGTH))
            or (sequence_id is not None and type(sequence_id) is not int and not isinstance(sequence_id, str))
        ):
            raise MalformedRuntimeRow("final sequence_id must be a bounded scalar")
        identity["sequenceId"] = sequence_id
    if "created_at" in row:
        identity["timestamp"] = sanitize_json_value(row["created_at"])
    return identity


def _sql(value: Any, key: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise MalformedRuntimeRow(f"final {key} must be a string or null")
    return None if value is None else value[:MAX_STRING_LENGTH]


def _validate_trace_consistency(
    decision: str,
    executed: bool,
    blocked_at: Any,
    events: list[BrowserEvent],
) -> None:
    signals = {str(event.get("decision", "")).lower() for event in events}
    if decision == "ALLOW" and (
        blocked_at is not None or signals & {"deny", "denied", "blocked", "error"}
    ):
        raise MalformedRuntimeRow("final ALLOW contradicts blocked or error module trace")
    if decision == "DENY":
        if type(blocked_at) is not str or blocked_at not in ALLOWED_MODULE_IDS:
            raise MalformedRuntimeRow("final DENY blocked_at must be an allowlisted module ID")
        if executed:
            raise MalformedRuntimeRow("final DENY cannot report executed=true")
        if not events:
            raise MalformedRuntimeRow("final DENY lacks a deny signal")
        terminal = events[-1]
        terminal_decision = str(terminal.get("decision", "")).lower()
        if terminal_decision not in {"deny", "denied", "blocked"}:
            raise MalformedRuntimeRow("final DENY must terminate with deny evidence")
        if terminal.get("moduleId") != blocked_at:
            raise MalformedRuntimeRow("final DENY blocked_at must match terminal deny module")


def normalize_final_result(row: Mapping[str, Any]) -> BrowserFinalResult:
    """Normalize a raw ``raw_turn_outputs.jsonl`` row into a bounded browser DTO."""

    source = _mapping(row, "final result")
    required_keys = (
        "decision", "blocked_at", "executed", "execution_result_json", "execution_columns",
        "raw_sql", "final_sql", "module_trace", "latency_ms", "error",
    )
    for key in required_keys:
        _required(source, key, "final result")
    if type(source["executed"]) is not bool:
        raise MalformedRuntimeRow("final executed must be a boolean")
    if source["module_trace"] is None or not isinstance(source["module_trace"], list):
        raise MalformedRuntimeRow("final module_trace must be a list")

    events = normalize_events(source["module_trace"])
    decision = _decision(source["decision"])
    _validate_trace_consistency(decision, source["executed"], source["blocked_at"], events)
    detected_at = _detector(source, events)
    result: BrowserFinalResult = cast(BrowserFinalResult, _final_identity(source))
    direct_mode = source.get("setting_id") == "direct_sql"
    result["mode"] = "direct" if direct_mode else "trustedsql"
    result["resultType"] = "sql"
    result.update({
        "decision": decision,
        "detectedAt": None if direct_mode else detected_at,
        "enforcedAt": "trustedsql" if decision == "DENY" else None,
        "executed": source["executed"],
        "dbTouched": source["executed"] if decision != "DENY" else False,
        "columns": _columns(source["execution_columns"], required=source["executed"]),
        "rows": _result_rows(source["execution_result_json"], required=source["executed"]),
        "rawSql": _sql(source["raw_sql"], "raw_sql"),
        "finalSql": _sql(source["final_sql"], "final_sql"),
        "latencyMs": sanitize_json_value(source["latency_ms"]),
        "error": None if source["error"] is None else redact_error(source["error"]),
        "events": events,
    })
    if direct_mode:
        route = ["chat", "orchestrator", "context_memory", "sql_generator"]
        if decision == "ALLOW" and source["executed"]:
            route.append("education_db")
    elif decision == "ERROR":
        route = list(_COMMON_ROUTE)
        if detected_at is not None:
            route.append(detected_at)
    else:
        route = [*_COMMON_ROUTE, "trustedsql"]
        if decision == "ALLOW" and source["executed"]:
            route.append("education_db")
    result["route"] = route
    return result


def normalize_final_row(row: Mapping[str, Any]) -> BrowserFinalResult:
    """Descriptive alias for :func:`normalize_final_result`."""

    return normalize_final_result(row)


__all__ = [
    "ALLOWED_MODULE_IDS",
    "BrowserEvent",
    "BrowserFinalResult",
    "ContractError",
    "MalformedRuntimeRow",
    "normalize_event",
    "normalize_events",
    "normalize_final_result",
    "normalize_module_event",
    "normalize_final_row",
]
