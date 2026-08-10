from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ModuleDecision(str, Enum):
    CONTINUE = "CONTINUE"
    DENY = "DENY"
    ERROR = "ERROR"


class ModuleStatus(str, Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"
    REWRITE = "REWRITE"
    WARN = "WARN"
    ERROR = "ERROR"
    SKIP = "SKIP"


class TurnDecision(str, Enum):
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
    execution_result_preview: Any = None
    blocked_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeTurnInput:
    run_id: str
    architecture_id: str
    sequence_id: str | None
    sample_id: str
    source_dataset: str
    turn_id: int
    role: str
    user_id: int
    nlq: str
    history: list[TurnHistoryItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["history"] = [item.to_dict() for item in self.history]
        return data


@dataclass
class SecurityContext:
    role: str
    user_id: int
    schema_index: Any
    schema_ddl: str
    history: list[TurnHistoryItem]


@dataclass
class ModuleResult:
    module_id: str
    stage: str
    status: str
    decision: str
    artifact: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    llm_usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ArchitectureTurnOutput:
    run_id: str
    architecture_id: str
    sequence_id: str | None
    sample_id: str
    source_dataset: str
    seq_label: str
    turn_id: int
    turn_label: str
    role: str
    user_id: int
    nlq: str
    decision: str
    blocked_at: str | None = None
    raw_sql: str | None = None
    final_sql: str | None = None
    executed: bool = False
    execution_result_json: Any = None
    module_trace: list[ModuleResult] = field(default_factory=list)
    latency_ms: float = 0.0
    llm_usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    attack_tags: dict[str, Any] = field(default_factory=dict)
    primary_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["module_trace"] = [result.to_dict() for result in self.module_trace]
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

