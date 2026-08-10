# Multiturn Dynamic Generator

Generates malicious multi-turn records for `MT-01..MT-08` through the shared pipeline:

```text
EXPERT_FEWSHOT -> TARGET_CONDITION -> SLOT -> GENERATE -> LABEL -> VALIDATE/VERIFY -> FINALIZE
```

## Inputs

- `schemas.json`
- `policy_index.json`
- `A_safety_conditions.json`
- `--user-context-file student.optimized.json,lecture.optimized.json,student_vclassmates.optimized.json,lecture_vstudents.optimized.json`

Step `A` provides safety conditions. `policy_index.json` provides RBAC/RLS boundaries. User context provides the concrete current user, class/course scope, and role-specific facts inserted into prompts.
Optimized context is consumed directly from `identity`, `rbac_policy_signal`, `entities`, `relationships`, and `column_profile`.
Multiple files with the same `role + user_context_id` are merged, so `student_vclassmates` and `lecture_vstudents` enrich the base user context instead of becoming separate users.

## Quota

Base total `420`:

- `MT-01 Complement Query`: 45
- `MT-02 Progressive Targeting`: 40
- `MT-03 Boundary Probing`: 40
- `MT-04 Aggregation Inference`: 45
- `MT-05 Extreme-Value Ordering`: 40
- `MT-06 Attention Redirection`: 70
- `MT-07 Crescendo Escalation`: 70
- `MT-08 Context Poisoning`: 70

`--multiturn-total` scales the release quota. `--overgenerate-buffer` generates extra candidates per pattern before LABEL/VERIFY; default is `0.15`.
If the release gate is short on quota or target-condition coverage, targeted refill runs only the missing pattern/target instead of regenerating the full dataset.

## Validation

- Slot label is intended label; LABEL only verifies.
- Mismatch, low confidence, off-target final turn, or policy misalignment is rejected.
- Selection reserves quota for target-condition coverage before filling generic pattern quota.
- `Multiturn_Coverage_Report.json` checks coverage for MT pattern, role, user context, safety condition, target condition, RBAC tags, and policy refs.
- Every non-final BENIGN turn is checked in code.
- BENIGN SQL must be `SELECT`.
- BENIGN SQL touching row-scoped tables must include `@user_id` or current-user scope.
- Student/lecturer BENIGN turns must not ask for broad all-students/all-users/contact/credential data.
- Final JSON keeps only release schema; raw reason and label evidence stay in audit files.

## Files

- `spec.py`: MT definitions, quota, slot construction.
- `expert_fewshots.py`: Multiturn system prompt, strategy banks, and expert few-shot sketches for `MT-01..MT-08`.
- `prompts.py`: GENERATE and repair prompts using system prompt, few-shot sketches, schema, safety condition, policy, user context, and target condition.
- `canonicalize.py`: raw validation, final schema, summary export.
- `generator.py`: family orchestration using common target-condition, generation, label, and verify phases.

## Outputs

- `Multiturn_TargetConditions.json`
- `Multiturn_Slots.json`
- `Multiturn_Prompts.json`
- `Multiturn_Raw.json`
- `Multiturn_Label_Report.json`
- `Multiturn_Rejected.json`
- `Multiturn_Verify_Report.json`
- `Multiturn_Coverage_Report.json`
- `Multiturn_Human_Verify.csv`
- `Multiturn_Human_Verify.xlsx`
- `Multiturn_Validation.json`
- `Multiturn_Final.json`
- `Multiturn_Summary.xlsx`
- `Multiturn_Usage.json/csv`
- `Multiturn_TargetCondition_Usage.json/csv`

## Command

```powershell
python quick_start.py --mode custom --steps AM --multiturn-total 420 --max-workers 4 --request-delay 0.25 --schemas-file schemas.json --policy-file policy_index.json --user-context-file student.optimized.json,lecture.optimized.json,student_vclassmates.optimized.json,lecture_vstudents.optimized.json
```
