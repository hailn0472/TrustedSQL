"""Generator for single-turn prompt-injection conversations."""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from data_synthesis.common.labeling import build_label_prompt as build_common_label_prompt
from data_synthesis.common.generation_runner import run_generation_jobs
from data_synthesis.common.pipeline_contract import artifact_names, pipeline_contract_summary
from data_synthesis.common.policy_index import PolicyIndex
from data_synthesis.common.quota import apply_overgenerate_buffer
from data_synthesis.common.target_conditions import attach_target_conditions
from data_synthesis.common.user_context import UserContextIndex

from .canonicalize import (
    build_raw_audit_record,
    canonicalize_sequence,
    export_summary_excel,
    parse_and_validate,
    validate_canonical_dataset,
)
from .prompts import build_repair_prompt, build_singleturn_pi_prompt
from .spec import (
    build_generation_plan,
    expected_total,
    scaled_pi_counts,
    scaled_slot_counts,
    summarize_jobs,
    summarize_slots,
)


FAMILY = "SingleturnPI"
ARTIFACTS = artifact_names(FAMILY)
RAW_FILENAME = ARTIFACTS.raw
FINAL_FILENAME = ARTIFACTS.final
PROMPTS_FILENAME = ARTIFACTS.prompts
ERRORS_FILENAME = ARTIFACTS.errors
SUMMARY_EXCEL_FILENAME = ARTIFACTS.summary_excel
VALIDATION_FILENAME = ARTIFACTS.validation


def generate_singleturn_pi_dataset(
    schemas: Sequence[str],
    policy_index: PolicyIndex,
    output_dir: str,
    *,
    model: str,
    save_every: int = 50,
    max_parse_retries: int = 1,
    max_workers: int = 1,
    request_delay: float = 1.0,
    total: Optional[int] = None,
    user_context_index: Optional[UserContextIndex] = None,
    label_threshold: float = 0.75,
    overgenerate_buffer: float = 0.15,
    duplicate_threshold: float = 0.96,
    raw_filename: str = RAW_FILENAME,
    final_filename: str = FINAL_FILENAME,
    prompts_filename: str = PROMPTS_FILENAME,
    summary_excel_filename: str = SUMMARY_EXCEL_FILENAME,
    validation_filename: str = VALIDATION_FILENAME,
) -> Dict[str, Any]:
    """Generate and verify single-turn attacks under fixed release quotas.

    Quotas jointly preserve injection type and RBAC-violation coverage. Raw
    candidates remain separate from the validated final release.
    """

    expected_pi = scaled_pi_counts(total)
    expected_slots = scaled_slot_counts(total)
    generation_slots = apply_overgenerate_buffer(expected_slots, overgenerate_buffer)
    jobs = build_generation_plan(
        schemas,
        policy_index,
        total=total,
        user_context_index=user_context_index,
        quota_counts=generation_slots,
    )
    jobs = attach_target_conditions(
        jobs=jobs,
        output_dir=output_dir,
        dataset_family=FAMILY,
        model=model,
        max_workers=max_workers,
        request_delay=request_delay,
        save_every=save_every,
    )
    print(f"  Planned Singleturn PI records: {len(jobs)} / base plan {expected_total()}")
    print(f"  Release PI counts: {expected_pi}")
    print(f"  Release slot counts: {expected_slots}")
    print(f"  Generated PI counts with buffer: {summarize_jobs(jobs)}")
    print(f"  Generated slot counts with buffer: {summarize_slots(jobs)}")

    result = run_generation_jobs(
        jobs=jobs,
        output_dir=output_dir,
        model=model,
        build_prompt=build_singleturn_pi_prompt,
        build_repair_prompt=build_repair_prompt,
        parse_validate=parse_and_validate,
        canonicalize=canonicalize_sequence,
        build_raw_record=build_raw_audit_record,
        validate_dataset=lambda records: validate_canonical_dataset(
            records,
            expected_pi_counts=expected_pi,
            expected_slot_counts=expected_slots,
        ),
        export_summary=lambda records, path: export_summary_excel(
            records,
            path,
            expected_pi_counts=expected_pi,
            expected_slot_counts=expected_slots,
        ),
        raw_filename=raw_filename,
        final_filename=final_filename,
        prompts_filename=prompts_filename,
        errors_filename=ERRORS_FILENAME,
        summary_excel_filename=summary_excel_filename,
        validation_filename=validation_filename,
        expected_release_counts=expected_slots,
        release_quota_key=lambda record: _slot_key_from_record(record),
        build_label_prompt=lambda candidate, parsed, job: build_common_label_prompt(
            candidate=candidate,
            parsed=parsed,
            job=job,
            dataset_family=FAMILY,
        ),
        dataset_family=FAMILY,
        label_report_filename=ARTIFACTS.label_report,
        rejected_filename=ARTIFACTS.rejected,
        verify_report_filename=ARTIFACTS.verify_report,
        human_verify_csv_filename=ARTIFACTS.human_verify_csv,
        human_verify_excel_filename=ARTIFACTS.human_verify_excel,
        label_threshold=label_threshold,
        duplicate_threshold=duplicate_threshold,
        banner="SINGLETURN PI: Malicious prompt-injection generation",
        save_every=save_every,
        max_parse_retries=max_parse_retries,
        max_workers=max_workers,
        request_delay=request_delay,
    )
    result["pi_counts"] = result["validation"]["pi_counts"]
    result["slot_counts"] = result["validation"]["slot_counts"]
    result["pipeline_contract"] = pipeline_contract_summary(FAMILY)
    return result


def _slot_key_from_record(record: Dict[str, Any]) -> str:
    tags = record.get("attack_tags") or {}
    value = tags.get("rbac_violation")
    if isinstance(value, list):
        value = None if not value else value[0]
    return f"{record.get('primary_type')}:{value or 'null'}"
