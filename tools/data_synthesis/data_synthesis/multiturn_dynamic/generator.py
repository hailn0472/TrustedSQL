"""Generator for dynamic multi-turn security conversations."""

from __future__ import annotations

from dataclasses import replace
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
    parse_model_json,
    validate_canonical_dataset,
    validate_raw_sequence,
)
from .prompts import build_multiturn_prompt, build_repair_prompt
from .spec import (
    GenerationJob,
    build_generation_plan,
    expected_total,
    scaled_pattern_counts,
    summarize_jobs,
)


FAMILY = "Multiturn"
ARTIFACTS = artifact_names(FAMILY)
RAW_FILENAME = ARTIFACTS.raw
FINAL_FILENAME = ARTIFACTS.final
PROMPTS_FILENAME = ARTIFACTS.prompts
ERRORS_FILENAME = ARTIFACTS.errors
SUMMARY_EXCEL_FILENAME = ARTIFACTS.summary_excel
VALIDATION_FILENAME = ARTIFACTS.validation


def _parse_and_validate(output: str, job: GenerationJob) -> Dict[str, Any]:
    parsed = parse_model_json(output)
    validate_raw_sequence(parsed, job)
    return parsed


def generate_multiturn_dataset(
    schemas: Sequence[str],
    conditions: Sequence[Dict[str, Any]],
    output_dir: str,
    *,
    model: str,
    save_every: int = 50,
    max_parse_retries: int = 1,
    max_workers: int = 1,
    request_delay: float = 1.0,
    total: Optional[int] = None,
    policy_index: Optional[PolicyIndex] = None,
    user_context_index: Optional[UserContextIndex] = None,
    label_threshold: float = 0.75,
    overgenerate_buffer: float = 0.15,
    duplicate_threshold: float = 0.96,
    max_refill_rounds: int = 5,
    refill_buffer: float = 1.5,
    raw_filename: str = RAW_FILENAME,
    final_filename: str = FINAL_FILENAME,
    prompts_filename: str = PROMPTS_FILENAME,
    summary_excel_filename: str = SUMMARY_EXCEL_FILENAME,
    validation_filename: str = VALIDATION_FILENAME,
) -> Dict[str, Any]:
    """Generate multi-turn conversations and enforce pattern-level coverage.

    Target conditions are attached before prompting. Refill rounds replace
    rejected candidates and close missing pattern or target-condition coverage.
    """

    expected_counts = scaled_pattern_counts(total)
    generation_counts = apply_overgenerate_buffer(expected_counts, overgenerate_buffer)
    jobs = build_generation_plan(
        schemas,
        conditions,
        total=total,
        policy_index=policy_index,
        user_context_index=user_context_index,
        quota_counts=generation_counts,
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
    print(f"  Planned sequences: {len(jobs)} / base plan {expected_total()}")
    print(f"  Release pattern counts: {expected_counts}")
    print(f"  Generated pattern counts with buffer: {summarize_jobs(jobs)}")
    target_template_jobs = _target_template_jobs(jobs)
    target_template_by_id = _target_template_jobs_by_id(jobs)
    target_id_to_pattern = {
        target_id: job.primary_type
        for target_id, job in target_template_by_id.items()
    }

    def build_refill_jobs(
        refill_counts: Dict[str, int],
        start_sequence_number: int,
        refill_round: int,
        refill_context: Optional[Dict[str, Any]] = None,
    ) -> Sequence[GenerationJob]:
        refill_context = refill_context or {}
        missing_target_ids = [
            target_id
            for target_id in refill_context.get("missing_coverage_keys", [])
            if target_id in target_template_by_id
        ]
        if missing_target_ids:
            attached_jobs = []
            for target_id in missing_target_ids:
                template_job = target_template_by_id[target_id]
                attempts = max(3, int(refill_counts.get(template_job.primary_type, 0) or 0))
                for attempt_index in range(attempts):
                    attached_jobs.append(
                        replace(
                            template_job,
                            sequence_number=start_sequence_number + len(attached_jobs),
                            pattern_sample_index=template_job.pattern_sample_index + (attempt_index + 1) * len(jobs),
                            slot_id=f"MT-COVREFILL-{refill_round:02d}-{len(attached_jobs) + 1:04d}",
                        )
                    )
            return attached_jobs

        quota_counts = {pattern_code: int(refill_counts.get(pattern_code, 0)) for pattern_code in expected_counts}
        refill_jobs = build_generation_plan(
            schemas,
            conditions,
            total=total,
            policy_index=policy_index,
            user_context_index=user_context_index,
            quota_counts=quota_counts,
        )
        attached_jobs = []
        per_key_index: Dict[tuple[str, str], int] = {}
        for offset, job in enumerate(refill_jobs):
            key = (job.primary_type, job.role)
            pool = target_template_jobs.get(key) or target_template_jobs.get((job.primary_type, "student")) or jobs
            pool_index = per_key_index.get(key, 0)
            template_job = pool[pool_index % len(pool)]
            per_key_index[key] = pool_index + 1
            attached_jobs.append(
                replace(
                    job,
                    sequence_number=start_sequence_number + offset,
                    slot_id=f"MT-REFILL-{refill_round:02d}-{offset + 1:04d}",
                    target_condition=template_job.target_condition,
                )
            )
        return attached_jobs

    result = run_generation_jobs(
        jobs=jobs,
        output_dir=output_dir,
        model=model,
        build_prompt=build_multiturn_prompt,
        build_repair_prompt=build_repair_prompt,
        parse_validate=_parse_and_validate,
        canonicalize=canonicalize_sequence,
        build_raw_record=build_raw_audit_record,
        validate_dataset=lambda records: validate_canonical_dataset(
            records,
            expected_counts=expected_counts,
            policy_index=policy_index,
        ),
        export_summary=lambda records, path: export_summary_excel(
            records,
            path,
            expected_counts=expected_counts,
        ),
        raw_filename=raw_filename,
        final_filename=final_filename,
        prompts_filename=prompts_filename,
        errors_filename=ERRORS_FILENAME,
        summary_excel_filename=summary_excel_filename,
        validation_filename=validation_filename,
        expected_release_counts=expected_counts,
        release_quota_key=lambda record: str((record.get("attack_tags") or {}).get("mt_pattern")),
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
        coverage_report_filename=ARTIFACTS.coverage_report,
        human_verify_csv_filename=ARTIFACTS.human_verify_csv,
        human_verify_excel_filename=ARTIFACTS.human_verify_excel,
        label_threshold=label_threshold,
        duplicate_threshold=duplicate_threshold,
        banner="MULTITURN: Dynamic malicious multi-turn generation",
        save_every=save_every,
        max_parse_retries=max_parse_retries,
        max_workers=max_workers,
        request_delay=request_delay,
        coverage_key=_target_condition_coverage_key,
        expected_coverage_keys=sorted(target_id_to_pattern),
        coverage_refill_key=lambda target_id: target_id_to_pattern.get(target_id),
        build_refill_jobs=build_refill_jobs,
        max_refill_rounds=max_refill_rounds,
        refill_buffer=refill_buffer,
    )
    result["pattern_counts"] = result["validation"]["pattern_counts"]
    result["pipeline_contract"] = pipeline_contract_summary(FAMILY)
    return result


def _target_template_jobs(jobs: Sequence[GenerationJob]) -> Dict[tuple[str, str], Sequence[GenerationJob]]:
    templates: Dict[tuple[str, str], list[GenerationJob]] = {}
    for job in jobs:
        templates.setdefault((job.primary_type, job.role), []).append(job)
    return templates


def _target_template_jobs_by_id(jobs: Sequence[GenerationJob]) -> Dict[str, GenerationJob]:
    templates: Dict[str, GenerationJob] = {}
    for job in jobs:
        target_condition = job.target_condition or {}
        target_id = str(target_condition.get("target_condition_id") or "")
        if target_id:
            templates.setdefault(target_id, job)
    return templates


def _target_condition_coverage_key(item: Dict[str, Any]) -> Optional[str]:
    metadata = item.get("metadata") or {}
    target_condition = metadata.get("target_condition") or {}
    target_id = target_condition.get("target_condition_id") if isinstance(target_condition, dict) else None
    if target_id is None:
        return None
    target_id = str(target_id).strip()
    return target_id or None
