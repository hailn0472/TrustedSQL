from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from trustedsql.sql.schema import SchemaGraph, normalize_name


def current_user_bindings(
    row_filter: str | None,
    graph: SchemaGraph,
    user_id: int,
) -> list[dict[str, Any]]:
    """Resolve @user_id comparisons in their SQL scope and validate them against the schema."""
    if not row_filter:
        return []
    try:
        expression = sqlglot.parse_one(f"SELECT 1 WHERE {row_filter}", read="postgres")
    except Exception:
        return []

    bindings: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for scope in traverse_scope(expression):
        for equality in scope.expression.find_all(exp.EQ):
            if equality.find_ancestor(exp.Select) is not scope.expression:
                continue
            column = _user_parameter_column(equality)
            if column is None:
                continue
            table = _resolve_column_table(column, scope)
            column_name = normalize_name(column.name)
            if not table or not graph.has_column(table, column_name):
                continue
            key = (table, column_name)
            if key in seen:
                continue
            seen.add(key)
            bindings.append({
                "table": table,
                "column": column_name,
                "operator": "=",
                "value": user_id,
            })
    return bindings


def predicate_matches_binding(
    predicate: dict[str, Any],
    bindings: list[dict[str, Any]],
) -> bool:
    if str(predicate.get("operator") or "=").upper() != "=":
        return False
    table = normalize_name(str(predicate.get("table") or ""))
    column = normalize_name(str(predicate.get("column") or ""))
    value = predicate.get("value")
    return any(
        table == binding["table"]
        and column == binding["column"]
        and str(value).strip() == str(binding["value"])
        for binding in bindings
    )


def _user_parameter_column(equality: exp.EQ) -> exp.Column | None:
    if isinstance(equality.left, exp.Column) and _is_user_parameter(equality.right):
        return equality.left
    if isinstance(equality.right, exp.Column) and _is_user_parameter(equality.left):
        return equality.right
    return None


def _is_user_parameter(value: exp.Expression) -> bool:
    return isinstance(value, exp.Parameter) and normalize_name(str(value.this)) == "user_id"


def _resolve_column_table(column: exp.Column, scope: Scope) -> str | None:
    qualifier = normalize_name(column.table or "")
    if qualifier:
        source = scope.sources.get(qualifier)
        if isinstance(source, exp.Table):
            return normalize_name(source.name)
        if qualifier:
            return qualifier

    tables = [
        normalize_name(source.name)
        for source in scope.sources.values()
        if isinstance(source, exp.Table)
    ]
    unique_tables = list(dict.fromkeys(tables))
    return unique_tables[0] if len(unique_tables) == 1 else None
