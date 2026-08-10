from __future__ import annotations

from pathlib import Path
from typing import Any

from trustedsql_gnn.inference.runtime import RuntimeIntentEngine
from trustedsql_gnn.security.contracts import AuthContext, PolicyRoute
from trustedsql_gnn.security.guards import SecurityEvidenceGuard
from trustedsql_gnn.paths import GNNPaths


SECURITY_TRANSITION_SIGNALS = {
    "PUBLIC_OR_IDENTITY_TO_PRIVATE": "public_or_identity_to_private",
    "SAFE_TO_EXTERNAL_TARGET": "safe_to_external_target",
    "COHORT_TO_SPECIFIC_EXTERNAL": "cohort_to_specific_external",
    "RANGE_OUTSIDE_ALLOWED_COHORT": "range_outside_allowed_cohort",
    "AGGREGATE_TO_IDENTITY": "aggregate_to_identity",
    "ROLE_MUTATION": "role_mutation",
    "INSTRUCTION_OVERRIDE": "instruction_override",
    "ENCODED_INSTRUCTION": "encoded_instruction",
    "SQL_PAYLOAD": "sql_payload",
}


class GNNIntentPhase:
    """Standalone method_v1 GNN intent phase.

    This wrapper does not authorize access, inspect SQL, execute SQL, or read
    evaluation labels. It only resolves multi-turn intent/scope/target/security
    evidence and emits JSON that a host pipeline can feed into its risk guard or
    policy planner.
    """

    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        device: str = "cpu",
        allow_hash_encoder: bool = False,
        log_path: str | Path | None = None,
    ) -> None:
        self.project_root = (
            Path(project_root).resolve()
            if project_root
            else Path(__file__).resolve().parents[3]
        )
        paths = GNNPaths.from_project_root(self.project_root)
        self.engine = RuntimeIntentEngine.from_checkpoint(
            root=self.project_root,
            checkpoint_path=paths.checkpoint_path,
            device=device,
            allow_hash_encoder=allow_hash_encoder,
            log_path=log_path,
            gnn_authority=False,
        )
        self.guard = SecurityEvidenceGuard()

    def reset_conversation(self, conversation_id: str) -> None:
        self.engine.reset_session(conversation_id)

    def run_turn(
        self,
        *,
        conversation_id: str,
        role: str,
        user_id: str | int,
        current_text: str,
        turn_id: int | None = None,
    ) -> dict[str, Any]:
        runtime_result = self.engine.predict_next(
            conversation_id=conversation_id,
            role=role.lower(),
            text=current_text,
            turn_id=turn_id,
        )
        resolution = runtime_result.resolution
        legacy_adapter = runtime_result.legacy_adapter
        security_signals = self._security_signals_from_resolution(resolution)
        route = PolicyRoute(
            intent=resolution.primary_intent,
            legacy_intent=legacy_adapter.get("legacy_intent"),
            operation=resolution.operation,
            requested_scope=self._requested_scope(role, resolution.scope),
            target_relation=resolution.target_relation,
            target_concepts=resolution.target_concepts,
            candidate_policy_refs=[],
            security_signals=security_signals,
            confidence=self._confidence(resolution),
            needs_planner=False,
            ambiguity_reasons=[],
            source_resolution=resolution,
        )
        guarded_route = self.guard.apply(
            query=current_text,
            history=runtime_result.request.history,
            auth=AuthContext(user_id=user_id, role=role.lower()),
            resolution=resolution,
            route=route,
        )
        combined_security_signals = list(
            dict.fromkeys([*security_signals, *guarded_route.security_signals])
        )
        return {
            "phase": "GNN_INTENT_PHASE",
            "version": "method_v1_gnn_intent",
            "runtime_authority": "advisory",
            "input": {
                "conversation_id": conversation_id,
                "role": role.lower(),
                "user_id": str(user_id),
                "current_text": current_text,
                "turn_id": runtime_result.request.current_turn_id,
                "history_turn_count": len(runtime_result.request.history),
            },
            "output": {
                "intent_resolution": resolution.model_dump(mode="json"),
                "legacy_adapter": legacy_adapter,
                "security_transition": resolution.security_transition,
                "security_signals": combined_security_signals,
                "guard_signals": guarded_route.guard_signals,
                "guard_evidence": guarded_route.guard_evidence,
                "policy_planner_hint": {
                    "legacy_intent": legacy_adapter.get("legacy_intent"),
                    "semantic_intent": resolution.primary_intent,
                    "operation": resolution.operation,
                    "scope": resolution.scope,
                    "requested_scope": guarded_route.requested_scope,
                    "target_relation": resolution.target_relation,
                    "target_concepts": resolution.target_concepts,
                    "confidence": guarded_route.confidence,
                    "needs_planner": guarded_route.needs_planner,
                    "ambiguity_reasons": guarded_route.ambiguity_reasons,
                },
                "risk_guard_hint": {
                    "deny_or_restrict_recommended": bool(combined_security_signals),
                    "security_transition": resolution.security_transition,
                    "guard_signals": guarded_route.guard_signals,
                    "security_signals": combined_security_signals,
                },
                "graph_debug": runtime_result.shadow.get("graph_debug"),
                "shadow": runtime_result.shadow,
            },
        }

    @staticmethod
    def _security_signals_from_resolution(resolution: Any) -> list[str]:
        signal = SECURITY_TRANSITION_SIGNALS.get(resolution.security_transition)
        return [signal] if signal else []

    @staticmethod
    def _requested_scope(role: str, scope: str) -> str:
        if scope == "SELF":
            return "self"
        if scope == "ENROLLED_COHORT":
            return "enrolled_class"
        if scope == "ASSIGNED_COHORT":
            return "assigned_class"
        if scope in {"PUBLIC", "GLOBAL"}:
            return "global"
        if scope in {"EXTERNAL_INDIVIDUAL", "EXTERNAL_COHORT"}:
            return "other_user" if role.lower() == "student" else "global"
        return "ambiguous"

    @staticmethod
    def _confidence(resolution: Any) -> str:
        uncertainty = resolution.uncertainty or {}
        margin = float(uncertainty.get("top1_top2_margin") or 0.0)
        entropy = float(uncertainty.get("intent_entropy") or 9.0)
        if margin >= 0.40 and entropy <= 1.0:
            return "high"
        if margin >= 0.15 and entropy <= 2.0:
            return "medium"
        return "low"

