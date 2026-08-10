from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Category = Literal["BENIGN_SINGLE_TURN", "BENIGN_MULTI_TURN", "MALICIOUS_MULTI_TURN"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReferenceTarget(StrictModel):
    target_turn: int = Field(ge=1)
    target_concept: str
    surface: str | None = None


class Mention(StrictModel):
    surface: str
    concept: str
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    source: str = "generator"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class TurnLabels(StrictModel):
    semantic_intent: str
    operation: str
    scope: str
    target_relation: str
    transition: str
    target_concepts: list[str] = Field(default_factory=list)
    reference_targets: list[ReferenceTarget] = Field(default_factory=list)
    security_transition: str = "NONE"


class ConversationTurn(StrictModel):
    turn_id: int = Field(ge=1)
    text: str = Field(min_length=1)
    labels: TurnLabels
    mentions: list[Mention] = Field(default_factory=list)
    sql_gt: str | None = None


class GenerationMetadata(StrictModel):
    generator_version: str
    surface_variant_id: str
    contrastive_pair_id: str | None = None
    entity_seed: str
    pattern_revision: str = "v1"
    extra: dict[str, Any] = Field(default_factory=dict)


class IntentConversation(StrictModel):
    conversation_id: str
    pattern_id: str
    category: Category
    role: Literal["student", "lecturer", "admin"]
    turns: list[ConversationTurn] = Field(min_length=1, max_length=8)
    generation_metadata: GenerationMetadata

    @model_validator(mode="after")
    def validate_turn_order(self) -> "IntentConversation":
        ids = [turn.turn_id for turn in self.turns]
        if ids != list(range(1, len(ids) + 1)):
            raise ValueError("turn_ids_must_be_contiguous_from_one")
        for turn in self.turns:
            for ref in turn.labels.reference_targets:
                if ref.target_turn >= turn.turn_id:
                    raise ValueError("reference_target_must_precede_source_turn")
        if self.category == "BENIGN_SINGLE_TURN" and len(self.turns) != 1:
            raise ValueError("benign_single_turn_must_have_exactly_one_turn")
        if self.category != "BENIGN_SINGLE_TURN" and len(self.turns) < 2:
            raise ValueError("multi_turn_category_requires_at_least_two_turns")
        return self


class HistoryTurn(StrictModel):
    turn_id: int
    text: str
    predicted_or_gold_state: TurnLabels | None = None
    mentions: list[Mention] = Field(default_factory=list)


class IntentSample(StrictModel):
    sample_id: str
    conversation_id: str
    pattern_id: str
    category: Category
    role: str
    current_turn_id: int
    history: list[HistoryTurn]
    current_text: str
    current_mentions: list[Mention] = Field(default_factory=list)
    labels: TurnLabels | None = None
    generation_metadata: GenerationMetadata


class RuntimeIntentRequest(StrictModel):
    conversation_id: str
    role: Literal["student", "lecturer", "admin"]
    current_turn_id: int = Field(ge=1)
    history: list[HistoryTurn] = Field(default_factory=list)
    current_text: str = Field(min_length=1)
    current_mentions: list[Mention] = Field(default_factory=list)


class GraphNode(StrictModel):
    node_id: str
    node_type: str
    label: str
    turn_id: int | None = None
    text: str = ""
    attrs: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(StrictModel):
    source: str
    target: str
    edge_type: str
    attrs: dict[str, Any] = Field(default_factory=dict)


class IntentGraph(StrictModel):
    graph_id: str
    sample_id: str
    current_turn_id: int
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntentResolution(StrictModel):
    primary_intent: str
    intent_candidates: list[dict[str, Any]]
    operation: str
    scope: str
    target_relation: str
    transition: str
    target_concepts: list[str]
    reference_links: list[dict[str, Any]]
    security_transition: str
    uncertainty: dict[str, Any]
    source: str = "gnn_intent_v1"

