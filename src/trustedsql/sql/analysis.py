from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from trustedsql.sql.schema import SchemaGraph, normalize_name


@dataclass
class SqlAnalysis:
    parser_status: str
    is_select_only: bool = False
    multi_statement: bool = False
    tables: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    columns: list[dict[str, str | None]] = field(default_factory=list)
    normalized_sql: str | None = None
    risks: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "parser_status": self.parser_status,
            "is_select_only": self.is_select_only,
            "multi_statement": self.multi_statement,
            "tables": self.tables,
            "aliases": self.aliases,
            "columns": self.columns,
            "normalized_sql": self.normalized_sql,
            "risks": self.risks,
            "error": self.error,
        }


DANGEROUS_PATTERNS = [
    (re.compile(r"\b(drop|delete|insert|update|alter|truncate|create|grant|revoke|copy)\b", re.I), "DANGEROUS_KEYWORD"),
    (re.compile(r"--|/\*|\*/"), "COMMENT_MARKER"),
    (re.compile(r"\bunion\s+select\b", re.I), "UNION_SELECT"),
    (re.compile(r"(?<!:):[A-Za-z_]\w*|@[A-Za-z_]\w*"), "UNRESOLVED_PLACEHOLDER"),
]


def analyze_sql(sql: str | None, schema: SchemaGraph | None = None) -> SqlAnalysis:
    if not sql or not str(sql).strip():
        return SqlAnalysis(parser_status="NO_SQL", error="empty sql")
    text = str(sql).strip().rstrip(";")
    risks = [name for pattern, name in DANGEROUS_PATTERNS if pattern.search(text)]
    try:
        parsed_parts = [part for part in sqlglot.parse(text, read="postgres") if part]
        multi_statement = len(parsed_parts) > 1
        parsed = parsed_parts[0] if parsed_parts else None
    except Exception as exc:  # noqa: BLE001
        return SqlAnalysis(parser_status="FAILED", multi_statement=False, risks=risks, error=str(exc))
    if parsed is None:
        return SqlAnalysis(parser_status="FAILED", multi_statement=False, risks=risks, error="sql parser returned no statement")
    tables, aliases, columns = _scope_references(parsed, schema)
    is_select_only = isinstance(parsed, exp.Select) or bool(parsed.find(exp.Select))
    if parsed.key.upper() not in {"SELECT", "WITH"}:
        is_select_only = False
    normalized = parsed.sql(dialect="postgres")
    return SqlAnalysis(
        parser_status="PARSED",
        is_select_only=is_select_only,
        multi_statement=multi_statement,
        tables=sorted(set(tables)),
        aliases=aliases,
        columns=columns,
        normalized_sql=normalized,
        risks=risks,
    )


def _resolve_unqualified(column: str, tables: list[str], schema: SchemaGraph | None) -> str | None:
    if not schema:
        return None
    matches = [table for table in tables if schema.has_column(table, column)]
    if len(matches) == 1:
        return matches[0]
    return None


def _scope_references(parsed: exp.Expression, schema: SchemaGraph | None) -> tuple[list[str], dict[str, str], list[dict[str, str | None]]]:
    all_tables: list[str] = []
    all_aliases: dict[str, str] = {}
    columns: list[dict[str, str | None]] = []
    seen_columns: set[int] = set()
    for scope in traverse_scope(parsed):
        scope_aliases = _scope_aliases(scope)
        scope_tables = sorted({table for table in scope_aliases.values() if table})
        for table in scope_tables:
            if table not in all_tables:
                all_tables.append(table)
        for alias, table in scope_aliases.items():
            all_aliases.setdefault(alias, table)
        for col in scope.columns:
            marker = id(col)
            if marker in seen_columns:
                continue
            seen_columns.add(marker)
            table = normalize_name(col.table) if col.table else None
            resolved_table = scope_aliases.get(table, table) if table else _resolve_unqualified(col.name, scope_tables, schema)
            columns.append({"table": resolved_table, "column": normalize_name(col.name)})
    if not all_tables:
        for table_exp in parsed.find_all(exp.Table):
            table = normalize_name(table_exp.name)
            alias = normalize_name(table_exp.alias_or_name)
            if table not in all_tables:
                all_tables.append(table)
            all_aliases.setdefault(alias, table)
    return sorted(set(all_tables)), all_aliases, columns


def _scope_aliases(scope: Scope) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for alias, (_, source) in scope.selected_sources.items():
        alias_name = normalize_name(alias)
        if isinstance(source, exp.Table):
            aliases[alias_name] = normalize_name(source.name)
        elif isinstance(source, Scope):
            base_tables = sorted({normalize_name(table.name) for table in source.tables})
            if len(base_tables) == 1:
                aliases[alias_name] = base_tables[0]
    return aliases
