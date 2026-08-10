from __future__ import annotations

from typing import Any

from architecture_baselines.schemas import ModuleDecision, ModuleResult, ModuleStatus
from architecture_baselines.sql import analyze_sql
from architecture_baselines.utils.timing import measure_ms


class SqlSafetyGuard:
    module_id = "D3"

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def run(self, raw_sql: str) -> ModuleResult:
        with measure_ms() as timer:
            signature = analyze_sql(raw_sql)
            detected = list(signature.risks)
            if signature.multi_statement:
                detected.append("multi_statement")
            if not signature.is_select_only:
                detected.append("non_select_or_unparsed")
            if signature.parser_status != "PARSED":
                verdict = "UNKNOWN"
                status = ModuleStatus.BLOCK.value if self.config.get("strict_unknown_deny", True) else ModuleStatus.WARN.value
                decision = ModuleDecision.DENY.value if self.config.get("strict_unknown_deny", True) else ModuleDecision.CONTINUE.value
            elif detected:
                verdict, status, decision = "UNSAFE", ModuleStatus.BLOCK.value, ModuleDecision.DENY.value
            else:
                verdict, status, decision = "SAFE", ModuleStatus.PASS.value, ModuleDecision.CONTINUE.value
            artifact = {"sql_safety_verdict": verdict, "select_only": signature.is_select_only, "multi_statement": signature.multi_statement, "detected_risks": detected, "normalized_sql": signature.normalized_sql, "parser_status": signature.parser_status}
        return ModuleResult(self.module_id, "sql_safety_guard", status, decision, artifact, signature.to_dict(), timer.elapsed_ms, error=signature.error)


