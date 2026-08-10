from __future__ import annotations

import json
from dataclasses import asdict

from trustedsql.modules.common import timed_module
from trustedsql.prompts.prompt_loader import render_prompt
from trustedsql.providers.client import GeminiClient, OpenAICompatibleClient
from trustedsql.schemas import GenerationInput, ModuleResult, TrustedContext


def run(
    context: TrustedContext,
    generation: GenerationInput,
    llm: GeminiClient | OpenAICompatibleClient | None,
    module_config: dict,
) -> tuple[str | None, ModuleResult]:
    def _inner() -> ModuleResult:
        if not llm:
            return ModuleResult("M6", "policy_aware_sql_generator", "ERROR", error="LLM_NOT_CONFIGURED")
        cfg = module_config.get("llm") or module_config.get("vertex", {}) or {}
        history = [
            {
                "turn_id": item.turn_id,
                "nlq": item.nlq,
                "decision": item.decision,
                "final_sql": item.final_sql,
                "result": item.execution_result_json,
            }
            for item in context.history
        ]
        m5_section = _guide_section("Verified M5 authorization constraints", generation.m5_guide)
        prompt = render_prompt(
            "m6_sql_generator.txt",
            role=context.role,
            user_id=context.user_id,
            nlq=context.nlq,
            history=history,
            m5_guide_section=m5_section,
            schema=generation.role_authorized_schema,
        )
        response = llm.generate_text(
            prompt,
            temperature=float(cfg.get("temperature", 0.0)),
            max_output_tokens=int(cfg.get("max_output_tokens", module_config.get("max_output_tokens", 1200))),
        )
        sql = _clean_sql(response.text)
        return ModuleResult(
            "M6",
            "policy_aware_sql_generator",
            "ALLOW" if sql else "ERROR",
            artifact={
                "raw_sql": sql,
                "raw_sql_chars": len(sql) if sql else 0,
                "m5_guide_included": generation.m5_guide is not None,
                "role_authorized_schema_chars": len(generation.role_authorized_schema),
                "history_turn_count": len(history),
                "nlq": context.nlq,
            },
            audit={"prompt_sections": ["m5"] if generation.m5_guide else []},
            llm_usage=response.usage,
            error=None if sql else "EMPTY_SQL",
            raw_objects={"generation_input": asdict(generation), "prompt_payload": {
                "role": context.role,
                "user_id": context.user_id,
                "current_nlq": context.nlq,
                "history": history,
                "m5_guide": generation.m5_guide,
                "role_authorized_schema": generation.role_authorized_schema,
            }},
        )

    result = timed_module("M6", "policy_aware_sql_generator", _inner)
    return result.artifact.get("raw_sql"), result


def _guide_section(title: str, value: dict | None) -> str:
    if not value:
        return ""
    return f"{title} (validated runtime context):\n{json.dumps(value, ensure_ascii=False, indent=2)}"


def _clean_sql(text: str) -> str | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("sql"):
            stripped = stripped[3:].strip()
    lines = [line for line in stripped.splitlines() if not line.strip().startswith("--")]
    return "\n".join(lines).strip() or None
