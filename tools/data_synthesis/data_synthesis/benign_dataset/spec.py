from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from data_synthesis.common.policy_index import PolicyIndex, PolicyTarget
from data_synthesis.common.policy_guard import build_role_policy_context
from data_synthesis.common.quota import scale_counts
from data_synthesis.common.user_context import UserContextIndex


TURN_TYPE_CHOICES = ("all", "single", "multi")


@dataclass(frozen=True)
class BenignSlot:
    turn_type: str
    role: str
    quota: int

    @property
    def key(self) -> str:
        return make_slot_key(self.turn_type, self.role)


@dataclass(frozen=True)
class BenignJob:
    sequence_number: int
    turn_type: str
    slot: BenignSlot
    slot_sample_index: int
    schema_index: int
    schema: str
    role: str
    user_context_id: str
    policy_target: PolicyTarget
    turn_count: int
    policy_context: Optional[Dict[str, Any]] = None
    user_context: Optional[Dict[str, Any]] = None
    target_condition: Optional[Dict[str, Any]] = None
    slot_id: Optional[str] = None

    @property
    def sequence_id(self) -> str:
        prefix = "ST" if self.turn_type == "single" else "MT"
        return f"{prefix}-{self.sequence_number:04d}"

    @property
    def primary_type(self) -> str:
        return "BENIGN"

    @property
    def attack_tags(self) -> Dict[str, Any]:
        return {
            "injection_type": None,
            "rbac_violation": None,
            "violated_policies": None,
            "mt_pattern": None,
        }

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "id": self.sequence_id,
            "slot_id": self.slot_id,
            "turn_type": self.turn_type,
            "primary_type": self.primary_type,
            "slot_key": self.slot.key,
            "slot_sample_index": self.slot_sample_index,
            "schema_index": self.schema_index,
            "role": self.role,
            "user_context_id": self.user_context_id,
            "seq_label": "BENIGN",
            "turn_count": self.turn_count,
            "policy_target": self.policy_target.to_prompt_context(),
            "policy_context": self.policy_context,
            "user_context": self.user_context,
            "target_condition": self.target_condition,
            "attack_tags": self.attack_tags,
        }


BENIGN_SLOTS: Sequence[BenignSlot] = (
    BenignSlot("single", "student", 150),
    BenignSlot("single", "lecturer", 120),
    BenignSlot("single", "admin", 66),
    BenignSlot("multi", "student", 80),
    BenignSlot("multi", "lecturer", 40),
    BenignSlot("multi", "admin", 20),
)


def make_slot_key(turn_type: str, role: str) -> str:
    return f"{turn_type}:{role}"


def base_slot_counts(turn_type: str = "all", roles: Optional[Sequence[str]] = None) -> Dict[str, int]:
    _validate_turn_type(turn_type)
    role_filter = _normalize_roles(roles)
    return OrderedDict(
        (slot.key, slot.quota)
        for slot in BENIGN_SLOTS
        if turn_type == "all" or slot.turn_type == turn_type
        if role_filter is None or slot.role in role_filter
    )


def expected_total(turn_type: str = "all", roles: Optional[Sequence[str]] = None) -> int:
    return sum(base_slot_counts(turn_type, roles=roles).values())


def scaled_slot_counts(
    total: Optional[int] = None,
    *,
    turn_type: str = "all",
    roles: Optional[Sequence[str]] = None,
) -> Dict[str, int]:
    return scale_counts(base_slot_counts(turn_type, roles=roles), total, label="Benign total")


def scaled_primary_counts(
    total: Optional[int] = None,
    *,
    turn_type: str = "all",
    roles: Optional[Sequence[str]] = None,
) -> Dict[str, int]:
    return {"BENIGN": sum(scaled_slot_counts(total, turn_type=turn_type, roles=roles).values())}


def build_generation_plan(
    schemas: Sequence[str],
    policy_index: PolicyIndex,
    *,
    user_context_index: Optional[UserContextIndex] = None,
    total: Optional[int] = None,
    turn_type: str = "all",
    roles: Optional[Sequence[str]] = None,
    quota_counts: Optional[Dict[str, int]] = None,
) -> List[BenignJob]:
    _validate_turn_type(turn_type)
    if not schemas:
        raise ValueError("At least one schema is required for Benign generation.")
    if user_context_index is None:
        raise ValueError("user_context_index is required for Benign generation.")

    jobs: List[BenignJob] = []
    per_type_sequence = {"single": 0, "multi": 0}
    quotas = quota_counts or scaled_slot_counts(total, turn_type=turn_type, roles=roles)
    slot_by_key = {slot.key: slot for slot in BENIGN_SLOTS}

    for slot_key, quota in quotas.items():
        slot = slot_by_key[slot_key]
        for slot_sample_index in range(quota):
            schema_index = len(jobs) % len(schemas)
            schema = schemas[schema_index]
            targets = policy_index.targets_for(
                role=slot.role,
                rbac_violation=None,
                schema=schema,
            )
            selected_user_context = (
                user_context_index.select(slot.role, slot_sample_index)
                if user_context_index is not None
                else None
            )
            user_context_id = selected_user_context.user_context_id
            target = targets[(slot_sample_index + schema_index) % len(targets)]
            policy_index.validate_refs(target.violated_policies)
            per_type_sequence[slot.turn_type] += 1
            jobs.append(
                BenignJob(
                    sequence_number=per_type_sequence[slot.turn_type],
                    turn_type=slot.turn_type,
                    slot=slot,
                    slot_sample_index=slot_sample_index,
                    schema_index=schema_index,
                    schema=schema,
                    role=slot.role,
                    user_context_id=user_context_id,
                    policy_target=target,
                    turn_count=_resolve_turn_count(slot.turn_type, slot_sample_index),
                    policy_context=build_role_policy_context(
                        policy_index,
                        role=slot.role,
                        user_context_id=user_context_id,
                    ),
                    user_context=selected_user_context.to_prompt_context() if selected_user_context else None,
                )
            )
    return jobs


def summarize_slots(jobs: Iterable[BenignJob]) -> Dict[str, int]:
    counts = {slot.key: 0 for slot in BENIGN_SLOTS}
    for job in jobs:
        counts[job.slot.key] += 1
    return {key: value for key, value in counts.items() if value}


def summarize_turn_types(jobs: Iterable[BenignJob]) -> Dict[str, int]:
    counts = {"single": 0, "multi": 0}
    for job in jobs:
        counts[job.turn_type] += 1
    return {key: value for key, value in counts.items() if value}


def _resolve_turn_count(turn_type: str, slot_sample_index: int) -> int:
    if turn_type == "single":
        return 1
    return 2 + (slot_sample_index % 3)


def _validate_turn_type(turn_type: str) -> None:
    if turn_type not in TURN_TYPE_CHOICES:
        raise ValueError(f"turn_type must be one of {TURN_TYPE_CHOICES}, got {turn_type!r}.")


def _normalize_roles(roles: Optional[Sequence[str]]) -> Optional[Set[str]]:
    if roles is None:
        return None
    normalized = {str(role).strip().lower() for role in roles if str(role).strip()}
    if not normalized:
        return None
    valid_roles = {slot.role for slot in BENIGN_SLOTS}
    invalid = sorted(normalized - valid_roles)
    if invalid:
        raise ValueError(f"Unsupported benign role(s): {invalid}. Valid roles: {sorted(valid_roles)}")
    return normalized
