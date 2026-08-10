"""TrustedSQL DataTrain Generation.

``data_synthesis`` is the stable import path retained for existing notebooks
and release scripts.  The package provides reviewer-facing workflow metadata,
dataset-family definitions, and lazy public generation entry points.
"""

from .api import (
    build_intent_release,
    generate_benign,
    generate_intent_conversations,
    generate_multi_turn,
    generate_policy_grounded_gnn,
    generate_single_turn_prompt_injection,
    package_frozen_datatrain,
)
from .registry import (
    DATASET_FAMILIES,
    DatasetFamily,
    DatasetFamilySpec,
    dataset_family_summary,
    get_dataset_family,
)
from .workflow import (
    PACKAGE_TITLE,
    PHASE_STAGE_MAP,
    STABLE_IMPORT_PATH,
    WORKFLOW_DEFINITIONS,
    WORKFLOW_VERSION,
    PipelineStage,
    WorkflowPhase,
    phase_for_stage,
    workflow_summary,
)

__version__ = "1.0.0"

__all__ = [
    "DATASET_FAMILIES",
    "PACKAGE_TITLE",
    "PHASE_STAGE_MAP",
    "STABLE_IMPORT_PATH",
    "WORKFLOW_DEFINITIONS",
    "WORKFLOW_VERSION",
    "DatasetFamily",
    "DatasetFamilySpec",
    "PipelineStage",
    "WorkflowPhase",
    "build_intent_release",
    "dataset_family_summary",
    "generate_benign",
    "generate_intent_conversations",
    "generate_multi_turn",
    "generate_policy_grounded_gnn",
    "generate_single_turn_prompt_injection",
    "get_dataset_family",
    "package_frozen_datatrain",
    "phase_for_stage",
    "workflow_summary",
]
