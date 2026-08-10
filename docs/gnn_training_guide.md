# GNN Training Guide

This repository is packaged for runtime, evaluation, and reproducible
Conversation-Risk Model development. It keeps the GNN training source code,
configuration, promoted checkpoint, and active train/validation/internal-test
corpus. Historical candidate data, archived diagnostic splits, large reports,
and candidate checkpoints remain in the external thesis artifact package.

## What Is Included

```text
src/trustedsql_gnn/              GNN source code
configs/gnn/                    GNN taxonomy, graph, and training config
artifacts/models/intent_gnn/v1/  Promoted runtime checkpoint and manifests
data/training/intent_gnn/v1/      Active model-development corpus
evaluation/notebooks/train_intent_gnn_vi.ipynb
```

Runtime uses:

```text
artifacts/models/intent_gnn/v1/best.pt
artifacts/models/intent_gnn/v1/encoder_manifest.json
artifacts/text_encoder/all-MiniLM-L6-v2/
configs/gnn/
```

## Training Data Layout

The active promoted corpus is already expected at:

```text
data/training/intent_gnn/v1/
  train.jsonl
  validation.jsonl
  test.jsonl
  dataset_manifest.json
  split_summary.json
```

This primary corpus intentionally excludes archived `hard_holdout` and
`locked_0107_test` diagnostics. Keep historical candidates, review logs,
training reports, and generated prepared releases outside Git or in the
supplementary artifact package.

## Setup

Install training dependencies and fetch the pinned text encoder:

```powershell
python -m pip install -e ".[train,dev]"
python tools/preprocessing/fetch_text_encoder.py
```

The text encoder is verified against:

```text
artifacts/models/intent_gnn/v1/encoder_manifest.json
```

## Inspect Dataset

```powershell
trustedsql-gnn inspect
```

This reads `data/training/intent_gnn/v1/*.jsonl` and reports split counts.

## Prepare Release

```powershell
trustedsql-gnn prepare --run-id <gnn_run>
```

This creates a materialized training release under:

```text
outputs/training/<gnn_run>/prepared/
  intent_samples.jsonl
  split_manifest.json
  dataset_v2_summary.json
  dataset_v2_validation_report.json
```

## Train

```powershell
trustedsql-gnn train --run-id <gnn_run> --device cuda
```

Useful options:

```powershell
trustedsql-gnn train --run-id <gnn_run> --device cpu --smoke-samples 50
trustedsql-gnn train --run-id <gnn_run> --device cuda --epochs 30 --sampling-mode family_micro_balanced
```

Training writes candidate artifacts under:

```text
outputs/training/<gnn_run>/
  checkpoints/candidate.pt
  reports/training_report.json
  prepared/
```

## Evaluate Candidate

```powershell
trustedsql-gnn evaluate --run-id <gnn_run> --checkpoint outputs/training/<gnn_run>/checkpoints/candidate.pt
```

This evaluates the candidate against the prepared validation and internal-test
splits.

## Promote Runtime Checkpoint

Promote only after reviewing the training and evaluation reports:

```powershell
trustedsql-gnn promote `
  --checkpoint outputs/training/<gnn_run>/checkpoints/candidate.pt `
  --confirmed-by <name> `
  --evaluation-report outputs/training/<gnn_run>/reports/training_report.json
```

Promotion writes:

```text
artifacts/models/intent_gnn/v1/best.pt
artifacts/models/intent_gnn/v1/model_manifest.json
```

The source repository should keep the promoted checkpoint, clean manifests, and
active model-development corpus. Candidate checkpoints, reports, logs,
historical split packages, and prepared releases should remain outside Git or in
the external thesis artifact package.

## Notebook

An interactive notebook skeleton is available at:

```text
evaluation/notebooks/train_intent_gnn_vi.ipynb
```

Use the CLI commands above as the source of truth. The notebook is for guided
execution and inspection, not for storing committed outputs.
