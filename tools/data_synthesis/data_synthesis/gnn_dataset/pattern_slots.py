from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from data_synthesis.common.quota import scale_counts
from data_synthesis.common.user_context import UserContextIndex

from .pattern_loader import PatternBank, PatternSpec, pattern_to_prompt_context
from .policy_compiler import (
    CompiledPolicyBundle,
    CompiledTarget,
    SUPPORTED_GENERATION_ROLES,
)


@dataclass(frozen=True)
class GNNJob:
    sequence_number: int
    pattern: PatternSpec
    pattern_sample_index: int
    slot_id: str
    target: CompiledTarget
    turn_policy_targets: List[CompiledTarget]
    schema: str
    role: str
    user_context_id: str
    user_context: Optional[Dict[str, Any]]
    policy_bundle: CompiledPolicyBundle
    protocol_assignments: Optional[Dict[str, Any]] = None
    generation_attempt: int = 0

    @property
    def sequence_id(self) -> str:
        return f"GNN-{self.sequence_number:06d}"

    @property
    def pattern_id(self) -> str:
        return self.pattern.pattern_id

    @property
    def dataset_family(self) -> str:
        return self.pattern.dataset_family

    @property
    def primary_type(self) -> str:
        return self.pattern.primary_type

    @property
    def turn_type(self) -> str:
        return "multi" if len(self.pattern.turn_blueprint) > 1 else "single"

    @property
    def seq_label(self) -> str:
        return self.pattern.seq_label

    @property
    def attack_tags(self) -> Dict[str, Any]:
        if self.dataset_family == "singleturn_pi":
            injection_type = self.primary_type
            mt_pattern = None
        elif self.dataset_family == "multiturn":
            injection_type = None
            mt_pattern = self.primary_type
        else:
            injection_type = None
            mt_pattern = None
        violations = []
        if self.target.primary_violation:
            violations.append(self.target.primary_violation)
        return {
            "injection_type": injection_type,
            "rbac_violation": violations or None,
            "primary_rbac_violation": self.target.primary_violation,
            "secondary_rbac_violations": [],
            "violated_policies": (
                [self.target.policy_ref]
                if self.target.primary_violation is not None
                else None
            ),
            "mt_pattern": mt_pattern,
        }

    @property
    def policy_context(self) -> Dict[str, Any]:
        return self.target.to_dict()

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "id": self.sequence_id,
            "slot_id": self.slot_id,
            "dataset_family": self.dataset_family,
            "pattern_id": self.pattern_id,
            "primary_type": self.primary_type,
            "turn_type": self.turn_type,
            "seq_label": self.seq_label,
            "pattern_sample_index": self.pattern_sample_index,
            "role": self.role,
            "user_context_id": self.user_context_id,
            "pattern": pattern_to_prompt_context(self.pattern),
            "compiled_target": self.target.to_dict(),
            "turn_policy_targets": [
                {
                    "turn_index": index,
                    "turn_label": self.pattern.turn_blueprint[index - 1]["turn_label"],
                    "sql_gt_policy": self.pattern.turn_blueprint[index - 1]["sql_gt_policy"],
                    "intent": self.pattern.turn_blueprint[index - 1].get("intent"),
                    "scope": self.pattern.turn_blueprint[index - 1].get("scope"),
                    "policy_target": target.to_dict(),
                }
                for index, target in enumerate(self.turn_policy_targets, 1)
            ],
            "hard_negatives": self.pattern.hard_negatives,
            "generation_validation": self.pattern.generation_validation,
            "policy_context": self.policy_context,
            "user_context": self.user_context,
            "attack_tags": self.attack_tags,
            "schema": self.schema,
            "protocol_assignments": self.protocol_assignments or {},
            "generation_attempt": self.generation_attempt,
        }


def base_pattern_counts(bank: PatternBank) -> Dict[str, int]:
    return OrderedDict((pattern.pattern_id, pattern.target_count) for pattern in bank.active_patterns)


def scaled_pattern_counts(bank: PatternBank, total: Optional[int] = None) -> Dict[str, int]:
    if _is_balanced_benign_multiturn_bank(bank):
        return _scaled_balanced_benign_multiturn_counts(bank, total)
    return scale_counts(base_pattern_counts(bank), total, label="GNN total")


def _is_balanced_benign_multiturn_bank(bank: PatternBank) -> bool:
    families = {pattern.dataset_family for pattern in bank.active_patterns}
    return families == {"benign", "multiturn"} and any(
        pattern.pattern_id.startswith("BENIGN_MT_")
        for pattern in bank.active_patterns
    )


def _scaled_balanced_benign_multiturn_counts(
    bank: PatternBank,
    total: Optional[int],
) -> Dict[str, int]:
    patterns = list(bank.active_patterns)
    requested_total = total or sum(pattern.target_count for pattern in patterns)
    benign_single = OrderedDict(
        (pattern.pattern_id, pattern.target_count)
        for pattern in patterns
        if pattern.dataset_family == "benign" and len(pattern.turn_blueprint) == 1
    )
    benign_multi = OrderedDict(
        (pattern.pattern_id, pattern.target_count)
        for pattern in patterns
        if pattern.dataset_family == "benign" and len(pattern.turn_blueprint) > 1
    )
    malicious_multi = OrderedDict(
        (pattern.pattern_id, pattern.target_count)
        for pattern in patterns
        if pattern.dataset_family == "multiturn"
    )
    group_counts = scale_counts(
        OrderedDict(
            [
                ("benign_single", 1000),
                ("benign_multi", 1500),
                ("malicious_multi", 2500),
            ]
        ),
        requested_total,
        label="balanced GNN family total",
    )
    counts: Dict[str, int] = OrderedDict()
    counts.update(scale_counts(benign_single, group_counts["benign_single"], label="benign single total"))
    counts.update(scale_counts(benign_multi, group_counts["benign_multi"], label="benign multi total"))
    counts.update(scale_counts(malicious_multi, group_counts["malicious_multi"], label="malicious multi total"))
    return counts


def build_generation_plan(
    pattern_bank: PatternBank,
    policy_bundle: CompiledPolicyBundle,
    user_context_index: UserContextIndex,
    *,
    quota_counts: Dict[str, int],
    sequence_start: int = 1,
) -> List[GNNJob]:
    jobs: List[GNNJob] = []
    sequence_number = sequence_start
    patterns_by_id = {pattern.pattern_id: pattern for pattern in pattern_bank.active_patterns}
    available_roles = [
        role
        for role in SUPPORTED_GENERATION_ROLES
        if role in user_context_index.by_role
    ]
    if not available_roles:
        raise ValueError("GNN generation requires student and/or lecturer user context.")

    for pattern_id, count in quota_counts.items():
        pattern = patterns_by_id[pattern_id]
        targets = _compatible_targets(pattern, policy_bundle, available_roles)
        if not targets:
            raise ValueError(f"{pattern_id}: no compatible compiled policy targets.")
        for sample_index in range(1, count + 1):
            target = targets[(sample_index - 1) % len(targets)]
            turn_policy_targets = _select_turn_policy_targets(
                pattern,
                policy_bundle,
                target,
            )
            context = user_context_index.select(target.role, sample_index - 1)
            slot_id = f"GNN-SLOT-{sequence_number:06d}"
            schema_tables = list(target.target_tables)
            for turn_target in turn_policy_targets:
                schema_tables.extend(turn_target.target_tables)
            jobs.append(
                GNNJob(
                    sequence_number=sequence_number,
                    pattern=pattern,
                    pattern_sample_index=sample_index,
                    slot_id=slot_id,
                    target=target,
                    turn_policy_targets=turn_policy_targets,
                    schema=policy_bundle.compact_schema(schema_tables),
                    role=target.role,
                    user_context_id=context.user_context_id,
                    user_context=context.to_prompt_context(),
                    policy_bundle=policy_bundle,
                )
            )
            sequence_number += 1
    return jobs


def summarize_jobs(jobs: Sequence[GNNJob]) -> Dict[str, int]:
    return dict(Counter(job.pattern_id for job in jobs))


def _compatible_targets(
    pattern: PatternSpec,
    policy_bundle: CompiledPolicyBundle,
    roles: Sequence[str],
) -> List[CompiledTarget]:
    targets: List[CompiledTarget] = []
    for role in roles:
        if role not in pattern.allowed_roles:
            continue
        role_targets = policy_bundle.targets_for(
            role=role,
            primary_violation=pattern.primary_violation,
            compatible_scopes=pattern.compatible_scopes,
        )
        if not role_targets and pattern.primary_type != "BENIGN":
            role_targets = policy_bundle.targets_for(
                role=role,
                primary_violation=pattern.primary_violation,
            )
        if pattern.preferred_policy_refs:
            preferred = [
                target
                for target in role_targets
                if target.policy_ref in pattern.preferred_policy_refs
            ]
            if preferred:
                role_targets = preferred
        targets.extend(role_targets)
    targets = _apply_intent_target_preferences(pattern, targets)
    return sorted(
        targets,
        key=lambda target: (
            target.role,
            target.policy_ref,
            target.scope_type,
            target.target_id,
        ),
    )


def _select_turn_policy_targets(
    pattern: PatternSpec,
    policy_bundle: CompiledPolicyBundle,
    attack_target: CompiledTarget,
) -> List[CompiledTarget]:
    if pattern.primary_type == "BENIGN":
        return [attack_target for _ in pattern.turn_blueprint]
    targets: List[CompiledTarget] = []
    for turn in pattern.turn_blueprint:
        if turn.get("turn_label") != "BENIGN":
            targets.append(attack_target)
            continue
        targets.append(
            _select_allowed_target_for_turn(
                policy_bundle,
                attack_target,
                turn,
            )
        )
    return targets


def _select_allowed_target_for_turn(
    policy_bundle: CompiledPolicyBundle,
    attack_target: CompiledTarget,
    turn: Dict[str, Any],
) -> CompiledTarget:
    scope_aliases = {
        "SELF_SCOPE": "SELF",
        "ENROLLED_SCOPE": "ENROLLED",
        "ASSIGNED_SCOPE": "ASSIGNED",
        "GLOBAL_SCOPE": "ALL",
        "PUBLIC_REFERENCE": "ALL",
        "ALL": "ALL",
    }
    desired_scope = scope_aliases.get(str(turn.get("scope")), str(turn.get("scope")))
    candidates = policy_bundle.targets_for(
        role=attack_target.role,
        primary_violation=None,
        compatible_scopes=[desired_scope],
    )
    if not candidates:
        candidates = policy_bundle.targets_for(
            role=attack_target.role,
            primary_violation=None,
        )
    if not candidates:
        raise ValueError(
            f"No allowed policy target for role={attack_target.role}, "
            f"scope={desired_scope}, intent={turn.get('intent')}."
        )

    intent = str(turn.get("intent") or "").upper()
    if "SESSION" in intent or "SESSION_PLAN" in intent:
        preferred_refs = ["C12"]
    elif "PUBLIC_COURSE" in intent or "COURSE_CATALOG" in intent:
        preferred_refs = ["C08"]
    elif "CLASSMATE" in intent and attack_target.role == "student":
        preferred_refs = ["S04"]
    elif ("STUDENT" in intent or "ROSTER" in intent) and attack_target.role == "lecturer":
        preferred_refs = ["L08", "L07"]
    elif ("RESULT" in intent or "ACADEMIC" in intent or "GRADE" in intent) and attack_target.role == "student":
        preferred_refs = ["S07", "S03"]
    elif ("CLASS" in intent or "COURSE" in intent) and attack_target.role == "lecturer":
        preferred_refs = ["L03", "L04"]
    elif attack_target.role == "student":
        preferred_refs = ["S03", "S05", "S06", "S07", "S08"]
    else:
        preferred_refs = ["L03", "L04", "L05", "L06", "L07", "L08"]
    for policy_ref in preferred_refs:
        for candidate in candidates:
            if candidate.policy_ref == policy_ref:
                return candidate
    return sorted(candidates, key=lambda item: (item.policy_ref, item.target_id))[0]


def _apply_intent_target_preferences(
    pattern: PatternSpec,
    targets: List[CompiledTarget],
) -> List[CompiledTarget]:
    preferred_by_pattern = {
        "BENIGN_ENROLLED_CLASSMATE_IDENTITY_ROSTER": ["student:S04:BENIGN"],
        "BENIGN_PUBLIC_COURSE_CATALOG_LOOKUP": [
            "student:C08:BENIGN",
            "lecturer:C08:BENIGN",
        ],
        "BENIGN_SELF_SCOPE_ACADEMIC_LOOKUP": [
            "student:S07:BENIGN",
            "student:S03:BENIGN",
        ],
        "BENIGN_MT_SELF_ACADEMIC_FOLLOWUP": [
            "student:S07:BENIGN",
            "student:S03:BENIGN",
        ],
        "BENIGN_MT_PUBLIC_CATALOG_EXPLORATION": [
            "student:C08:BENIGN",
            "lecturer:C08:BENIGN",
        ],
        "BENIGN_MT_CLASSMATE_IDENTITY_FOLLOWUP": ["student:S04:BENIGN"],
        "BENIGN_MT_LECTURER_ASSIGNED_CLASS_FLOW": [
            "lecturer:L08:BENIGN",
            "lecturer:L07:BENIGN",
            "lecturer:L03:BENIGN",
        ],
        "MT-02_PROGRESSIVE_PERSONAL_INFO_TARGETING": ["student:S01:RB-03"],
        "MT-04_AGGREGATE_DIFFERENCE_INFERENCE": ["student:S07:RB-03"],
        "MT-07_CRESCENDO_SCOPE_ESCALATION": ["student:S07:RB-03"],
        "PI-02_ENCODED_HIDDEN_INSTRUCTION_DATA_ACCESS": [
            "student:S01:RB-02",
            "lecturer:L01:RB-02",
        ],
        "RB-03_ROW_SCOPE_OTHER_SUBJECT": ["student:S01:RB-03"],
    }
    preferred_ids = preferred_by_pattern.get(pattern.pattern_id)
    if not preferred_ids:
        return targets
    for target_id in preferred_ids:
        selected = [target for target in targets if target.target_id == target_id]
        if selected:
            return selected
    return targets
