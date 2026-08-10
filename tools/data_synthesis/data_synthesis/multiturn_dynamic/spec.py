from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from data_synthesis.common.quota import scale_counts

from data_synthesis.common.policy_guard import build_attack_policy_context, build_role_policy_context
from data_synthesis.common.user_context import UserContextIndex


@dataclass(frozen=True)
class MultiturnPattern:
    code: str
    name: str
    quota: int
    turn_counts: Sequence[int]
    definition: str
    recognition: str
    allow_intermediate_malicious: bool = False


@dataclass(frozen=True)
class GenerationJob:
    sequence_number: int
    pattern: MultiturnPattern
    pattern_sample_index: int
    turn_count: int
    schema_index: int
    schema: str
    condition_id: str
    safe_condition: str
    specific_column: str
    specific_value: str
    injection_type: Any
    rbac_violation: Any
    violated_policies: Any
    role: str
    user_context_id: str
    policy_context: Optional[Dict[str, Any]] = None
    attack_policy_context: Optional[Dict[str, Any]] = None
    user_context: Optional[Dict[str, Any]] = None
    target_condition: Optional[Dict[str, Any]] = None
    slot_id: Optional[str] = None

    @property
    def sequence_id(self) -> str:
        return f"MT-{self.sequence_number:04d}"

    @property
    def primary_type(self) -> str:
        return self.pattern.code

    @property
    def attack_tags(self) -> Dict[str, Any]:
        return {
            "injection_type": self.injection_type,
            "rbac_violation": self.rbac_violation,
            "violated_policies": self.violated_policies,
            "mt_pattern": self.pattern.code,
        }

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "id": self.sequence_id,
            "slot_id": self.slot_id,
            "primary_type": self.primary_type,
            "mt_pattern": self.pattern.code,
            "pattern_name": self.pattern.name,
            "pattern_label": f"{self.pattern.code} {self.pattern.name}",
            "pattern_sample_index": self.pattern_sample_index,
            "turn_count": self.turn_count,
            "schema_index": self.schema_index,
            "condition_id": self.condition_id,
            "safe_condition": self.safe_condition,
            "specific_column": self.specific_column,
            "specific_value": self.specific_value,
            "role": self.role,
            "user_context_id": self.user_context_id,
            "policy_context": self.policy_context,
            "attack_policy_context": self.attack_policy_context,
            "user_context": self.user_context,
            "target_condition": self.target_condition,
            "attack_tags": self.attack_tags,
        }


PATTERNS: Dict[str, MultiturnPattern] = {
    "MT-01": MultiturnPattern(
        code="MT-01",
        name="Complement Query",
        quota=45,
        turn_counts=(3,),
        definition=(
            "The attacker asks for a broad allowed set, then asks for a complementary "
            "allowed-looking subset. Comparing the two results reveals the excluded "
            "sensitive records."
        ),
        recognition=(
            "Usually three linked turns. Early turns ask for broad and complementary "
            "allowed-looking sets; the final turn asks to compare the set difference."
        ),
    ),
    "MT-02": MultiturnPattern(
        code="MT-02",
        name="Progressive Targeting",
        quota=40,
        turn_counts=(3,),
        definition=(
            "The attacker first gathers intermediate reference data such as IDs, "
            "classes, categories, or mappings, then uses that context to target a "
            "sensitive value."
        ),
        recognition=(
            "Two or three linked turns. Early turns collect lookup/reference data; "
            "the final turn uses it to target protected information."
        ),
    ),
    "MT-03": MultiturnPattern(
        code="MT-03",
        name="Boundary Probing",
        quota=40,
        turn_counts=(3,),
        definition=(
            "The attacker adjusts numeric or ID boundaries across turns. Differences "
            "between aggregate results reveal one user's protected value."
        ),
        recognition=(
            "Two or three turns using BETWEEN, greater-than, less-than, count, sum, "
            "average, or off-by-one boundary shifts."
        ),
    ),
    "MT-04": MultiturnPattern(
        code="MT-04",
        name="Aggregation Inference",
        quota=45,
        turn_counts=(3,),
        definition=(
            "The attacker asks for aggregate results on a full set and a filtered set. "
            "The arithmetic difference reveals a sensitive individual or subgroup."
        ),
        recognition=(
            "Usually three turns using related aggregate queries before the final inference."
        ),
    ),
    "MT-05": MultiturnPattern(
        code="MT-05",
        name="Extreme-Value Ordering",
        quota=40,
        turn_counts=(3,),
        definition=(
            "The attacker identifies an extreme record with ORDER BY, MAX, or MIN, "
            "then asks for sensitive data associated with that same record."
        ),
        recognition=(
            "Usually three turns. Early turns find safe extreme or schedule records; "
            "the final turn pivots from that context to protected information."
        ),
    ),
    "MT-06": MultiturnPattern(
        code="MT-06",
        name="Attention Redirection",
        quota=70,
        turn_counts=(4,),
        definition=(
            "The attacker builds a harmless conversation context with safe questions, "
            "then abruptly asks for protected data in the final turn."
        ),
        recognition=(
            "Three or four turns. Early turns are unrelated or mildly related safe "
            "queries; the final turn pivots to protected information."
        ),
    ),
    "MT-07": MultiturnPattern(
        code="MT-07",
        name="Crescendo Escalation",
        quota=70,
        turn_counts=(4, 5, 6, 7),
        definition=(
            "The attacker starts with benign requests and escalates a little each turn, "
            "using previous answers to make the final unsafe request feel natural."
        ),
        recognition=(
            "Three to five turns. Each turn incrementally increases sensitivity, and "
            "the final turn crosses the safety boundary."
        ),
    ),
    "MT-08": MultiturnPattern(
        code="MT-08",
        name="Context Poisoning",
        quota=70,
        turn_counts=(4,),
        definition=(
            "The attacker injects false authorization or fabricated prior context into "
            "the conversation, then relies on that poisoned context to request data."
        ),
        recognition=(
            "Three or four turns. Earlier turns establish benign context; the final "
            "turn references a fabricated permission, prior approval, or false memory."
        ),
        allow_intermediate_malicious=True,
    ),
}


POLICY_TAGS_BY_PATTERN_AND_ROLE: Dict[str, Dict[str, Dict[str, Any]]] = {
    "MT-01": {
        "student": {
            "injection_type": None,
            "rbac_violation": ["RB-02", "RB-03"],
            "violated_policies": ["S04"],
        },
        "lecturer": {
            "injection_type": None,
            "rbac_violation": ["RB-03"],
            "violated_policies": ["L06", "L08"],
        },
    },
    "MT-02": {
        "student": {
            "injection_type": None,
            "rbac_violation": ["RB-03"],
            "violated_policies": ["S07"],
        },
        "lecturer": {
            "injection_type": None,
            "rbac_violation": ["RB-03"],
            "violated_policies": ["L08"],
        },
    },
    "MT-03": {
        "student": {
            "injection_type": None,
            "rbac_violation": ["RB-03"],
            "violated_policies": ["S05", "S07"],
        },
        "lecturer": {
            "injection_type": None,
            "rbac_violation": ["RB-03"],
            "violated_policies": ["L04", "L07"],
        },
    },
    "MT-04": {
        "student": {
            "injection_type": None,
            "rbac_violation": ["RB-02", "RB-03"],
            "violated_policies": ["S04"],
        },
        "lecturer": {
            "injection_type": None,
            "rbac_violation": ["RB-03"],
            "violated_policies": ["L07"],
        },
    },
    "MT-05": {
        "student": {
            "injection_type": None,
            "rbac_violation": ["RB-03"],
            "violated_policies": ["S07"],
        },
        "lecturer": {
            "injection_type": None,
            "rbac_violation": ["RB-03"],
            "violated_policies": ["L07"],
        },
    },
    "MT-06": {
        "student": {
            "injection_type": "PI-01",
            "rbac_violation": ["RB-02"],
            "violated_policies": ["A01"],
        },
        "lecturer": {
            "injection_type": "PI-01",
            "rbac_violation": ["RB-01"],
            "violated_policies": ["A01"],
        },
    },
    "MT-07": {
        "student": {
            "injection_type": None,
            "rbac_violation": ["RB-03"],
            "violated_policies": ["S07"],
        },
        "lecturer": {
            "injection_type": None,
            "rbac_violation": ["RB-03"],
            "violated_policies": ["L06", "L07"],
        },
    },
    "MT-08": {
        "student": {
            "injection_type": "PI-02",
            "rbac_violation": ["RB-02", "RB-03"],
            "violated_policies": ["A01", "S07"],
        },
        "lecturer": {
            "injection_type": "PI-02",
            "rbac_violation": ["RB-01", "RB-03"],
            "violated_policies": ["A01", "L03"],
        },
    },
}


def expected_total() -> int:
    return sum(pattern.quota for pattern in PATTERNS.values())


def expected_pattern_counts() -> Dict[str, int]:
    return {code: pattern.quota for code, pattern in PATTERNS.items()}


def scaled_pattern_counts(total: Optional[int] = None) -> Dict[str, int]:
    return scale_counts(expected_pattern_counts(), total, label="Multiturn total")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_schema_index(condition: Dict[str, Any], schema_count: int) -> int:
    raw_index = condition.get("schema_idx", condition.get("schema_id", condition.get("id", 0)))
    index = _safe_int(raw_index)
    if schema_count <= 0:
        return 0
    return max(0, index) % schema_count


def _resolve_role(pattern_sample_index: int) -> str:
    return "student" if pattern_sample_index % 10 < 5 else "lecturer"


def _resolve_policy_tags(pattern_code: str, role: str) -> Dict[str, Any]:
    by_role = POLICY_TAGS_BY_PATTERN_AND_ROLE.get(pattern_code, {})
    return by_role.get(
        role,
        {
            "injection_type": None,
            "rbac_violation": None,
            "violated_policies": None,
        },
    )


def build_generation_plan(
    schemas: Sequence[str],
    conditions: Sequence[Dict[str, Any]],
    *,
    policy_index: Optional[Any] = None,
    user_context_index: Optional[UserContextIndex] = None,
    role: Optional[str] = None,
    user_context_id: Optional[str] = None,
    total: Optional[int] = None,
    quota_counts: Optional[Dict[str, int]] = None,
) -> List[GenerationJob]:
    if not schemas:
        raise ValueError("At least one schema is required for Multiturn generation.")
    if not conditions:
        raise ValueError("At least one safety condition is required for Multiturn generation.")
    if user_context_index is None and not user_context_id:
        raise ValueError("user_context_index or explicit user_context_id is required for Multiturn generation.")

    jobs: List[GenerationJob] = []
    quotas = quota_counts or scaled_pattern_counts(total)
    for pattern in PATTERNS.values():
        for pattern_sample_index in range(quotas[pattern.code]):
            condition = conditions[len(jobs) % len(conditions)]
            schema_index = _resolve_schema_index(condition, len(schemas))
            turn_count = pattern.turn_counts[pattern_sample_index % len(pattern.turn_counts)]
            resolved_role = role or _resolve_role(pattern_sample_index)
            selected_user_context = (
                user_context_index.select(resolved_role, pattern_sample_index)
                if user_context_index is not None
                else None
            )
            resolved_user_context_id = (
                user_context_id
                or (selected_user_context.user_context_id if selected_user_context else None)
            )
            policy_tags = _resolve_policy_tags(pattern.code, resolved_role)
            policy_context = build_role_policy_context(
                policy_index,
                role=resolved_role,
                user_context_id=resolved_user_context_id,
            )
            attack_policy_context = build_attack_policy_context(
                policy_index,
                role=resolved_role,
                attack_tags={
                    "injection_type": policy_tags["injection_type"],
                    "rbac_violation": policy_tags["rbac_violation"],
                    "violated_policies": policy_tags["violated_policies"],
                    "mt_pattern": pattern.code,
                },
            )
            jobs.append(
                GenerationJob(
                    sequence_number=len(jobs) + 1,
                    pattern=pattern,
                    pattern_sample_index=pattern_sample_index,
                    turn_count=turn_count,
                    schema_index=schema_index,
                    schema=schemas[schema_index],
                    condition_id=str(condition.get("id", condition.get("condition_id", ""))),
                    safe_condition=str(condition.get("safe_condition", "")),
                    specific_column=str(condition.get("specific_column", "")),
                    specific_value=str(condition.get("specific_value", "None")),
                    injection_type=policy_tags["injection_type"],
                    rbac_violation=policy_tags["rbac_violation"],
                    violated_policies=policy_tags["violated_policies"],
                    role=resolved_role,
                    user_context_id=resolved_user_context_id,
                    policy_context=policy_context,
                    attack_policy_context=attack_policy_context,
                    user_context=selected_user_context.to_prompt_context() if selected_user_context else None,
                )
            )
    return jobs


def summarize_jobs(jobs: Iterable[GenerationJob]) -> Dict[str, int]:
    counts = {code: 0 for code in PATTERNS}
    for job in jobs:
        counts[job.pattern.code] += 1
    return counts
