from __future__ import annotations

import re
from typing import Any

from architecture_baselines.llm import LLMClient
from architecture_baselines.prompts import TEXT2SQL_SYSTEM, text2sql_prompt
from architecture_baselines.schemas import ModuleDecision, ModuleResult, ModuleStatus, SecurityContext
from architecture_baselines.utils.timing import measure_ms


def extract_sql(text: str) -> str | None:
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", cleaned, flags=re.I | re.S)
    if fenced:
        cleaned = fenced.group(1).strip()
    match = re.search(r"\bSELECT\b.*", cleaned, flags=re.I | re.S)
    if match:
        return match.group(0).strip().rstrip(";") + ";"
    return cleaned.rstrip(";") + ";"


class TextToSqlGenerator:
    module_id = "G1"

    def __init__(self, llm: LLMClient | None = None, config: dict[str, Any] | None = None):
        self.llm = llm
        self.config = config or {}

    def run(self, nlq: str, context: SecurityContext, scoped_schema_ddl: str | None = None) -> ModuleResult:
        with measure_ms() as timer:
            usage: dict[str, Any] = {}
            schema = scoped_schema_ddl or context.schema_ddl
            if not self.llm:
                raw_sql, error = None, "No LLM client configured."
            else:
                text, usage = self.llm.generate_text(TEXT2SQL_SYSTEM, text2sql_prompt(nlq, context.role, context.user_id, schema, context.history))
                raw_sql, error = extract_sql(text), None
            status = ModuleStatus.PASS.value if raw_sql else ModuleStatus.ERROR.value
            decision = ModuleDecision.CONTINUE.value if raw_sql else ModuleDecision.ERROR.value
        return ModuleResult(self.module_id, "text2sql_generator", status, decision, {"raw_sql": raw_sql, "used_schema_scope": "scoped" if scoped_schema_ddl else "full_schema", "used_role_scoped_schema": bool(scoped_schema_ddl)}, {}, timer.elapsed_ms, usage, error)

