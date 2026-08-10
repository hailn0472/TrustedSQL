from __future__ import annotations

import re
from typing import Any

from trustedsql.providers.client import GeminiClient, OpenAICompatibleClient
from trustedsql.providers.output_schemas import PromptIntegrityOutput
from trustedsql.modules.common import timed_module
from trustedsql.prompts.prompt_loader import render_prompt
from trustedsql.schemas import ModuleResult, TrustedContext


PATTERNS = [
    (re.compile(r"\b(ignore|bypass|override|disable)\b.*\b(policy|permission|role|guard|instruction)\b", re.I), "POLICY_BYPASS"),
    (re.compile(r"\b(show|dump|export|return)\b.*\b(all|entire|every)\b.*\b(table|database|record|student|user)\b", re.I), "RAW_DUMP"),
    (re.compile(r"\b(drop|delete|insert|update|alter|truncate|grant|revoke)\b", re.I), "DANGEROUS_SQL_PAYLOAD"),
    (re.compile(r"(--|/\*|\*/|;\s*(select|drop|delete|update|insert))", re.I), "SQL_PAYLOAD"),
    (re.compile(r"\b(i am|act as|pretend)\b.*\b(admin|lecturer|teacher|system)\b", re.I), "FABRICATED_AUTHORITY"),
]


def run(
    context: TrustedContext,
    llm: GeminiClient | OpenAICompatibleClient | None,
    module_config: dict[str, Any],
) -> ModuleResult:
    def _inner() -> ModuleResult:
        hits = [name for pattern, name in PATTERNS if pattern.search(context.nlq)]
        recent_history = _recent_history(context.history, module_config)
        usage: dict[str, Any] = {}
        llm_verdict = "ALLOW"
        llm_reason = ""
        parsed = None
        if not hits and module_config.get("llm_classifier", True) and llm:
            llm_cfg = module_config.get("llm") or module_config.get("vertex", {}) or {}
            temperature = float(llm_cfg.get("temperature", 0.0))
            max_tokens = int(llm_cfg.get("max_output_tokens", 256))

            prompt = render_prompt(
                "m1_prompt_integrity_guard.txt",
                role=context.role,
                user_id=context.user_id,
                recent_history_nlqs=[h.nlq for h in recent_history],
                nlq=context.nlq,
                output_schema=PromptIntegrityOutput.model_json_schema(),
            )
            data, usage = llm.generate_json(
                prompt,
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            parsed = PromptIntegrityOutput.model_validate(data)
            llm_verdict = parsed.decision
            llm_reason = parsed.reason
        decision = "DENY" if hits or llm_verdict == "DENY" else "ALLOW"
        raw_objects: dict[str, Any] = {}
        if parsed is not None:
            raw_objects["prompt_integrity_raw"] = parsed.model_dump()
        return ModuleResult(
            module_id="M1",
            stage="prompt_integrity_guard",
            decision=decision,
            artifact={
                "decision": decision,
                "nlq": context.nlq,
                "nlq_chars": len(context.nlq),
                "recent_history_nlqs": [h.nlq for h in recent_history],
                "heuristic_hits": hits,
                "llm_verdict": llm_verdict,
                "llm_reason": llm_reason,
                "patterns_checked": len(PATTERNS),
            },
            audit={
                "heuristic_hits": hits,
                "llm_decision": llm_verdict,
                "reason": llm_reason,
                "classifier_used": not hits and module_config.get("llm_classifier", True) and llm is not None,
            },
            llm_usage=usage,
            raw_objects=raw_objects,
        )

    return timed_module("M1", "prompt_integrity_guard", _inner)


def _recent_history(history: list[Any], module_config: dict[str, Any]) -> list[Any]:
    raw_limit = module_config.get("history_turn_limit", 3)
    if str(raw_limit).lower() == "max":
        return list(history)
    history_limit = int(raw_limit)
    return history[-history_limit:] if history_limit > 0 else []

