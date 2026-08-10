from __future__ import annotations

from typing import Any

from architecture_baselines.llm import LLMClient
from architecture_baselines.prompts import RESOURCE_EXTRACT_SYSTEM, resource_extract_prompt
from architecture_baselines.policy import PolicyIndex
from architecture_baselines.schemas import ModuleDecision, ModuleResult, ModuleStatus, SecurityContext
from architecture_baselines.sql import SchemaIndex
from architecture_baselines.utils.timing import measure_ms


class TableColumnAccessGuardSchemaScoper:
    module_id = "D2"

    def __init__(self, policy: PolicyIndex, schema: SchemaIndex, llm: LLMClient | None = None, config: dict[str, Any] | None = None):
        self.policy = policy
        self.schema = schema
        self.llm = llm
        self.config = config or {}

    def _fallback_resource_request(self, nlq: str) -> dict[str, Any]:
        lowered = nlq.lower()
        requested_tables = [table for table in self.schema.table_names() if table in lowered]
        requested_columns: dict[str, list[str]] = {}
        for table in requested_tables:
            columns = [column for column in self.schema.columns_by_table.get(table, []) if column in lowered]
            if columns:
                requested_columns[table] = columns
        return {
            "intent": "deterministic_schema_token_match" if requested_tables or requested_columns else "",
            "requested_tables": requested_tables,
            "requested_columns": requested_columns,
            "evidence": "deterministic schema-token match" if requested_tables or requested_columns else "no exact schema-token match",
        }

    def _normalize_resource_request(self, value: dict[str, Any]) -> dict[str, Any]:
        tables: list[str] = []
        for table in value.get("requested_tables") or []:
            table_name = str(table).lower().strip()
            if table_name and table_name not in tables:
                tables.append(table_name)
        columns: dict[str, list[str]] = {}
        raw_columns = value.get("requested_columns") or {}
        if isinstance(raw_columns, dict):
            for table, cols in raw_columns.items():
                table_name = str(table).lower().strip()
                normalized_cols: list[str] = []
                for col in cols or []:
                    col_name = str(col).lower().strip()
                    if col_name and col_name not in normalized_cols:
                        normalized_cols.append(col_name)
                if table_name and normalized_cols:
                    columns[table_name] = normalized_cols
                    if table_name not in tables:
                        tables.append(table_name)
        return {
            "intent": str(value.get("intent") or "").strip(),
            "requested_tables": tables,
            "requested_columns": columns,
            "evidence": str(value.get("evidence") or value.get("reason") or ""),
        }

    def _build_extracted_scope(self, role: str, resource_request: dict[str, Any]) -> tuple[list[str], dict[str, list[str]]]:
        scoped_columns: dict[str, list[str]] = {}
        requested_columns: dict[str, list[str]] = resource_request.get("requested_columns") or {}
        for table in resource_request.get("requested_tables") or []:
            permitted = self.policy.permitted_columns(role, table)
            if not permitted:
                continue
            extracted = requested_columns.get(table) or []
            if extracted:
                columns = [column for column in self.schema.columns_by_table.get(table, []) if column in set(extracted) and column in permitted]
            else:
                columns = [column for column in self.schema.columns_by_table.get(table, []) if column in permitted]
            structural = self._structural_columns(table, permitted)
            for column in structural:
                if column not in columns:
                    columns.append(column)
            if columns:
                scoped_columns[table] = columns
        return sorted(scoped_columns), scoped_columns

    def _structural_columns(self, table: str, permitted: set[str]) -> list[str]:
        columns = self.schema.columns_by_table.get(table, [])
        return [column for column in columns if column in permitted and (column == "id" or column.endswith("_id"))]

    def run(self, nlq: str, context: SecurityContext) -> ModuleResult:
        with measure_ms() as timer:
            schema_context = self.schema.ddl_text
            usage: dict[str, Any] = {}
            extraction_error: str | None = None
            extractor_source = "deterministic"
            if self.llm and self.config.get("llm_extract_resources", True):
                try:
                    extracted, usage = self.llm.generate_json(RESOURCE_EXTRACT_SYSTEM, resource_extract_prompt(nlq, context.role, context.user_id, schema_context, context.history))
                    resource_request = self._normalize_resource_request(extracted)
                    extractor_source = "llm"
                except Exception as exc:
                    resource_request = {"intent": "", "requested_tables": [], "requested_columns": {}, "evidence": ""}
                    extraction_error = f"resource_extractor_failed: {exc}"
            else:
                resource_request = self._fallback_resource_request(nlq)

            role_policy = self.policy.role_policy(context.role)
            violations: list[dict[str, Any]] = []
            extraction_errors: list[str] = []
            schema_tables = set(self.schema.table_names())
            for table in resource_request["requested_tables"]:
                if table not in schema_tables:
                    extraction_errors.append(f"unknown_table:{table}")
                    continue
                if table not in role_policy.allowed_tables:
                    violations.append({"code": "RB-01", "table": table, "message": "NLQ-level requested table outside role access matrix"})
            for table, columns in resource_request["requested_columns"].items():
                if table not in schema_tables:
                    extraction_errors.append(f"unknown_table_for_columns:{table}")
                    continue
                known_columns = set(self.schema.columns_by_table.get(table, []))
                permitted = self.policy.permitted_columns(context.role, table)
                for column in columns:
                    if column not in known_columns:
                        extraction_errors.append(f"unknown_column:{table}.{column}")
                    elif not permitted or column not in permitted:
                        violations.append({"code": "RB-02", "table": table, "column": column, "message": "NLQ-level requested column outside role access matrix"})
            if extraction_error:
                extraction_errors.append(extraction_error)
            if not resource_request["requested_tables"] and not resource_request["requested_columns"]:
                extraction_errors.append("no_table_or_column_extracted")

            included_tables: list[str] = []
            scoped_columns: dict[str, list[str]] = {}
            scoped_schema = ""
            if not extraction_errors:
                included_tables, scoped_columns = self._build_extracted_scope(context.role, resource_request)
                scoped_schema = self.schema.scoped_ddl(included_tables, scoped_columns)

            if extraction_errors:
                verdict, status, decision, error = "ERROR", ModuleStatus.ERROR.value, ModuleDecision.ERROR.value, "; ".join(extraction_errors)
            elif violations:
                verdict, status, decision, error = "DENY", ModuleStatus.BLOCK.value, ModuleDecision.DENY.value, None
            else:
                verdict, status, decision, error = "ALLOW", ModuleStatus.PASS.value, ModuleDecision.CONTINUE.value, None

            artifact = {
                "resource_request": resource_request,
                "table_column_access_verdict": verdict,
                "table_column_violations": violations,
                "extraction_errors": extraction_errors,
                "scoped_schema_ddl": scoped_schema,
                "included_tables": included_tables,
                "included_columns": scoped_columns,
                "schema_scope_includes_structural_columns": True,
                "excluded_tables": sorted(set(self.schema.table_names()) - set(included_tables)),
            }
        return ModuleResult(
            self.module_id,
            "table_column_access_guard_schema_scoper",
            status,
            decision,
            artifact,
            {
                "access_source": "role_access_matrix",
                "resource_extractor_source": extractor_source,
                "schema_context_mode": "full_schema_ddl",
                "schema_context_source": getattr(self.schema, "source_path", None),
                "schema_scope_mode": self.config.get("schema_scope_mode", "extracted_allowed_schema"),
                "rls_checked": False,
            },
            timer.elapsed_ms,
            usage,
            error,
        )

