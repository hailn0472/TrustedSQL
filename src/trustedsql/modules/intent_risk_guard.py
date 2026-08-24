from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from trustedsql.providers.client import GeminiClient
from trustedsql.schemas import ModuleResult, TrustedContext


STRONG_DENY_SIGNALS = {
    "safe_to_external_target",
    "public_or_identity_to_private",
    "student_code_private_guard",
    "private_external_guard",
    "secret_access_guard",
    "authority_claim_guard",
    "encoded_instruction",
    "sql_payload",
}

AGGREGATE_REVIEW_SIGNALS = {
    "aggregate_to_identity",
    "aggregate_to_identity_guard",
}

EXTERNAL_SCOPES = {
    "EXTERNAL_COHORT",
    "EXTERNAL_INDIVIDUAL",
    "GLOBAL",
}

PRIVATE_FIELD_TERMS = {
    "phone",
    "phone_number",
    "email",
    "gmail",
    "mail",
    "average",
    "score",
    "grade",
    "status",
    "course average",
    "academic result",
    "result",
}

PRIVATE_TARGET_CONCEPTS = {
    "ACADEMIC_RESULT",
    "PROFILE",
    "CONTACT",
    "PII",
}

STUDENT_CODE_RE = re.compile(r"\b[A-Z]{2}\d{5,}\b", re.I)
_DEFAULT_GUARD: "M2IntentGuard | None" = None


class M2IntentGuard:
    def __init__(
        self,
        *,
        package_root: str | Path | None = None,
        mode: str = "calibrated",
        device: str = "cpu",
        allow_hash_encoder: bool = False,
    ) -> None:
        self.project_root = (
            Path(package_root).resolve()
            if package_root
            else Path(__file__).resolve().parents[3]
        )
        self.mode = mode
        self.device = device
        self.allow_hash_encoder = allow_hash_encoder
        self._phase: Any = None

    def run(
        self,
        context: TrustedContext,
        llm: GeminiClient | None,
        module_config: dict[str, Any],
    ) -> ModuleResult:
        started = time.perf_counter()
        try:
            m2_result = self._run_phase(context)
            compact = compact_m2_output(m2_result)
            hard_deny_enabled = module_config.get("hard_deny", True) is not False
            policy = evaluate_m2_policy(
                compact,
                mode=self.mode,
                hard_deny=hard_deny_enabled,
            )
            decision = policy["decision"]
            downstream_hint = build_downstream_hint(compact, policy, context.nlq)
            return ModuleResult(
                module_id="M2",
                stage="m2_intent_risk_guard",
                decision=decision,
                artifact={
                    "decision": decision,
                    "nlq": context.nlq,
                    "nlq_chars": len(context.nlq),
                    "role": context.role,
                    "user_id": context.user_id,
                    "turn_id": context.turn_id,
                    "history_turn_count": len(context.history),
                    "m2_policy_planner_hint": compact["policy_planner_hint"],
                    "m2_risk_guard_hint": compact["risk_guard_hint"],
                    "m2_downstream_hint": downstream_hint,
                    "intent_resolution": compact.get("intent_resolution", {}),
                    "graph_debug": compact.get("graph_debug", {}),
                },
                audit={
                    **audit_from_compact(compact),
                    "engine": "trustedsql_m2_intent_gnn_v1",
                    "mode": self.mode,
                    "reason_code": policy["reason_code"],
                    "strong_signals": policy["strong_signals"],
                    "aggregate_review_signals": policy["aggregate_review_signals"],
                    "aggregate_escalated": policy["aggregate_escalated"],
                    "hard_deny_enabled": hard_deny_enabled,
                    "hard_deny_would_trigger": policy.get("hard_deny_would_trigger", False),
                    "waived_hard_deny_reasons": policy.get("waived_hard_deny_reasons", {}),
                },
                latency_ms=(time.perf_counter() - started) * 1000,
                raw_objects={
                    "raw_m2_result": m2_result,
                    "compact": compact,
                    "policy": policy,
                    "downstream_hint": downstream_hint,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return ModuleResult(
                module_id="M2",
                stage="m2_intent_risk_guard",
                decision="ERROR",
                artifact={"decision": "ERROR"},
                audit={"engine": "trustedsql_m2_intent_gnn_v1", "mode": self.mode},
                latency_ms=(time.perf_counter() - started) * 1000,
                error=str(exc),
            )

    def reset_conversation(self, conversation_id: str) -> None:
        if self._phase is not None:
            self._phase.reset_conversation(conversation_id)

    def _run_phase(self, context: TrustedContext) -> dict[str, Any]:
        phase = self._load_phase()
        conversation_id = conversation_id_for_context(context)
        self._sync_history(phase, conversation_id, context)
        return phase.run_turn(
            conversation_id=conversation_id,
            role=context.role,
            user_id=context.user_id,
            current_text=context.nlq,
            turn_id=context.turn_id,
        )

    def _sync_history(self, phase: Any, conversation_id: str, context: TrustedContext) -> None:
        session = phase.engine.get_session(conversation_id)
        if session and session.turns and session.turns[-1].turn_id >= context.turn_id:
            phase.reset_conversation(conversation_id)
            session = None
        current_count = len(session.turns) if session else 0
        needed = [item for item in sorted(context.history, key=lambda h: h.turn_id) if item.turn_id > current_count]
        for item in needed:
            phase.run_turn(
                conversation_id=conversation_id,
                role=context.role,
                user_id=context.user_id,
                current_text=item.nlq,
                turn_id=item.turn_id,
            )

    def _load_phase(self) -> Any:
        if self._phase is not None:
            return self._phase
        if not self.project_root.exists():
            raise FileNotFoundError(f"TrustedSQL project root not found: {self.project_root}")
        from trustedsql_gnn import GNNIntentPhase

        self._phase = GNNIntentPhase(
            project_root=self.project_root,
            device=self.device,
            allow_hash_encoder=self.allow_hash_encoder,
        )
        return self._phase


def run(context: TrustedContext, llm: GeminiClient | None, module_config: dict[str, Any]) -> ModuleResult:
    global _DEFAULT_GUARD
    phase_config = module_config.get("intent_gnn", {}) or {}
    guard_key = (
        str(phase_config.get("project_root") or phase_config.get("package_root") or ""),
        str(phase_config.get("mode") or module_config.get("mode") or "calibrated"),
        str(phase_config.get("device") or module_config.get("device") or "cpu"),
        bool(phase_config.get("allow_hash_encoder", module_config.get("allow_hash_encoder", False))),
    )
    if _DEFAULT_GUARD is None or getattr(_DEFAULT_GUARD, "_guard_key", None) != guard_key:
        _DEFAULT_GUARD = M2IntentGuard(
            package_root=phase_config.get("project_root") or phase_config.get("package_root"),
            mode=guard_key[1],
            device=guard_key[2],
            allow_hash_encoder=guard_key[3],
        )
        _DEFAULT_GUARD._guard_key = guard_key  # type: ignore[attr-defined]
    return _DEFAULT_GUARD.run(context, llm, module_config)


def conversation_id_for_context(context: TrustedContext) -> str:
    sequence_key = context.sequence_id or context.sample_id
    return f"{context.run_id}:{context.setting_id}:{sequence_key}"


def compact_m2_output(result: dict[str, Any]) -> dict[str, Any]:
    output = result.get("output", {})
    input_payload = result.get("input", {}) or {}
    resolution = output.get("intent_resolution") or {}
    risk = output.get("risk_guard_hint") or {}
    planner = output.get("policy_planner_hint") or {}
    security_signals = list(dict.fromkeys((risk.get("security_signals") or []) + (risk.get("guard_signals") or [])))
    return {
        "nlq": input_payload.get("current_text") or "",
        "intent_resolution": resolution,
        "risk_guard_hint": risk,
        "policy_planner_hint": planner,
        "graph_debug": output.get("graph_debug") or {},
        "primary_intent": resolution.get("primary_intent"),
        "operation": resolution.get("operation"),
        "scope": resolution.get("scope"),
        "target_relation": resolution.get("target_relation"),
        "transition": resolution.get("transition"),
        "security_transition": resolution.get("security_transition"),
        "target_concepts": resolution.get("target_concepts") or [],
        "uncertainty": resolution.get("uncertainty") or {},
        "deny_or_restrict_recommended": bool(risk.get("deny_or_restrict_recommended")),
        "security_signals": security_signals,
    }


def evaluate_m2_policy(
    compact: dict[str, Any],
    *,
    mode: str = "calibrated",
    hard_deny: bool = True,
) -> dict[str, Any]:
    signals = set(compact.get("security_signals") or [])
    strong = sorted(signals & STRONG_DENY_SIGNALS)
    aggregate = sorted(signals & AGGREGATE_REVIEW_SIGNALS)
    aggregate_escalation = aggregate and _aggregate_has_private_external_target(compact)
    waived_reasons = _m2_waived_hard_deny_reasons(compact, strong, aggregate_escalation)
    if waived_reasons:
        waived = set(waived_reasons.get("strong_signals") or [])
        strong = [signal for signal in strong if signal not in waived]
        if waived_reasons.get("aggregate_escalation"):
            aggregate_escalation = False
    strict_recommendation = bool(mode == "strict" and compact.get("deny_or_restrict_recommended"))
    hard_deny_would_trigger = bool(strict_recommendation or strong or aggregate_escalation)
    if not hard_deny:
        return {
            "decision": "ALLOW",
            "reason_code": "M2_HARD_DENY_DISABLED" if hard_deny_would_trigger else (
                "M2_ALLOW_OR_AGGREGATE_REVIEW_ONLY" if aggregate else "M2_ALLOW"
            ),
            "strong_signals": strong,
            "aggregate_review_signals": aggregate,
            "aggregate_escalated": bool(aggregate_escalation),
            "hard_deny_enabled": False,
            "hard_deny_would_trigger": hard_deny_would_trigger,
            "waived_hard_deny_reasons": waived_reasons,
        }
    if strict_recommendation:
        return {
            "decision": "DENY",
            "reason_code": "M2_STRICT_RECOMMENDATION",
            "strong_signals": strong,
            "aggregate_review_signals": aggregate,
            "aggregate_escalated": False,
            "hard_deny_enabled": True,
            "hard_deny_would_trigger": True,
        }
    if strong or aggregate_escalation:
        return {
            "decision": "DENY",
            "reason_code": "M2_AGGREGATE_PRIVATE_EXTERNAL_SIGNAL" if aggregate_escalation and not strong else "M2_STRONG_SECURITY_SIGNAL",
            "strong_signals": strong,
            "aggregate_review_signals": aggregate,
            "aggregate_escalated": bool(aggregate_escalation),
            "hard_deny_enabled": True,
            "hard_deny_would_trigger": True,
        }
    return {
        "decision": "ALLOW",
        "reason_code": "M2_ALLOW_OR_AGGREGATE_REVIEW_ONLY" if aggregate else "M2_ALLOW",
        "strong_signals": strong,
        "aggregate_review_signals": aggregate,
        "aggregate_escalated": False,
        "hard_deny_enabled": True,
        "hard_deny_would_trigger": False,
        "waived_hard_deny_reasons": waived_reasons,
    }


def build_downstream_hint(compact: dict[str, Any], policy: dict[str, Any], nlq: str = "") -> dict[str, Any]:
    signals = compact.get("security_signals") or []
    action = "allow"
    if policy.get("decision") == "DENY":
        action = "deny"
    elif policy.get("hard_deny_would_trigger"):
        action = "restrict_scope" if _has_private_field_request(compact, nlq) else "planner_caution"
    elif policy.get("aggregate_review_signals"):
        action = "restrict_scope" if _has_private_field_request(compact, nlq) else "planner_caution"
    constraints = {
        "private_fields_to_avoid": sorted(_private_terms_in_request(compact, nlq)),
        "requires_scope_binding": action in {"restrict_scope", "planner_caution", "deny"},
        "target_identity_caution": _has_target_identity(compact, nlq),
        "do_not_expand_cohort_or_public_identity_to_private_record": action in {"restrict_scope", "planner_caution", "deny"},
    }
    hint = {
        "intent": compact.get("primary_intent"),
        "scope": compact.get("scope"),
        "target_relation": compact.get("target_relation"),
        "target_concepts": compact.get("target_concepts") or [],
        "security_signals": signals,
        "security_transition": compact.get("security_transition"),
        "recommended_action": action,
        "planner_constraints": constraints,
    }
    if policy.get("decision") == "DENY":
        hint["deny_reason_code"] = policy.get("reason_code")
    elif action != "allow":
        hint["review_reason_code"] = policy.get("reason_code")
    return hint


def _aggregate_has_private_external_target(compact: dict[str, Any]) -> bool:
    text = _compact_text(compact)
    external = compact.get("scope") in EXTERNAL_SCOPES or _has_target_identity(compact, text)
    return bool(external and _has_private_field_request(compact, text))


def _m2_waived_hard_deny_reasons(
    compact: dict[str, Any],
    strong_signals: list[str],
    aggregate_escalation: bool,
) -> dict[str, Any]:
    waived_strong: list[str] = []
    waived_aggregate = False
    text = _compact_text(compact)
    if "public_or_identity_to_private" in strong_signals:
        if _is_clear_self_scope_request(compact, text) or _is_catalog_metadata_request(compact, text):
            waived_strong.append("public_or_identity_to_private")
    if aggregate_escalation and _is_clear_self_scope_request(compact, text):
        waived_aggregate = True
    if not waived_strong and not waived_aggregate:
        return {}
    return {
        "strong_signals": waived_strong,
        "aggregate_escalation": waived_aggregate,
    }


def _is_clear_self_scope_request(compact: dict[str, Any], text: str) -> bool:
    scope = str(compact.get("scope") or "").upper()
    target_relation = str(compact.get("target_relation") or "").upper()
    planner = compact.get("policy_planner_hint") or {}
    requested_scope = str(planner.get("requested_scope") or "").lower()
    has_self_marker = bool(
        re.search(r"\b(my|me|mine|myself|own|current user|profile header)\b", text)
        or "for myself" in text
        or requested_scope == "self"
        or scope == "SELF"
        or target_relation == "CURRENT_USER"
    )
    if not has_self_marker:
        return False
    return not _has_explicit_external_person_target(text)


def _is_catalog_metadata_request(compact: dict[str, Any], text: str) -> bool:
    request_text = str(compact.get("nlq") or "").lower()
    if _has_explicit_external_person_target(request_text):
        return False
    catalog_terms = {
        "course code",
        "course name",
        "english course name",
        "vietnamese course name",
        "minimum passing",
        "minimum average",
        "min_avg_mark_to_pass",
        "time allocation",
        "number of credits",
        "no_credit",
        "credit",
        "description",
        "active",
    }
    private_person_terms = {
        "email",
        "gmail",
        "phone",
        "address",
        "date of birth",
        "student code",
        "full name",
        "grade value",
        "scores",
    }
    has_catalog_term = any(term in request_text for term in catalog_terms)
    has_course_code = bool(re.search(r"\b[A-Z]{2,4}\d{3}[A-Za-z]?\b", request_text))
    has_private_person_term = any(term in request_text for term in private_person_terms)
    return bool((has_catalog_term or has_course_code) and not has_private_person_term)


def _has_explicit_external_person_target(text: str) -> bool:
    external_terms = (
        "classmate",
        "classmates",
        "other student",
        "other students",
        "that student",
        "those students",
        "each student",
        "student's",
        "lecturer's",
    )
    if any(term in text for term in external_terms):
        return True
    if STUDENT_CODE_RE.search(text):
        return True
    return False


def _has_target_identity(compact: dict[str, Any], nlq: str = "") -> bool:
    text = _compact_text(compact, nlq)
    target_relation = str(compact.get("target_relation") or "").upper()
    target_concepts = {str(item).upper() for item in compact.get("target_concepts") or []}
    return bool(
        STUDENT_CODE_RE.search(text)
        or "EXTERNAL" in target_relation
        or "STUDENT" in target_concepts
        or "that student" in text
        or "classmate" in text
    )


def _has_private_field_request(compact: dict[str, Any], nlq: str = "") -> bool:
    return bool(_private_terms_in_request(compact, nlq))


def _private_terms_in_request(compact: dict[str, Any], nlq: str = "") -> set[str]:
    text = _compact_text(compact, nlq)
    terms = {term for term in PRIVATE_FIELD_TERMS if term in text}
    target_concepts = {str(item).upper() for item in compact.get("target_concepts") or []}
    if target_concepts & PRIVATE_TARGET_CONCEPTS:
        terms |= {concept.lower() for concept in target_concepts & PRIVATE_TARGET_CONCEPTS}
    return terms


def _compact_text(compact: dict[str, Any], nlq: str = "") -> str:
    parts = [
        nlq,
        str(compact.get("nlq") or ""),
        str(compact.get("primary_intent") or ""),
        str(compact.get("scope") or ""),
        str(compact.get("target_relation") or ""),
        " ".join(str(item) for item in compact.get("target_concepts") or []),
    ]
    return " ".join(parts).lower()


def audit_from_compact(compact: dict[str, Any]) -> dict[str, Any]:
    return {
        "m2_primary_intent": compact.get("primary_intent"),
        "m2_operation": compact.get("operation"),
        "m2_scope": compact.get("scope"),
        "m2_target_relation": compact.get("target_relation"),
        "m2_transition": compact.get("transition"),
        "m2_security_transition": compact.get("security_transition"),
        "m2_security_signals": compact.get("security_signals") or [],
        "m2_target_concepts": compact.get("target_concepts") or [],
        "m2_uncertainty": compact.get("uncertainty") or {},
    }
