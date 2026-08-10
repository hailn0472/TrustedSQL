from __future__ import annotations

import re
from dataclasses import asdict

import sqlglot
from sqlglot import exp

from trustedsql.modules.common import timed_module
from trustedsql.policy.index import PolicyIndex
from trustedsql.schemas import GenerationInput, ModuleResult, SqlValidationResult, TrustedContext
from trustedsql.sql.analysis import analyze_sql


def run(
    context: TrustedContext,
    raw_sql: str | None,
    generation: GenerationInput | None,
) -> tuple[SqlValidationResult | None, ModuleResult]:
    module_id = "M7"
    stage = "sql_conformance_validator"

    def _inner() -> ModuleResult:
        analysis = analyze_sql(raw_sql, context.schema_graph)
        risks = list(analysis.risks)
        if analysis.parser_status != "PARSED":
            return ModuleResult(module_id, stage, "DENY",
                artifact={"raw_sql": raw_sql, "analysis": analysis.to_dict()},
                audit={"reason_code": "SQL_PARSE_FAILED"})
        if analysis.multi_statement or not analysis.is_select_only:
            return ModuleResult(module_id, stage, "DENY",
                artifact={"raw_sql": raw_sql, "analysis": analysis.to_dict(), "multi_statement": analysis.multi_statement, "is_select_only": analysis.is_select_only},
                audit={"reason_code": "NON_SELECT_OR_MULTI_STATEMENT"})
        if any(r in risks for r in ["DANGEROUS_KEYWORD", "COMMENT_MARKER", "UNION_SELECT", "UNRESOLVED_PLACEHOLDER"]):
            return ModuleResult(module_id, stage, "DENY",
                artifact={"raw_sql": raw_sql, "analysis": analysis.to_dict(), "risks_detected": [r for r in risks if r in ["DANGEROUS_KEYWORD", "COMMENT_MARKER", "UNION_SELECT", "UNRESOLVED_PLACEHOLDER"]]},
                audit={"reason_code": "DANGEROUS_SQL_PATTERN"})
        violations = _policy_violations(
            analysis.tables,
            analysis.columns,
            context.policy_index,
            context.role,
        )
        if violations:
            return ModuleResult(
                module_id,
                stage,
                "DENY",
                artifact={
                    "raw_sql": raw_sql,
                    "analysis": analysis.to_dict(),
                    "violations": violations,
                    "violations_count": len(violations),
                    "m5_guide_included": bool(generation and generation.m5_guide),
                },
                audit={
                    "reason_code": "SQL_POLICY_CONFORMANCE_FAILED",
                    "m5_guide_included": bool(generation and generation.m5_guide),
                },
            )
        return ModuleResult(module_id, stage, "ALLOW",
            artifact={
                "final_sql": analysis.normalized_sql,
                "final_sql_chars": len(analysis.normalized_sql) if analysis.normalized_sql else 0,
                "analysis": analysis.to_dict(),
                "tables_referenced": analysis.tables,
                "columns_referenced": analysis.columns,
                "m5_guide_included": bool(generation and generation.m5_guide),
            })

    result = timed_module(module_id, stage, _inner)
    final_sql = result.artifact.get("final_sql") if result.decision == "ALLOW" else None
    validation = SqlValidationResult(result.decision, final_sql, result.audit.get("reason_code")) if result else None
    if validation is not None:
        result.raw_objects["sql_validation_result"] = asdict(validation)
    if result.artifact.get("analysis"):
        result.raw_objects["sql_analysis"] = result.artifact["analysis"]
    return validation, result


def _policy_violations(tables: list[str], columns: list[dict], policy: PolicyIndex, role: str) -> list[str]:
    violations: list[str] = []
    for table in tables:
        if not policy.role_can_access_table(role, table):
            violations.append(f"table_not_allowed:{table}")
    for col in columns:
        table = col.get("table")
        column = col.get("column")
        if table and column and policy.role_can_access_table(role, table) and not policy.role_can_access_column(role, table, column):
            violations.append(f"column_not_allowed:{table}.{column}")
    return sorted(set(violations))
