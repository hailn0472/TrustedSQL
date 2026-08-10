from __future__ import annotations

from architecture_baselines.db import DatabaseExecutor
from architecture_baselines.schemas import ModuleDecision, ModuleResult, ModuleStatus
from architecture_baselines.utils.timing import measure_ms


class ReadonlyExecutorModule:
    module_id = "X1"

    def __init__(self, executor: DatabaseExecutor):
        self.executor = executor

    def run(self, final_sql: str) -> ModuleResult:
        with measure_ms() as timer:
            try:
                result = self.executor.execute_readonly(final_sql)
                if result.get("executed"):
                    status, decision, error = ModuleStatus.PASS.value, ModuleDecision.CONTINUE.value, None
                else:
                    status, decision, error = ModuleStatus.ERROR.value, ModuleDecision.ERROR.value, result.get("db_error")
            except Exception as exc:
                result = {"executed": False, "rows": None, "row_count": 0, "db_error": str(exc)}
                status, decision, error = ModuleStatus.ERROR.value, ModuleDecision.ERROR.value, str(exc)
        return ModuleResult(self.module_id, "readonly_executor", status, decision, result, {}, timer.elapsed_ms, error=error)

