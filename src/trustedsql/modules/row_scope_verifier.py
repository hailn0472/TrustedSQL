from __future__ import annotations

import heapq
import itertools
import re
from dataclasses import asdict
from typing import Any

from trustedsql.db.executor import DatabaseExecutor
from trustedsql.modules.common import timed_module
from trustedsql.policy.row_filter import current_user_bindings
from trustedsql.schemas import ModuleResult, ResourceContract, ScopeProofResult, TrustedContext, VerifiedAuthorization
from trustedsql.sql.schema import ForeignKey, SchemaGraph, normalize_name


def run(
    context: TrustedContext,
    resource: ResourceContract | None,
    db: DatabaseExecutor,
) -> tuple[ScopeProofResult | None, VerifiedAuthorization | None, ModuleResult]:
    def _inner() -> ModuleResult:
        if resource is None:
            return ModuleResult("M5", "row_scope_proof_verifier", "DENY", audit={"reason_code": "MISSING_RESOURCE_CONTRACT"})
        if resource.scope_type == "ALL":
            proof = ScopeProofResult("ALLOW", reason_code="SCOPE_ALL")
            authorization = _authorization_from_resource(resource, context, verified_targets=[])
            return _allow_result(resource, proof, authorization)
        if not resource.row_filter:
            return ModuleResult("M5", "row_scope_proof_verifier", "DENY", audit={"reason_code": "MISSING_ROW_FILTER"})
        bindings = current_user_bindings(resource.row_filter, context.schema_graph, context.user_id)
        if not bindings:
            return ModuleResult("M5", "row_scope_proof_verifier", "DENY", audit={"reason_code": "UNRESOLVED_CURRENT_USER_BINDING"})
        if not resource.requires_db_proof:
            proof = ScopeProofResult("ALLOW", reason_code="NO_EXTERNAL_TARGET")
            authorization = _authorization_from_resource(resource, context, verified_targets=[])
            return _allow_result(resource, proof, authorization)
        proof = _compile_and_run_proof(context, resource, db)
        if proof.decision != "ALLOW":
            return ModuleResult(
                "M5",
                "row_scope_proof_verifier",
                "DENY",
                artifact={"scope_proof": asdict(proof), "resource_contract": asdict(resource)},
                audit={"reason_code": proof.reason_code},
                raw_objects={"scope_proof_result": asdict(proof)},
            )
        authorization = _authorization_from_resource(
            resource,
            context,
            verified_targets=[dict(item) for item in resource.target_identity_predicates],
        )
        return _allow_result(resource, proof, authorization)

    result = timed_module("M5", "row_scope_proof_verifier", _inner)
    proof_data = result.artifact.get("scope_proof") if result.artifact else None
    auth_data = result.artifact.get("verified_authorization") if result.artifact else None
    proof = ScopeProofResult(**proof_data) if proof_data else None
    authorization = VerifiedAuthorization(**auth_data) if auth_data else None
    return proof, authorization, result


def _allow_result(
    resource: ResourceContract,
    proof: ScopeProofResult,
    authorization: VerifiedAuthorization,
) -> ModuleResult:
    return ModuleResult(
        "M5",
        "row_scope_proof_verifier",
        "ALLOW",
        artifact={
            "scope_proof": asdict(proof),
            "proof_sql": proof.proof_sql,
            "resource_contract": asdict(resource),
            "verified_authorization": asdict(authorization),
        },
        audit={
            "policy_refs": resource.policy_refs,
            "scope_type": resource.scope_type,
            "target_identity_predicates_count": len(resource.target_identity_predicates),
            "query_filter_predicates_excluded_count": len(resource.query_filter_predicates),
        },
        raw_objects={"scope_proof_result": asdict(proof), "verified_authorization": asdict(authorization)},
    )


def _authorization_from_resource(
    resource: ResourceContract,
    context: TrustedContext,
    *,
    verified_targets: list[dict[str, Any]],
) -> VerifiedAuthorization:
    return VerifiedAuthorization(
        policy_refs=list(resource.policy_refs),
        scope_type=resource.scope_type,
        current_user_bindings=current_user_bindings(resource.row_filter, context.schema_graph, context.user_id),
        verified_targets=verified_targets,
    )


def _compile_and_run_proof(context: TrustedContext, resource: ResourceContract, db: DatabaseExecutor) -> ScopeProofResult:
    anchor = normalize_name(resource.scope_anchor_table or _anchor_from_row_filter(resource.row_filter) or "")
    if not anchor or not context.schema_graph.has_table(anchor):
        return ScopeProofResult("DENY", reason_code="INVALID_SCOPE_ANCHOR")
    predicates = _valid_target_predicates(resource.target_identity_predicates, context.schema_graph)
    if len(predicates) != len(resource.target_identity_predicates):
        return ScopeProofResult("DENY", reason_code="INVALID_TARGET_PREDICATE")
    if not predicates:
        return ScopeProofResult("ALLOW", reason_code="NO_EXTERNAL_TARGET")
    sql, params = _compile_exists_sql(
        graph=context.schema_graph,
        anchor_table=anchor,
        row_filter=str(resource.row_filter),
        user_id=context.user_id,
        predicates=predicates,
    )
    if not sql:
        return ScopeProofResult("DENY", reason_code="UNSUPPORTED_SCOPE_PROOF")
    ok, error = db.exists(sql, params)
    if error:
        return ScopeProofResult("DENY", proof_sql=sql, reason_code=f"DB_PROOF_ERROR:{error}")
    return ScopeProofResult(
        "ALLOW" if ok else "DENY",
        proof_sql=sql,
        matched_count=1 if ok else 0,
        reason_code="PROOF_TRUE" if ok else "PROOF_FALSE",
    )


def _compile_exists_sql(
    graph: SchemaGraph,
    anchor_table: str,
    row_filter: str,
    user_id: int,
    predicates: list[dict[str, Any]],
) -> tuple[str | None, dict[str, Any]]:
    anchor = normalize_name(anchor_table)
    aliases = {anchor: "t0"}
    joins: list[str] = []
    params: dict[str, Any] = {"user_id": user_id}
    alias_counter = 1
    preferred_tables = _tables_from_row_filter(row_filter, graph)
    for predicate in predicates:
        table = normalize_name(str(predicate["table"]))
        if table in aliases:
            continue
        path = _policy_grounded_join_path(graph, anchor, table, preferred_tables)
        if not path:
            return None, {}
        current_table = anchor
        for fk in path:
            next_table = graph.neighbor_for_fk(current_table, fk)
            if next_table not in aliases:
                right_alias = f"t{alias_counter}"
                alias_counter += 1
                joins.append(
                    f"JOIN {next_table} {right_alias} ON "
                    f"{_join_condition(fk, aliases[current_table], right_alias, current_table)}"
                )
                aliases[next_table] = right_alias
            current_table = next_table
    where_parts = [_compile_row_filter(row_filter, aliases)]
    for index, predicate in enumerate(predicates):
        table = normalize_name(str(predicate["table"]))
        column = normalize_name(str(predicate["column"]))
        if table not in aliases or not graph.has_column(table, column):
            return None, {}
        compiled, values = _compile_entity_predicate(
            aliases[table], column, str(predicate.get("operator") or "="), predicate["value"], index
        )
        if not compiled:
            return None, {}
        where_parts.append(compiled)
        params.update(values)
    sql = f"SELECT EXISTS (SELECT 1 FROM {anchor} {aliases[anchor]} {' '.join(joins)} WHERE {' AND '.join(where_parts)})"
    return sql, params


def _policy_grounded_join_path(
    graph: SchemaGraph,
    start: str,
    end: str,
    preferred_tables: set[str],
) -> list[ForeignKey]:
    start, end = normalize_name(start), normalize_name(end)
    if start == end:
        return []
    adjacency: dict[str, list[tuple[str, ForeignKey]]] = {}
    for fk in graph.foreign_keys:
        adjacency.setdefault(fk.table, []).append((fk.ref_table, fk))
        adjacency.setdefault(fk.ref_table, []).append((fk.table, fk))
    serial = itertools.count()
    queue: list[tuple[int, int, int, str, list[ForeignKey]]] = [(0, 0, next(serial), start, [])]
    best: dict[str, tuple[int, int]] = {start: (0, 0)}
    while queue:
        penalty, length, _, table, path = heapq.heappop(queue)
        if table == end:
            return path
        if best.get(table, (10**9, 10**9)) < (penalty, length):
            continue
        for neighbor, fk in adjacency.get(table, []):
            next_cost = (penalty + (0 if neighbor in preferred_tables or neighbor == end else 1), length + 1)
            if next_cost >= best.get(neighbor, (10**9, 10**9)):
                continue
            best[neighbor] = next_cost
            heapq.heappush(queue, (next_cost[0], next_cost[1], next(serial), neighbor, path + [fk]))
    return []


def _tables_from_row_filter(row_filter: str, graph: SchemaGraph) -> set[str]:
    names = {normalize_name(value) for value in re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)", row_filter, re.I)}
    names |= {normalize_name(value) for value in re.findall(r"\b([A-Za-z_][\w]*)\.", row_filter)}
    return {name for name in names if graph.has_table(name)}


def _valid_target_predicates(
    predicates: list[dict[str, Any]],
    graph: SchemaGraph,
) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for predicate in predicates:
        table = normalize_name(str(predicate.get("table") or ""))
        column = normalize_name(str(predicate.get("column") or ""))
        operator = _normalize_operator(predicate.get("operator"), predicate.get("value"))
        value = predicate.get("value")
        if not graph.has_column(table, column) or not _is_valid_value(value, operator):
            continue
        if _looks_like_identifier_column(column) and _has_non_numeric_identifier_value(value):
            continue
        valid.append({"table": table, "column": column, "operator": operator, "value": value})
    return valid


def _compile_entity_predicate(alias: str, column: str, operator: str, value: Any, index: int) -> tuple[str | None, dict[str, Any]]:
    operator = _normalize_operator(operator, value)
    name = f"entity_{index}"
    qualified = f"{alias}.{column}"
    if operator in {"IS NULL", "IS NOT NULL"} and value is None:
        return f"{qualified} {operator}", {}
    if operator == "BETWEEN" and isinstance(value, list) and len(value) == 2:
        return f"{qualified} BETWEEN :{name}_low AND :{name}_high", {f"{name}_low": value[0], f"{name}_high": value[1]}
    if operator == "IN" and isinstance(value, list) and value:
        params = {f"{name}_{i}": item for i, item in enumerate(value)}
        return f"{qualified} IN ({', '.join(':' + key for key in params)})", params
    if operator in {"=", "!=", "LIKE", ">", ">=", "<", "<="}:
        return f"{qualified} {operator} :{name}", {name: value}
    return None, {}


def _normalize_operator(value: Any, predicate_value: Any = None) -> str:
    if value in (None, "") and isinstance(predicate_value, str) and "%" in predicate_value:
        return "LIKE"
    operator = str(value or "=").upper()
    return operator if operator in {"=", "!=", "LIKE", "IN", "BETWEEN", ">", ">=", "<", "<=", "IS NULL", "IS NOT NULL"} else "="


def _is_valid_value(value: Any, operator: str) -> bool:
    if operator in {"IS NULL", "IS NOT NULL"}:
        return value is None
    if operator == "IN":
        return isinstance(value, list) and bool(value) and all(_is_scalar(item) for item in value)
    if operator == "BETWEEN":
        return isinstance(value, list) and len(value) == 2 and all(_is_scalar(item) for item in value)
    return _is_scalar(value)


def _is_scalar(value: Any) -> bool:
    return value not in (None, "") and isinstance(value, (str, int, float, bool))


def _has_non_numeric_identifier_value(value: Any) -> bool:
    values = value if isinstance(value, list) else [value]
    return any(isinstance(item, str) and not item.strip().isdigit() for item in values)


def _looks_like_identifier_column(column: str) -> bool:
    return normalize_name(column) == "id" or normalize_name(column).endswith("_id")


def _compile_row_filter(row_filter: str, aliases: dict[str, str]) -> str:
    compiled = row_filter.replace("@user_id", ":user_id")
    for table in sorted(aliases, key=len, reverse=True):
        compiled = re.sub(rf"\b{re.escape(table)}\.", f"{aliases[table]}.", compiled)
    return compiled


def _join_condition(fk: ForeignKey, left_alias: str, right_alias: str, current_table: str) -> str:
    if normalize_name(current_table) == fk.table:
        return f"{left_alias}.{fk.column} = {right_alias}.{fk.ref_column}"
    return f"{left_alias}.{fk.ref_column} = {right_alias}.{fk.column}"


def _anchor_from_row_filter(row_filter: str | None) -> str | None:
    if not row_filter:
        return None
    match = re.search(r"\b([A-Za-z_][\w]*)\.", row_filter)
    return normalize_name(match.group(1)) if match else None
