# TrustedSQL

TrustedSQL is a policy-grounded Text-to-SQL method evaluated on utility, refusal-based security, multi-turn security, and path-aware runtime performance.

Runtime pipeline:

```text
C0 -> M1 -> M2 -> M3 -> M4 -> M5 -> M6 -> M7 -> X1
```

Runtime modules never receive `sql_gt`, expected results, dataset labels, attack tags, training labels, or evaluator evidence. Ground-truth SQL is loaded only by `benchmark_eval` after runtime.

Reviewer-facing documentation:

- `docs/architecture_to_code_mapping.md` maps `C0/M1/.../X1` to source files.
- `docs/dataset_and_artifact_policy.md` defines evaluation benchmark, smoke-test subset, GNN training dataset, human-review records, and API-run reproducibility expectations.

## Project Layout

```text
configs/                    Runtime, provider, method, dataset, and GNN configuration
data/benchmark/             Versioned evaluation benchmark records
database/                   PostgreSQL schema bootstrap and benchmark snapshot manifest
resources/                  Policy and database schema
artifacts/                  Runtime model and compact-schema artifacts
src/trustedsql/             Runtime method
src/architecture_baselines/ Copied old architecture runtime for baseline experiments
src/trustedsql_gnn/         GNN runtime, training code, and model utilities
src/benchmark_eval/         Shared automatic scientific evaluation
evaluation/                 Protocol, notebook, and evaluation entrypoint
tools/                      Preprocessing, validation, and maintenance utilities
tests/                      Runtime, GNN, and evaluation regression tests
outputs/                    Generated runs; excluded from Git
```

## Setup

```powershell
python -m pip install -e ".[train,dev]"
python tools/preprocessing/fetch_text_encoder.py
```

The text encoder is pinned by model ID, revision, and SHA-256 in `artifacts/models/intent_gnn/v1/encoder_manifest.json`. Its verified local files are stored under the Git-ignored `artifacts/text_encoder/` directory; this is a required model dependency, not a runtime result cache.

`requirements.txt` and `pyproject.toml` keep lower bounds for normal
installation, including the Vertex AI RAG demo and corpus-ingestion dependencies.
`requirements-lock.txt` records the direct package versions used
for the public source-release smoke tests. For archival reproduction, regenerate
a fully transitive lock file in the target environment and store it with the
experiment artifacts.

## Database State

`database/script.sql` contains the public schema bootstrap. It does not include
table rows. Full utility reproduction requires the benchmark database seed or
snapshot used by the experiment runs because execution-equivalence metrics
depend on actual row values.

If rows can be public, place them at `database/seed/benchmark_seed.sql` and
record checksums in `database/SHA256SUMS`. If rows are distributed separately,
complete `database/benchmark_snapshot_manifest.example.json` with the archive
URL or DOI, SHA-256 checksum, PostgreSQL version, snapshot date, restore command,
and runtime database role.

## Runtime

The default method config enables the full TrustedSQL pipeline. The verified M5 authorization guide is part of the default M6 runtime context, not a separate setting.

```powershell
python run_trustedsql.py run --run-id <run_id>
python run_trustedsql.py run --run-id <run_id> --settings full_trustedsql
python run_trustedsql.py run --run-id <run_id> --rerun-api-429
```

Runtime artifacts are written to:

```text
outputs/runs/<run_id>/
  run_manifest.json
  runtime/
    raw_turn_outputs.jsonl
    module_events.jsonl
    module_logs/
    turn_runtime.csv
    checkpoint.json
    runtime_error_summary.json
```

## Automatic Evaluation

```powershell
python evaluation/run_evaluation.py evaluate --run-id <run_id>
```

The evaluator re-executes every ground-truth SQL against the configured fixed database snapshot and writes evidence under `evaluation/evidence/` and canonical JSON/CSV metrics under `evaluation/metrics/`. It validates runtime completeness and benchmark dataset fingerprints before computing metrics; runtime-only resources such as prompts and GNN assets are retained in the run manifest for provenance. There is no runtime/evaluation result cache, human-review phase, or adjudication phase.

Metrics:

- Utility: ST-EX, ST-Soft-F1, MT-Turn-EX, MT-Turn-Soft-F1, MT-IEX.
- Single-turn security: ASR, Refusal Recall, Refusal Precision, Refusal F1, Over-refusal Rate.
- Multi-turn security: Prefix-RS, Sequence ASR, Sequence Refusal Recall, Conditional ASR, Conditional Refusal Recall, Valid Secure Sequence Rate.
- Performance: mean latency, p95 latency, input tokens, and output tokens on three protocol-defined paths.

`ERROR` is always separate from `DENY` and never counts as a successful refusal.

## Experiment Runner

Paper-level experiments are defined as YAML files under `configs/experiments/` and executed through one runner:

```powershell
python evaluation/run_experiment.py --experiment configs/experiments/ex1_main_full_results.yaml --phase all
python evaluation/run_experiment.py --experiment configs/experiments/ex2_baseline_comparison.yaml --phase all
python evaluation/run_experiment.py --experiment configs/experiments/ex3_ablation.yaml --phase all
```

The runner materializes resolved per-run configs under `outputs/experiments/<experiment_run_id>/resolved_configs/`, runs either `trustedsql` or `architecture_baselines` depending on `runtime_kind`, evaluates with `benchmark_eval`, and writes `experiment_manifest.json`, `run_index.csv`, and aggregate metrics.

## GNN Lifecycle

The repository is packaged for runtime, evaluation, and reproducible
Conversation-Risk Model development. It includes the promoted runtime
checkpoint and the active GNN model-development train/validation/internal-test
corpus under `data/training/intent_gnn/v1/`. Historical candidate data,
archived diagnostic splits, large reports, and candidate checkpoints belong in
the external thesis artifact package.

See `docs/gnn_training_guide.md` for the full retraining workflow.

```powershell
trustedsql-gnn inspect
trustedsql-gnn prepare --run-id <gnn_run>
trustedsql-gnn train --run-id <gnn_run> --device cuda
trustedsql-gnn evaluate --run-id <gnn_run> --checkpoint <checkpoint>
trustedsql-gnn promote --checkpoint <checkpoint> --confirmed-by <name> --evaluation-report <report>
```

Only `promote` writes the runtime checkpoint. Candidate checkpoints, materialized releases, reports, caches, and logs remain under `outputs/training/`.

## Integrity Checks

```powershell
python tools/validation/check_repository_artifacts.py
python -m pytest
```

`audit_training_benchmark_leakage.py` is kept for offline training-artifact
audits. It checks the active `data/training/intent_gnn/v1/` splits against the
benchmark and is separate from the default runtime smoke checks.
