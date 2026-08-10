# TrustedSQL Project Structure

The repository separates runtime, model lifecycle code, scientific evaluation, canonical benchmark data, and generated artifacts.

```text
src/trustedsql/       Production runtime and paper-level modules
src/architecture_baselines/
                      Copied old architecture runtime used for baseline experiments
src/trustedsql_gnn/   Shared graph/model code, inference, training code, and model diagnostics
src/benchmark_eval/   Shared automatic utility, security, and performance metrics
database/             Schema-only PostgreSQL bootstrap script
```

Policy and schema are immutable runtime resources under `resources/`. `database/script.sql` is a schema-only bootstrap script for creating an empty PostgreSQL database with the same core tables. Evaluation benchmark snapshots are under `data/benchmark/`. The final paper protocol uses the evaluation benchmark rather than a committed smoke-test subset. The active GNN model-development train, validation, and internal-test splits are under `data/training/intent_gnn/v1/`; human-review records, historical candidates, archived diagnostic splits, and large training reports belong in the external thesis artifact package when needed. Promoted runtime artifacts are under `artifacts/`; generated outputs are excluded from Git.

Dataset and artifact terms are defined in `docs/dataset_and_artifact_policy.md`. The paper-to-code mapping for `C0/M1/.../X1` is defined in `docs/architecture_to_code_mapping.md`.

Runtime runs are stored under `outputs/runs/<run_id>/`. Experiment-level manifests and resolved per-run configs are stored under `outputs/experiments/<experiment_run_id>/`. Training runs are stored under `outputs/training/<run_id>/`. A unified `run_manifest.json` records the resolved runtime configuration, benchmark selection, runtime kind, and fingerprints of the resources actually used. Evaluation invocations are appended separately.

The evaluator has no review workflow. It validates runtime completeness and benchmark dataset identity, executes every ground-truth SQL after runtime without a result cache, and emits machine-readable evidence and metrics under the run's `evaluation/` directory. Runtime-only resources remain in the runtime snapshot for provenance but are not used to reject post-runtime metric computation.
