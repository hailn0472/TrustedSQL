"""Scientific workflow definitions for TrustedSQL dataset construction.

The implementation keeps seven fine-grained stages for artifact compatibility
and groups them into five reviewer-facing phases.  This module contains names
and mappings only; generation behavior remains in the family-specific modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Tuple


PACKAGE_TITLE = "TrustedSQL DataTrain Generation"
STABLE_IMPORT_PATH = "data_synthesis"
WORKFLOW_VERSION = "datatrain_generation_v1"


class WorkflowPhase(str, Enum):
    """High-level phases used in methodology and reviewer documentation."""

    GROUNDING = "grounding"
    PLANNING = "planning"
    GENERATION = "generation"
    VERIFICATION = "verification"
    RELEASE = "release"


class PipelineStage(str, Enum):
    """Artifact-producing stages retained by the executable pipeline."""

    EXPERT_FEWSHOT = "expert_fewshot"
    TARGET_CONDITION = "target_condition"
    SLOT = "slot"
    GENERATE_RAW = "generate_raw"
    LABEL = "label"
    VALIDATE_VERIFY = "validate_verify"
    FINALIZE = "finalize"


@dataclass(frozen=True)
class PhaseDefinition:
    """Traceable description of one workflow phase."""

    phase: WorkflowPhase
    title: str
    purpose: str
    stages: Tuple[PipelineStage, ...]
    principal_outputs: Tuple[str, ...]

    def as_dict(self) -> Dict[str, object]:
        return {
            "phase": self.phase.value,
            "title": self.title,
            "purpose": self.purpose,
            "stages": [stage.value for stage in self.stages],
            "principal_outputs": list(self.principal_outputs),
        }


WORKFLOW_DEFINITIONS: Tuple[PhaseDefinition, ...] = (
    PhaseDefinition(
        phase=WorkflowPhase.GROUNDING,
        title="Policy and Pattern Grounding",
        purpose=(
            "Load schema, policy, role/context, taxonomy, pattern, and target-condition "
            "constraints before any candidate is generated."
        ),
        stages=(PipelineStage.EXPERT_FEWSHOT, PipelineStage.TARGET_CONDITION),
        principal_outputs=("validated contracts", "target conditions"),
    ),
    PhaseDefinition(
        phase=WorkflowPhase.PLANNING,
        title="Quota and Slot Planning",
        purpose=(
            "Translate release targets into deterministic generation jobs while "
            "preserving family, label, role, and pattern quotas."
        ),
        stages=(PipelineStage.SLOT,),
        principal_outputs=("generation slots", "prompt-ready jobs"),
    ),
    PhaseDefinition(
        phase=WorkflowPhase.GENERATION,
        title="Controlled Candidate Generation",
        purpose=(
            "Generate complete conversation candidates from grounded jobs and retain "
            "raw responses, prompts, usage metadata, and repair attempts for audit."
        ),
        stages=(PipelineStage.GENERATE_RAW,),
        principal_outputs=("raw candidates", "generation audit trail"),
    ),
    PhaseDefinition(
        phase=WorkflowPhase.VERIFICATION,
        title="Labeling and Verification",
        purpose=(
            "Canonicalize candidates, assess labels, reject invalid or duplicate "
            "records, and verify structural, policy, and coverage constraints."
        ),
        stages=(PipelineStage.LABEL, PipelineStage.VALIDATE_VERIFY),
        principal_outputs=("label reports", "validation reports", "accepted candidates"),
    ),
    PhaseDefinition(
        phase=WorkflowPhase.RELEASE,
        title="Release Construction",
        purpose=(
            "Select quota-complete records, write immutable release artifacts, and "
            "export summaries, manifests, splits, or graph artifacts where applicable."
        ),
        stages=(PipelineStage.FINALIZE,),
        principal_outputs=("final dataset", "release manifest", "summary artifacts"),
    ),
)


PHASE_STAGE_MAP: Dict[WorkflowPhase, Tuple[PipelineStage, ...]] = {
    definition.phase: definition.stages for definition in WORKFLOW_DEFINITIONS
}


def phase_for_stage(stage: PipelineStage | str) -> WorkflowPhase:
    """Return the reviewer-facing phase that owns an implementation stage."""

    normalized = PipelineStage(stage)
    for phase, stages in PHASE_STAGE_MAP.items():
        if normalized in stages:
            return phase
    raise ValueError(f"Unmapped pipeline stage: {normalized.value}")


def workflow_summary() -> Dict[str, object]:
    """Return a JSON-serializable description of the scientific workflow."""

    return {
        "project": PACKAGE_TITLE,
        "stable_import_path": STABLE_IMPORT_PATH,
        "workflow_version": WORKFLOW_VERSION,
        "phases": [definition.as_dict() for definition in WORKFLOW_DEFINITIONS],
    }
