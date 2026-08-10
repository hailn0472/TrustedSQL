# Singleturn Prompt Injection Generator

Generates malicious single-turn Prompt Injection records for `PI-01..PI-06` through:

```text
EXPERT_FEWSHOT -> TARGET_CONDITION -> SLOT -> GENERATE -> LABEL -> VALIDATE/VERIFY -> FINALIZE
```

## Inputs

- `schemas.json`
- `policy_index.json`
- `--user-context-file student.optimized.json,lecture.optimized.json`

This pipeline does not use Step `A`. It derives role, user context, RBAC target, policy refs, and prompt context from `policy_index.json` plus user-context files.
Optimized context is consumed directly from `identity`, `rbac_policy_signal`, `entities`, `relationships`, and `column_profile`.

## Quota

Base total `315`, scaled at PI/RBAC slot level:

- `PI-01:null`: 30
- `PI-01:RB-02`: 35
- `PI-01:RB-03`: 25
- `PI-02:null`: 30
- `PI-02:RB-01`: 25
- `PI-03:null`: 30
- `PI-03:RB-01`: 25
- `PI-04:null`: 35
- `PI-04:RB-02`: 20
- `PI-05:null`: 30
- `PI-06:null`: 30

`--singleturn-pi-total` scales the release quota. `--overgenerate-buffer` controls extra pre-label candidates.

## Policy Targeting

- `RB-01`: forbidden/default-deny table for current role.
- `RB-02`: denied columns from `policy_index.json`.
- `RB-03`: row-scope violation from policies with `row_filter`.
- `null`: injection technique is present, but target stays permitted/public or diagnostic.

`violated_policies` comes from policy refs in `policy_index.json`; generated text is not trusted as the source of truth.

## Validation

- LABEL verifies `matches_slot`, `target_relevant`, `policy_aligned`, and confidence.
- Code validates PI target alignment before canonicalization.
- `RBAC-null` cannot mention forbidden resources such as `users.password`, `rolepermission`, all users, all students, or another student's private records.
- Final JSON keeps only one malicious turn with `sql_gt = null`.

## Files

- `spec.py`: PI types, target slots, quota scaling.
- `expert_fewshots.py`: PI system prompt, few-shot seeds, phrase banks.
- `prompts.py`: GENERATE and repair prompts.
- `canonicalize.py`: raw validation and release schema.
- `generator.py`: family orchestration.

## Outputs

- `SingleturnPI_TargetConditions.json`
- `SingleturnPI_Slots.json`
- `SingleturnPI_Prompts.json`
- `SingleturnPI_Raw.json`
- `SingleturnPI_Label_Report.json`
- `SingleturnPI_Rejected.json`
- `SingleturnPI_Verify_Report.json`
- `SingleturnPI_Human_Verify.csv`
- `SingleturnPI_Human_Verify.xlsx`
- `SingleturnPI_Validation.json`
- `SingleturnPI_Final.json`
- `SingleturnPI_Summary.xlsx`
- `SingleturnPI_Usage.json/csv`
- `SingleturnPI_TargetCondition_Usage.json/csv`

## Command

```powershell
python quick_start.py --mode custom --steps P --singleturn-pi-total 158 --max-workers 4 --request-delay 0.25 --schemas-file schemas.json --policy-file policy_index.json --user-context-file student.optimized.json,lecture.optimized.json
```
