from __future__ import annotations

from typing import Any

from architecture_baselines.db import DatabaseExecutor
from architecture_baselines.llm import LLMClient
from architecture_baselines.modules import ContextBuilder, InputAttackGuard, ReadonlyExecutorModule, RlsRewriteHook, SqlSafetyGuard, TableColumnAccessGuardSchemaScoper, TextToSqlGenerator
from architecture_baselines.policy import PolicyIndex
from architecture_baselines.sql import SchemaIndex


class ModuleRegistry:
    def __init__(self, policy: PolicyIndex, schema: SchemaIndex, schema_ddl: str, config: dict[str, Any], llm: LLMClient | None, executor: DatabaseExecutor):
        modules_config = config.get("modules", {})
        self.modules = {
            "C0": ContextBuilder(schema, schema_ddl),
            "D1": InputAttackGuard(llm=llm, config=modules_config.get("D1", {})),
            "D2": TableColumnAccessGuardSchemaScoper(policy, schema, llm=llm, config=modules_config.get("D2", {})),
            "G1": TextToSqlGenerator(llm=llm, config=modules_config.get("G1", {})),
            "D3": SqlSafetyGuard(config=modules_config.get("D3", {})),
            "D4": RlsRewriteHook(policy, config=modules_config.get("D4", {})),
            "X1": ReadonlyExecutorModule(executor),
        }

    def get(self, module_id: str) -> Any:
        return self.modules[module_id]


