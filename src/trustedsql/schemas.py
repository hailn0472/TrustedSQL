from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class RuntimeDecision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    ERROR = "ERROR"


@dataclass(frozen=True)
class TurnHistoryItem:
    turn_id: int
    nlq: str
    decision: str
    raw_sql: str | None = None
    final_sql: str | None = None
    executed: bool = False
    execution_result_json: Any = None
    blocked_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeTurnInput:
    run_id: str
    setting_id: str
    sequence_id: str | None
    sample_id: str
    turn_id: int
    role: str
    user_id: int
    nlq: str
    history: list[TurnHistoryItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["history"] = [item.to_dict() for item in self.history]
        return data


@dataclass(frozen=True)
class NormalizedTurn:
    turn_id: int
    nlq: str
    sql_gt: str | None
    turn_label: str


@dataclass(frozen=True)
class NormalizedSequence:
    sample_id: str
    source_dataset: str
    turn_type: str
    seq_label: str
    role: str
    user_id: int
    attack_tags: dict[str, Any]
    turns: list[NormalizedTurn]
    primary_type: str | None = None


@dataclass
class ModuleResult:
    module_id: str
    stage: str
    decision: str
    artifact: dict[str, Any] = field(default_factory=dict)
    audit: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    llm_usage: dict[str, Any] = field(default_factory=dict)
    raw_objects: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrustedContext:
    run_id: str
    setting_id: str
    sequence_id: str | None
    sample_id: str
    turn_id: int
    role: str
    user_id: int
    nlq: str
    history: list[TurnHistoryItem]
    schema_ddl: str
    schema_graph: Any
    policy_index: Any
    compact_schema: str | None = None


@dataclass
class ResourcePlan:
    intent: str
    policy_refs: list[str]
    requested_resources: list[dict[str, Any]]
    scope_type: str
    target_resource_table: str | None = None
    target_identity_predicates: list[dict[str, Any]] = field(default_factory=list)
    query_filter_predicates: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ResourceContract:
    policy_refs: list[str]
    scope_type: str
    target_resource_table: str | None
    scope_anchor_table: str | None
    row_filter: str | None = None
    target_identity_predicates: list[dict[str, Any]] = field(default_factory=list)
    query_filter_predicates: list[dict[str, Any]] = field(default_factory=list)
    requires_db_proof: bool = True


@dataclass
class VerifiedAuthorization:
    policy_refs: list[str]
    scope_type: str
    current_user_bindings: list[dict[str, Any]] = field(default_factory=list)
    verified_targets: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GenerationInput:
    role_authorized_schema: str
    m5_guide: dict[str, Any] | None = None


@dataclass
class ScopeProofResult:
    decision: str
    proof_sql: str | None = None
    matched_count: int | None = None
    reason_code: str | None = None


@dataclass
class SqlValidationResult:
    decision: str
    final_sql: str | None
    reason_code: str | None = None


@dataclass
class MethodTurnOutput:
    run_id: str
    setting_id: str
    sequence_id: str | None
    sample_id: str
    turn_id: int
    role: str
    user_id: int
    nlq: str
    decision: str
    blocked_at: str | None = None
    raw_sql: str | None = None
    final_sql: str | None = None
    executed: bool = False
    execution_result_json: Any = None
    execution_columns: list[str] = field(default_factory=list)
    module_trace: list[ModuleResult] = field(default_factory=list)
    latency_ms: float = 0.0
    llm_usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["module_trace"] = [item.to_dict() for item in self.module_trace]
        return data
