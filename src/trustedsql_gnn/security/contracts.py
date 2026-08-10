from __future__ import annotations

from typing import Any

from pydantic import Field

from trustedsql_gnn.contracts import IntentResolution, StrictModel


class AuthContext(StrictModel):
    user_id: int
    role: str
    is_authenticated: bool = True


class PolicyRoute(StrictModel):
    intent: str
    legacy_intent: str | None = None
    operation: str
    requested_scope: str
    target_relation: str
    target_concepts: list[str]
    candidate_policy_refs: list[str]
    security_signals: list[str] = Field(default_factory=list)
    guard_signals: list[str] = Field(default_factory=list)
    guard_evidence: list[dict[str, Any]] = Field(default_factory=list)
    confidence: str = "high"
    needs_planner: bool = False
    ambiguity_reasons: list[str] = Field(default_factory=list)
    source_resolution: IntentResolution

