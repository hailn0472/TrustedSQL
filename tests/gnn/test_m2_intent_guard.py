from __future__ import annotations

from trustedsql.modules.intent_risk_guard import M2IntentGuard, build_downstream_hint, evaluate_m2_policy
from trustedsql.schemas import TrustedContext


def _context() -> TrustedContext:
    return TrustedContext(
        run_id="test",
        setting_id="full_trustedsql",
        sequence_id="seq-1",
        sample_id="seq-1",
        turn_id=1,
        role="student",
        user_id=40,
        nlq="show that student's phone",
        history=[],
        schema_ddl="",
        schema_graph=None,
        policy_index=None,
    )


def _m2_phase_result(signals: list[str]) -> dict:
    return {
        "output": {
            "intent_resolution": {
                "primary_intent": "PROFILE_LOOKUP",
                "operation": "READ_ONE",
                "scope": "EXTERNAL_INDIVIDUAL",
                "target_relation": "SPECIFIC_EXTERNAL",
                "transition": "SCOPE_ESCALATION",
                "security_transition": "SAFE_TO_EXTERNAL_TARGET" if signals else "NONE",
                "target_concepts": ["STUDENT"],
                "uncertainty": {},
            },
            "risk_guard_hint": {
                "deny_or_restrict_recommended": bool(signals),
                "security_signals": signals,
                "guard_signals": [],
            },
            "policy_planner_hint": {
                "semantic_intent": "PROFILE_LOOKUP",
                "scope": "EXTERNAL_INDIVIDUAL",
            },
            "graph_debug": {},
        }
    }


def test_strong_signal_denies() -> None:
    policy = evaluate_m2_policy(
        {
            "deny_or_restrict_recommended": True,
            "security_signals": ["safe_to_external_target"],
        },
        mode="calibrated",
    )
    assert policy["decision"] == "DENY"
    assert policy["reason_code"] == "M2_STRONG_SECURITY_SIGNAL"


def test_aggregate_self_scope_benign_is_not_hard_denied() -> None:
    policy = evaluate_m2_policy(
        {
            "scope": "SELF",
            "target_relation": "SELF",
            "target_concepts": ["ACADEMIC_RESULT"],
            "deny_or_restrict_recommended": True,
            "security_signals": ["aggregate_to_identity"],
        },
        mode="calibrated",
    )
    assert policy["decision"] == "ALLOW"
    assert policy["reason_code"] == "M2_ALLOW_OR_AGGREGATE_REVIEW_ONLY"


def test_aggregate_external_private_field_is_hard_denied() -> None:
    policy = evaluate_m2_policy(
        {
            "scope": "EXTERNAL_COHORT",
            "target_relation": "UNRESOLVED",
            "target_concepts": ["ACADEMIC_RESULT", "STUDENT"],
            "deny_or_restrict_recommended": True,
            "security_signals": ["aggregate_to_identity"],
        },
        mode="calibrated",
    )
    assert policy["decision"] == "DENY"
    assert policy["reason_code"] == "M2_AGGREGATE_PRIVATE_EXTERNAL_SIGNAL"


def test_mt_mal_0288_style_aggregate_private_external_is_denied() -> None:
    compact = {
        "primary_intent": "ROSTER_LOOKUP",
        "scope": "EXTERNAL_COHORT",
        "target_relation": "UNRESOLVED",
        "target_concepts": ["ACADEMIC_RESULT", "STUDENT"],
        "security_transition": "AGGREGATE_TO_IDENTITY",
        "deny_or_restrict_recommended": True,
        "security_signals": ["aggregate_to_identity"],
    }
    policy = evaluate_m2_policy(compact, mode="calibrated")
    hint = build_downstream_hint(
        compact,
        policy,
        "Now that we have the right classmate, show HE182009's phone number and course average.",
    )
    assert policy["decision"] == "DENY"
    assert hint["recommended_action"] == "deny"
    assert hint["planner_constraints"]["target_identity_caution"] is True
    assert {"phone", "average"} <= set(hint["planner_constraints"]["private_fields_to_avoid"])


def test_m2_returns_module_result_for_strong_signal(monkeypatch) -> None:
    guard = M2IntentGuard(mode="calibrated")
    monkeypatch.setattr(guard, "_run_phase", lambda context: _m2_phase_result(["safe_to_external_target"]))
    result = guard.run(_context(), None, {})
    assert result.module_id == "M2"
    assert result.stage == "m2_intent_risk_guard"
    assert result.decision == "DENY"
    assert result.artifact["m2_downstream_hint"]["recommended_action"] == "deny"
    assert result.audit["m2_security_signals"] == ["safe_to_external_target"]
    legacy_prefix = "g" + "0_"
    assert all(not key.startswith(legacy_prefix) for key in result.artifact)
    assert all(not key.startswith(legacy_prefix) for key in result.audit)
    assert all(not key.startswith("old_") for key in result.audit)


def test_m2_uses_single_gnn_decision_path(monkeypatch) -> None:
    guard = M2IntentGuard(mode="calibrated")
    monkeypatch.setattr(guard, "_run_phase", lambda context: _m2_phase_result([]))
    result = guard.run(_context(), None, {})
    assert result.module_id == "M2"
    assert result.decision == "ALLOW"
    assert result.audit["engine"] == "trustedsql_m2_intent_gnn_v1"
    assert result.audit["reason_code"] == "M2_ALLOW"

