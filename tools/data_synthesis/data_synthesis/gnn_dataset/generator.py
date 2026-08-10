"""Policy-grounded conversation and graph dataset generator."""

from __future__ import annotations

import json
import os
import hashlib
from collections import Counter
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence

from data_synthesis.common.generation_runner import GenerationValidationError, run_generation_jobs
from data_synthesis.common.io import save_json
from data_synthesis.common.quota import apply_overgenerate_buffer
from data_synthesis.common.user_context import UserContextIndex

from .canonicalize import (
    build_raw_audit_record,
    canonicalize_sequence,
    export_summary_excel,
    parse_and_validate,
    validate_canonical_dataset,
)
from .graph_export import export_graph_artifacts
from .pattern_loader import (
    load_pattern_bank,
    validate_pattern_policy_compatibility,
)
from .pattern_slots import build_generation_plan, scaled_pattern_counts, summarize_jobs
from .policy_compiler import compile_policy_bundle
from .protocols import (
    ProtocolPlanner,
    build_refill_protocol_assignments,
    released_protocol_status,
    validate_released_assignments,
)
from .prompts import (
    build_gnn_label_prompt,
    build_gnn_prompt,
    build_gnn_response_schema,
    build_repair_prompt,
)
from .release_audit import (
    build_release_coverage,
    export_stratified_human_review,
    write_release_manifest,
)
from .scientific_release import (
    create_human_external_package,
    export_metric_protocol,
    load_human_external_rows,
    validate_human_external_import,
)
from .shortcut_baseline import build_shortcut_baseline_report
from .sql_compiler import compile_job_sql_contracts


FAMILY = "GNN"

PATTERN_VALIDATION_FILENAME = "GNN_PatternBank_Validation.json"
POLICY_VALIDATION_FILENAME = "GNN_PolicyCompiler_Validation.json"
SLOTS_FILENAME = "GNN_Slots.json"
PROMPTS_FILENAME = "GNN_Prompts.json"
RAW_FILENAME = "GNN_Raw.json"
ERRORS_FILENAME = "GNN_Errors.json"
LABEL_REPORT_FILENAME = "GNN_Label_Report.json"
REJECTED_FILENAME = "GNN_Rejected.json"
VERIFY_REPORT_FILENAME = "GNN_Verify_Report.json"
COVERAGE_REPORT_FILENAME = "GNN_Coverage_Report.json"
HUMAN_VERIFY_CSV_FILENAME = "GNN_Human_Verify.csv"
HUMAN_VERIFY_EXCEL_FILENAME = "GNN_Human_Verify.xlsx"
VALIDATION_FILENAME = "GNN_Validation.json"
FINAL_FILENAME = "GNN_Final.json"
SUMMARY_EXCEL_FILENAME = "GNN_Summary.xlsx"
GRAPHS_JSONL_FILENAME = "GNN_Graphs.jsonl"
TARGETS_JSONL_FILENAME = "GNN_Targets.jsonl"
AUDIT_GRAPHS_JSONL_FILENAME = "GNN_Audit_Graphs.jsonl"
SHORTCUT_BASELINE_FILENAME = "GNN_Shortcut_Baseline_Report.json"
PROTOCOL_ASSIGNMENTS_FILENAME = "GNN_Protocol_Assignments.json"
PROTOCOL_STATUS_FILENAME = "GNN_Protocol_Status.json"
RELEASE_MANIFEST_FILENAME = "GNN_Release_Manifest.json"
SPLIT_REPORT_FILENAME = "GNN_Split_Report.json"
STRATIFIED_REVIEW_CSV_FILENAME = "GNN_Stratified_Human_Review.csv"
STRATIFIED_REVIEW_XLSX_FILENAME = "GNN_Stratified_Human_Review.xlsx"
PREFLIGHT_REPORT_FILENAME = "GNN_Preflight_Report.json"
VALIDATOR_VERSION = "gnn_policy_grounded_validator_v8_balanced_shortcut_gate"
PIPELINE_SOURCE_FILES = (
    "canonicalize.py",
    "deterministic_verify.py",
    "generator.py",
    "pattern_slots.py",
    "prompts.py",
    "protocols.py",
    "shortcut_baseline.py",
    "sql_compiler.py",
)


def generate_gnn_dataset(
    output_dir: str,
    *,
    pattern_bank_path: str,
    policy_dir: str,
    model: str,
    total: Optional[int] = None,
    families: Optional[Sequence[str]] = None,
    user_context_index: UserContextIndex,
    save_every: int = 50,
    max_parse_retries: int = 1,
    max_workers: int = 1,
    request_delay: float = 1.0,
    label_threshold: float = 0.75,
    overgenerate_buffer: float = 0.15,
    duplicate_threshold: float = 0.96,
    max_refill_rounds: int = 5,
    refill_buffer: float = 1.5,
    split_seed: int = 20260606,
    human_review_ratio: float = 0.2,
    external_policy_dir: Optional[str] = None,
    external_user_context_index: Optional[UserContextIndex] = None,
    schema_holdout_total: Optional[int] = None,
    human_external_import: Optional[str] = None,
    protocol_child: bool = False,
    preflight_report_path: Optional[str] = None,
    force_large_run: bool = False,
) -> Dict[str, Any]:
    """Generate the GNN corpus from validated pattern and policy contracts.

    The workflow compiles policy targets, plans protocol-aware slots, verifies
    conversations, and exports graph, target, audit, and manifest artifacts.
    Large runs require an explicit preflight decision.
    """

    if total is not None and total >= 5000 and not protocol_child and not force_large_run:
        _require_large_run_preflight(preflight_report_path)
    policy_bundle = compile_policy_bundle(policy_dir)
    pattern_bank = load_pattern_bank(pattern_bank_path, families=families)
    compatibility = validate_pattern_policy_compatibility(
        pattern_bank,
        policy_bundle,
        available_roles=sorted(user_context_index.by_role),
    )
    if not compatibility["ok"]:
        raise ValueError(
            "Pattern/policy compatibility failed: "
            + "; ".join(compatibility["errors"][:20])
        )
    pattern_validation_path = os.path.join(output_dir, PATTERN_VALIDATION_FILENAME)
    save_json(
        pattern_validation_path,
        {
            "source_path": pattern_bank.source_path,
            "schema_version": pattern_bank.schema_version,
            "structural_validation": pattern_bank.validation,
            "policy_grounding_validation": compatibility,
        },
    )
    policy_validation_path = os.path.join(output_dir, POLICY_VALIDATION_FILENAME)
    save_json(
        policy_validation_path,
        {
            "source_dir": policy_bundle.source_dir,
            **policy_bundle.validation,
            **policy_bundle.manifest_fragment(),
        },
    )

    expected_counts = scaled_pattern_counts(pattern_bank, total)
    generation_counts = apply_overgenerate_buffer(expected_counts, overgenerate_buffer)
    expected_base_jobs = build_generation_plan(
        pattern_bank,
        policy_bundle,
        user_context_index,
        quota_counts=expected_counts,
    )
    base_jobs = _build_buffered_jobs(
        expected_base_jobs,
        expected_counts=expected_counts,
        generation_counts=generation_counts,
    )
    protocol_planner = ProtocolPlanner(expected_base_jobs, seed=split_seed)
    jobs = protocol_planner.assign_jobs(base_jobs)
    expected_jobs = protocol_planner.assign_jobs(expected_base_jobs)
    compiled_sql_contracts = {
        job.slot_id: compile_job_sql_contracts(job)
        for job in jobs
    }
    if protocol_child:
        for job in [*jobs, *expected_jobs]:
            assignments = job.protocol_assignments or {}
            assignments["schema_holdout"] = {
                "protocol": "schema_holdout",
                "status": "READY",
                "split": "test",
                "split_group_id": f"external_schema:{job.target.target_id}",
                "isolation_key": policy_bundle.source_hashes["ddl"],
                "split_reason": "independent_external_policy_bundle_test_only",
            }
    protocol_assignments_path = os.path.join(output_dir, PROTOCOL_ASSIGNMENTS_FILENAME)
    save_json(
        protocol_assignments_path,
        {
            **protocol_planner.report(),
            "jobs": [
                {
                    "slot_id": job.slot_id,
                    "pattern_id": job.pattern_id,
                    "role": job.role,
                    "policy_target_id": job.target.target_id,
                    "protocol_assignments": job.protocol_assignments,
                }
                for job in jobs
            ],
        },
    )
    expected_release_buckets = dict(Counter(_job_release_bucket(job) for job in expected_jobs))
    refill_templates: Dict[str, List[Any]] = {}
    refill_surface_templates: Dict[str, Any] = {}
    for job in expected_jobs:
        refill_templates.setdefault(_job_release_bucket(job), []).append(job)
        refill_surface_templates[_job_surface_coverage_key(job)] = job
    expected_surface_coverage = sorted(refill_surface_templates)
    slots_path = os.path.join(output_dir, SLOTS_FILENAME)
    save_json(
        slots_path,
        {
            "expected_pattern_counts": expected_counts,
            "generation_pattern_counts": generation_counts,
            "expected_release_buckets": expected_release_buckets,
            "expected_surface_coverage": expected_surface_coverage,
            "policy_compiler": policy_bundle.manifest_fragment(),
            "jobs": [
                {
                    **job.to_metadata(),
                    "compiled_sql_contracts": compiled_sql_contracts[job.slot_id],
                }
                for job in jobs
            ],
        },
    )
    print(f"  GNN pattern bank: {pattern_bank_path}")
    print(f"  GNN active patterns: {len(pattern_bank.active_patterns)}")
    print(f"  GNN release counts: {expected_counts}")
    print(f"  GNN generated counts with buffer: {summarize_jobs(jobs)}")

    def build_refill_jobs(
        refill_counts: Dict[str, int],
        sequence_start: int,
        _round: int,
        _context: Optional[Dict[str, Any]],
    ):
        refill_jobs = []
        next_sequence = sequence_start
        context = _context or {}
        if context.get("reason") == "coverage":
            for coverage_key in context.get("missing_coverage_keys") or []:
                template = refill_surface_templates.get(str(coverage_key))
                if template is None:
                    continue
                refill_jobs.append(
                    replace(
                        template,
                        sequence_number=next_sequence,
                        slot_id=f"GNN-SLOT-{next_sequence:06d}",
                        generation_attempt=(_round * 100) + 1,
                        protocol_assignments=build_refill_protocol_assignments(
                            template,
                            refill_round=_round,
                            refill_index=0,
                        ),
                    )
                )
                next_sequence += 1
            return refill_jobs
        for bucket, count in sorted(refill_counts.items()):
            templates = refill_templates.get(bucket) or []
            if not templates:
                continue
            for refill_index in range(count):
                template = templates[(refill_index + _round - 1) % len(templates)]
                refill_jobs.append(
                    replace(
                        template,
                        sequence_number=next_sequence,
                        slot_id=f"GNN-SLOT-{next_sequence:06d}",
                        generation_attempt=(_round * 100) + refill_index + 1,
                        protocol_assignments=build_refill_protocol_assignments(
                            template,
                            refill_round=_round,
                            refill_index=refill_index,
                        ),
                    )
                )
                next_sequence += 1
        return refill_jobs

    try:
        result = run_generation_jobs(
            jobs=jobs,
            output_dir=output_dir,
            model=model,
            build_prompt=build_gnn_prompt,
            build_repair_prompt=build_repair_prompt,
            parse_validate=parse_and_validate,
            canonicalize=canonicalize_sequence,
            build_raw_record=build_raw_audit_record,
            validate_dataset=lambda records: validate_canonical_dataset(
                records,
                pattern_bank=pattern_bank,
                expected_pattern_counts=expected_counts,
            ),
            export_summary=lambda records, path: export_summary_excel(
                records,
                path,
                expected_pattern_counts=expected_counts,
            ),
            raw_filename=RAW_FILENAME,
            final_filename=FINAL_FILENAME,
            prompts_filename=PROMPTS_FILENAME,
            errors_filename=ERRORS_FILENAME,
            summary_excel_filename=SUMMARY_EXCEL_FILENAME,
            validation_filename=VALIDATION_FILENAME,
            expected_release_counts=expected_release_buckets,
            release_quota_key=_record_release_bucket,
            build_label_prompt=build_gnn_label_prompt,
            dataset_family=FAMILY,
            label_report_filename=LABEL_REPORT_FILENAME,
            rejected_filename=REJECTED_FILENAME,
            verify_report_filename=VERIFY_REPORT_FILENAME,
            coverage_report_filename=COVERAGE_REPORT_FILENAME,
            human_verify_csv_filename=HUMAN_VERIFY_CSV_FILENAME,
            human_verify_excel_filename=HUMAN_VERIFY_EXCEL_FILENAME,
            label_threshold=label_threshold,
            duplicate_threshold=duplicate_threshold,
            duplicate_group_key=_record_duplicate_group,
            banner="GNN: Pattern-bank driven graph-supervised dataset generation",
            save_every=save_every,
            max_parse_retries=max_parse_retries,
            max_workers=max_workers,
            request_delay=request_delay,
            build_refill_jobs=build_refill_jobs,
            max_refill_rounds=max_refill_rounds,
            refill_buffer=refill_buffer,
            build_response_schema=build_gnn_response_schema,
            coverage_key=_item_surface_coverage_key,
            expected_coverage_keys=expected_surface_coverage,
            coverage_refill_key=lambda key: (
                _job_release_bucket(refill_surface_templates[key])
                if key in refill_surface_templates
                else None
            ),
        )
    except GenerationValidationError:
        _refresh_protocol_assignments_from_prompts(
            output_dir,
            protocol_planner.report(),
        )
        _write_preflight_report(
            output_dir,
            expected_counts=expected_counts,
            total=total,
            model=model,
            max_refill_rounds=max_refill_rounds,
        )
        raise
    _refresh_protocol_assignments_from_prompts(
        output_dir,
        protocol_planner.report(),
    )

    with open(result["final_path"], "r", encoding="utf-8") as f:
        final_records = json.load(f)
    final_records = _attach_provenance(
        final_records,
        output_dir=output_dir,
        policy_bundle=policy_bundle,
        pattern_bank_path=pattern_bank_path,
        model=model,
    )
    save_json(result["final_path"], final_records)
    protocol_validation = validate_released_assignments(final_records, protocol_planner)
    if not protocol_validation["ok"]:
        raise ValueError(
            "Released protocol assignments failed validation: "
            + "; ".join(protocol_validation["errors"][:20])
        )

    graphs_path = os.path.join(output_dir, GRAPHS_JSONL_FILENAME)
    targets_path = os.path.join(output_dir, TARGETS_JSONL_FILENAME)
    audit_graphs_path = os.path.join(output_dir, AUDIT_GRAPHS_JSONL_FILENAME)
    graph_validation = export_graph_artifacts(
        final_records,
        feature_path=graphs_path,
        targets_path=targets_path,
        audit_path=audit_graphs_path,
    )
    print(f"  [OK] Sanitized GNN Graph JSONL: {graphs_path}")
    shortcut_baseline = build_shortcut_baseline_report(
        final_records,
        protocol="balanced",
        fail_threshold=0.80,
    )
    shortcut_baseline_path = os.path.join(output_dir, SHORTCUT_BASELINE_FILENAME)
    save_json(shortcut_baseline_path, shortcut_baseline)
    if not shortcut_baseline["ok"]:
        raise ValueError(
            "Shortcut baseline gate failed: "
            f"{shortcut_baseline_path}; blocking_splits={shortcut_baseline['blocking_splits']}"
        )

    release_coverage = build_release_coverage(final_records)
    coverage_path = result["coverage_report_path"]
    with open(coverage_path, "r", encoding="utf-8") as handle:
        runner_coverage = json.load(handle)
    save_json(
        coverage_path,
        {
            "runner_coverage": runner_coverage,
            "policy_grounded_release_coverage": release_coverage,
            "eligible_policy_refs": sorted(
                ref
                for ref, policy in policy_bundle.policies.items()
                if set(policy.allowed_roles).intersection({"student", "lecturer"})
            ),
            "uncovered_policy_refs": compatibility["uncovered_policy_refs"],
        },
    )

    split_report = {
        **protocol_planner.report(),
        "release_validation": protocol_validation,
    }
    split_report_path = os.path.join(output_dir, SPLIT_REPORT_FILENAME)
    split_paths: Dict[str, str] = {}
    save_json(split_report_path, split_report)

    in_policy_status = released_protocol_status(
        final_records,
        protocol="in_policy",
        planned_status=protocol_planner.in_policy_status,
    )
    policy_holdout_status = released_protocol_status(
        final_records,
        protocol="policy_holdout",
        planned_status=protocol_planner.policy_holdout_status,
    )
    balanced_status = released_protocol_status(
        final_records,
        protocol="balanced",
        planned_status=protocol_planner.balanced_status,
    )
    in_policy_export = export_metric_protocol(
        final_records,
        protocol="in_policy",
        status=in_policy_status,
        output_dir=output_dir,
    )
    policy_holdout_export = export_metric_protocol(
        final_records,
        protocol="policy_holdout",
        status=policy_holdout_status,
        output_dir=output_dir,
    )
    balanced_export = export_metric_protocol(
        final_records,
        protocol="balanced",
        status=balanced_status,
        output_dir=output_dir,
    )

    human_status = create_human_external_package(
        output_dir=output_dir,
        policy_manifest=policy_bundle.manifest_fragment(),
        policy_source_dir=policy_bundle.source_dir,
        user_contexts=user_context_index.to_prompt_contexts(),
    )
    if human_external_import:
        human_status = validate_human_external_import(
            human_external_import,
            synthetic_records=final_records,
        )
        human_status["protocol"] = "human_external"
        human_status["import_path"] = os.path.abspath(human_external_import)
        if human_status["status"] == "READY":
            save_json(
                os.path.join(
                    output_dir,
                    "protocols",
                    "human_external",
                    "Human_External_Final.json",
                ),
                load_human_external_rows(human_external_import),
            )
        save_json(
            os.path.join(output_dir, "protocols", "human_external", "validation.json"),
            human_status,
        )

    schema_status: Dict[str, Any] = {
        "protocol": "schema_holdout",
        "status": "UNAVAILABLE",
        "reason": "external_policy_bundle_not_provided",
    }
    schema_result = None
    if external_policy_dir and not protocol_child:
        external_bundle = compile_policy_bundle(external_policy_dir)
        if external_bundle.source_hashes == policy_bundle.source_hashes:
            schema_status = {
                "protocol": "schema_holdout",
                "status": "UNAVAILABLE",
                "reason": "external_policy_bundle_hash_matches_training_bundle",
            }
        elif external_user_context_index is None:
            schema_status = {
                "protocol": "schema_holdout",
                "status": "UNAVAILABLE",
                "reason": "external_user_context_not_provided",
            }
        else:
            schema_dir = os.path.join(output_dir, "protocols", "schema_holdout")
            schema_result = generate_gnn_dataset(
                schema_dir,
                pattern_bank_path=pattern_bank_path,
                policy_dir=external_policy_dir,
                model=model,
                total=schema_holdout_total or total,
                families=families,
                user_context_index=external_user_context_index,
                save_every=save_every,
                max_parse_retries=max_parse_retries,
                max_workers=max_workers,
                request_delay=request_delay,
                label_threshold=label_threshold,
                overgenerate_buffer=overgenerate_buffer,
                duplicate_threshold=duplicate_threshold,
                max_refill_rounds=max_refill_rounds,
                refill_buffer=refill_buffer,
                split_seed=split_seed,
                human_review_ratio=human_review_ratio,
                protocol_child=True,
            )
            schema_status = {
                "protocol": "schema_holdout",
                "status": "READY",
                "reason": "independent_policy_bundle_generated_as_test_only",
                "directory": schema_dir,
                "record_count": schema_result["total_records"],
                "training_source_hashes": policy_bundle.source_hashes,
                "external_source_hashes": external_bundle.source_hashes,
            }

    protocol_status = {
        "assignment_validation": protocol_validation,
        "graph_sanitization": graph_validation,
        "in_policy": {**in_policy_status, "export": in_policy_export},
        "policy_holdout": {
            **policy_holdout_status,
            "export": policy_holdout_export,
        },
        "balanced": {
            **balanced_status,
            "export": balanced_export,
            "shortcut_baseline": shortcut_baseline,
        },
        "schema_holdout": schema_status,
        "human_external": human_status,
    }
    protocol_status_path = os.path.join(output_dir, PROTOCOL_STATUS_FILENAME)
    save_json(protocol_status_path, protocol_status)

    review_payload = export_stratified_human_review(
        final_records,
        csv_path=os.path.join(output_dir, STRATIFIED_REVIEW_CSV_FILENAME),
        xlsx_path=os.path.join(output_dir, STRATIFIED_REVIEW_XLSX_FILENAME),
        ratio=human_review_ratio,
    )
    manifest_path = os.path.join(output_dir, RELEASE_MANIFEST_FILENAME)
    manifest = write_release_manifest(
        manifest_path,
        policy_bundle=policy_bundle,
        pattern_bank_path=pattern_bank_path,
        prompt_path=os.path.join(output_dir, PROMPTS_FILENAME),
        model=model,
        seed=split_seed,
        final_records=final_records,
        split_report=split_report,
        protocol_status=protocol_status,
    )

    result["pattern_bank_validation_path"] = pattern_validation_path
    result["policy_compiler_validation_path"] = policy_validation_path
    result["slots_path"] = slots_path
    result["graphs_jsonl_path"] = graphs_path
    result["targets_jsonl_path"] = targets_path
    result["audit_graphs_jsonl_path"] = audit_graphs_path
    result["shortcut_baseline_path"] = shortcut_baseline_path
    result["protocol_assignments_path"] = protocol_assignments_path
    result["protocol_status_path"] = protocol_status_path
    result["release_manifest_path"] = manifest_path
    result["split_report_path"] = split_report_path
    result["split_paths"] = split_paths
    result["stratified_human_review"] = review_payload
    result["release_manifest"] = manifest
    result["protocol_status"] = protocol_status
    result["shortcut_baseline"] = shortcut_baseline
    result["schema_holdout_result"] = schema_result
    result["pattern_counts"] = result["validation"].get("pattern_counts", {})
    result["preflight_report"] = _write_preflight_report(
        output_dir,
        expected_counts=expected_counts,
        total=total,
        model=model,
        max_refill_rounds=max_refill_rounds,
    )
    return result


def _require_large_run_preflight(path: Optional[str]) -> None:
    if not path or not os.path.exists(path):
        raise ValueError(
            "GNN total >= 5000 requires --gnn-preflight-report from a passing 300-record pilot, "
            "or --gnn-force-large-run."
        )
    with open(path, "r", encoding="utf-8") as handle:
        report = json.load(handle)
    if not report.get("ALLOW_5000"):
        raise ValueError(f"Preflight report does not allow a 5000-record run: {path}")
    if report.get("validator_version") != VALIDATOR_VERSION:
        raise ValueError(
            "Preflight report is stale: validator version does not match current pipeline."
        )
    if report.get("pipeline_source_hashes") != _pipeline_source_hashes():
        raise ValueError(
            "Preflight report is stale: generation/compiler/validator source changed. "
            "Run a new 300-record pilot."
        )


def _write_preflight_report(
    output_dir: str,
    *,
    expected_counts: Dict[str, int],
    total: Optional[int],
    model: str,
    max_refill_rounds: int,
) -> Dict[str, Any]:
    def load(name: str, default: Any) -> Any:
        path = os.path.join(output_dir, name)
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    verify = load(VERIFY_REPORT_FILENAME, {})
    errors = load(ERRORS_FILENAME, [])
    usage = load("GNN_Usage.json", {})
    rejected = load(REJECTED_FILENAME, {})
    labels = load(LABEL_REPORT_FILENAME, {})
    prompts = load(PROMPTS_FILENAME, [])
    error_categories = Counter(
        str(item.get("error_category") or "UNKNOWN")
        for item in errors
    )
    phase_summary = (usage.get("summary") or {}).get("by_phase") or {}
    initial_requests = int((phase_summary.get("generation") or {}).get("request_count") or 0)
    refill_requests = sum(
        int(values.get("request_count") or 0)
        for phase, values in phase_summary.items()
        if phase.startswith("refill_") and phase.endswith("_generation")
    )
    selected_total = int(verify.get("selected_total") or 0)
    expected_total = sum(expected_counts.values())
    initial_error_categories = verify.get("initial_error_categories") or {}
    structural_errors = int(initial_error_categories.get("STRUCTURE") or 0)
    initial_structural_pass_rate = (
        max(0.0, (initial_requests - structural_errors) / initial_requests)
        if initial_requests
        else 0.0
    )
    rejected_total = int((rejected or {}).get("total") or verify.get("rejected_total") or 0)
    duplicate_reject_count = sum(
        1
        for item in (rejected.get("records") or [])
        if str(item.get("release_reject_reason") or "").startswith("duplicate_")
    )
    initial_label_records = [
        item
        for item in labels.get("records") or []
        if int((item.get("metadata") or {}).get("generation_attempt") or 0) < 100
    ]
    label_total = len(initial_label_records)
    label_pass_count = sum(
        1
        for item in initial_label_records
        if (item.get("label_report") or {}).get("pass")
    )
    final_pass_rate = label_pass_count / max(label_total, 1)
    refill_ratio = refill_requests / max(initial_requests, 1)
    duplicate_rate = duplicate_reject_count / max(label_total, 1)
    initial_sql_policy_errors = int(initial_error_categories.get("SQL_POLICY") or 0)
    deterministic_sql_policy_pass_rate = (
        max(0.0, (initial_requests - initial_sql_policy_errors) / initial_requests)
        if initial_requests
        else 0.0
    )
    coverage = verify.get("coverage") or {}
    surface_coverage = coverage.get("target_condition_id") or {}
    surface_coverage_ok = not bool(surface_coverage.get("missing_values"))
    canonical_validation = verify.get("canonical_validation") or {}
    pattern_counts = (
        verify.get("selected_counts")
        or canonical_validation.get("pattern_counts")
        or {}
    )
    pattern_quota_ok = all(
        int(pattern_counts.get(pattern_id) or 0) == int(count)
        for pattern_id, count in expected_counts.items()
    )
    missing_counts = verify.get("missing_counts") or {}
    blocking_release_error_count = 0 if bool(verify.get("ok")) and not missing_counts else sum(error_categories.values())
    release_structural_pass_ok = bool(verify.get("ok")) and blocking_release_error_count == 0
    duplicate_audit = verify.get("duplicate_audit") or {}
    final_exact_duplicate_count = 0
    final_near_duplicate_rate = 0.0
    final_path = os.path.join(output_dir, FINAL_FILENAME)
    final_records = load(FINAL_FILENAME, [])
    if isinstance(final_records, list) and final_records:
        duplicate_metrics = _final_duplicate_metrics(final_records)
        final_exact_duplicate_count = duplicate_metrics["exact_duplicate_count"]
        final_near_duplicate_rate = duplicate_metrics["near_duplicate_rate"]
    protocol_status = load(PROTOCOL_STATUS_FILENAME, {})
    shortcut_baseline = load(SHORTCUT_BASELINE_FILENAME, {})
    graph_sanitization = protocol_status.get("graph_sanitization") or {}
    graph_leakage_ok = bool(graph_sanitization.get("ok")) if protocol_status else False
    graph_target_sync_ok = _graph_target_ids_match(output_dir)
    is_20_preflight = expected_total == 20 and len([v for v in expected_counts.values() if v]) == 20
    is_300_pilot = expected_total == 300
    allow_5000 = bool(
        is_300_pilot
        and verify.get("ok")
        and selected_total == 300
        and pattern_quota_ok
        and surface_coverage_ok
        and release_structural_pass_ok
        and deterministic_sql_policy_pass_rate == 1.0
        and final_pass_rate >= 0.70
        and refill_ratio <= 0.25
        and final_exact_duplicate_count == 0
        and final_near_duplicate_rate < 0.05
        and graph_leakage_ok
        and graph_target_sync_ok
        and bool(shortcut_baseline.get("ok", False))
        and blocking_release_error_count == 0
    )
    report = {
        "model": model,
        "requested_total": total,
        "expected_total": expected_total,
        "selected_total": selected_total,
        "all_patterns_covered": all(
            int((verify.get("selected_counts") or {}).get(key, 0)) >= count
            for key, count in (verify.get("expected_counts") or {}).items()
        ),
        "release_gate_ok": bool(verify.get("ok")),
        "initial_request_count": initial_requests,
        "refill_generation_request_count": refill_requests,
        "initial_structural_pass_rate": initial_structural_pass_rate,
        "release_structural_pass_ok": release_structural_pass_ok,
        "deterministic_sql_policy_pass_rate": deterministic_sql_policy_pass_rate,
        "initial_label_pass_rate": final_pass_rate,
        "refill_request_ratio": refill_ratio,
        "duplicate_reject_count": duplicate_reject_count,
        "initial_duplicate_reject_rate": duplicate_rate,
        "duplicate_audit": duplicate_audit,
        "final_exact_duplicate_count": final_exact_duplicate_count,
        "final_near_duplicate_rate": final_near_duplicate_rate,
        "surface_coverage_ok": surface_coverage_ok,
        "pattern_quota_ok": pattern_quota_ok,
        "graph_leakage_ok": graph_leakage_ok,
        "graph_target_sync_ok": graph_target_sync_ok,
        "shortcut_baseline_ok": shortcut_baseline.get("ok"),
        "shortcut_baseline": shortcut_baseline,
        "initial_error_count": sum(error_categories.values()),
        "blocking_release_error_count": blocking_release_error_count,
        "unresolved_release_error_count": blocking_release_error_count,
        "error_categories": dict(error_categories),
        "initial_error_categories": initial_error_categories,
        "missing_counts": missing_counts,
        "max_refill_rounds": max_refill_rounds,
        "prompt_count": len(prompts),
        "usage_summary": usage.get("summary") or {},
        "source_hashes": load(RELEASE_MANIFEST_FILENAME, {}).get("source_hashes_sha256"),
        "prompt_hash": _sha256(os.path.join(output_dir, PROMPTS_FILENAME))
        if os.path.exists(os.path.join(output_dir, PROMPTS_FILENAME))
        else None,
        "validator_version": VALIDATOR_VERSION,
        "pipeline_source_hashes": _pipeline_source_hashes(),
        "stage": "PRECHECK_20" if is_20_preflight else ("PILOT_300" if is_300_pilot else "OTHER"),
        "ALLOW_5000": allow_5000,
    }
    save_json(os.path.join(output_dir, PREFLIGHT_REPORT_FILENAME), report)
    return report


def _refresh_protocol_assignments_from_prompts(
    output_dir: str,
    planner_report: Dict[str, Any],
) -> None:
    prompts_path = os.path.join(output_dir, PROMPTS_FILENAME)
    if not os.path.exists(prompts_path):
        return
    with open(prompts_path, "r", encoding="utf-8") as handle:
        prompts = json.load(handle)
    jobs = [
        {
            "slot_id": item.get("slot_id"),
            "pattern_id": item.get("pattern_id"),
            "role": item.get("role"),
            "policy_target_id": (item.get("compiled_target") or {}).get("target_id"),
            "protocol_assignments": item.get("protocol_assignments") or {},
            "generation_batch": (item.get("prompt_build") or {}).get("batch_name"),
            "generation_round": (item.get("prompt_build") or {}).get("batch_round"),
        }
        for item in prompts
    ]
    save_json(
        os.path.join(output_dir, PROTOCOL_ASSIGNMENTS_FILENAME),
        {
            **planner_report,
            "assignment_count": len(jobs),
            "jobs": jobs,
        },
    )


def _build_buffered_jobs(
    expected_jobs: Sequence[Any],
    *,
    expected_counts: Dict[str, int],
    generation_counts: Dict[str, int],
) -> list[Any]:
    jobs = list(expected_jobs)
    by_pattern: Dict[str, list[Any]] = {}
    for job in expected_jobs:
        by_pattern.setdefault(job.pattern_id, []).append(job)
    next_sequence = len(jobs) + 1
    for pattern_id, generated_count in generation_counts.items():
        extra = max(0, int(generated_count) - int(expected_counts.get(pattern_id, 0)))
        templates = by_pattern.get(pattern_id) or []
        for extra_index in range(extra):
            template = templates[extra_index % len(templates)]
            jobs.append(
                replace(
                    template,
                    sequence_number=next_sequence,
                    slot_id=f"GNN-SLOT-{next_sequence:06d}",
                    generation_attempt=extra_index + 1,
                )
            )
            next_sequence += 1
    return jobs


def _attach_provenance(
    records: Sequence[Dict[str, Any]],
    *,
    output_dir: str,
    policy_bundle: Any,
    pattern_bank_path: str,
    model: str,
) -> list[Dict[str, Any]]:
    prompts_path = os.path.join(output_dir, PROMPTS_FILENAME)
    prompts = []
    if os.path.exists(prompts_path):
        with open(prompts_path, "r", encoding="utf-8") as handle:
            prompts = json.load(handle)
    prompt_by_slot = {
        str(item.get("slot_id")): item
        for item in prompts
        if item.get("slot_id")
    }
    enriched = []
    for record in records:
        item = json.loads(json.dumps(record, ensure_ascii=False))
        prompt_item = prompt_by_slot.get(str(item.get("slot_id"))) or {}
        prompt = str(prompt_item.get("prompt") or "")
        prompt_build = prompt_item.get("prompt_build") or {}
        item["provenance"] = {
            "policy_source_hashes_sha256": policy_bundle.source_hashes,
            "pattern_bank_sha256": _sha256(pattern_bank_path),
            "generator_model": model,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else None,
            "validator_version": VALIDATOR_VERSION,
            "protocol_assignment_version": "gnn_protocols_v2",
            "generation_batch": prompt_build.get("batch_name"),
            "generation_round": prompt_build.get("batch_round"),
            "human_review_status": "NOT_REVIEWED",
        }
        enriched.append(item)
    return enriched


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pipeline_source_hashes() -> Dict[str, str]:
    module_dir = os.path.dirname(os.path.abspath(__file__))
    hashes = {
        name: _sha256(os.path.join(module_dir, name))
        for name in PIPELINE_SOURCE_FILES
    }
    common_dir = os.path.abspath(os.path.join(module_dir, "..", "common"))
    synthesis_dir = os.path.abspath(os.path.join(module_dir, ".."))
    hashes["common/generation_runner.py"] = _sha256(
        os.path.join(common_dir, "generation_runner.py")
    )
    hashes["common/labeling.py"] = _sha256(os.path.join(common_dir, "labeling.py"))
    hashes["common/user_context.py"] = _sha256(os.path.join(common_dir, "user_context.py"))
    hashes["gemini_client.py"] = _sha256(os.path.join(synthesis_dir, "gemini_client.py"))
    return hashes


def _job_release_bucket(job: Any) -> str:
    assignments = job.protocol_assignments or {}
    return "|".join(
        [
            job.pattern_id,
            job.target.target_id,
            str((assignments.get("in_policy") or {}).get("split") or "NA"),
            str((assignments.get("policy_holdout") or {}).get("split") or "NA"),
        ]
    )


def _record_release_bucket(record: Dict[str, Any]) -> str:
    assignments = record.get("protocol_assignments") or {}
    boundary = record.get("security_boundary") or {}
    return "|".join(
        [
            str(record.get("pattern_id")),
            str(boundary.get("target_id")),
            str((assignments.get("in_policy") or {}).get("split") or "NA"),
            str((assignments.get("policy_holdout") or {}).get("split") or "NA"),
        ]
    )


def _job_surface_coverage_key(job: Any) -> str:
    assignments = job.protocol_assignments or {}
    return f"{job.pattern_id}|surface={assignments.get('surface_variant_id') or ''}"


def _record_surface_coverage_key(record: Dict[str, Any]) -> str:
    assignments = record.get("protocol_assignments") or {}
    return (
        f"{record.get('pattern_id')}|surface="
        f"{assignments.get('surface_variant_id') or ''}"
    )


def _item_surface_coverage_key(item: Dict[str, Any]) -> Optional[str]:
    record = item.get("canonical") or {}
    return _record_surface_coverage_key(record) if record else None


def _surface_coverage_release_bucket(coverage_key: str) -> Optional[str]:
    marker = "|surface="
    return coverage_key.split(marker, 1)[0] if marker in coverage_key else None


def _record_duplicate_group(record: Dict[str, Any]) -> str:
    boundary = record.get("security_boundary") or {}
    return "|".join(
        [
            str(record.get("pattern_id") or ""),
            str(record.get("role") or ""),
            str(boundary.get("target_id") or ""),
        ]
    )


def _final_duplicate_metrics(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    exact_seen = set()
    exact_duplicates = 0
    grouped: Dict[str, List[str]] = {}
    near_pairs = 0
    comparisons = 0
    for record in records:
        exact_text = _normalized_record_text(record, include_sql=True)
        if exact_text in exact_seen:
            exact_duplicates += 1
        exact_seen.add(exact_text)
        normalized = _normalized_record_text(record, include_sql=False)
        group = _record_duplicate_group(record)
        for existing in grouped.get(group, []):
            comparisons += 1
            max_len = max(len(normalized), len(existing), 1)
            min_len = min(len(normalized), len(existing))
            if min_len / max_len < 0.96:
                continue
            if SequenceMatcher(None, normalized, existing).ratio() >= 0.96:
                near_pairs += 1
        grouped.setdefault(group, []).append(normalized)
    return {
        "exact_duplicate_count": exact_duplicates,
        "near_duplicate_pair_count": near_pairs,
        "near_duplicate_comparisons": comparisons,
        "near_duplicate_rate": near_pairs / max(comparisons, 1),
    }


def _normalized_record_text(
    record: Dict[str, Any],
    *,
    include_sql: bool,
) -> str:
    parts = [
        str(record.get("turn_type") or ""),
        str(record.get("primary_type") or ""),
        str(record.get("role") or ""),
    ]
    for turn in record.get("turns") or []:
        parts.extend(
            [
                str(turn.get("turn_label") or ""),
                str(turn.get("nlq") or ""),
            ]
        )
        if include_sql:
            parts.append(str(turn.get("sql_gt") or ""))
    return " ".join(parts).lower().strip()


def _graph_target_ids_match(output_dir: str) -> bool:
    paths = [
        os.path.join(output_dir, GRAPHS_JSONL_FILENAME),
        os.path.join(output_dir, TARGETS_JSONL_FILENAME),
        os.path.join(output_dir, AUDIT_GRAPHS_JSONL_FILENAME),
    ]
    if not all(os.path.exists(path) for path in paths):
        return False

    def ids(path: str) -> set[str]:
        values = set()
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                item = json.loads(line)
                graph_id = item.get("graph_id")
                if graph_id:
                    values.add(str(graph_id))
        return values

    feature_ids, target_ids, audit_ids = [ids(path) for path in paths]
    return bool(feature_ids) and feature_ids == target_ids == audit_ids
