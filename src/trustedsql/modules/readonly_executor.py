from __future__ import annotations

from trustedsql.db.executor import DatabaseExecutor
from trustedsql.modules.common import timed_module
from trustedsql.schemas import ModuleResult


def run(final_sql: str | None, db: DatabaseExecutor) -> ModuleResult:
    def _inner() -> ModuleResult:
        result = db.execute_read_only(final_sql)
        decision = "ALLOW" if result.executed else "ERROR"
        return ModuleResult(
            "X1",
            "readonly_sql_executor",
            decision,
            artifact={
                "executed": result.executed,
                "row_count": result.row_count,
                "execution_time_ms": result.execution_time_ms,
                "db_error": result.db_error,
                "rows": result.rows if result.executed else [],
                "columns": list(result.columns or []) if result.executed else [],
                "rows_preview": result.rows[:5] if result.executed and len(result.rows) > 5 else (result.rows if result.executed else []),
                "final_sql": final_sql,
                "final_sql_chars": len(final_sql) if final_sql else 0,
            },
            error=result.db_error if not result.executed else None,
            raw_objects={
                "execution_result": {
                    "executed": result.executed,
                    "row_count": result.row_count,
                    "execution_time_ms": result.execution_time_ms,
                    "db_error": result.db_error,
                    "statement_timeout_ms": db.statement_timeout_ms,
                },
            },
        )

    return timed_module("X1", "readonly_sql_executor", _inner)

