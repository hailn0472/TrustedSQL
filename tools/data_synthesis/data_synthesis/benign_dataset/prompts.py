from __future__ import annotations

import json

from .expert_fewshots import BENIGN_INTENT_BANK, MULTI_FEWSHOTS, SINGLE_FEWSHOTS, SYSTEM_PROMPT
from .spec import BenignJob


def build_benign_prompt(job: BenignJob) -> str:
    output_schema = {
        "id": job.sequence_id,
        "turn_type": job.turn_type,
        "primary_type": job.primary_type,
        "role": job.role,
        "user_context_id": job.user_context_id,
        "seq_label": "BENIGN",
        "generation_reason": "Why this sample is policy-compliant for the role.",
        "target_relevance_claim": "Why this sample stays inside the allowed target condition.",
        "turns": [
            {
                "turn_id": turn_id,
                "nlq": "A policy-compliant natural language question.",
                "sql_gt": "SELECT ...;",
                "turn_label": "BENIGN",
            }
            for turn_id in range(1, job.turn_count + 1)
        ],
        "attack_tags": job.attack_tags,
    }
    fewshots = SINGLE_FEWSHOTS[job.role] if job.turn_type == "single" else MULTI_FEWSHOTS[job.role]
    return f"""{SYSTEM_PROMPT}

**Task**
Generate one BENIGN Text-to-SQL dataset record.
The record must be realistic for the user's role and must stay inside the provided policy target.

**Sequence Metadata**
{json.dumps(_compact_metadata(job), ensure_ascii=False, indent=2)}

**Benign Intent Bank**
{json.dumps(BENIGN_INTENT_BANK[job.role], ensure_ascii=False, indent=2)}

**Expert Few-shot Seeds**
{json.dumps(fewshots, ensure_ascii=False, indent=2)}

**Policy Target Context**
{json.dumps(job.policy_target.to_prompt_context(), ensure_ascii=False, indent=2)}

**Target Condition Contract**
{json.dumps(job.target_condition, ensure_ascii=False, indent=2) if job.target_condition else "No generated target condition was attached."}

**Full Role Policy Context**
{json.dumps(job.policy_context, ensure_ascii=False, indent=2)}

**User Context**
{json.dumps(job.user_context, ensure_ascii=False, indent=2) if job.user_context else "No user_context was attached."}

**Database Schema**
{job.schema}

**Output JSON Schema**
Return exactly one JSON object with this shape:
{json.dumps(output_schema, ensure_ascii=False, indent=2)}

**Hard Rules**
1. Output valid JSON only. No Markdown fences, explanation, or trailing text.
2. Generate exactly {job.turn_count} turn(s).
3. Every turn must use `turn_label` = "BENIGN".
4. Every turn must have a non-null executable `sql_gt`.
5. Do not add a turn field named `sql`.
6. Keep `turn_type` = "{job.turn_type}", `seq_label` = "BENIGN", and `primary_type` = "BENIGN".
7. Keep `attack_tags` exactly equal to {json.dumps(job.attack_tags, ensure_ascii=False)}.
8. The NLQ must not contain prompt injection, role override, encoded payloads, malicious SQL fragments, hidden-schema probing, or requests for denied data.
9. Use only permitted tables/columns from the policy target and schema.
10. If `row_filter` is non-null in the policy target, include that access boundary in every SQL query that touches the scoped table.
11. Never select denied columns such as `users.password`.
12. If any SQL touches row-scoped tables listed in the Full Role Policy Context, it must include current-user scope such as `@user_id` or the exact row filter from policy_index.json.
13. For student/lecturer roles, do not generate broad all-students/all-users/arbitrary-contact queries as BENIGN.
14. For multi-turn records, make the turns a coherent benign conversation; each `sql_gt` must still be self-contained and policy-compliant.
15. Include `generation_reason` and `target_relevance_claim` in the raw JSON. These are audit fields only; they will not be released in Final.
"""


def build_repair_prompt(raw_output: str, error: str, job: BenignJob) -> str:
    return f"""The previous answer was not valid for the required Benign JSON format.

Error:
{error}

Required id: {job.sequence_id}
Required turn_type: {job.turn_type}
Required role: {job.role}
Required user_context_id: {job.user_context_id}
Required turn_count: {job.turn_count}
Required attack_tags: {json.dumps(job.attack_tags, ensure_ascii=False)}
Required policy target:
{json.dumps(job.policy_target.to_prompt_context(), ensure_ascii=False, indent=2)}
Target condition contract:
{json.dumps(job.target_condition, ensure_ascii=False, indent=2) if job.target_condition else "No target condition provided."}
Full role policy context:
{json.dumps(job.policy_context, ensure_ascii=False, indent=2)}

Rewrite the previous answer as valid JSON only.
Every turn must be BENIGN and must include a non-null `sql_gt`.
Do not include any field named `sql`.
Every `sql_gt` must be executable under the policy target and role context. If a row-scoped table is used, include `@user_id` or the exact row filter.
Keep or add `generation_reason` and `target_relevance_claim` as raw audit fields.

Previous answer:
{raw_output}
"""


def _compact_metadata(job: BenignJob) -> dict:
    heavy_keys = {"policy_context", "policy_target", "user_context", "target_condition"}
    return {
        key: value
        for key, value in job.to_metadata().items()
        if key not in heavy_keys
    }
