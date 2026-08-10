from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CREATE_RE = re.compile(r"CREATE\s+TABLE\s+(?:public\.)?([A-Za-z_][\w]*)\s*\((.*?)(?=\n\);|\);)", re.IGNORECASE | re.DOTALL)
FK_RE = re.compile(
    r"FOREIGN\s+KEY\s*\((?P<col>[A-Za-z_][\w]*)\)\s+REFERENCES\s+(?:public\.)?(?P<ref_table>[A-Za-z_][\w]*)\s*\((?P<ref_col>[A-Za-z_][\w]*)\)",
    re.IGNORECASE,
)


@dataclass
class ForeignKey:
    table: str
    column: str
    ref_table: str
    ref_column: str

    def edge(self, left_alias: str, right_alias: str, left_table: str) -> str:
        if normalize_name(left_table) == self.table:
            return f"{left_alias}.{self.column} = {right_alias}.{self.ref_column}"
        return f"{left_alias}.{self.ref_column} = {right_alias}.{self.column}"


@dataclass
class SchemaGraph:
    ddl: str
    table_columns: dict[str, list[str]]
    table_chunks: dict[str, str]
    foreign_keys: list[ForeignKey] = field(default_factory=list)

    def has_table(self, table: str) -> bool:
        return normalize_name(table) in self.table_columns

    def has_column(self, table: str, column: str) -> bool:
        return normalize_name(column) in self.table_columns.get(normalize_name(table), [])

    def structural_columns(self, table: str) -> set[str]:
        table = normalize_name(table)
        cols = {"id"}
        for col in self.table_columns.get(table, []):
            if col.endswith("_id") or col == "id":
                cols.add(col)
        for fk in self.foreign_keys:
            if fk.table == table:
                cols.add(fk.column)
            if fk.ref_table == table:
                cols.add(fk.ref_column)
        return cols

    def scoped_ddl(
        self,
        tables: list[str],
        columns_by_table: dict[str, list[str]] | None = None,
        *,
        include_structural: bool = True,
    ) -> str:
        selected = [normalize_name(t) for t in tables if self.has_table(t)]
        blocks: list[str] = []
        for table in selected:
            if not columns_by_table:
                blocks.append(self.table_chunks[table])
                continue
            wanted = {normalize_name(c) for c in columns_by_table.get(table, [])}
            if include_structural:
                wanted |= self.structural_columns(table)
            blocks.append(_filter_table_chunk(self.table_chunks[table], wanted))
        rels = []
        selected_set = set(selected)
        for fk in self.foreign_keys:
            if fk.table in selected_set and fk.ref_table in selected_set:
                rels.append(f"-- public.{fk.table}({fk.column}) -> public.{fk.ref_table}({fk.ref_column})")
        if rels:
            blocks.append("-- Relationships among included tables:\n" + "\n".join(rels))
        return "\n\n".join(blocks)

    def find_join_path(self, start: str, end: str) -> list[ForeignKey]:
        start, end = normalize_name(start), normalize_name(end)
        if start == end:
            return []
        graph: dict[str, list[tuple[str, ForeignKey]]] = {}
        for fk in self.foreign_keys:
            graph.setdefault(fk.table, []).append((fk.ref_table, fk))
            graph.setdefault(fk.ref_table, []).append((fk.table, fk))
        queue: deque[tuple[str, list[ForeignKey]]] = deque([(start, [])])
        seen = {start}
        while queue:
            table, path = queue.popleft()
            for nxt, fk in graph.get(table, []):
                if nxt in seen:
                    continue
                if nxt == end:
                    return path + [fk]
                seen.add(nxt)
                queue.append((nxt, path + [fk]))
        return []

    def neighbor_for_fk(self, current_table: str, fk: ForeignKey) -> str:
        current_table = normalize_name(current_table)
        if current_table == fk.table:
            return fk.ref_table
        if current_table == fk.ref_table:
            return fk.table
        raise ValueError(f"Foreign key does not touch {current_table}: {fk}")


def normalize_name(value: str) -> str:
    return value.strip().strip('"').split(".")[-1].lower()


def _filter_table_chunk(chunk: str, wanted_columns: set[str]) -> str:
    body_match = re.search(r"CREATE\s+TABLE\s+public\.([A-Za-z_][\w]*)\s*\((.*)\n\);", chunk, re.IGNORECASE | re.DOTALL)
    if not body_match:
        return chunk
    table = normalize_name(body_match.group(1))
    kept: list[str] = []
    constraint_lines: list[str] = []
    for raw_line in body_match.group(2).splitlines():
        line = raw_line.strip().rstrip(",")
        if not line:
            continue
        upper = line.upper()
        if upper.startswith(("CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK")):
            constraint_lines.append(line)
            continue
        col_name = normalize_name(line.split()[0])
        if col_name in wanted_columns:
            kept.append(line)
    for line in constraint_lines:
        fk = FK_RE.search(line)
        if fk:
            cols = {normalize_name(fk.group("col"))}
        else:
            first_group = re.search(r"\(([^()]*)\)", line)
            cols = {
                normalize_name(column)
                for column in (first_group.group(1).split(",") if first_group else [])
                if re.fullmatch(r'\s*"?[A-Za-z_][\w]*"?\s*', column)
            }
        if cols and cols.issubset(wanted_columns):
            kept.append(line)
    if not kept:
        kept = [f"{col} text" for col in sorted(wanted_columns)]
    body = ",\n  ".join(kept)
    return f"CREATE TABLE public.{table} (\n  {body}\n);"


def load_schema_graph(path: Path) -> SchemaGraph:
    ddl = path.read_text(encoding="utf-8-sig")
    table_columns: dict[str, list[str]] = {}
    table_chunks: dict[str, str] = {}
    foreign_keys: list[ForeignKey] = []
    for match in CREATE_RE.finditer(ddl):
        table = normalize_name(match.group(1))
        body = match.group(2)
        chunk = f"CREATE TABLE public.{table} ({body}\n);"
        cols: list[str] = []
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(",")
            if not line or line.upper().startswith(("CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK")):
                fk = FK_RE.search(line)
                if fk:
                    foreign_keys.append(
                        ForeignKey(
                            table=table,
                            column=normalize_name(fk.group("col")),
                            ref_table=normalize_name(fk.group("ref_table")),
                            ref_column=normalize_name(fk.group("ref_col")),
                        )
                    )
                continue
            name = line.split()[0].strip('"')
            if re.match(r"^[A-Za-z_][\w]*$", name):
                cols.append(normalize_name(name))
            fk = FK_RE.search(line)
            if fk:
                foreign_keys.append(
                    ForeignKey(
                        table=table,
                        column=normalize_name(fk.group("col")),
                        ref_table=normalize_name(fk.group("ref_table")),
                        ref_column=normalize_name(fk.group("ref_col")),
                    )
                )
        table_columns[table] = cols
        table_chunks[table] = chunk
    return SchemaGraph(ddl=ddl, table_columns=table_columns, table_chunks=table_chunks, foreign_keys=foreign_keys)


def compact_schema_summary(graph: SchemaGraph) -> str:
    lines: list[str] = []
    for table in sorted(graph.table_columns):
        lines.append(f"Table {table}:")
        column_lines = _column_definition_lines(graph.table_chunks.get(table, ""))
        if column_lines:
            lines.append("  columns:")
            for line in column_lines:
                lines.append(f"  - {line}")
        else:
            lines.append(f"  columns: {', '.join(graph.table_columns[table])}")
        constraints = _summary_constraint_lines(graph.table_chunks.get(table, ""))
        if constraints:
            lines.append("  constraints:")
            for line in constraints:
                lines.append(f"  - {line}")
        relationships = [
            f"{fk.table}.{fk.column} -> {fk.ref_table}.{fk.ref_column}"
            for fk in graph.foreign_keys
            if fk.table == table or fk.ref_table == table
        ]
        if relationships:
            lines.append("  relationships:")
            for rel in relationships:
                lines.append(f"  - {rel}")
    return "\n".join(lines)


def role_authorized_schema(graph: SchemaGraph, role_matrix: dict[str, dict[str, list[str]]], role: str) -> str:
    """Build a prompt-ready schema strictly from canonical DDL and the role matrix."""
    raw_tables = role_matrix.get(role, {})
    tables = sorted(normalize_name(table) for table in raw_tables if graph.has_table(table))
    columns_by_table = {
        normalize_name(table): [
            normalize_name(column)
            for column in columns
            if graph.has_column(table, column)
        ]
        for table, columns in raw_tables.items()
        if graph.has_table(table)
    }
    selected = set(tables)
    missing_relationship_columns: list[str] = []
    for fk in graph.foreign_keys:
        if fk.table not in selected or fk.ref_table not in selected:
            continue
        if fk.column not in columns_by_table.get(fk.table, []):
            missing_relationship_columns.append(f"{fk.table}.{fk.column}")
        if fk.ref_column not in columns_by_table.get(fk.ref_table, []):
            missing_relationship_columns.append(f"{fk.ref_table}.{fk.ref_column}")
    if missing_relationship_columns:
        missing = ", ".join(sorted(set(missing_relationship_columns)))
        raise ValueError(f"Role matrix omits structural relationship columns for {role}: {missing}")
    return graph.scoped_ddl(tables, columns_by_table, include_structural=False)


def role_authorized_compact_schema(
    graph: SchemaGraph,
    role_matrix: dict[str, dict[str, list[str]]],
    role: str,
    *,
    examples: dict[tuple[str, str], list[str]] | None = None,
) -> str:
    """Build a compact prompt schema strictly from canonical DDL and the role matrix."""
    raw_tables = role_matrix.get(role, {})
    tables = sorted(normalize_name(table) for table in raw_tables if graph.has_table(table))
    columns_by_table = {
        normalize_name(table): [
            normalize_name(column)
            for column in columns
            if graph.has_column(table, column)
        ]
        for table, columns in raw_tables.items()
        if graph.has_table(table)
    }
    selected = set(tables)
    missing_relationship_columns: list[str] = []
    for fk in graph.foreign_keys:
        if fk.table not in selected or fk.ref_table not in selected:
            continue
        if fk.column not in columns_by_table.get(fk.table, []):
            missing_relationship_columns.append(f"{fk.table}.{fk.column}")
        if fk.ref_column not in columns_by_table.get(fk.ref_table, []):
            missing_relationship_columns.append(f"{fk.ref_table}.{fk.ref_column}")
    if missing_relationship_columns:
        missing = ", ".join(sorted(set(missing_relationship_columns)))
        raise ValueError(f"Role matrix omits structural relationship columns for {role}: {missing}")

    lines = [
        f"ROLE-AUTHORIZED COMPACT SCHEMA FOR {role}",
        "Meaning: listed tables and columns are visible to this role; relationships are valid join paths.",
        "Column examples, when present, illustrate value formats only; they are not authorization evidence.",
        "",
        "TABLES",
    ]
    for table in tables:
        meta = _compact_table_metadata(graph.table_chunks.get(table, ""))
        rendered_columns: list[str] = []
        for column in columns_by_table.get(table, []):
            tags = _compact_column_tags(table, column, graph, meta)
            sample_values = (examples or {}).get((table, column), [])
            if sample_values:
                tags.append("ex: " + "|".join(str(value) for value in sample_values[:3]))
            tag_text = f" [{', '.join(tags)}]" if tags else ""
            rendered_columns.append(f"{column}:{_compact_coarse_type(meta['column_types'].get(column, 'unknown'))}{tag_text}")
        lines.append(f"- {table}({'; '.join(rendered_columns)})")

    relationships = [
        f"- {fk.table}.{fk.column} -> {fk.ref_table}.{fk.ref_column}"
        for fk in sorted(graph.foreign_keys, key=lambda item: (item.table, item.column, item.ref_table, item.ref_column))
        if fk.table in selected
        and fk.ref_table in selected
        and fk.column in columns_by_table.get(fk.table, [])
        and fk.ref_column in columns_by_table.get(fk.ref_table, [])
    ]
    if relationships:
        lines.extend(["", "RELATIONSHIPS", *relationships])
    return "\n".join(lines).rstrip() + "\n"


def _column_definition_lines(chunk: str) -> list[str]:
    body = _table_body(chunk)
    if body is None:
        return []
    lines: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip().rstrip(",")
        if not line:
            continue
        if line.upper().startswith(("CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK")):
            continue
        lines.append(line)
    return lines


def _summary_constraint_lines(chunk: str) -> list[str]:
    body = _table_body(chunk)
    if body is None:
        return []
    lines: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip().rstrip(",")
        if line.upper().startswith(("CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK")):
            lines.append(line)
    return lines


def _compact_column_tags(table: str, column: str, graph: SchemaGraph, meta: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    if column in meta["primary_keys"]:
        tags.append("PK")
    if column in meta["unique_columns"]:
        tags.append("UNIQUE")
    for fk in graph.foreign_keys:
        if fk.table == table and fk.column == column:
            tags.append(f"FK->{fk.ref_table}.{fk.ref_column}")
    return tags


def _compact_table_metadata(chunk: str) -> dict[str, Any]:
    column_types: dict[str, str] = {}
    primary_keys: set[str] = set()
    unique_columns: set[str] = set()
    body = _table_body(chunk)
    if body is None:
        return {"column_types": column_types, "primary_keys": primary_keys, "unique_columns": unique_columns}
    for raw_line in body.splitlines():
        line = raw_line.strip().rstrip(",")
        if not line:
            continue
        upper = line.upper()
        if upper.startswith(("CONSTRAINT", "PRIMARY", "UNIQUE")):
            if "PRIMARY KEY" in upper:
                primary_keys.update(_compact_columns_in_parentheses(line))
            if "UNIQUE" in upper:
                for group in _compact_column_groups_in_parentheses(line):
                    if len(group) == 1:
                        unique_columns.update(group)
            continue
        match = re.match(r'(?P<name>"?[A-Za-z_][\w]*"?)\s+(?P<type>.+)$', line)
        if not match:
            continue
        column = normalize_name(match.group("name"))
        raw_type = match.group("type").split(" DEFAULT ")[0].split(" NOT NULL")[0].split(" NULL")[0].strip()
        column_types[column] = raw_type
        if "PRIMARY KEY" in upper:
            primary_keys.add(column)
        if "UNIQUE" in upper:
            unique_columns.add(column)
    return {"column_types": column_types, "primary_keys": primary_keys, "unique_columns": unique_columns}


def _compact_columns_in_parentheses(line: str) -> set[str]:
    columns: set[str] = set()
    for group in _compact_column_groups_in_parentheses(line):
        columns.update(group)
    return columns


def _compact_column_groups_in_parentheses(line: str) -> list[set[str]]:
    groups: list[set[str]] = []
    for raw_group in re.findall(r"\(([^()]+)\)", line):
        columns = {normalize_name(item.strip()) for item in raw_group.split(",") if normalize_name(item.strip())}
        if columns:
            groups.append(columns)
    return groups


def _compact_coarse_type(raw_type: str) -> str:
    text = raw_type.lower().strip()
    if not text:
        return "unknown"
    if any(token in text for token in ["int", "serial"]):
        return "int"
    if any(token in text for token in ["numeric", "decimal", "float", "double", "real"]):
        return "numeric"
    if "bool" in text:
        return "bool"
    if "timestamp" in text:
        return "timestamp"
    if re.search(r"\bdate\b", text):
        return "date"
    if any(token in text for token in ["char", "text", "uuid"]):
        return "text"
    if "public." in text or "enum" in text:
        return "enum"
    return text.split()[0]


def _table_body(chunk: str) -> str | None:
    match = re.search(r"CREATE\s+TABLE\s+public\.[A-Za-z_][\w]*\s*\((.*)\n\);", chunk, re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else None
