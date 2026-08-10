from __future__ import annotations

from architecture_baselines.policy import PolicyIndex, rewrite_sql_with_policy
from architecture_baselines.schemas import ModuleDecision, ModuleResult, ModuleStatus, SecurityContext
from architecture_baselines.utils.timing import measure_ms


class RlsRewriteHook:
    module_id = "D4"

    def __init__(self, policy: PolicyIndex, config: dict[str, object] | None = None):
        self.policy = policy
        self.config = config or {}

    def run(self, candidate_sql: str, context: SecurityContext) -> ModuleResult:
        with measure_ms() as timer:
            schema_columns = getattr(context.schema_index, "columns_by_table", None)
            rewrite = rewrite_sql_with_policy(candidate_sql, context.role, context.user_id, self.policy, schema_columns)
            rewrite_status = str(rewrite.get("rewrite_status") or "NOOP")
            if rewrite_status == "SUCCESS":
                final_sql = rewrite.get("rewritten_sql") or candidate_sql
                status = ModuleStatus.REWRITE.value
                reason = "RLS predicate was injected by the rewrite hook."
            elif rewrite_status == "NOOP":
                final_sql = rewrite.get("rewritten_sql") or candidate_sql
                status = ModuleStatus.PASS.value
                reason = "No RLS rewrite was applied."
            else:
                final_sql = candidate_sql
                status = ModuleStatus.WARN.value
                reason = "RLS rewrite failed or was unsupported; runtime continues with original SQL."
            artifact = {"rewrite_applied": bool(rewrite.get("rewrite_applied", False)), "rewrite_status": rewrite_status, "rewrite_strategy": rewrite.get("rewrite_strategy", "NONE"), "injected_conditions": rewrite.get("injected_conditions", []), "final_sql": final_sql, "decision_reason": reason}
        return ModuleResult(self.module_id, "rls_rewrite_hook", status, ModuleDecision.CONTINUE.value, artifact, {"rewrite": rewrite, "runtime_policy_blocking": False}, timer.elapsed_ms)


