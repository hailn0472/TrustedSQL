from __future__ import annotations

import hashlib
import math
import copy
from collections import Counter, defaultdict
from dataclasses import replace
from typing import Any, Dict, List, Sequence

from .pattern_slots import GNNJob


PROTOCOL_VERSION = "gnn_protocols_v2"


class ProtocolPlanner:
    def __init__(self, jobs: Sequence[GNNJob], *, seed: int):
        self.seed = seed
        self.user_split_map, self.in_policy_status = _build_user_split_map(jobs, seed=seed)
        self.policy_split_map, self.policy_holdout_status = _build_policy_split_map(
            jobs,
            seed=seed,
        )
        self.balanced_split_map = _build_balanced_split_map(jobs)
        self.balanced_status = _build_balanced_status(
            jobs,
            split_map=self.balanced_split_map,
        )

    def assign_jobs(self, jobs: Sequence[GNNJob]) -> List[GNNJob]:
        return [
            replace(job, protocol_assignments=self.assignment_for(job))
            for job in jobs
        ]

    def assignment_for(self, job: GNNJob) -> Dict[str, Any]:
        surface_variant_id = f"{job.pattern_id}:surface:{(job.pattern_sample_index - 1) % 8:02d}"
        variation_plan = _build_variation_plan(job, surface_variant_id)
        target_entity = "|".join(job.target.target_tables)
        in_policy_group = f"{job.user_context_id}|{target_entity}|{surface_variant_id}"
        policy_group = _policy_group_key(job)
        balanced_split = self.balanced_split_map.get(job.slot_id, _balanced_split(job))
        balanced_group = _balanced_group_key(job)
        return {
            "assignment_version": PROTOCOL_VERSION,
            "assignment_seed": self.seed,
            "assigned_before_generation": True,
            "surface_variant_id": surface_variant_id,
            "variation_plan": variation_plan,
            "in_policy": {
                "protocol": "in_policy",
                "status": self.in_policy_status["status"],
                "split": self.user_split_map.get(job.user_context_id),
                "split_group_id": in_policy_group,
                "isolation_key": job.user_context_id,
                "split_reason": "authenticated_user_group_holdout",
            },
            "policy_holdout": {
                "protocol": "policy_holdout",
                "status": self.policy_holdout_status["status"],
                "split": self.policy_split_map.get(policy_group, "train"),
                "split_group_id": policy_group,
                "isolation_key": policy_group,
                "split_reason": "deterministic_role_boundary_scope_stratified_policy_holdout",
            },
            "balanced": {
                "protocol": "balanced",
                "status": self.balanced_status["status"],
                "split": balanced_split,
                "split_group_id": balanced_group,
                "isolation_key": f"{balanced_group}:{job.pattern_sample_index}",
                "split_reason": "deterministic_label_turntype_pattern_role_scope_stratified_split",
            },
        }

    def report(self) -> Dict[str, Any]:
        return {
            "version": PROTOCOL_VERSION,
            "seed": self.seed,
            "created_before_generation": True,
            "in_policy": {
                **self.in_policy_status,
                "user_split_mapping": self.user_split_map,
            },
            "policy_holdout": {
                **self.policy_holdout_status,
                "policy_group_split_mapping": self.policy_split_map,
            },
            "balanced": self.balanced_status,
        }


def _build_variation_plan(job: GNNJob, surface_variant_id: str) -> Dict[str, Any]:
    return _build_variation_plan_for_index(
        job,
        surface_variant_id,
        max(job.pattern_sample_index - 1, 0),
    )


def build_refill_protocol_assignments(
    job: GNNJob,
    *,
    refill_round: int,
    refill_index: int,
) -> Dict[str, Any]:
    assignments = copy.deepcopy(job.protocol_assignments or {})
    surface_variant_id = str(assignments.get("surface_variant_id") or "")
    base_index = max(job.pattern_sample_index - 1, 0)
    alternate_index = base_index + (max(refill_round, 1) * 1000) + refill_index
    assignments["variation_plan"] = _build_variation_plan_for_index(
        job,
        surface_variant_id,
        alternate_index,
    )
    assignments["refill_variation"] = {
        "round": refill_round,
        "index": refill_index,
        "preserved_surface_variant_id": surface_variant_id,
        "preserved_target_id": job.target.target_id,
    }
    return assignments


def _build_variation_plan_for_index(
    job: GNNJob,
    surface_variant_id: str,
    sample_index: int,
) -> Dict[str, Any]:
    surface_index = sample_index % 8
    variation_cycle = sample_index // 8
    framings = [
        "concise_direct",
        "conversational_coreference",
        "administrative_task",
        "semester_schedule",
        "course_or_class_anchor",
        "comparison_or_ranking",
        "polite_indirect",
        "compact_operational",
    ]
    attack_families = [
        "direct_request",
        "practical_pretext",
        "continuation_request",
        "format_conversion",
        "comparison_request",
        "audit_pretext",
        "helpdesk_pretext",
        "reporting_pretext",
    ]
    motivations = [
        "prepare_for_a_meeting",
        "verify_a_recent_change",
        "complete_a_course_task",
        "resolve_a_support_question",
        "prepare_a_progress_summary",
        "cross_check_a_report",
        "plan_the_next_semester",
        "respond_to_a_class_request",
        "review_an_exception",
        "compare_two_available_views",
        "confirm_a_deadline_related_detail",
        "organize_a_follow_up",
        "prepare_a_short_brief",
        "validate_an_export",
        "investigate_an_inconsistency",
        "answer_a_time_sensitive_question",
    ]
    audiences = [
        "for_personal_review",
        "for_a_course_coordinator",
        "for_a_class_discussion",
        "for_a_support_ticket",
        "for_a_progress_meeting",
        "for_a_short_email",
        "for_a_planning_note",
        "for_a_compliance_check",
    ]
    output_forms = [
        "short_answer",
        "two_column_list",
        "ordered_summary",
        "single_sentence",
        "compact_table",
        "bullet_list",
        "comparison",
        "status_note",
    ]
    temporal_frames = [
        "current_semester",
        "most_recent_update",
        "before_next_class",
        "for_today",
        "for_the_next_meeting",
        "during_course_planning",
        "after_a_recent_change",
        "without_a_time_reference",
    ]
    sentence_structures = [
        "question_first",
        "context_then_request",
        "goal_then_constraint",
        "reference_then_follow_up",
        "comparison_then_request",
        "polite_request_with_reason",
        "brief_command_with_context",
        "two_clause_request",
    ]
    anchors = _variation_anchors(job)
    if job.target.primary_violation == "RB-02":
        target_columns = list(job.target.denied_columns)
    else:
        target_columns = [
            column
            for column in job.target.target_columns
            if column
            not in {
                "user_id",
                "student_id",
                "lecturer_id",
                "grade_detail_id",
                "enrollment_id",
                "class_course_id",
            }
        ]
    return {
        "variation_plan_id": f"{job.pattern_id}:variation:{sample_index:06d}",
        "surface_variant_id": surface_variant_id,
        "variation_cycle": variation_cycle,
        "lexical_seed": _hash(
            0,
            f"{job.slot_id}|{job.pattern_id}|{job.target.target_id}|{sample_index}",
        )[:12],
        "discourse_framing": framings[surface_index],
        "entity_anchor": anchors[sample_index % len(anchors)] if anchors else None,
        "requested_attribute": (
            target_columns[
                (surface_index + variation_cycle) % len(target_columns)
            ]
            if target_columns
            else None
        ),
        "relationship_scope": (
            job.target.forbidden_subject
            if job.seq_label == "MALICIOUS"
            else job.target.allowed_subject
        ),
        "attack_wording_family": attack_families[
            (surface_index + variation_cycle) % len(attack_families)
        ],
        "task_motivation": motivations[
            (sample_index * 3 + surface_index) % len(motivations)
        ],
        "intended_audience": audiences[
            (sample_index * 5 + variation_cycle) % len(audiences)
        ],
        "requested_output_form": output_forms[
            (sample_index * 7 + surface_index) % len(output_forms)
        ],
        "temporal_frame": temporal_frames[
            (sample_index * 11 + variation_cycle) % len(temporal_frames)
        ],
        "sentence_structure": sentence_structures[
            (sample_index * 13 + surface_index) % len(sentence_structures)
        ],
    }


def _variation_anchors(job: GNNJob) -> List[Dict[str, str]]:
    context = job.user_context
    pattern_id = job.pattern_id
    if pattern_id in {
        "MT-02_PROGRESSIVE_PERSONAL_INFO_TARGETING",
        "RB-03_ROW_SCOPE_OTHER_SUBJECT",
    }:
        identity = ((context or {}).get("optimized_context") or {}).get("identity") or {}
        excluded = {
            str(identity.get("student_code") or ""),
            str(identity.get("fullname") or ""),
        }
        catalog = (
            ((context or {}).get("optimized_context") or {}).get("relation_catalog")
            or {}
        )
        peer_anchors: List[Dict[str, str]] = []
        for person in [
            *(catalog.get("classmates") or []),
            *(catalog.get("students") or []),
        ]:
            if not isinstance(person, dict):
                continue
            for key in ("student_code", "fullname"):
                value = person.get(key)
                candidate = {"kind": key, "value": str(value)}
                if value and str(value) not in excluded and candidate not in peer_anchors:
                    peer_anchors.append(candidate)
        if peer_anchors:
            return peer_anchors
    if pattern_id in {
        "BENIGN_PUBLIC_COURSE_CATALOG_LOOKUP",
        "MT-06_ATTENTION_REDIRECTION_TO_PRIVATE_DATA",
    }:
        course_anchors = [
            anchor
            for anchor in _context_anchors(context)
            if anchor["kind"] in {"course_code", "course_name_en"}
        ]
        if course_anchors:
            return course_anchors
    if pattern_id in {
        "BENIGN_ENROLLED_CLASSMATE_IDENTITY_ROSTER",
        "BENIGN_SELF_SCOPE_ACADEMIC_LOOKUP",
        "MT-04_AGGREGATE_DIFFERENCE_INFERENCE",
        "MT-05_EXTREME_PEER_ATTRIBUTE_LEAKAGE",
        "MT-07_CRESCENDO_SCOPE_ESCALATION",
    }:
        academic = [
            anchor
            for anchor in _context_anchors(context)
            if anchor["kind"]
            in {"semester", "class_name", "course_code", "course_name_en"}
        ]
        if academic:
            return academic
    return _context_anchors(context)


def _context_anchors(context: Any) -> List[Dict[str, str]]:
    preferred = {
        "course_code",
        "course_name_en",
        "class_name",
        "semester",
        "student_code",
        "fullname",
        "room",
    }
    anchors: List[Dict[str, str]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in preferred and item not in (None, ""):
                    candidate = {"kind": str(key), "value": str(item)}
                    if candidate not in anchors:
                        anchors.append(candidate)
                elif isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(context or {})
    return anchors[:256]


def validate_released_assignments(
    records: Sequence[Dict[str, Any]],
    planner: ProtocolPlanner,
) -> Dict[str, Any]:
    errors: List[str] = []
    seen_by_protocol: Dict[str, Dict[str, set[str]]] = {
        "in_policy": defaultdict(set),
        "policy_holdout": defaultdict(set),
    }
    primary_train = Counter()
    for record in records:
        assignments = record.get("protocol_assignments") or {}
        for protocol in ("in_policy", "policy_holdout"):
            assignment = assignments.get(protocol) or {}
            split = assignment.get("split")
            key = assignment.get("isolation_key")
            if split and key:
                seen_by_protocol[protocol][str(key)].add(str(split))
                if protocol == "policy_holdout" and split == "train":
                    primary_train[str(record.get("primary_type"))] += 1
        if assignments.get("assignment_seed") != planner.seed:
            errors.append(f"{record.get('id')}: protocol assignment seed changed.")
        if not assignments.get("assigned_before_generation"):
            errors.append(f"{record.get('id')}: assignment is not marked pre-generation.")

    for protocol, keys in seen_by_protocol.items():
        for key, splits in keys.items():
            if len(splits) > 1:
                errors.append(f"{protocol}: isolation key {key!r} appears in {sorted(splits)}.")

    expected_primary = {
        str(record.get("primary_type"))
        for record in records
    }
    missing_train = sorted(expected_primary.difference(primary_train))
    return {
        "ok": not errors,
        "errors": errors,
        "policy_holdout_train_primary_counts": dict(primary_train),
        "policy_holdout_missing_train_primary_types": missing_train,
    }


def released_protocol_status(
    records: Sequence[Dict[str, Any]],
    *,
    protocol: str,
    planned_status: Dict[str, Any],
) -> Dict[str, Any]:
    if planned_status.get("status") != "READY":
        return dict(planned_status)
    counts = Counter(
        str(((record.get("protocol_assignments") or {}).get(protocol) or {}).get("split"))
        for record in records
        if ((record.get("protocol_assignments") or {}).get(protocol) or {}).get("split")
    )
    missing_splits = [
        split
        for split in ("train", "validation", "test")
        if counts.get(split, 0) == 0
    ]
    if missing_splits:
        return {
            **planned_status,
            "status": "UNAVAILABLE",
            "reason": "released_records_missing_required_splits",
            "released_split_counts": dict(counts),
            "missing_splits": missing_splits,
        }
    if protocol == "policy_holdout":
        all_primary = {str(record.get("primary_type")) for record in records}
        train_primary = {
            str(record.get("primary_type"))
            for record in records
            if ((record.get("protocol_assignments") or {}).get(protocol) or {}).get("split") == "train"
        }
        missing_primary = sorted(all_primary.difference(train_primary))
        if missing_primary:
            return {
                **planned_status,
                "status": "UNAVAILABLE",
                "reason": "released_train_missing_primary_type_support",
                "released_split_counts": dict(counts),
                "missing_primary_types": missing_primary,
            }
    return {
        **planned_status,
        "released_split_counts": dict(counts),
    }


def _build_user_split_map(
    jobs: Sequence[GNNJob],
    *,
    seed: int,
) -> tuple[Dict[str, str], Dict[str, Any]]:
    users = sorted({job.user_context_id for job in jobs}, key=lambda value: _hash(seed, value))
    if len(users) < 3:
        return {}, {
            "status": "UNAVAILABLE",
            "reason": "requires_at_least_three_distinct_authenticated_users",
            "distinct_user_count": len(users),
            "target_ratios": {"train": 0.8, "validation": 0.1, "test": 0.1},
        }
    counts = _ratio_counts(len(users), (0.8, 0.1, 0.1))
    mapping: Dict[str, str] = {}
    cursor = 0
    for split, count in zip(("train", "validation", "test"), counts):
        for user in users[cursor : cursor + count]:
            mapping[user] = split
        cursor += count
    return mapping, {
        "status": "READY",
        "reason": "authenticated_user_groups_available",
        "distinct_user_count": len(users),
        "target_ratios": {"train": 0.8, "validation": 0.1, "test": 0.1},
        "split_counts": dict(Counter(mapping.values())),
    }


def _build_policy_split_map(
    jobs: Sequence[GNNJob],
    *,
    seed: int,
) -> tuple[Dict[str, str], Dict[str, Any]]:
    strata: Dict[str, set[str]] = defaultdict(set)
    group_jobs: Dict[str, List[GNNJob]] = defaultdict(list)
    for job in jobs:
        group = _policy_group_key(job)
        stratum = f"{job.role}|{job.target.primary_violation or 'BENIGN'}|{job.target.scope_type}"
        strata[stratum].add(group)
        group_jobs[group].append(job)

    mapping: Dict[str, str] = {}
    warnings: List[str] = []
    for stratum, groups in sorted(strata.items()):
        ordered = sorted(groups, key=lambda value: _hash(seed, f"{stratum}:{value}"))
        if len(ordered) < 3:
            mapping.update({group: "train" for group in ordered})
            warnings.append(f"{stratum}: fewer than three policy groups; kept in train.")
            continue
        counts = _ratio_counts(len(ordered), (0.7, 0.15, 0.15))
        cursor = 0
        for split, count in zip(("train", "validation", "test"), counts):
            for group in ordered[cursor : cursor + count]:
                mapping[group] = split
            cursor += count

    # Each primary type must remain represented in train. Moving the smallest
    # necessary policy group is deterministic and happens before generation.
    primary_groups: Dict[str, List[str]] = defaultdict(list)
    for group, grouped_jobs in group_jobs.items():
        for primary in {job.primary_type for job in grouped_jobs}:
            primary_groups[primary].append(group)
    for primary, groups in sorted(primary_groups.items()):
        if any(mapping.get(group) == "train" for group in groups):
            continue
        selected = min(groups, key=lambda value: _hash(seed, f"train-anchor:{primary}:{value}"))
        mapping[selected] = "train"
        warnings.append(f"{primary}: moved {selected} to train to preserve label support.")

    split_counts = Counter(mapping.values())
    ready = bool(split_counts["validation"] and split_counts["test"])
    return mapping, {
        "status": "READY" if ready else "UNAVAILABLE",
        "reason": (
            "stratified_policy_groups_available"
            if ready
            else "insufficient_policy_groups_for_validation_and_test"
        ),
        "target_ratios": {"train": 0.7, "validation": 0.15, "test": 0.15},
        "split_counts": dict(split_counts),
        "warnings": warnings,
    }


def _balanced_group_key(job: GNNJob) -> str:
    return "|".join(
        [
            job.seq_label,
            job.turn_type,
            job.pattern_id,
            job.role,
            job.target.scope_type,
            job.target.primary_violation or "BENIGN",
        ]
    )


def _balanced_split(job: GNNJob) -> str:
    # 70/15/15 cycle inside every label/turn/pattern/role/scope stratum.
    index = (job.pattern_sample_index - 1) % 20
    if index < 14:
        return "train"
    if index < 17:
        return "validation"
    return "test"


def _build_balanced_split_map(jobs: Sequence[GNNJob]) -> Dict[str, str]:
    grouped: Dict[str, List[GNNJob]] = defaultdict(list)
    for job in jobs:
        grouped["|".join([job.seq_label, job.turn_type, job.pattern_id])].append(job)
    split_map: Dict[str, str] = {}
    for bucket_jobs in grouped.values():
        ordered = sorted(bucket_jobs, key=lambda item: (item.pattern_sample_index, item.slot_id))
        train_count, validation_count, test_count = _ratio_counts(
            len(ordered),
            (0.7, 0.15, 0.15),
        )
        for index, job in enumerate(ordered):
            if index < train_count:
                split = "train"
            elif index < train_count + validation_count:
                split = "validation"
            else:
                split = "test"
            split_map[job.slot_id] = split
        if test_count == 0:
            # Keep the local variable intentionally used for readability in audits.
            continue
    return split_map


def _build_balanced_status(
    jobs: Sequence[GNNJob],
    *,
    split_map: Dict[str, str],
) -> Dict[str, Any]:
    split_counts = Counter(split_map.get(job.slot_id, _balanced_split(job)) for job in jobs)
    required_bucket_splits: Dict[str, set[str]] = defaultdict(set)
    stratum_splits: Dict[str, set[str]] = defaultdict(set)
    warnings: List[str] = []
    for job in jobs:
        split = split_map.get(job.slot_id, _balanced_split(job))
        required_bucket_splits[
            "|".join([job.seq_label, job.turn_type, job.pattern_id])
        ].add(split)
        stratum_splits[_balanced_group_key(job)].add(split)
    blocking_missing: List[str] = []
    for bucket, splits in sorted(required_bucket_splits.items()):
        missing = sorted({"train", "validation", "test"}.difference(splits))
        if missing:
            blocking_missing.append(f"{bucket}: missing {missing}")
    for stratum, splits in sorted(stratum_splits.items()):
        missing = sorted({"train", "validation", "test"}.difference(splits))
        if missing:
            warnings.append(f"{stratum}: missing {missing}")
    ready = (
        not blocking_missing
        and all(split_counts.get(split, 0) for split in ("train", "validation", "test"))
    )
    return {
        "status": "READY" if ready else "UNAVAILABLE",
        "reason": (
            "balanced_label_turntype_pattern_splits_available"
            if ready
            else "insufficient_records_for_balanced_stratified_split"
        ),
        "target_ratios": {"train": 0.7, "validation": 0.15, "test": 0.15},
        "split_counts": dict(split_counts),
        "required_bucket_count": len(required_bucket_splits),
        "blocking_missing": blocking_missing,
        "stratum_count": len(stratum_splits),
        "warnings": warnings,
    }


def _policy_group_key(job: GNNJob) -> str:
    if job.target.policy_ref == "DEFAULT_DENY":
        table = job.target.target_tables[0] if job.target.target_tables else "unknown"
        return f"DEFAULT_DENY:{job.role}:{table}"
    return job.target.policy_ref


def _ratio_counts(total: int, ratios: tuple[float, float, float]) -> tuple[int, int, int]:
    raw = [total * ratio for ratio in ratios]
    counts = [int(math.floor(value)) for value in raw]
    remaining = total - sum(counts)
    order = sorted(
        range(3),
        key=lambda index: (raw[index] - counts[index], -index),
        reverse=True,
    )
    for index in order[:remaining]:
        counts[index] += 1
    if total >= 3:
        for index in (1, 2):
            if counts[index] == 0:
                donor = max(range(3), key=lambda candidate: counts[candidate])
                counts[donor] -= 1
                counts[index] += 1
    return counts[0], counts[1], counts[2]


def _hash(seed: int, value: str) -> str:
    return hashlib.sha256(f"{seed}:{value}".encode("utf-8")).hexdigest()
