# Policy-Grounded GNN Dataset

Step `G` separates behavioral taxonomy from authorization ground truth.

## Final DataTrain v1 Package

The promoted GNN model-development corpus used by TrustedSQL is the frozen
DataTrain v1 package under `data/training/intent_gnn/v1`. The active package
contains three partitions:

- `train.jsonl`: parameter optimization.
- `validation.jsonl`: early stopping and checkpoint selection.
- `test.jsonl`: post-training component evaluation.

Its record ID families are:

- `FULLMT-*`: full multi-turn behavioral conversations.
- `ANCHOR-*`: benign single-turn anchor conversations.
- `0107-*`: audited 0107 augmentation rows.
- `AUGMT-*`: audited augmented multi-turn validation rows.

Use `datatrain_v1_builder.py` with `config/datatrain_final_v1.example.json` to verify
and repackage this final corpus:

```powershell
cd tools/data_synthesis
python -m data_synthesis.gnn_dataset.datatrain_v1_builder --config config/datatrain_final_v1.example.json
```

The builder copies the frozen active splits, validates counts, checks duplicate
conversation IDs, validates required schema and label fields, computes SHA-256
hashes, and rejects unexpected conversation ID families such as `EXEC-*`.

`execution_v2.py` remains a procedural development generator. Its `EXEC-*`
records are useful for controlled generator experiments, but they are not the
promoted corpus used for the final TrustedSQL Conversation-Risk Model
checkpoint.

## Inputs

- `pattern/pattern_bank_v2.policy_grounded.json`: 20 behavioral pattern constraints.
- `Policy 6-4/ddl_upgrade.txt`: tables, columns, and foreign keys.
- `Policy 6-4/policy_index.json`: role, scope, row filter, and violation boundaries.
- `Policy 6-4/role_access_matrix.json`: permitted table columns by role.
- Student and lecturer user-context JSON files. A directory is accepted; the current recommended input is `--user-context-file "User Context"`.

Admin policy is validated but admin samples are not generated until a grounded admin context exists. Runtime tables such as conversation sessions and memory storage are excluded from targets.

## Flow

```text
compile policy -> validate pattern compatibility -> create concrete slots
-> generate candidate -> deterministic SQL/policy gate -> LLM label
-> dynamic graph evidence -> quota/duplicate/release gate
-> final JSON + graph JSONL + user-group split + audit artifacts
```

The slot is the intended label. LABEL never changes it. A candidate that does not exhibit the primary violation is rejected and the missing slot is refilled.

`security_boundary` in Final is the compiled slot snapshot. `graph_evidence` is derived from the released candidate and is not copied from Pattern Bank v1.

## Training Contract

- Use `GNN_Graphs.jsonl` as encoder input.
- Join labels from `GNN_Targets.jsonl` by `graph_id`.
- Never use `GNN_Audit_Graphs.jsonl` as model input.
- Check `GNN_Protocol_Status.json` before reporting metrics. A protocol marked `UNAVAILABLE` has no valid metric-ready split.
- Protocol assignments are saved before Gemini generation in `GNN_Protocol_Assignments.json`.

The feature graph excludes labels, pattern IDs, policy IDs, RBAC IDs, turn labels, malicious-reason markers, and policy-verdict edges.

## Command

```powershell
python quick_start.py --steps G --gnn-total 200 --gnn-policy-dir "Policy 6-4" --pattern-bank pattern/pattern_bank_v2.policy_grounded.json --user-context-file "User Context"
```

The default model is `gemini-2.5-flash-lite`. Use `--max-workers`, `--overgenerate-buffer`, and `--max-refill-rounds` to control throughput and release completion.

## Scale Gate

Gemini generates only `turn_contents[].nlq`. Code owns every structural field,
label, attack tag, and SQL value. BENIGN SQL is compiled deterministically from
DDL, the per-turn policy target, role, and authenticated bindings. Unsafe SQL is
always forced to `null`.

Release quota is keyed by pattern, target, and protocol split. Surface variants
are coverage constraints rather than quota buckets. Each slot also receives a
scale-safe variation plan with a unique ID, concrete context anchor, requested
attribute, wording family, and deterministic lexical seed.

Before generating 5,000 records, run a 20-record full-taxonomy preflight and a
100-record pilot. Totals of 5,000 or more require:

```powershell
--gnn-preflight-report <pilot-output>/GNN_Preflight_Report.json
```

The report must contain `ALLOW_5000=true` and hashes matching the current
compiler, prompt, protocol, canonicalizer, and validator source. Any critical
code change invalidates the report and requires a new pilot. The
`--gnn-force-large-run` option is a research override and should not be used for
a release run.

Generation and LABEL both checkpoint model results by batch index. Re-running
the same output directory resumes missing requests only. LABEL receives a
compact slot/policy contract rather than the full user-context payload.

## Scientific Audit

- The release manifest stores SHA-256 hashes for policy, DDL, matrix, pattern bank, and prompts.
- Coverage is reported by pattern, role, policy, scope, table, and violation.
- Train/validation/test are grouped by authenticated user context. Empty splits are reported when too few distinct users exist.
- The stratified workbook samples 20% per pattern, with at least one record per represented pattern.
- Generated, rejected, repaired, refilled, usage, and cost records remain available for audit.
