from __future__ import annotations

from architecture_baselines.schemas import ModuleDecision, ModuleResult, ModuleStatus, RuntimeTurnInput, SecurityContext
from architecture_baselines.sql import SchemaIndex
from architecture_baselines.utils.timing import measure_ms


class ContextBuilder:
    module_id = "C0"

    def __init__(self, schema: SchemaIndex, schema_ddl: str):
        self.schema = schema
        self.schema_ddl = schema_ddl

    def run(self, turn_input: RuntimeTurnInput) -> tuple[ModuleResult, SecurityContext]:
        with measure_ms() as timer:
            context = SecurityContext(turn_input.role, turn_input.user_id, self.schema, self.schema_ddl, turn_input.history)
        return (
            ModuleResult(
                module_id=self.module_id,
                stage="context_builder",
                status=ModuleStatus.PASS.value,
                decision=ModuleDecision.CONTINUE.value,
                artifact={"security_context": {"role": context.role, "user_id": context.user_id, "history_count": len(context.history)}},
                evidence={"sql_gt_in_runtime": False, "dataset_labels_in_runtime": False},
                latency_ms=timer.elapsed_ms,
            ),
            context,
        )


