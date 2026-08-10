# Benign Dataset Generator

Generates benign-only Text-to-SQL records through:

```text
EXPERT_FEWSHOT -> TARGET_CONDITION -> SLOT -> GENERATE -> LABEL -> VALIDATE/VERIFY -> FINALIZE
```

Benign uses allowed policy targets, not protected attack targets. It provides the safe class for training/evaluation.

## Inputs

- `schemas.json`
- `policy_index.json`
- `--user-context-file` with every role needed by the selected benign quota

For the full default benign plan, provide student, lecturer, and admin contexts. If the scaled quota does not include a role, that role is not required.
Optimized context is consumed directly from `identity`, `rbac_policy_signal`, `entities`, `relationships`, and `column_profile`.

## Quota

Base total `476`:

- `single:student`: 150
- `single:lecturer`: 120
- `single:admin`: 66
- `multi:student`: 80
- `multi:lecturer`: 40
- `multi:admin`: 20

Use `--benign-total` to scale. Use `--benign-turn-type single|multi|all` to restrict the subset.

## Validation

- Every turn must be `BENIGN`.
- Every `sql_gt` must be non-null and `SELECT`.
- Row-scoped tables must include `@user_id` or equivalent current-user scope.
- Student/lecturer broad all-users/all-students/contact/credential requests are rejected.
- Denied columns such as `users.password` are rejected.
- LABEL verifies the generated NLQ/SQL stays inside the allowed target condition.
- Exact and near duplicates are filtered before Final.

## Output Schema

- `id`: `ST-0001...` for single-turn, `MT-0001...` for multi-turn
- `turn_type`: `single` or `multi`
- `primary_type`: `BENIGN`
- `role`
- `user_context_id`
- `turns`
- `seq_label`: `BENIGN`
- `attack_tags`: all fields `null`

Raw audit fields such as `generation_reason`, `target_relevance_claim`, and label evidence are not released in Final.

## Files

- `spec.py`: benign slots, quota scaling, role/user-context assignment.
- `expert_fewshots.py`: benign intent banks and examples.
- `prompts.py`: GENERATE and repair prompts.
- `canonicalize.py`: raw validation and release schema.
- `generator.py`: family orchestration.

## Outputs

- `Benign_TargetConditions.json`
- `Benign_Slots.json`
- `Benign_Prompts.json`
- `Benign_Raw.json`
- `Benign_Label_Report.json`
- `Benign_Rejected.json`
- `Benign_Verify_Report.json`
- `Benign_Human_Verify.csv`
- `Benign_Human_Verify.xlsx`
- `Benign_Validation.json`
- `Benign_Final.json`
- `Benign_Summary.xlsx`
- `Benign_Usage.json/csv`
- `Benign_TargetCondition_Usage.json/csv`

## Command

```powershell
python quick_start.py --mode custom --steps B --benign-total 100 --benign-turn-type all --schemas-file schemas.json --policy-file policy_index.json --user-context-file student.optimized.json,lecture.optimized.json,admin.optimized.json
```
