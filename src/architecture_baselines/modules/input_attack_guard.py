from __future__ import annotations

import re
from typing import Any

from architecture_baselines.llm import LLMClient
from architecture_baselines.prompts import INPUT_ATTACK_SYSTEM, input_attack_prompt
from architecture_baselines.schemas import ModuleDecision, ModuleResult, ModuleStatus, SecurityContext
from architecture_baselines.utils.timing import measure_ms


ATTACK_PATTERNS = [
    (re.compile(r"\b(ignore|bypass|skip|disable)\b.*\b(policy|permission|security|guard|whitelist)\b", re.I), "POLICY_BYPASS"),
    (re.compile(r"\b(drop|delete|update|insert|alter|truncate)\s+(table|from|into|public\.|select)\b", re.I), "SQL_PAYLOAD"),
    (re.compile(r";\s*(select|drop|delete|update|insert|alter)\b", re.I), "SQL_PAYLOAD"),
    (re.compile(r"\b(previous|earlier|above)\b.*\b(complement|outside|hidden|forbidden|not allowed)\b", re.I), "MULTI_TURN_ABUSE"),
    (re.compile(r"\bpretend\b.*\b(admin|lecturer|teacher|authorized|whitelist)\b", re.I), "CONTEXT_POISONING"),
]


class InputAttackGuard:
    module_id = "D1"

    def __init__(self, llm: LLMClient | None = None, config: dict[str, Any] | None = None):
        self.llm = llm
        self.config = config or {}

    def run(self, nlq: str, context: SecurityContext) -> ModuleResult:
        with measure_ms() as timer:
            risks = [risk for pattern, risk in ATTACK_PATTERNS if pattern.search(nlq)]
            usage: dict[str, Any] = {}
            error: str | None = None
            artifact = {"input_risk_verdict": "SAFE", "risk_type": "NONE", "reason": "No deterministic attack pattern matched."}
            if risks:
                artifact = {"input_risk_verdict": "ATTACK", "risk_type": risks[0], "reason": f"Deterministic pattern matched: {risks[0]}"}
            elif self.llm and self.config.get("llm_classifier", True):
                try:
                    artifact, usage = self.llm.generate_json(INPUT_ATTACK_SYSTEM, input_attack_prompt(nlq, context.role, context.user_id, context.history))
                except Exception as exc:
                    artifact = {"input_risk_verdict": "ERROR", "risk_type": "NONE", "reason": f"LLM classifier failed: {exc}"}
                    error = str(exc)
            verdict = str(artifact.get("input_risk_verdict", "ATTACK")).upper()
            if error:
                status, decision = ModuleStatus.ERROR.value, ModuleDecision.ERROR.value
            elif verdict not in {"SAFE", "ATTACK"}:
                artifact["input_risk_verdict"] = "ATTACK"
                artifact["reason"] = f"Invalid classifier verdict {verdict!r}; fail-closed as attack."
                status, decision = ModuleStatus.BLOCK.value, ModuleDecision.DENY.value
            elif verdict == "ATTACK" and self.config.get("deny_on_attack", True):
                status, decision = ModuleStatus.BLOCK.value, ModuleDecision.DENY.value
            else:
                status, decision = ModuleStatus.PASS.value, ModuleDecision.CONTINUE.value
        return ModuleResult(self.module_id, "input_attack_guard", status, decision, artifact, {"deterministic_risks": risks}, timer.elapsed_ms, usage, error)

