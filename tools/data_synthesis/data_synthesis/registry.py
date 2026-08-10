"""Registry of dataset families exposed by TrustedSQL DataTrain Generation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Tuple


class DatasetFamily(str, Enum):
    """Stable identifiers for the supported construction branches."""

    BENIGN = "benign"
    SINGLE_TURN_PROMPT_INJECTION = "single_turn_prompt_injection"
    MULTI_TURN = "multi_turn"
    POLICY_GROUNDED_GNN = "policy_grounded_gnn"
    INTENT_CONVERSATION = "intent_conversation"


@dataclass(frozen=True)
class DatasetFamilySpec:
    """Reviewer-facing metadata for one dataset family."""

    family: DatasetFamily
    title: str
    implementation_module: str
    generation_mode: str
    primary_dimensions: Tuple[str, ...]
    description: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "family": self.family.value,
            "title": self.title,
            "implementation_module": self.implementation_module,
            "generation_mode": self.generation_mode,
            "primary_dimensions": list(self.primary_dimensions),
            "description": self.description,
        }


DATASET_FAMILIES: Tuple[DatasetFamilySpec, ...] = (
    DatasetFamilySpec(
        family=DatasetFamily.BENIGN,
        title="Policy-Compliant Conversations",
        implementation_module="data_synthesis.benign_dataset",
        generation_mode="llm_constrained",
        primary_dimensions=("turn_type", "role"),
        description="Benign single-turn and multi-turn conversations grounded in role policy.",
    ),
    DatasetFamilySpec(
        family=DatasetFamily.SINGLE_TURN_PROMPT_INJECTION,
        title="Single-Turn Prompt Injection",
        implementation_module="data_synthesis.singleturn_prompt_injection",
        generation_mode="llm_constrained",
        primary_dimensions=("injection_type", "rbac_violation"),
        description="Single-turn adversarial requests organized by injection and policy-violation type.",
    ),
    DatasetFamilySpec(
        family=DatasetFamily.MULTI_TURN,
        title="Dynamic Multi-Turn Attacks",
        implementation_module="data_synthesis.multiturn_dynamic",
        generation_mode="llm_constrained",
        primary_dimensions=("mt_pattern", "role", "target_condition"),
        description="Multi-turn conversations whose risk emerges through a controlled transition pattern.",
    ),
    DatasetFamilySpec(
        family=DatasetFamily.POLICY_GROUNDED_GNN,
        title="Policy-Grounded GNN Corpus",
        implementation_module="data_synthesis.gnn_dataset.generator",
        generation_mode="llm_constrained_with_graph_export",
        primary_dimensions=("pattern_id", "role", "policy_target", "protocol"),
        description="Policy-grounded conversations with graph targets, audits, and evaluation protocols.",
    ),
    DatasetFamilySpec(
        family=DatasetFamily.INTENT_CONVERSATION,
        title="Intent Conversation Corpus",
        implementation_module="data_synthesis.gnn_dataset.execution_v2",
        generation_mode="deterministic_task_contract",
        primary_dimensions=("category", "intent", "transition", "security_transition"),
        description="Deterministic conversation construction from explicit task and taxonomy contracts.",
    ),
)


_FAMILY_INDEX = {spec.family: spec for spec in DATASET_FAMILIES}


def get_dataset_family(family: DatasetFamily | str) -> DatasetFamilySpec:
    """Resolve a family identifier to its immutable registry entry."""

    return _FAMILY_INDEX[DatasetFamily(family)]


def dataset_family_summary() -> Tuple[Dict[str, object], ...]:
    """Return all registry entries in their documented order."""

    return tuple(spec.as_dict() for spec in DATASET_FAMILIES)
