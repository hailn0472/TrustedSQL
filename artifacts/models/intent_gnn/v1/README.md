# Intent GNN Runtime Artifact

This directory contains the promoted Intent GNN runtime checkpoint used by the
TrustedSQL M2 Intent Risk Guard.

## Files

```text
best.pt                      Promoted runtime checkpoint
model_manifest.json          Runtime provenance, model config, hashes, and metrics
encoder_manifest.json        Pinned text encoder identity and hashes
training_config.origin.json  Training configuration snapshot
MODEL_CARD.md                Reviewer-facing model documentation
SHA256SUMS                   Artifact hash manifest
```

## Verify

From this directory:

```powershell
Get-Content SHA256SUMS
```

To recompute a hash:

```powershell
Get-FileHash best.pt -Algorithm SHA256
```

## Runtime Use

The checkpoint is loaded by:

```text
src/trustedsql_gnn/inference/predictor.py
```

and consumed by:

```text
src/trustedsql/modules/intent_risk_guard.py
```

The active model-development corpus is stored under
`data/training/intent_gnn/v1/`. Historical reports, candidate checkpoints, and
archived diagnostic splits are kept in the supplementary artifact package. See
`docs/gnn_training_guide.md` and `MODEL_CARD.md` for retraining and provenance
notes.
