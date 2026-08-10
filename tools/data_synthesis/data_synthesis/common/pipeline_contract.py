from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

from data_synthesis.workflow import (
    PACKAGE_TITLE,
    WORKFLOW_DEFINITIONS,
    WORKFLOW_VERSION,
    PipelineStage,
)


# Backward-compatible name: these values are implementation stages, not the
# five reviewer-facing scientific phases defined in data_synthesis.workflow.
PIPELINE_PHASES: Tuple[str, ...] = tuple(stage.value for stage in PipelineStage)
PIPELINE_STAGES: Tuple[str, ...] = PIPELINE_PHASES
WORKFLOW_PHASES: Tuple[str, ...] = tuple(
    definition.phase.value for definition in WORKFLOW_DEFINITIONS
)


RELEASE_TOP_LEVEL_FIELDS: Tuple[str, ...] = (
    "id",
    "turn_type",
    "primary_type",
    "role",
    "user_context_id",
    "turns",
    "seq_label",
    "attack_tags",
)


RELEASE_TURN_FIELDS: Tuple[str, ...] = (
    "turn_id",
    "nlq",
    "sql_gt",
    "turn_label",
)


RAW_AUDIT_FIELDS: Tuple[str, ...] = (
    "generation_reason",
    "target_relevance_claim",
    "raw_model_output",
)


@dataclass(frozen=True)
class PipelineArtifacts:
    target_condition_prompts: str
    target_conditions: str
    target_condition_usage_json: str
    target_condition_usage_csv: str
    slots: str
    prompts: str
    raw: str
    errors: str
    label_report: str
    rejected: str
    verify_report: str
    coverage_report: str
    human_verify_csv: str
    human_verify_excel: str
    validation: str
    final: str
    summary_excel: str
    usage_json: str
    usage_csv: str


def artifact_names(family: str) -> PipelineArtifacts:
    return PipelineArtifacts(
        target_condition_prompts=f"{family}_TargetCondition_Prompts.json",
        target_conditions=f"{family}_TargetConditions.json",
        target_condition_usage_json=f"{family}_TargetCondition_Usage.json",
        target_condition_usage_csv=f"{family}_TargetCondition_Usage.csv",
        slots=f"{family}_Slots.json",
        prompts=f"{family}_Prompts.json",
        raw=f"{family}_Raw.json",
        errors=f"{family}_Errors.json",
        label_report=f"{family}_Label_Report.json",
        rejected=f"{family}_Rejected.json",
        verify_report=f"{family}_Verify_Report.json",
        coverage_report=f"{family}_Coverage_Report.json",
        human_verify_csv=f"{family}_Human_Verify.csv",
        human_verify_excel=f"{family}_Human_Verify.xlsx",
        validation=f"{family}_Validation.json",
        final=f"{family}_Final.json",
        summary_excel=f"{family}_Summary.xlsx",
        usage_json=f"{family}_Usage.json",
        usage_csv=f"{family}_Usage.csv",
    )


def pipeline_contract_summary(family: str) -> Dict[str, object]:
    artifacts = artifact_names(family)
    return {
        "project": PACKAGE_TITLE,
        "workflow_version": WORKFLOW_VERSION,
        "family": family,
        "workflow_phases": [definition.as_dict() for definition in WORKFLOW_DEFINITIONS],
        "stages": list(PIPELINE_STAGES),
        # Retained because existing manifests and notebooks read this key.
        "phases": list(PIPELINE_PHASES),
        "release_top_level_fields": list(RELEASE_TOP_LEVEL_FIELDS),
        "release_turn_fields": list(RELEASE_TURN_FIELDS),
        "raw_audit_fields": list(RAW_AUDIT_FIELDS),
        "artifacts": artifacts.__dict__,
    }
