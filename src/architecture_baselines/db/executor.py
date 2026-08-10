from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine, text

from architecture_baselines.sql.parser import ensure_select_only


@dataclass
class DatabaseExecutor:
    url: str
    statement_timeout_ms: int = 3000
    max_result_rows: int = 200
    connect_timeout_s: int = 10
    enforce_select_assertion: bool = True

    def __post_init__(self) -> None:
        self.engine = create_engine(self.url, pool_pre_ping=True, connect_args={"connect_timeout": self.connect_timeout_s})

    def execute_readonly(self, sql: str) -> dict[str, Any]:
        if self.enforce_select_assertion:
            ok, signature = ensure_select_only(sql)
            if not ok:
                return {"executed": False, "rows": None, "row_count": 0, "db_error": "environment_select_assertion_failed", "signature": signature}
        limited_sql = sql.strip().rstrip(";")
        with self.engine.connect() as conn:
            conn.execute(text(f"SET LOCAL statement_timeout = {int(self.statement_timeout_ms)}"))
            result = conn.execute(text(limited_sql))
            rows = [dict(row._mapping) for row in result.fetchmany(self.max_result_rows)]
            return {"executed": True, "rows": rows, "row_count": len(rows), "columns": list(result.keys()), "db_error": None}

    def smoke_test(self) -> bool:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True

