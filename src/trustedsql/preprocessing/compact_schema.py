from __future__ import annotations

import re
from dataclasses import asdict, dataclass
import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from trustedsql.sql.schema import SchemaGraph, load_schema_graph, normalize_name
from trustedsql.utils.io import write_json


@dataclass(frozen=True)
class CompactSchemaResult:
    output_path: str
    table_count: int
    column_count: int
    relationship_count: int
    characters: int
    example_column_count: int = 0


def generate_compact_schema_prompt(
    *,
    ddl_path: Path,
    output_path: Path,
    database_url: str | None = None,
    example_limit: int = 3,
) -> CompactSchemaResult:
    """Generate a deterministic prompt-ready compact schema from canonical DDL.

    This artifact is intentionally independent of benchmark labels. Optional
    value examples are queried from the database only for whitelisted identifier
    and categorical columns to help LLMs ground user-mentioned literals.
    """

    schema = load_schema_graph(ddl_path)
    examples = collect_column_examples(
        schema,
        database_url=database_url or os.environ.get("TRUSTEDSQL_DATABASE_URL") or os.environ.get("DATABASE_URL"),
        limit=example_limit,
    )
    rendered = render_compact_schema(schema, examples=examples)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")

    result = CompactSchemaResult(
        output_path=str(output_path),
        table_count=len(schema.table_columns),
        column_count=sum(len(cols) for cols in schema.table_columns.values()),
        relationship_count=len(schema.foreign_keys),
        characters=len(rendered),
        example_column_count=len(examples),
    )
    manifest = {
        "source_ddl": str(ddl_path),
        "output_path": str(output_path),
        "examples": {
            "enabled": bool(examples),
            "source": "database_whitelisted_columns" if examples else "none",
            "limit_per_column": example_limit,
            "column_count": len(examples),
        },
        "stats": asdict(result),
    }
    write_json(output_path.with_suffix(".manifest.json"), manifest)
    return result


def render_compact_schema(
    schema: SchemaGraph,
    *,
    examples: dict[tuple[str, str], list[Any]] | None = None,
) -> str:
    lines: list[str] = [
        "COMPACT DATABASE SCHEMA",
        "Source: ddl.md",
        "Meaning: all listed tables, columns, keys, and relationships exist in the database schema.",
        "Column examples, when present, illustrate value formats only; they are not authorization evidence.",
        "",
        "TABLES",
    ]
    for table in sorted(schema.table_columns):
        meta = _table_metadata(table, schema)
        rendered_columns = []
        for column in schema.table_columns[table]:
            tags = _column_tags(table, column, schema, meta)
            sample_values = (examples or {}).get((table, column), [])
            if sample_values:
                tags.append("ex: " + "|".join(_format_example(value) for value in sample_values))
            tag_text = f" [{', '.join(tags)}]" if tags else ""
            rendered_columns.append(f"{column}:{_coarse_type(meta['column_types'].get(column, 'unknown'))}{tag_text}")
        lines.append(f"- {table}({'; '.join(rendered_columns)})")

    if schema.foreign_keys:
        lines.extend(["", "RELATIONSHIPS"])
        for fk in sorted(schema.foreign_keys, key=lambda item: (item.table, item.column, item.ref_table, item.ref_column)):
            lines.append(f"- {fk.table}.{fk.column} -> {fk.ref_table}.{fk.ref_column}")

    return "\n".join(lines).rstrip() + "\n"


def collect_column_examples(
    schema: SchemaGraph,
    *,
    database_url: str | None,
    limit: int = 3,
) -> dict[tuple[str, str], list[Any]]:
    if not database_url or limit <= 0:
        return {}
    engine = create_engine(database_url, future=True)
    examples: dict[tuple[str, str], list[Any]] = {}
    with engine.connect() as conn:
        for table in sorted(schema.table_columns):
            for column in schema.table_columns[table]:
                if not _include_example_column(table, column):
                    continue
                query = text(
                    f'SELECT value FROM ('
                    f'SELECT DISTINCT "{column}"::text AS value '
                    f'FROM public."{table}" '
                    f'WHERE "{column}" IS NOT NULL'
                    f') AS examples '
                    f'ORDER BY value '
                    f'LIMIT :limit'
                )
                try:
                    values = [row._mapping["value"] for row in conn.execute(query, {"limit": int(limit)})]
                except Exception:  # noqa: BLE001
                    values = []
                values = [_clean_example(value) for value in values if _clean_example(value) is not None]
                if values:
                    examples[(table, column)] = values[:limit]
    return examples


def parse_compact_schema_examples(compact_schema: str) -> dict[tuple[str, str], list[str]]:
    examples: dict[tuple[str, str], list[str]] = {}
    for raw_line in compact_schema.splitlines():
        line = raw_line.strip()
        if not line.startswith("- ") or "(" not in line or not line.endswith(")"):
            continue
        table = normalize_name(line[2:].split("(", 1)[0])
        body = line.split("(", 1)[1].rsplit(")", 1)[0]
        for raw_col in body.split("; "):
            if ":" not in raw_col or "ex:" not in raw_col:
                continue
            column = normalize_name(raw_col.split(":", 1)[0])
            match = re.search(r"ex:\s*([^\]]+)", raw_col)
            if not match:
                continue
            values = [value.strip() for value in match.group(1).split("|") if value.strip()]
            if values:
                examples[(table, column)] = values
    return examples


def _include_example_column(table: str, column: str) -> bool:
    column = normalize_name(column)
    deny = {
        "password",
        "gmail",
        "phone_number",
        "user_address",
        "comment",
        "description",
        "reason",
        "process_note",
        "file_url",
        "content",
        "embedding",
    }
    if column in deny:
        return False
    if column.endswith("_id") or column == "id":
        return False
    allow_exact = {
        "course_code",
        "class_name",
        "student_code",
        "framework_code",
        "major_code",
        "narrow_major_code",
        "dep_code",
        "campus_name",
        "semester",
        "status",
        "room",
        "slot",
        "category_name",
        "type_name",
        "role_name",
        "permission_name",
        "course_name_en",
        "course_name_vn",
        "major_name",
        "narrow_major_name",
        "dep_name",
    }
    if column in allow_exact:
        return True
    return column.endswith("_code") or column.endswith("_name")


def _clean_example(value: Any) -> Any | None:
    if value is None:
        return None
    text_value = str(value).strip()
    if not text_value:
        return None
    if len(text_value) > 40:
        return None
    if any(char in text_value for char in ["\n", "\r", "|", "]"]):
        return None
    return value


def _format_example(value: Any) -> str:
    text_value = str(value).strip()
    return re.sub(r"\s+", " ", text_value)


def _column_tags(table: str, column: str, schema: SchemaGraph, meta: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    if column in meta["primary_keys"]:
        tags.append("PK")
    if column in meta["unique_columns"]:
        tags.append("UNIQUE")
    for fk in schema.foreign_keys:
        if fk.table == table and fk.column == column:
            tags.append(f"FK->{fk.ref_table}.{fk.ref_column}")
    return tags


def _table_metadata(table: str, schema: SchemaGraph) -> dict[str, Any]:
    chunk = schema.table_chunks.get(table, "")
    column_types: dict[str, str] = {}
    primary_keys: set[str] = set()
    unique_columns: set[str] = set()

    for raw_line in _table_body_lines(chunk):
        line = raw_line.strip().rstrip(",")
        if not line:
            continue
        upper = line.upper()
        if upper.startswith(("CONSTRAINT", "PRIMARY", "UNIQUE")):
            if "PRIMARY KEY" in upper:
                primary_keys.update(_columns_in_parentheses(line))
            if "UNIQUE" in upper:
                for group in _column_groups_in_parentheses(line):
                    if len(group) == 1:
                        unique_columns.update(group)
            continue
        column, raw_type = _column_definition(line)
        if not column:
            continue
        column_types[column] = raw_type
        if "PRIMARY KEY" in upper:
            primary_keys.add(column)
        if "UNIQUE" in upper:
            unique_columns.add(column)

    return {
        "column_types": column_types,
        "primary_keys": primary_keys,
        "unique_columns": unique_columns,
    }


def _table_body_lines(chunk: str) -> list[str]:
    match = re.search(r"CREATE\s+TABLE\s+public\.[A-Za-z_][\w]*\s*\((.*)\n\);", chunk, re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    return match.group(1).splitlines()


def _column_definition(line: str) -> tuple[str | None, str]:
    match = re.match(r'(?P<name>"?[A-Za-z_][\w]*"?)\s+(?P<type>.+)$', line)
    if not match:
        return None, ""
    column = normalize_name(match.group("name"))
    raw_type = match.group("type").split(" DEFAULT ")[0].split(" NOT NULL")[0].split(" NULL")[0].strip()
    return column, raw_type


def _columns_in_parentheses(line: str) -> set[str]:
    columns: set[str] = set()
    for group in _column_groups_in_parentheses(line):
        columns.update(group)
    return columns


def _column_groups_in_parentheses(line: str) -> list[set[str]]:
    groups: list[set[str]] = []
    for raw_group in re.findall(r"\(([^()]+)\)", line):
        columns = {normalize_name(item.strip()) for item in raw_group.split(",") if normalize_name(item.strip())}
        if columns:
            groups.append(columns)
    return groups


def _coarse_type(raw_type: str) -> str:
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


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Generate a compact full-schema prompt file.")
    parser.add_argument("--ddl-path", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument("--database-url", default=os.environ.get("TRUSTEDSQL_DATABASE_URL") or os.environ.get("DATABASE_URL"))
    parser.add_argument("--example-limit", type=int, default=3)
    args = parser.parse_args()

    result = generate_compact_schema_prompt(
        ddl_path=args.ddl_path,
        output_path=args.output_path,
        database_url=args.database_url,
        example_limit=args.example_limit,
    )
    print(
        f"{result.table_count} tables, {result.column_count} columns, "
        f"{result.relationship_count} relationships, {result.characters} chars -> {result.output_path}"
    )


if __name__ == "__main__":
    main()
