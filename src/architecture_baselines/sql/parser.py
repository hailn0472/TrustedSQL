from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp


DDL_DML_RE = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|GRANT|REVOKE|COPY)\b", re.IGNORECASE)
COMMENT_RE = re.compile(r"(--|/\*)")
NUMERIC_TAUTOLOGY_RE = re.compile(r"\b(?:or|and)\s+(\d+)\s*=\s*\1\b", re.IGNORECASE)
QUOTED_TAUTOLOGY_RE = re.compile(r"\b(?:or|and)\s+(['\"])([^'\"]+)\1\s*=\s*\1\2\1", re.IGNORECASE)
BOOLEAN_TAUTOLOGY_RE = re.compile(r"\b(?:or|and)\s+true\b", re.IGNORECASE)


@dataclass
class SqlColumnRef:
    table: str | None
    column: str

    def to_dict(self) -> dict[str, str | None]:
        return {"table": self.table, "column": self.column}


@dataclass
class SqlSignature:
    normalized_sql: str | None
    parser_status: str
    is_select_only: bool
    multi_statement: bool
    tables: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    columns: list[SqlColumnRef] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "normalized_sql": self.normalized_sql,
            "parser_status": self.parser_status,
            "is_select_only": self.is_select_only,
            "multi_statement": self.multi_statement,
            "tables": self.tables,
            "aliases": self.aliases,
            "columns": [column.to_dict() for column in self.columns],
            "risks": self.risks,
            "error": self.error,
        }


def normalize_sql(sql: str) -> str:
    return " ".join((sql or "").strip().strip(";").split())


def analyze_sql(sql: str, dialect: str = "postgres") -> SqlSignature:
    normalized = normalize_sql(sql)
    risks: list[str] = []
    if not normalized:
        return SqlSignature(None, "FAILED", False, False, risks=["empty_sql"], error="empty SQL")
    if COMMENT_RE.search(normalized):
        risks.append("sql_comment_marker")
    if DDL_DML_RE.search(normalized):
        risks.append("ddl_dml_keyword")
    if NUMERIC_TAUTOLOGY_RE.search(normalized) or QUOTED_TAUTOLOGY_RE.search(normalized) or BOOLEAN_TAUTOLOGY_RE.search(normalized):
        risks.append("possible_tautology")
    try:
        expressions = sqlglot.parse(normalized, read=dialect)
    except Exception as exc:
        return SqlSignature(normalized, "FAILED", False, ";" in normalized, risks=risks, error=str(exc))

    multi = len(expressions) != 1
    is_select_only = bool(expressions) and isinstance(expressions[0], exp.Select) and not multi
    expression = expressions[0] if expressions else None
    tables: list[str] = []
    aliases: dict[str, str] = {}
    columns: list[SqlColumnRef] = []
    if expression is not None:
        for table in expression.find_all(exp.Table):
            table_name = (table.name or "").lower()
            if table_name:
                tables.append(table_name)
                alias = table.alias
                if alias:
                    aliases[alias.lower()] = table_name
        for subquery in expression.find_all(exp.Subquery):
            alias = subquery.alias
            if not alias:
                continue
            inner_tables = {
                (table.name or "").lower()
                for table in subquery.find_all(exp.Table)
                if table.name
            }
            if len(inner_tables) == 1:
                aliases[alias.lower()] = next(iter(inner_tables))
        tables = sorted(set(tables))
        for column in expression.find_all(exp.Column):
            table_name = (column.table or "").lower() or None
            if table_name and table_name in aliases:
                table_name = aliases[table_name]
            columns.append(SqlColumnRef(table=table_name, column=(column.name or "").lower()))
        for projection in expression.expressions:
            if isinstance(projection, exp.Star):
                columns.append(SqlColumnRef(table=None, column="*"))
    return SqlSignature(normalized, "PARSED", is_select_only, multi, tables, aliases, columns, risks)


def ensure_select_only(sql: str) -> tuple[bool, dict[str, Any]]:
    signature = analyze_sql(sql)
    safe = signature.parser_status == "PARSED" and signature.is_select_only and not signature.risks
    return safe, signature.to_dict()

