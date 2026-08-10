from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


@dataclass
class QueryResult:
    executed: bool
    rows: list[dict[str, Any]]
    row_count: int
    db_error: str | None
    execution_time_ms: float
    columns: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "executed": self.executed,
            "rows": self.rows,
            "row_count": self.row_count,
            "db_error": self.db_error,
            "execution_time_ms": self.execution_time_ms,
            "columns": list(self.columns or []),
        }


class DatabaseExecutor:
    def __init__(self, url: str | None, statement_timeout_ms: int = 10000, max_rows: int = 1000) -> None:
        self.url = url
        self.statement_timeout_ms = statement_timeout_ms
        self.max_rows = max_rows
        self._engine: Engine | None = create_engine(url, future=True) if url else None

    @property
    def available(self) -> bool:
        return self._engine is not None

    def execute_read_only(self, sql: str | None) -> QueryResult:
        started = time.perf_counter()
        if not sql:
            return QueryResult(False, [], 0, "NO_SQL", 0.0)
        if not self._engine:
            return QueryResult(False, [], 0, "DATABASE_NOT_CONFIGURED", 0.0)
        stripped = sql.strip()
        if not stripped.lower().startswith(("select", "with")):
            return QueryResult(False, [], 0, "READ_ONLY_ASSERTION_FAILED", 0.0)
        try:
            with self._engine.connect() as conn:
                conn.execute(text(f"SET statement_timeout = {int(self.statement_timeout_ms)}"))
                result = conn.execute(text(stripped))
                columns = list(result.keys())
                rows = [dict(row._mapping) for row in result.fetchmany(self.max_rows)]
            return QueryResult(True, rows, len(rows), None, (time.perf_counter() - started) * 1000, columns)
        except Exception as exc:  # noqa: BLE001
            return QueryResult(False, [], 0, str(exc), (time.perf_counter() - started) * 1000)

    def exists(self, sql: str, params: dict[str, Any]) -> tuple[bool | None, str | None]:
        if not self._engine:
            return None, "DATABASE_NOT_CONFIGURED"
        try:
            with self._engine.connect() as conn:
                conn.execute(text(f"SET statement_timeout = {int(self.statement_timeout_ms)}"))
                value = conn.execute(text(sql), params).scalar()
                return bool(value), None
        except Exception as exc:  # noqa: BLE001
            return None, str(exc)

