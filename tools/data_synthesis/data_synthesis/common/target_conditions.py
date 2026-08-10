from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from typing import Any, Dict, Iterable, List, Sequence

from data_synthesis.gemini_client import generate_batch

from .io import parse_model_json, save_json
from .pipeline_contract import artifact_names
from .usage import DEFAULT_PRICING, utc_now_iso, write_usage_logs


def attach_target_conditions(
    *,
    jobs: Sequence[Any],
    output_dir: str,
    dataset_family: str,
    model: str,
    max_workers: int = 1,
    request_delay: float = 1.0,
    save_every: int = 50,
    max_tokens: int = 4096,
) -> List[Any]:
    """Generate target-condition records and attach them to immutable job objects."""
    os.makedirs(output_dir, exist_ok=True)
    artifacts = artifact_names(dataset_family)
    unique_jobs = _unique_jobs_by_target_signature(jobs, dataset_family)
    prompts: List[str] = []
    prompt_build_records: List[Dict[str, Any]] = []
    for index, job in enumerate(unique_jobs):
        started_at = utc_now_iso()
        start_perf = time.perf_counter()
        prompt = build_target_condition_prompt(job, dataset_family)
        prompt_build_records.append(
            {
                "index": index,
                "id": _target_condition_id(dataset_family, index + 1),
                "job_id": job.sequence_id,
                "started_at_utc": started_at,
                "finished_at_utc": utc_now_iso(),
                "duration_seconds": time.perf_counter() - start_perf,
                "prompt_chars": len(prompt),
            }
        )
        prompts.append(prompt)

    prompts_path = os.path.join(output_dir, artifacts.target_condition_prompts)
    save_json(
        prompts_path,
        [
            {
                "target_condition_id": prompt_build["id"],
                "source_job": job.to_metadata(),
                "prompt_build": prompt_build,
                "prompt": prompt,
            }
            for job, prompt, prompt_build in zip(unique_jobs, prompts, prompt_build_records)
        ],
    )

    print(f"  Target-condition prompts: {len(prompts)}")
    results = generate_batch(
        prompts,
        model=model,
        max_tokens=max_tokens,
        save_every=save_every,
        max_workers=max_workers,
        delay=request_delay,
    )

    condition_by_signature: Dict[str, Dict[str, Any]] = {}
    conditions: List[Dict[str, Any]] = []
    usage_records: List[Dict[str, Any]] = []
    for index, (job, result, prompt_build) in enumerate(zip(unique_jobs, results, prompt_build_records)):
        target_condition_id = prompt_build["id"]
        usage_records.append(
            {
                "phase": "target_condition",
                "index": index,
                "id": target_condition_id,
                "model": result.get("model"),
                "metadata": job.to_metadata(),
                "prompt_chars": len(result.get("input", "")),
                "output_chars": len(result.get("output", "")),
                "prompt_build": prompt_build,
                "timing": result.get("timing", {}),
                "usage": result.get("usage", {}),
                "cost": result.get("cost", {}),
                "attempts": result.get("attempts", []),
            }
        )
        condition = _parse_or_fallback_condition(
            result.get("output", ""),
            job=job,
            dataset_family=dataset_family,
            target_condition_id=target_condition_id,
        )
        signature = _target_signature(job, dataset_family)
        condition_by_signature[signature] = condition
        conditions.append(condition)

    conditions_path = os.path.join(output_dir, artifacts.target_conditions)
    usage_json_path = os.path.join(output_dir, artifacts.target_condition_usage_json)
    usage_csv_path = os.path.join(output_dir, artifacts.target_condition_usage_csv)
    save_json(conditions_path, conditions)
    write_usage_logs(
        json_path=usage_json_path,
        csv_path=usage_csv_path,
        records=usage_records,
        pricing=DEFAULT_PRICING,
    )

    attached_jobs = []
    for index, job in enumerate(jobs, start=1):
        condition = condition_by_signature[_target_signature(job, dataset_family)]
        attached_jobs.append(
            replace(
                job,
                slot_id=_slot_id(dataset_family, index),
                target_condition=condition,
            )
        )

    slots_path = os.path.join(output_dir, artifacts.slots)
    save_json(slots_path, [job.to_metadata() for job in attached_jobs])
    print(f"  [OK] Target conditions: {conditions_path}")
    print(f"  [OK] Slots: {slots_path}")
    return attached_jobs


def build_target_condition_prompt(job: Any, dataset_family: str) -> str:
    metadata = _compact_job_metadata(job)
    output_schema = {
        "target_condition_id": "TC-0001",
        "dataset_family": dataset_family,
        "primary_type": getattr(job, "primary_type", None),
        "role": getattr(job, "role", None),
        "user_context_id": getattr(job, "user_context_id", None),
        "target_tables": [],
        "target_columns": [],
        "policy_refs": [],
        "scope": "allowed_or_violated_scope",
        "condition_text": "Short condition that the generated sample must target.",
        "relevance_rules": ["Rule used later by LABEL to decide target relevance."],
        "benign_allowed": dataset_family == "Benign",
    }
    return f"""You are defining target conditions for a Text-to-SQL security dataset.
This phase does not generate dataset records. It creates a precise target contract that later GENERATE and LABEL phases must follow.

Dataset family: {dataset_family}

Slot/job metadata:
{json.dumps(metadata, ensure_ascii=False, indent=2)}

Database schema:
{getattr(job, "schema", "")}

Policy target context, if any:
{json.dumps(getattr(getattr(job, "policy_target", None), "to_prompt_context", lambda: None)(), ensure_ascii=False, indent=2)}

Attack policy context, if any:
{json.dumps(getattr(job, "attack_policy_context", None), ensure_ascii=False, indent=2)}

Role policy context:
{json.dumps(getattr(job, "policy_context", None), ensure_ascii=False, indent=2)}

User context:
{json.dumps(getattr(job, "user_context", None), ensure_ascii=False, indent=2)}

Safety condition, if any:
{getattr(job, "safe_condition", "")}

Return valid JSON only with this exact shape:
{json.dumps(output_schema, ensure_ascii=False, indent=2)}

Rules:
1. For Multiturn and SingleturnPI, the target condition must describe the protected or violated target.
2. For Benign, the target condition must describe an allowed, policy-compliant target only.
3. policy_refs must be copied from policy_index-derived metadata only; do not invent refs.
4. If Attack policy context is provided, target_tables/target_columns must come from those target_policies. Do not switch to another policy only because the safety condition mentions a different table.
5. relevance_rules must be concrete enough for a later verifier to reject off-target generations.
6. Keep the condition compact; this record will be inserted into later prompts.
"""


def _parse_or_fallback_condition(
    raw_output: str,
    *,
    job: Any,
    dataset_family: str,
    target_condition_id: str,
) -> Dict[str, Any]:
    fallback_used = False
    try:
        parsed = parse_model_json(raw_output)
    except Exception:
        parsed = {}
        fallback_used = True

    policy_target = getattr(job, "policy_target", None)
    attack_policy_context = getattr(job, "attack_policy_context", None) or {}
    target_context = policy_target.to_prompt_context() if policy_target else {}
    attack_tags = getattr(job, "attack_tags", {})
    policy_tables, policy_columns = _policy_target_tables_columns(attack_policy_context)
    target_tables = _as_list(parsed.get("target_tables")) or _as_list(target_context.get("tables"))
    target_columns = _as_list(parsed.get("target_columns")) or _as_list(target_context.get("columns"))
    if policy_tables and not set(_normalize_names(target_tables)).intersection(_normalize_names(policy_tables)):
        target_tables = policy_tables
        target_columns = policy_columns
    elif policy_columns and target_columns:
        normalized_policy_columns = set(_normalize_names(policy_columns))
        normalized_target_columns = set(_normalize_names(target_columns))
        if not normalized_target_columns.intersection(normalized_policy_columns):
            target_columns = policy_columns

    allowed_policy_refs = set(_as_list(attack_tags.get("violated_policies")) + _as_list(attack_tags.get("rbac_violation")))
    policy_refs = [ref for ref in _as_list(parsed.get("policy_refs")) if not allowed_policy_refs or ref in allowed_policy_refs]
    if not policy_refs:
        policy_refs = _as_list(target_context.get("violated_policies")) or _as_list(attack_tags.get("violated_policies"))

    return {
        "target_condition_id": target_condition_id,
        "dataset_family": dataset_family,
        "primary_type": getattr(job, "primary_type", None),
        "role": getattr(job, "role", None),
        "user_context_id": getattr(job, "user_context_id", None),
        "target_tables": target_tables,
        "target_columns": target_columns,
        "policy_refs": policy_refs,
        "scope": str(parsed.get("scope") or target_context.get("target_kind") or "policy_target"),
        "condition_text": str(
            parsed.get("condition_text")
            or target_context.get("description")
            or _fallback_condition_text(dataset_family, job, attack_policy_context)
        ),
        "relevance_rules": _as_list(parsed.get("relevance_rules")) or _fallback_relevance_rules(dataset_family, job),
        "benign_allowed": bool(parsed.get("benign_allowed", dataset_family == "Benign")),
        "fallback_used": fallback_used,
        "raw_model_output": raw_output,
    }


def _fallback_condition_text(dataset_family: str, job: Any, attack_policy_context: Dict[str, Any]) -> str:
    if dataset_family == "Benign":
        return "The generated question and SQL must remain inside the permitted role policy target."
    guidance = attack_policy_context.get("final_turn_guidance") or []
    if guidance:
        return str(guidance[0])
    return "The generated sample must target the slot's protected policy boundary."


def _fallback_relevance_rules(dataset_family: str, job: Any) -> List[str]:
    if dataset_family == "Benign":
        return [
            "All SQL must be SELECT-only and policy-compliant for the current role.",
            "The NLQ must not request forbidden, cross-user, or hidden policy data.",
        ]
    return [
        "The malicious request must match the slot primary_type.",
        "The malicious request must target the selected policy/safety boundary.",
    ]


def _policy_target_tables_columns(attack_policy_context: Dict[str, Any]) -> tuple[List[str], List[str]]:
    tables: List[str] = []
    columns: List[str] = []
    for policy in attack_policy_context.get("target_policies") or []:
        for table, table_columns in (policy.get("denied_columns") or {}).items():
            if table == "ALL_OTHER_TABLES":
                continue
            tables.append(str(table))
            columns.extend(f"{table}.{column}" for column in _as_list(table_columns) if column != "ALL_COLUMNS")
        for table, table_columns in (policy.get("permitted_columns") or {}).items():
            if table == "ALL_OTHER_TABLES":
                continue
            tables.append(str(table))
            columns.extend(f"{table}.{column}" for column in _as_list(table_columns) if column != "ALL_COLUMNS")
        row_filter = str(policy.get("row_filter") or "")
        for table in _tables_from_row_filter(row_filter):
            tables.append(table)
    return sorted(set(tables)), sorted(set(columns))


def _tables_from_row_filter(row_filter: str) -> List[str]:
    tables: List[str] = []
    for token in row_filter.replace("(", " ").replace(")", " ").replace("=", " ").split():
        if "." not in token:
            continue
        table = token.split(".", 1)[0].strip()
        if table and table.replace("_", "").isalnum():
            tables.append(table)
    return tables


def _normalize_names(values: Sequence[str]) -> List[str]:
    normalized: List[str] = []
    for value in values:
        text = str(value).strip().lower()
        if text:
            normalized.append(text)
            if "." in text:
                normalized.append(text.split(".", 1)[0])
    return normalized


def _unique_jobs_by_target_signature(jobs: Sequence[Any], dataset_family: str) -> List[Any]:
    seen = set()
    unique_jobs: List[Any] = []
    for job in jobs:
        signature = _target_signature(job, dataset_family)
        if signature in seen:
            continue
        seen.add(signature)
        unique_jobs.append(job)
    return unique_jobs


def _compact_job_metadata(job: Any) -> Dict[str, Any]:
    heavy_keys = {
        "policy_context",
        "attack_policy_context",
        "policy_target",
        "user_context",
        "target_condition",
    }
    return {
        key: value
        for key, value in job.to_metadata().items()
        if key not in heavy_keys
    }


def _target_signature(job: Any, dataset_family: str) -> str:
    metadata = job.to_metadata()
    target = metadata.get("policy_target") or metadata.get("attack_policy_context") or {}
    signature = {
        "family": dataset_family,
        "primary_type": metadata.get("primary_type"),
        "role": metadata.get("role"),
        "schema_index": metadata.get("schema_index"),
        "condition_id": metadata.get("condition_id"),
        "policy_target": target,
        "attack_tags": metadata.get("attack_tags"),
    }
    return json.dumps(signature, ensure_ascii=False, sort_keys=True)


def _target_condition_id(dataset_family: str, index: int) -> str:
    return f"{_prefix(dataset_family)}-TC-{index:04d}"


def _slot_id(dataset_family: str, index: int) -> str:
    return f"{_prefix(dataset_family)}-SLOT-{index:04d}"


def _prefix(dataset_family: str) -> str:
    return {
        "Multiturn": "MT",
        "SingleturnPI": "PI",
        "Benign": "BN",
    }.get(dataset_family, "DS")


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if str(value).strip():
        return [str(value)]
    return []
