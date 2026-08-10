from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from trustedsql.modules.common import merge_usage, timed_module
from trustedsql.prompts.prompt_loader import render_prompt
from trustedsql.providers.client import GeminiClient, OpenAICompatibleClient
from trustedsql.providers.output_schemas import ResourcePlannerOutput
from trustedsql.schemas import ModuleResult, ResourcePlan, TrustedContext
from trustedsql.sql.schema import normalize_name


def run(
    context: TrustedContext,
    llm: GeminiClient | OpenAICompatibleClient | None,
    module_config: dict[str, Any],
    m2_hint: dict[str, Any] | None = None,
) -> tuple[ResourcePlan | None, ModuleResult]:
    def _inner() -> ModuleResult:
        if not llm:
            return ModuleResult("M3", "policy_grounded_resource_planner", "ERROR", error="LLM_NOT_CONFIGURED")
        plan, usage = _llm_plan(context, llm, module_config, m2_hint)
        return ModuleResult(
            module_id="M3",
            stage="policy_grounded_resource_planner",
            decision="ALLOW",
            artifact={"nlq": context.nlq, "access_plan": plan.__dict__, "llm_planner_used": True},
            audit={
                "history_turn_count": len(context.history),
                "history_is_unbounded": True,
                "compact_schema_chars": len(context.compact_schema or ""),
                "m2_hint_available_for_authorization": bool(m2_hint),
            },
            raw_objects={"access_plan": plan.__dict__},
            llm_usage=usage,
        )

    result = timed_module("M3", "policy_grounded_resource_planner", _inner)
    if result.decision != "ALLOW":
        return None, result
    return ResourcePlan(**result.artifact["access_plan"]), result


def _llm_plan(
    context: TrustedContext,
    llm: GeminiClient | OpenAICompatibleClient,
    module_config: dict[str, Any],
    m2_hint: dict[str, Any] | None,
) -> tuple[ResourcePlan, dict[str, Any]]:
    if not context.compact_schema:
        raise ValueError("Missing compact schema for M3")
    cfg = module_config.get("llm") or module_config.get("vertex", {}) or {}
    history = [
        {
            "turn_id": item.turn_id,
            "nlq": item.nlq,
            "decision": item.decision,
            "final_sql": item.final_sql,
            "execution_result_json": item.execution_result_json,
        }
        for item in context.history
    ]
    prompt = render_prompt(
        "m3_access_planner.txt",
        role=context.role,
        user_id=context.user_id,
        nlq=context.nlq,
        history=history,
        m2_hint=m2_hint or {},
        policy_summary=context.policy_index.policy_summary_for_role(context.role),
        compact_schema=context.compact_schema,
        output_schema=ResourcePlannerOutput.model_json_schema(),
    )
    _maybe_log_m3_prompt(context, prompt, module_config)
    temperature = float(cfg.get("temperature", 0.0))
    max_output_tokens = int(cfg.get("max_output_tokens", module_config.get("max_output_tokens", 1600)))
    usage: dict[str, Any] = {}
    data: dict[str, Any] | None = None
    try:
        data, first_usage = llm.generate_json(
            prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        merge_usage(usage, first_usage)
        parsed = ResourcePlannerOutput.model_validate(data)
    except (ValidationError, ValueError) as exc:
        repair_prompt = "\n".join([
            prompt,
            "",
            "The previous structured response was invalid.",
            f"Validation error: {str(exc)[:1200]}",
            f"Previous JSON: {json.dumps(data, ensure_ascii=False) if data is not None else 'not parseable'}",
            "Return one corrected JSON object only. Do not include SQL, expressions, or column references as predicate values.",
        ])
        repaired, repair_usage = llm.generate_json(
            repair_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        merge_usage(usage, repair_usage)
        usage["structured_retry_count"] = 1
        parsed = ResourcePlannerOutput.model_validate(repaired)
    return _plan_from_output(parsed), usage


def _plan_from_output(output: ResourcePlannerOutput) -> ResourcePlan:
    resources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for resource in output.requested_resources:
        table = normalize_name(resource.table)
        if not table or table in seen:
            continue
        seen.add(table)
        resources.append({
            "table": table,
            "columns": _dedupe(normalize_name(column) for column in resource.columns if column),
        })
    return ResourcePlan(
        intent=output.intent.strip(),
        policy_refs=_dedupe(str(ref) for ref in output.policy_refs if ref),
        requested_resources=resources,
        scope_type=output.scope_type,
        target_resource_table=normalize_name(output.target_resource_table) if output.target_resource_table else None,
        target_identity_predicates=[_predicate_dict(item.model_dump()) for item in output.target_identity_predicates],
        query_filter_predicates=[_predicate_dict(item.model_dump()) for item in output.query_filter_predicates],
    )


def _predicate_dict(value: dict[str, Any]) -> dict[str, Any]:
    result = {
        "table": normalize_name(str(value.get("table") or "")),
        "column": normalize_name(str(value.get("column") or "")),
        "operator": str(value.get("operator") or "=").upper(),
        "value": value.get("value"),
    }
    return result


def _dedupe(values: Any) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _maybe_log_m3_prompt(context: TrustedContext, prompt: str, module_config: dict[str, Any]) -> None:
    enabled = bool(module_config.get("log_prompts") or os.environ.get("TRUSTEDSQL_LOG_M3_PROMPTS"))
    if not enabled:
        return
    log_dir = module_config.get("prompt_log_dir") or os.environ.get("TRUSTEDSQL_M3_PROMPT_LOG_DIR")
    root = Path(str(log_dir or "debug/prompts/m3"))
    sequence = _safe_path_part(context.sequence_id or context.sample_id)
    path = (
        root
        / _safe_path_part(context.run_id)
        / _safe_path_part(context.setting_id)
        / f"{sequence}__{_safe_path_part(context.sample_id)}__turn_{context.turn_id}.txt"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            f"run_id={context.run_id}",
            f"setting_id={context.setting_id}",
            f"sequence_id={context.sequence_id}",
            f"sample_id={context.sample_id}",
            f"turn_id={context.turn_id}",
            f"role={context.role}",
            f"user_id={context.user_id}",
            "",
            "--- PROMPT ---",
            prompt,
        ]),
        encoding="utf-8",
    )


def _safe_path_part(value: Any) -> str:
    text = str(value or "none")
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._") or "none"
