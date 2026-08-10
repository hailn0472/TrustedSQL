from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


CREATE_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+(?:public\.)?\"?([A-Za-z_][\w]*)\"?\s*\((.*?)\);", re.IGNORECASE | re.DOTALL)
COLUMN_RE = re.compile(r"^\s*\"?([A-Za-z_][\w]*)\"?\s+")
FOREIGN_KEY_RE = re.compile(
    r"FOREIGN\s+KEY\s*\((?P<local>[^)]*)\)\s+REFERENCES\s+(?:public\.)?\"?(?P<table>[A-Za-z_][\w]*)\"?\s*\((?P<ref>[^)]*)\)",
    re.IGNORECASE,
)
PRIMARY_UNIQUE_RE = re.compile(r"(?:PRIMARY\s+KEY|UNIQUE)\s*\((?P<columns>[^)]*)\)", re.IGNORECASE)
TABLE_CONSTRAINT_PREFIXES = ("CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "EXCLUDE")


@dataclass(frozen=True)
class ForeignKey:
    table: str
    column: str
    ref_table: str
    ref_column: str


@dataclass
class SchemaIndex:
    ddl_text: str
    columns_by_table: dict[str, list[str]]
    ddl_by_table: dict[str, str]
    foreign_keys: list[ForeignKey] | None = None
    source_path: str | None = None

    def table_names(self) -> list[str]:
        return sorted(self.columns_by_table)

    def has_column(self, table: str, column: str) -> bool:
        return column.lower() in set(self.columns_by_table.get(table.lower(), []))

    def scoped_ddl(self, tables: list[str], columns_by_table: dict[str, list[str]] | None = None) -> str:
        chunks: list[str] = []
        included_tables = {table.lower() for table in tables}
        for table in tables:
            t = table.lower()
            if t not in self.columns_by_table:
                continue
            if not columns_by_table:
                chunks.append(self.ddl_by_table.get(t, f"-- table {t}: {', '.join(self.columns_by_table[t])}"))
            else:
                chunks.append(self._filtered_create_table(t, columns_by_table.get(t, []), included_tables, columns_by_table))
        if columns_by_table:
            relationships = self._scoped_relationships(included_tables, columns_by_table)
            if relationships:
                chunks.append("-- Relationships among included tables:\n" + "\n".join(relationships))
        return "\n\n".join(chunks)

    def _filtered_create_table(
        self,
        table: str,
        columns: list[str],
        included_tables: set[str] | None = None,
        columns_by_table: dict[str, list[str]] | None = None,
    ) -> str:
        allowed = {column.lower() for column in columns}
        kept = [column for column in self.columns_by_table.get(table, []) if column in allowed]
        if not kept:
            return f"CREATE TABLE public.{table} (\n);"
        ddl = self.ddl_by_table.get(table)
        if not ddl:
            body = ",\n".join(f"  {column} text" for column in kept)
            return f"CREATE TABLE public.{table} (\n{body}\n);"
        match = CREATE_TABLE_RE.search(ddl)
        if not match:
            body = ",\n".join(f"  {column} text" for column in kept)
            return f"CREATE TABLE public.{table} (\n{body}\n);"
        column_lines: list[str] = []
        for raw_line in _split_top_level_commas(match.group(2)):
            line = raw_line.strip().rstrip(",")
            upper = line.upper()
            if not line:
                continue
            col_match = COLUMN_RE.match(line)
            if upper.startswith(TABLE_CONSTRAINT_PREFIXES):
                if self._constraint_is_safe_for_scope(line, allowed, included_tables or set(), columns_by_table or {}):
                    column_lines.append(f"  {line}")
            elif col_match and col_match.group(1).lower() in allowed:
                column_lines.append(f"  {line}")
        body = ",\n".join(column_lines)
        return f"CREATE TABLE public.{table} (\n{body}\n);"

    def _constraint_is_safe_for_scope(
        self,
        line: str,
        allowed_columns: set[str],
        included_tables: set[str],
        columns_by_table: dict[str, list[str]],
    ) -> bool:
        upper = line.upper()
        if "FOREIGN KEY" in upper:
            match = FOREIGN_KEY_RE.search(line)
            if not match:
                return False
            local_columns = _identifier_list(match.group("local"))
            ref_table = match.group("table").lower()
            ref_columns = _identifier_list(match.group("ref"))
            ref_allowed = {column.lower() for column in columns_by_table.get(ref_table, [])}
            return bool(local_columns) and set(local_columns) <= allowed_columns and ref_table in included_tables and set(ref_columns) <= ref_allowed
        if "PRIMARY KEY" in upper or "UNIQUE" in upper:
            match = PRIMARY_UNIQUE_RE.search(line)
            if not match:
                return False
            key_columns = _identifier_list(match.group("columns"))
            return bool(key_columns) and set(key_columns) <= allowed_columns
        return False

    def _scoped_relationships(self, included_tables: set[str], columns_by_table: dict[str, list[str]]) -> list[str]:
        relationships: list[str] = []
        for table in sorted(included_tables):
            ddl = self.ddl_by_table.get(table)
            if not ddl:
                continue
            allowed = {column.lower() for column in columns_by_table.get(table, [])}
            match = CREATE_TABLE_RE.search(ddl)
            if not match:
                continue
            for raw_line in _split_top_level_commas(match.group(2)):
                line = raw_line.strip().rstrip(",")
                fk = FOREIGN_KEY_RE.search(line)
                if not fk:
                    continue
                local_columns = _identifier_list(fk.group("local"))
                ref_table = fk.group("table").lower()
                ref_columns = _identifier_list(fk.group("ref"))
                ref_allowed = {column.lower() for column in columns_by_table.get(ref_table, [])}
                if set(local_columns) <= allowed and ref_table in included_tables and set(ref_columns) <= ref_allowed:
                    relationships.append(f"-- public.{table}({', '.join(local_columns)}) -> public.{ref_table}({', '.join(ref_columns)})")
        return relationships


def load_schema_index(path: Path) -> SchemaIndex:
    text = path.read_text(encoding="utf-8-sig")
    columns_by_table: dict[str, list[str]] = {}
    ddl_by_table: dict[str, str] = {}
    foreign_keys: list[ForeignKey] = []
    for match in CREATE_TABLE_RE.finditer(text):
        table = match.group(1).lower()
        ddl_by_table[table] = match.group(0)
        columns: list[str] = []
        for raw_line in _split_top_level_commas(match.group(2)):
            line = raw_line.strip().rstrip(",")
            upper = line.upper()
            fk = FOREIGN_KEY_RE.search(line)
            if fk:
                local_columns = _identifier_list(fk.group("local"))
                ref_columns = _identifier_list(fk.group("ref"))
                ref_table = fk.group("table").lower()
                for local, ref in zip(local_columns, ref_columns, strict=False):
                    foreign_keys.append(ForeignKey(table, local, ref_table, ref))
            if not line or upper.startswith(TABLE_CONSTRAINT_PREFIXES):
                continue
            col_match = COLUMN_RE.match(line)
            if col_match:
                columns.append(col_match.group(1).lower())
        columns_by_table[table] = columns
    return SchemaIndex(text, columns_by_table, ddl_by_table, foreign_keys, str(path))


def normalize_name(value: str | None) -> str:
    return str(value or "").strip().strip('"').lower()


def _split_top_level_commas(body: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_single_quote = False
    in_double_quote = False
    i = 0
    while i < len(body):
        char = body[i]
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        elif not in_single_quote and not in_double_quote:
            if char == "(":
                depth += 1
            elif char == ")" and depth > 0:
                depth -= 1
            elif char == "," and depth == 0:
                parts.append("".join(current).strip())
                current = []
                i += 1
                continue
        current.append(char)
        i += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _identifier_list(value: str) -> list[str]:
    identifiers: list[str] = []
    for item in value.split(","):
        cleaned = item.strip().strip('"').lower()
        if cleaned:
            identifiers.append(cleaned)
    return identifiers

