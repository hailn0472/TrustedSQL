"""Shared execution engine for constrained LLM dataset generation.

Family modules provide planning, prompt, canonicalization, and validation
callbacks. This runner executes candidate generation, repair, labeling,
verification, quota selection, and artifact persistence consistently.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Sequence

from data_synthesis.gemini_client import generate_batch

from .human_verify import export_human_verify_files
from .labeling import parse_label_report, renumber_final_records, select_records_for_release
from .io import ensure_dir, save_json
from .usage import DEFAULT_PRICING, utc_now_iso, write_usage_logs


class GenerationValidationError(ValueError):
    """Raised when generated records fail parsing or validation."""


def run_generation_jobs(
    *,
    jobs: Sequence[Any],
    output_dir: str,
    model: str,
    build_prompt: Callable[[Any], str],
    build_repair_prompt: Callable[[str, str, Any], str],
    parse_validate: Callable[[str, Any], Dict[str, Any]],
    canonicalize: Callable[[Dict[str, Any], Any], Dict[str, Any]],
    build_raw_record: Callable[[Dict[str, Any], str, Any], Dict[str, Any]],
    validate_dataset: Callable[[Sequence[Dict[str, Any]]], Dict[str, Any]],
    export_summary: Callable[[Sequence[Dict[str, Any]], str], None],
    raw_filename: str,
    final_filename: str,
    prompts_filename: str,
    errors_filename: str,
    summary_excel_filename: str,
    validation_filename: str,
    banner: str,
    expected_release_counts: Optional[Dict[str, int]] = None,
    release_quota_key: Optional[Callable[[Dict[str, Any]], str]] = None,
    build_label_prompt: Optional[Callable[[Dict[str, Any], Dict[str, Any], Any], str]] = None,
    dataset_family: str = "Dataset",
    label_report_filename: str = "Label_Report.json",
    rejected_filename: str = "Rejected.json",
    verify_report_filename: str = "Verify_Report.json",
    coverage_report_filename: str = "Coverage_Report.json",
    human_verify_csv_filename: str = "Human_Verify.csv",
    human_verify_excel_filename: str = "Human_Verify.xlsx",
    label_threshold: float = 0.75,
    duplicate_threshold: float = 0.96,
    duplicate_group_key: Optional[Callable[[Dict[str, Any]], str]] = None,
    max_tokens: int = 8192,
    save_every: int = 50,
    max_parse_retries: int = 1,
    max_workers: int = 1,
    request_delay: float = 1.0,
    coverage_key: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
    expected_coverage_keys: Optional[Sequence[str]] = None,
    coverage_refill_key: Optional[Callable[[str], Optional[str]]] = None,
    build_refill_jobs: Optional[Callable[[Dict[str, int], int, int, Optional[Dict[str, Any]]], Sequence[Any]]] = None,
    max_refill_rounds: int = 0,
    refill_buffer: float = 1.0,
    build_response_schema: Optional[Callable[[Any], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run generation through candidate, verification, and release stages.

    Args:
        jobs: Grounded generation jobs produced by a family planner.
        output_dir: Directory receiving prompts, raw candidates, reports, and
            the finalized release.
        build_prompt: Converts one job into the controlled generation prompt.
        parse_validate: Parses one model response and enforces raw constraints.
        canonicalize: Converts a valid raw response to the release schema.
        validate_dataset: Applies family-level quota and consistency checks.

    Returns:
        A manifest-like dictionary containing records, validation results,
        artifact paths, and generation metadata.
    """

    ensure_dir(output_dir)
    prompts_path = os.path.join(output_dir, prompts_filename)
    raw_path = os.path.join(output_dir, raw_filename)
    final_path = os.path.join(output_dir, final_filename)
    excel_path = os.path.join(output_dir, summary_excel_filename)
    validation_path = os.path.join(output_dir, validation_filename)
    label_report_path = os.path.join(output_dir, label_report_filename)
    rejected_path = os.path.join(output_dir, rejected_filename)
    verify_report_path = os.path.join(output_dir, verify_report_filename)
    coverage_report_path = os.path.join(output_dir, coverage_report_filename)
    human_verify_csv_path = os.path.join(output_dir, human_verify_csv_filename)
    human_verify_excel_path = os.path.join(output_dir, human_verify_excel_filename)
    usage_json_path = os.path.join(output_dir, _usage_json_filename(raw_filename))
    usage_csv_path = os.path.join(output_dir, _usage_csv_filename(raw_filename))
    errors_path = os.path.join(output_dir, errors_filename)

    print("\n" + "=" * 60)
    print(f"  {banner}")
    print("=" * 60)
    print(f"  Planned records: {len(jobs)}")
    print(f"  Max workers: {max_workers}")
    print(f"  Request delay: {request_delay}s")

    usage_records: List[Dict[str, Any]] = []
    prompt_records: List[Dict[str, Any]] = []
    raw_records: List[Dict[str, Any]] = []
    candidate_items: List[Dict[str, Any]] = []
    unresolved_candidate_items: List[Dict[str, Any]] = []
    unresolved_errors: List[Dict[str, Any]] = []
    initial_error_categories: Counter = Counter()
    initial_invalid_count = 0

    def append_batch(batch_jobs: Sequence[Any], *, batch_name: str, batch_round: int) -> None:
        nonlocal initial_invalid_count
        batch = _run_generation_parse_batch(
            jobs=batch_jobs,
            output_dir=output_dir,
            raw_filename=raw_filename,
            model=model,
            build_prompt=build_prompt,
            build_repair_prompt=build_repair_prompt,
            parse_validate=parse_validate,
            canonicalize=canonicalize,
            build_raw_record=build_raw_record,
            max_tokens=max_tokens,
            save_every=save_every,
            max_parse_retries=max_parse_retries,
            max_workers=max_workers,
            request_delay=request_delay,
            index_offset=len(raw_records),
            batch_name=batch_name,
            batch_round=batch_round,
            build_response_schema=build_response_schema,
        )
        prompt_records.extend(batch["prompt_records"])
        raw_records.extend(batch["raw_records"])
        candidate_items.extend(batch["candidate_items"])
        unresolved_candidate_items.extend(batch["unresolved_candidate_items"])
        unresolved_errors.extend(batch["unresolved_errors"])
        usage_records.extend(batch["usage_records"])
        if batch_name == "initial":
            initial_invalid_count = int(batch.get("invalid_count") or 0)
            initial_error_categories.update(batch.get("initial_error_categories") or {})
        _label_new_candidates(
            batch["candidate_items"],
            checkpoint_name=f"{label_report_filename}.{batch_name}.checkpoint",
        )
        save_json(prompts_path, prompt_records)
        save_json(raw_path, raw_records)
        if unresolved_errors:
            save_json(errors_path, unresolved_errors)

    def _label_new_candidates(
        new_items: List[Dict[str, Any]],
        *,
        checkpoint_name: str,
    ) -> None:
        if not new_items:
            return
        if build_label_prompt is not None:
            label_reports, label_usage_records = _run_label_phase(
                new_items,
                model=model,
                build_label_prompt=build_label_prompt,
                label_threshold=label_threshold,
                max_tokens=max_tokens,
                save_every=save_every,
                max_workers=max_workers,
                request_delay=request_delay,
                output_dir=output_dir,
                checkpoint_name=checkpoint_name,
            )
            usage_records.extend(label_usage_records)
        else:
            label_reports = [
                {
                    "pass": True,
                    "matches_slot": True,
                    "target_relevant": True,
                    "policy_aligned": True,
                    "confidence": 1.0,
                    "evidence": {"mode": "label_phase_disabled"},
                    "reject_reasons": [],
                }
                for _ in new_items
            ]
        for item, report in zip(new_items, label_reports):
            if _record_has_control_characters(item.get("canonical") or {}):
                report = {
                    "pass": False,
                    "matches_slot": False,
                    "target_relevant": False,
                    "policy_aligned": False,
                    "confidence": 0.0,
                    "evidence": {},
                    "reject_reasons": ["control_character_in_candidate"],
                    "raw_label_output": report.get("raw_label_output", ""),
                }
            item["label_report"] = report

    append_batch(jobs, batch_name="initial", batch_round=0)

    selected_items, rejected_items, verify_report, final_records, validation = _select_and_validate(
        candidate_items=candidate_items,
        unresolved_candidate_items=unresolved_candidate_items,
        expected_release_counts=expected_release_counts,
        release_quota_key=release_quota_key,
        duplicate_threshold=duplicate_threshold,
        duplicate_group_key=duplicate_group_key,
        validate_dataset=validate_dataset,
        coverage_key=coverage_key,
        expected_coverage_keys=expected_coverage_keys,
    )
    coverage_report = _build_coverage_report(
        planned_jobs=jobs,
        selected_items=selected_items,
        candidate_items=candidate_items,
        final_records=final_records,
        expected_coverage_keys=expected_coverage_keys,
        coverage_key=coverage_key,
    )
    _attach_coverage_report(verify_report, coverage_report)

    refill_round = 0
    while (
        expected_release_counts
        and release_quota_key
        and build_refill_jobs is not None
        and refill_round < max_refill_rounds
        and (verify_report.get("missing_counts") or _coverage_missing_keys(verify_report))
    ):
        refill_round += 1
        refill_context: Dict[str, Any] = {"reason": "quota", "missing_coverage_keys": []}
        if verify_report.get("missing_counts"):
            refill_counts = _build_refill_counts(
                verify_report.get("missing_counts") or {},
                refill_buffer,
                refill_round=refill_round,
            )
        else:
            missing_coverage = _coverage_missing_keys(verify_report)
            refill_context = {
                "reason": "coverage",
                "missing_coverage_keys": missing_coverage,
            }
            refill_counts = _coverage_refill_counts(
                missing_coverage,
                coverage_refill_key=coverage_refill_key,
                refill_buffer=refill_buffer,
                refill_round=refill_round,
            )
        print(f"  [REFILL] Round {refill_round}/{max_refill_rounds}: {refill_counts}")
        refill_jobs = list(build_refill_jobs(refill_counts, len(raw_records) + 1, refill_round, refill_context))
        if not refill_jobs:
            print("  [REFILL] No refill jobs returned; stopping refill.")
            break
        append_batch(refill_jobs, batch_name=f"refill_{refill_round}", batch_round=refill_round)
        selected_items, rejected_items, verify_report, final_records, validation = _select_and_validate(
            candidate_items=candidate_items,
            unresolved_candidate_items=unresolved_candidate_items,
            expected_release_counts=expected_release_counts,
            release_quota_key=release_quota_key,
            duplicate_threshold=duplicate_threshold,
            duplicate_group_key=duplicate_group_key,
            validate_dataset=validate_dataset,
            coverage_key=coverage_key,
            expected_coverage_keys=expected_coverage_keys,
        )
        coverage_report = _build_coverage_report(
            planned_jobs=jobs,
            selected_items=selected_items,
            candidate_items=candidate_items,
            final_records=final_records,
            expected_coverage_keys=expected_coverage_keys,
            coverage_key=coverage_key,
        )
        _attach_coverage_report(verify_report, coverage_report)

    if unresolved_errors:
        print(f"  [WARN] {len(unresolved_errors)} outputs could not be parsed or validated. See {errors_path}.")

    verify_report["initial_generation_count"] = len(jobs)
    verify_report["initial_invalid_count"] = initial_invalid_count
    verify_report["initial_error_categories"] = dict(initial_error_categories)
    verify_report["initial_parse_validate_pass_rate"] = (
        (len(jobs) - initial_invalid_count) / len(jobs)
        if jobs
        else 1.0
    )
    save_json(validation_path, validation)
    save_json(label_report_path, _label_report_payload(candidate_items, label_threshold))
    save_json(rejected_path, _rejected_payload(rejected_items))
    save_json(coverage_report_path, coverage_report)
    save_json(verify_report_path, verify_report)
    human_verify_payload = export_human_verify_files(
        csv_path=human_verify_csv_path,
        excel_path=human_verify_excel_path,
        dataset_family=dataset_family,
        selected_items=selected_items,
        final_records=final_records,
        rejected_items=rejected_items,
        verify_report=verify_report,
    )
    usage_payload = write_usage_logs(
        json_path=usage_json_path,
        csv_path=usage_csv_path,
        records=usage_records,
        pricing=DEFAULT_PRICING,
    )

    if not verify_report["ok"]:
        raise GenerationValidationError(
            f"Dataset release gate failed. See {verify_report_path} and {validation_path}."
        )

    save_json(final_path, final_records)
    export_summary(final_records, excel_path)

    print(f"  [OK] Raw audit JSON: {raw_path}")
    print(f"  [OK] Final JSON: {final_path}")
    print(f"  [OK] Summary Excel: {excel_path}")
    print(f"  [OK] Label report: {label_report_path}")
    print(f"  [OK] Rejected: {rejected_path}")
    print(f"  [OK] Verify report: {verify_report_path}")
    print(f"  [OK] Coverage report: {coverage_report_path}")
    print(f"  [OK] Human verify CSV: {human_verify_csv_path}")
    print(f"  [OK] Human verify Excel: {human_verify_excel_path}")
    print(f"  [OK] Usage JSON: {usage_json_path}")
    print(f"  [OK] Usage CSV: {usage_csv_path}")

    return {
        "raw_path": raw_path,
        "final_path": final_path,
        "excel_path": excel_path,
        "validation_path": validation_path,
        "label_report_path": label_report_path,
        "rejected_path": rejected_path,
        "verify_report_path": verify_report_path,
        "coverage_report_path": coverage_report_path,
        "human_verify_csv_path": human_verify_csv_path,
        "human_verify_excel_path": human_verify_excel_path,
        "usage_json_path": usage_json_path,
        "usage_csv_path": usage_csv_path,
        "usage_summary": usage_payload["summary"],
        "total_records": len(final_records),
        "validation": validation,
        "verify_report": verify_report,
        "human_verify": human_verify_payload,
    }


def _select_and_validate(
    *,
    candidate_items: Sequence[Dict[str, Any]],
    unresolved_candidate_items: Sequence[Dict[str, Any]],
    expected_release_counts: Optional[Dict[str, int]],
    release_quota_key: Optional[Callable[[Dict[str, Any]], str]],
    duplicate_threshold: float,
    duplicate_group_key: Optional[Callable[[Dict[str, Any]], str]] = None,
    validate_dataset: Callable[[Sequence[Dict[str, Any]]], Dict[str, Any]],
    coverage_key: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
    expected_coverage_keys: Optional[Sequence[str]] = None,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
    if expected_release_counts and release_quota_key:
        selected_items, rejected_items, verify_report = select_records_for_release(
            candidates=candidate_items,
            expected_counts=expected_release_counts,
            quota_key=release_quota_key,
            duplicate_threshold=duplicate_threshold,
            duplicate_group_key=duplicate_group_key,
            coverage_key=coverage_key,
            expected_coverage_keys=expected_coverage_keys,
        )
    else:
        selected_items = [item for item in candidate_items if item["label_report"].get("pass")]
        rejected_items = [item for item in candidate_items if not item["label_report"].get("pass")]
        verify_report = {
            "ok": not rejected_items,
            "selected_total": len(selected_items),
            "rejected_total": len(rejected_items),
        }
    rejected_items = list(rejected_items) + unresolved_candidate_items
    verify_report["rejected_total"] = int(verify_report.get("rejected_total") or 0) + len(unresolved_candidate_items)
    verify_report["parse_validate_error_count"] = len(unresolved_candidate_items)

    final_records = renumber_final_records([item["canonical"] for item in selected_items])

    validation = validate_dataset(final_records)
    verify_report["canonical_validation"] = validation
    verify_report["ok"] = bool(verify_report.get("ok")) and bool(validation.get("ok"))
    return list(selected_items), list(rejected_items), verify_report, final_records, validation


def _build_refill_counts(
    missing_counts: Dict[str, Any],
    refill_buffer: float,
    *,
    refill_round: int = 1,
) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for key, value in missing_counts.items():
        try:
            missing = int(value)
        except (TypeError, ValueError):
            continue
        if missing <= 0:
            continue
        # Refill is adaptive: generate exactly the currently missing slots.
        # Later rounds only run for slots that remain unresolved.
        counts[str(key)] = missing
    return counts


def _coverage_refill_counts(
    missing_coverage_keys: Sequence[str],
    *,
    coverage_refill_key: Optional[Callable[[str], Optional[str]]],
    refill_buffer: float,
    refill_round: int = 1,
) -> Dict[str, int]:
    grouped = Counter()
    for coverage_value in missing_coverage_keys:
        refill_key = coverage_refill_key(coverage_value) if coverage_refill_key else None
        if refill_key:
            grouped[str(refill_key)] += 1
    return _build_refill_counts(
        dict(grouped),
        refill_buffer,
        refill_round=refill_round,
    )


def _coverage_missing_keys(verify_report: Dict[str, Any]) -> List[str]:
    coverage = verify_report.get("coverage") or {}
    target = coverage.get("target_condition_id") or {}
    return [str(item) for item in target.get("missing_values") or []]


def _attach_coverage_report(verify_report: Dict[str, Any], coverage_report: Dict[str, Any]) -> None:
    verify_report["coverage"] = coverage_report
    verify_report["ok"] = bool(verify_report.get("ok")) and bool(coverage_report.get("ok", True))


def _build_coverage_report(
    *,
    planned_jobs: Sequence[Any],
    selected_items: Sequence[Dict[str, Any]],
    candidate_items: Sequence[Dict[str, Any]],
    final_records: Sequence[Dict[str, Any]],
    expected_coverage_keys: Optional[Sequence[str]],
    coverage_key: Optional[Callable[[Dict[str, Any]], Optional[str]]],
) -> Dict[str, Any]:
    planned_metadata = [job.to_metadata() for job in planned_jobs]
    selected_metadata = [item.get("metadata") or {} for item in selected_items]
    dimensions = {
        "mt_pattern": _dimension_report(planned_metadata, selected_metadata, _metadata_mt_pattern),
        "role": _dimension_report(planned_metadata, selected_metadata, lambda metadata: _one(metadata.get("role"))),
        "user_context_id": _dimension_report(
            planned_metadata,
            selected_metadata,
            lambda metadata: _one(metadata.get("user_context_id")),
        ),
        "role_user_context": _dimension_report(planned_metadata, selected_metadata, _metadata_role_user_context),
        "condition_id": _dimension_report(
            planned_metadata,
            selected_metadata,
            lambda metadata: _one(metadata.get("condition_id")),
        ),
        "policy_ref": _dimension_report(planned_metadata, selected_metadata, _metadata_policy_refs),
        "rbac_violation": _dimension_report(planned_metadata, selected_metadata, _metadata_rbac_tags),
        "injection_type": _dimension_report(planned_metadata, selected_metadata, _metadata_injection_type),
    }

    if coverage_key and expected_coverage_keys:
        expected_targets = sorted({str(key) for key in expected_coverage_keys if str(key).strip()})
        selected_targets = sorted(
            {
                str(value)
                for item in selected_items
                for value in [coverage_key(item)]
                if value is not None and str(value).strip()
            }
        )
        dimensions["target_condition_id"] = _values_report(expected_targets, selected_targets)
    else:
        dimensions["target_condition_id"] = _dimension_report(
            planned_metadata,
            selected_metadata,
            _metadata_target_condition_id,
        )

    missing_dimensions = {
        name: report
        for name, report in dimensions.items()
        if report.get("missing_values")
    }
    strict = bool(coverage_key and expected_coverage_keys)
    return {
        "ok": not missing_dimensions if strict else True,
        "observed_ok": not missing_dimensions,
        "strict": strict,
        "selected_total": len(selected_items),
        "candidate_total": len(candidate_items),
        "final_total": len(final_records),
        **dimensions,
        "missing_dimensions": sorted(missing_dimensions),
    }


def _dimension_report(
    planned_metadata: Sequence[Dict[str, Any]],
    selected_metadata: Sequence[Dict[str, Any]],
    extractor: Callable[[Dict[str, Any]], Sequence[str]],
) -> Dict[str, Any]:
    expected = sorted({value for metadata in planned_metadata for value in extractor(metadata)})
    selected = sorted({value for metadata in selected_metadata for value in extractor(metadata)})
    return _values_report(expected, selected)


def _values_report(expected: Sequence[str], selected: Sequence[str]) -> Dict[str, Any]:
    expected_set = {str(value) for value in expected if str(value).strip()}
    selected_set = {str(value) for value in selected if str(value).strip()}
    missing = sorted(expected_set - selected_set)
    return {
        "expected_total": len(expected_set),
        "selected_total": len(selected_set),
        "missing_total": len(missing),
        "missing_values": missing,
        "selected_values": sorted(selected_set),
    }


def _one(value: Any) -> List[str]:
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _as_text_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    return _one(value)


def _metadata_attack_tags(metadata: Dict[str, Any]) -> Dict[str, Any]:
    tags = metadata.get("attack_tags")
    return tags if isinstance(tags, dict) else {}


def _metadata_mt_pattern(metadata: Dict[str, Any]) -> List[str]:
    tags = _metadata_attack_tags(metadata)
    return _one(tags.get("mt_pattern") or metadata.get("mt_pattern") or metadata.get("primary_type"))


def _metadata_role_user_context(metadata: Dict[str, Any]) -> List[str]:
    role = str(metadata.get("role") or "").strip()
    user_context_id = str(metadata.get("user_context_id") or "").strip()
    return [f"{role}:{user_context_id}"] if role and user_context_id else []


def _metadata_policy_refs(metadata: Dict[str, Any]) -> List[str]:
    tags = _metadata_attack_tags(metadata)
    refs = _as_text_list(tags.get("violated_policies"))
    target_condition = metadata.get("target_condition")
    if isinstance(target_condition, dict):
        refs.extend(_as_text_list(target_condition.get("policy_refs")))
    return [ref for ref in refs if not ref.upper().startswith("RB-")]


def _metadata_rbac_tags(metadata: Dict[str, Any]) -> List[str]:
    return _as_text_list(_metadata_attack_tags(metadata).get("rbac_violation"))


def _metadata_injection_type(metadata: Dict[str, Any]) -> List[str]:
    return _one(_metadata_attack_tags(metadata).get("injection_type"))


def _metadata_target_condition_id(metadata: Dict[str, Any]) -> List[str]:
    target_condition = metadata.get("target_condition")
    if isinstance(target_condition, dict):
        return _one(target_condition.get("target_condition_id"))
    return []


def _run_generation_parse_batch(
    *,
    jobs: Sequence[Any],
    output_dir: str,
    raw_filename: str,
    model: str,
    build_prompt: Callable[[Any], str],
    build_repair_prompt: Callable[[str, str, Any], str],
    parse_validate: Callable[[str, Any], Dict[str, Any]],
    canonicalize: Callable[[Dict[str, Any], Any], Dict[str, Any]],
    build_raw_record: Callable[[Dict[str, Any], str, Any], Dict[str, Any]],
    max_tokens: int,
    save_every: int,
    max_parse_retries: int,
    max_workers: int,
    request_delay: float,
    index_offset: int,
    batch_name: str,
    batch_round: int,
    build_response_schema: Optional[Callable[[Any], Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    prompts: List[str] = []
    prompt_build_records: List[Dict[str, Any]] = []
    for local_index, job in enumerate(jobs):
        global_index = index_offset + local_index
        started_at = utc_now_iso()
        start_perf = time.perf_counter()
        prompt = build_prompt(job)
        prompt_build_records.append(
            {
                "index": global_index,
                "id": job.sequence_id,
                "started_at_utc": started_at,
                "finished_at_utc": utc_now_iso(),
                "duration_seconds": time.perf_counter() - start_perf,
                "prompt_chars": len(prompt),
                "batch_name": batch_name,
                "batch_round": batch_round,
            }
        )
        prompts.append(prompt)

    prompt_records = [
        {
            **job.to_metadata(),
            "prompt": prompt,
            "prompt_build": prompt_build,
        }
        for job, prompt, prompt_build in zip(jobs, prompts, prompt_build_records)
    ]

    def save_checkpoint(results_so_far: List[Dict[str, str]]) -> None:
        if batch_name == "initial":
            checkpoint_name = f"{raw_filename}.checkpoint"
        else:
            checkpoint_name = f"{raw_filename}.{batch_name}.checkpoint"
        save_json(os.path.join(output_dir, checkpoint_name), results_so_far)

    checkpoint_path = os.path.join(
        output_dir,
        f"{raw_filename}.checkpoint"
        if batch_name == "initial"
        else f"{raw_filename}.{batch_name}.checkpoint",
    )
    model_results = generate_batch(
        prompts,
        model=model,
        max_tokens=max_tokens,
        save_callback=save_checkpoint,
        save_every=save_every,
        max_workers=max_workers,
        delay=request_delay,
        response_schemas=(
            [build_response_schema(job) for job in jobs]
            if build_response_schema is not None
            else None
        ),
        resume_results=_load_checkpoint_results(checkpoint_path),
    )

    parsed_by_index: Dict[int, Dict[str, Any]] = {}
    output_by_index: Dict[int, str] = {}
    invalid_items: List[Dict[str, Any]] = []
    usage_records: List[Dict[str, Any]] = []

    for local_index, (job, result) in enumerate(zip(jobs, model_results)):
        global_index = index_offset + local_index
        output = result.get("output", "")
        usage_records.append(
            _build_usage_record(
                phase="generation" if batch_name == "initial" else f"{batch_name}_generation",
                index=global_index,
                job=job,
                result=result,
                prompt_build=prompt_build_records[local_index],
            )
        )
        try:
            parsed_by_index[global_index] = parse_validate(output, job)
            output_by_index[global_index] = output
        except Exception as exc:  # noqa: BLE001 - aggregate all validation failures.
            invalid_items.append(
                {
                    "index": global_index,
                    "id": job.sequence_id,
                    "job": job,
                    "input": result.get("input", ""),
                    "output": output,
                    "error": str(exc),
                    "error_category": _classify_generation_error(str(exc)),
                }
            )

    repaired, repair_usage_records = _repair_invalid_outputs(
        invalid_items,
        model=model,
        build_repair_prompt=build_repair_prompt,
        parse_validate=parse_validate,
        max_parse_retries=max_parse_retries,
        save_every=save_every,
        max_tokens=max_tokens,
        max_workers=max_workers,
        request_delay=request_delay,
        phase_prefix=batch_name,
        build_response_schema=build_response_schema,
    )
    usage_records.extend(repair_usage_records)
    for index, item in repaired.items():
        parsed_by_index[index] = item["parsed"]
        output_by_index[index] = item["output"]

    unresolved_errors = [
        {
            "index": item["index"],
            "id": item["id"],
            "error": item["error"],
            "error_category": item.get("error_category") or _classify_generation_error(item["error"]),
            "input": item["input"],
            "output": item["output"],
            "metadata": item["job"].to_metadata(),
        }
        for item in invalid_items
        if item["index"] not in repaired
    ]

    raw_records: List[Dict[str, Any]] = []
    candidate_items: List[Dict[str, Any]] = []
    unresolved_by_index = {item["index"]: item for item in unresolved_errors}
    unresolved_candidate_items: List[Dict[str, Any]] = []
    for local_index, job in enumerate(jobs):
        global_index = index_offset + local_index
        if global_index in unresolved_by_index:
            item = unresolved_by_index[global_index]
            raw_error_record = {
                **job.to_metadata(),
                "turns": [],
                "raw_model_output": item.get("output", ""),
                "parse_error": item.get("error"),
            }
            raw_records.append(raw_error_record)
            unresolved_candidate_items.append(
                {
                    "index": global_index,
                    "id": job.sequence_id,
                    "metadata": job.to_metadata(),
                    "raw": raw_error_record,
                    "parsed": {},
                    "canonical": {},
                    "label_report": {
                        "pass": False,
                        "matches_slot": False,
                        "target_relevant": False,
                        "policy_aligned": False,
                        "confidence": 0.0,
                        "evidence": {},
                        "reject_reasons": [f"parse_validate_error:{item.get('error')}"],
                    },
                    "release_reject_reason": "parse_validate_error",
                    "error_category": item.get("error_category"),
                }
            )
            continue
        parsed = parsed_by_index[global_index]
        output = output_by_index[global_index]
        raw_record = build_raw_record(parsed, output, job)
        canonical_record = canonicalize(parsed, job)
        raw_records.append(raw_record)
        candidate_items.append(
            {
                "index": global_index,
                "id": job.sequence_id,
                "metadata": job.to_metadata(),
                "raw": raw_record,
                "parsed": parsed,
                "canonical": canonical_record,
            }
        )

    return {
        "prompt_records": prompt_records,
        "raw_records": raw_records,
        "candidate_items": candidate_items,
        "unresolved_candidate_items": unresolved_candidate_items,
        "unresolved_errors": unresolved_errors,
        "usage_records": usage_records,
        "invalid_count": len(invalid_items),
        "initial_error_categories": dict(
            Counter(
                item.get("error_category") or _classify_generation_error(item.get("error", ""))
                for item in invalid_items
            )
        ),
    }


def _repair_invalid_outputs(
    invalid_items: List[Dict[str, Any]],
    *,
    model: str,
    build_repair_prompt: Callable[[str, str, Any], str],
    parse_validate: Callable[[str, Any], Dict[str, Any]],
    max_parse_retries: int,
    save_every: int,
    max_tokens: int,
    max_workers: int,
    request_delay: float,
    phase_prefix: str = "initial",
    build_response_schema: Optional[Callable[[Any], Dict[str, Any]]] = None,
) -> tuple[Dict[int, Dict[str, Any]], List[Dict[str, Any]]]:
    repaired: Dict[int, Dict[str, Any]] = {}
    repair_usage_records: List[Dict[str, Any]] = []
    remaining = invalid_items

    for repair_round in range(1, max_parse_retries + 1):
        if not remaining:
            break
        repair_prompts = []
        repair_prompt_build_records = []
        for item in remaining:
            started_at = utc_now_iso()
            start_perf = time.perf_counter()
            prompt = build_repair_prompt(item["output"], item["error"], item["job"])
            repair_prompt_build_records.append(
                {
                    "index": item["index"],
                    "id": item["id"],
                    "started_at_utc": started_at,
                    "finished_at_utc": utc_now_iso(),
                    "duration_seconds": time.perf_counter() - start_perf,
                    "prompt_chars": len(prompt),
                    "repair_round": repair_round,
                }
            )
            repair_prompts.append(prompt)
        repair_results = generate_batch(
            repair_prompts,
            model=model,
            max_tokens=max_tokens,
            save_every=save_every,
            max_workers=max_workers,
            delay=request_delay,
            response_schemas=(
                [build_response_schema(item["job"]) for item in remaining]
                if build_response_schema is not None
                else None
            ),
        )

        next_remaining: List[Dict[str, Any]] = []
        for item, repair_result, prompt_build in zip(remaining, repair_results, repair_prompt_build_records):
            repaired_output = repair_result.get("output", "")
            repair_usage_records.append(
                _build_usage_record(
                    phase=(
                        f"repair_round_{repair_round}"
                        if phase_prefix == "initial"
                        else f"{phase_prefix}_repair_round_{repair_round}"
                    ),
                    index=item["index"],
                    job=item["job"],
                    result=repair_result,
                    prompt_build=prompt_build,
                )
            )
            try:
                parsed = parse_validate(repaired_output, item["job"])
                repaired[item["index"]] = {
                    "parsed": parsed,
                    "output": repaired_output,
                    "input": repair_result.get("input", ""),
                }
            except Exception as exc:  # noqa: BLE001
                next_remaining.append(
                    {
                        **item,
                        "output": repaired_output,
                        "error": str(exc),
                        "error_category": _classify_generation_error(str(exc)),
                    }
                )
        remaining = next_remaining

    return repaired, repair_usage_records


def _classify_generation_error(error: str) -> str:
    text = str(error).lower()
    if any(
        token in text
        for token in (
            "missing root",
            "unexpected root",
            "turn_contents",
            "must be an array",
            "expected ",
            "missing keys",
            "unexpected keys",
            "json",
        )
    ):
        return "STRUCTURE"
    if any(
        token in text
        for token in (
            "sql_gt",
            "denied table",
            "denied column",
            "authenticated-user binding",
            "unknown table",
            "internal table",
        )
    ):
        return "SQL_POLICY"
    if any(token in text for token in ("target", "subject", "scope", "primary violation")):
        return "TARGET"
    if "duplicate" in text:
        return "DUPLICATE"
    if any(token in text for token in ("api", "quota", "timeout", "empty string")):
        return "API"
    return "STRUCTURE"


def _run_label_phase(
    candidate_items: List[Dict[str, Any]],
    *,
    model: str,
    build_label_prompt: Callable[[Dict[str, Any], Dict[str, Any], Any], str],
    label_threshold: float,
    max_tokens: int,
    save_every: int,
    max_workers: int,
    request_delay: float,
    output_dir: str,
    checkpoint_name: str,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    label_prompts: List[str] = []
    label_prompt_build_records: List[Dict[str, Any]] = []
    for item in candidate_items:
        job = _metadata_job(item)
        started_at = utc_now_iso()
        start_perf = time.perf_counter()
        prompt = build_label_prompt(item["canonical"], item["parsed"], job)
        label_prompt_build_records.append(
            {
                "index": item["index"],
                "id": item["id"],
                "started_at_utc": started_at,
                "finished_at_utc": utc_now_iso(),
                "duration_seconds": time.perf_counter() - start_perf,
                "prompt_chars": len(prompt),
            }
        )
        label_prompts.append(prompt)

    checkpoint_path = os.path.join(output_dir, checkpoint_name)

    def save_label_checkpoint(results_so_far: List[Dict[str, Any]]) -> None:
        save_json(checkpoint_path, results_so_far)

    label_results = generate_batch(
        label_prompts,
        model=model,
        max_tokens=min(max_tokens, 1536),
        save_every=save_every,
        max_workers=max_workers,
        delay=request_delay,
        save_callback=save_label_checkpoint,
        resume_results=_load_checkpoint_results(checkpoint_path),
        response_schemas=[_label_response_schema()] * len(label_prompts),
    )

    label_reports: List[Dict[str, Any]] = []
    usage_records: List[Dict[str, Any]] = []
    for item, result, prompt_build in zip(candidate_items, label_results, label_prompt_build_records):
        usage_records.append(
            _build_usage_record(
                phase="label",
                index=item["index"],
                job=_metadata_job(item),
                result=result,
                prompt_build=prompt_build,
            )
        )
        try:
            report = parse_label_report(result.get("output", ""), threshold=label_threshold)
        except Exception as exc:  # noqa: BLE001 - label parse failure is a reject, not a generation crash.
            report = {
                "pass": False,
                "matches_slot": False,
                "target_relevant": False,
                "policy_aligned": False,
                "confidence": 0.0,
                "evidence": {},
                "reject_reasons": [f"label_parse_error:{exc}"],
                "raw_label_output": result.get("output", ""),
            }
        label_reports.append(report)
    return label_reports, usage_records


def _load_checkpoint_results(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _record_has_control_characters(value: Any) -> bool:
    if isinstance(value, str):
        return any(ord(char) < 32 and char not in "\t\n\r" for char in value)
    if isinstance(value, dict):
        return any(_record_has_control_characters(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_record_has_control_characters(item) for item in value)
    return False


def _label_response_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "matches_slot",
            "target_relevant",
            "policy_aligned",
            "confidence",
            "evidence",
            "reject_reasons",
        ],
        "properties": {
            "matches_slot": {"type": "boolean"},
            "target_relevant": {"type": "boolean"},
            "policy_aligned": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "slot_alignment",
                    "target_alignment",
                    "policy_alignment",
                    "graph_alignment",
                ],
                "properties": {
                    "slot_alignment": {"type": "string"},
                    "target_alignment": {"type": "string"},
                    "policy_alignment": {"type": "string"},
                    "graph_alignment": {"type": "string"},
                },
            },
            "reject_reasons": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
    }


def _metadata_job(item: Dict[str, Any]) -> Any:
    class _JobProxy:
        def __init__(self, metadata: Dict[str, Any], sequence_id: str):
            self._metadata = metadata
            self.sequence_id = sequence_id
            for key, value in metadata.items():
                setattr(self, key, value)

        def to_metadata(self) -> Dict[str, Any]:
            return self._metadata

    return _JobProxy(item["metadata"], item["id"])


def _label_report_payload(candidate_items: Sequence[Dict[str, Any]], label_threshold: float) -> Dict[str, Any]:
    reports = []
    for item in candidate_items:
        reports.append(
            {
                "id": item["id"],
                "metadata": item["metadata"],
                "label_report": item.get("label_report"),
            }
        )
    return {
        "label_threshold": label_threshold,
        "total": len(reports),
        "pass_count": sum(1 for item in reports if (item.get("label_report") or {}).get("pass")),
        "reject_count": sum(1 for item in reports if not (item.get("label_report") or {}).get("pass")),
        "records": reports,
    }


def _rejected_payload(rejected_items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "total": len(rejected_items),
        "records": [
            {
                "id": item["id"],
                "metadata": item["metadata"],
                "release_reject_reason": item.get("release_reject_reason"),
                "duplicate_similarity": item.get("duplicate_similarity"),
                "label_report": item.get("label_report"),
                "raw": item.get("raw"),
                "canonical": item.get("canonical"),
            }
            for item in rejected_items
        ],
    }


def _build_usage_record(
    *,
    phase: str,
    index: int,
    job: Any,
    result: Dict[str, Any],
    prompt_build: Dict[str, Any],
) -> Dict[str, Any]:
    prompt = result.get("input", "")
    output = result.get("output", "")
    return {
        "phase": phase,
        "index": index,
        "id": job.sequence_id,
        "model": result.get("model"),
        "metadata": job.to_metadata(),
        "prompt_chars": len(prompt),
        "output_chars": len(output),
        "prompt_build": prompt_build,
        "timing": result.get("timing", {}),
        "usage": result.get("usage", {}),
        "cost": result.get("cost", {}),
        "attempts": result.get("attempts", []),
    }


def _usage_json_filename(raw_filename: str) -> str:
    stem, _ = os.path.splitext(raw_filename)
    return f"{stem.replace('_Raw', '')}_Usage.json"


def _usage_csv_filename(raw_filename: str) -> str:
    stem, _ = os.path.splitext(raw_filename)
    return f"{stem.replace('_Raw', '')}_Usage.csv"
