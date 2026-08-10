# Dataset and Artifact Policy

This repository is organized as a runtime and evaluation repository. Large or historical training materials can live in a separate thesis artifact package, but every public checkpoint and reported experiment should still have enough provenance for a reviewer to verify what was run.

## Dataset Terminology

Use these names consistently in the paper and documentation.

| Term | Meaning | Repository location |
|---|---|---|
| Evaluation benchmark | Final dataset used to report EX1, EX2, and EX3 metrics. | `data/benchmark/v3/full/` for the final v3 protocol. Older benchmark snapshots, if retained, belong in supplementary or archival storage rather than the active source tree. |
| Smoke-test subset | Small operational subset used only to check that the pipeline runs. It is not a paper-result dataset. | Not committed as a separate dataset. Use `--max-samples` against the evaluation benchmark profile. |
| GNN model-development corpus | Separate data used to train, select, and internally evaluate the M2 Intent GNN. | Active promoted train/validation/test splits are included under `data/training/intent_gnn/v1/`. Historical candidates, hard/locked diagnostics, curation logs, and large reports belong in the supplementary artifact package. |
| Human-review records | Review/audit evidence for dataset construction. | External thesis artifact package or supplementary archive, unless the final paper requires publishing the records directly. |

Avoid using "sample dataset" unless it is explicitly qualified as a smoke-test subset or a training sample. The term is otherwise ambiguous because it can refer to multiple artifact families.

## Public Runtime Repository

The runtime repository should include:

- Runtime entry points and all TrustedSQL modules.
- Policy, schema, SQL safety, and provider adapter code without credentials.
- Architecture baselines used for EX2.
- Evaluation benchmark used for paper metrics, if license and privacy constraints allow it.
- Evaluator and metric implementation.
- EX1, EX2, and EX3 configurations.
- Promoted GNN checkpoint and inference/preprocessing code.
- Active GNN model-development corpus under `data/training/intent_gnn/v1/`.
- GNN checkpoint provenance files, including model card, manifest, encoder manifest, training config origin, and checksums.
- Tests and repository validation tools.
- Per-case and aggregate result artifacts only when intentionally publishing a frozen result package.
- Scripts or notebooks used to recreate paper tables and figures.
- Environment metadata such as `requirements.txt`, `pyproject.toml`, and optional lock files.

## External or Supplementary Archive

The following may be stored outside the runtime repository, but should have a stable path, DOI, release URL, or archive reference in the thesis/paper package:

- Historical GNN training candidates, archived diagnostic splits, and superseded split files.
- Raw AI-generated dataset candidates.
- Human-review audit records.
- Intermediate checkpoints.
- Large execution logs.
- Full prompt-generation history.
- Large model dependencies and caches.
- Historical training reports that are not required by runtime loading.

## GNN Checkpoint Provenance

The promoted M2 checkpoint is a runtime dependency, but it should not be treated as an unexplained binary blob. The runtime model directory should provide:

```text
artifacts/models/intent_gnn/v1/
  best.pt
  MODEL_CARD.md
  README.md
  model_manifest.json
  encoder_manifest.json
  training_config.origin.json
  SHA256SUMS
```

The model card and manifest should state the architecture, graph representation, label definition, training configuration, split identity, seed, framework versions where available, best-checkpoint metric, dataset fingerprints when public, checkpoint SHA-256, held-out metrics, and where to find excluded training artifacts.

## API-Based LLM Reproducibility

Runs that use Gemini, Vertex, OpenAI-compatible endpoints, or other hosted LLM APIs are not perfectly reproducible because the provider may update model implementations behind a stable model name. For each paper run, archive the resolved runtime artifacts under `outputs/runs/<run_id>/` and experiment artifacts under `outputs/experiments/<experiment_run_id>/`.

At minimum, preserve:

- `run_manifest.json`.
- Resolved run configuration.
- Provider name, model ID, endpoint or region, and model version if the provider exposes one.
- Execution date.
- Temperature, top-p, max output tokens, seed if supported, retry policy, and timeout.
- Prompt template fingerprints.
- Benchmark, policy, schema, and GNN artifact fingerprints.
- Code commit hash.
- Runtime outputs, module events, module logs, errors, and token usage.
- Evaluation evidence, per-case metrics, aggregate metrics, and SHA-256 checksums for the frozen result package.

Recommended publication archive:

```text
outputs/runs/<run_id>/
  run_manifest.json
  runtime/
    raw_turn_outputs.jsonl
    module_events.jsonl
    module_logs/
    turn_runtime.csv
    runtime_error_summary.json
  evaluation/
    evidence/
    metrics/

outputs/experiments/<experiment_run_id>/
  experiment_manifest.json
  run_index.csv
  resolved_configs/
  aggregate_metrics/
```

If raw provider response bodies are retained, remove secrets and sensitive data before publication. If only parsed/normalized module outputs are retained, state that limitation explicitly in the paper artifact README.
