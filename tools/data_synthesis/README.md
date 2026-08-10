# TrustedSQL Offline Data Synthesis Tools

This directory contains the supplementary source code used to construct,
validate, audit, and package the GNN model-development corpus for the
Conversation-Risk Model.

The code here is not required by the TrustedSQL runtime. Runtime execution uses
the frozen checkpoint and runtime graph encoder under `src/`. These tools are
kept in the repository to make the GNN data provenance inspectable by reviewers.

## Layout

```text
tools/data_synthesis/
|-- data_synthesis/              # Python package; stable import path
|   |-- common/                  # shared validation, quota, I/O, and audit helpers
|   |-- benign_dataset/          # benign conversation family
|   |-- singleturn_prompt_injection/
|   |-- multiturn_dynamic/
|   `-- gnn_dataset/             # GNN corpus generation, graph export, and packaging
`-- config/                      # example local configuration files
```

Run commands from this directory when using the package as `data_synthesis`:

```powershell
cd tools/data_synthesis
python -m data_synthesis.gnn_dataset.datatrain_v1_builder --config config/datatrain_final_v1.example.json
```

## Scope

- `gnn_dataset/execution_v2.py` and `gnn_dataset/generator.py` are offline
  generation and release-construction tools.
- `gnn_dataset/release_v2_packager.py` converts complete conversations into the
  sequence-level release schema used by the GNN training pipeline.
- `gnn_dataset/datatrain_v1_builder.py` verifies and repackages an already
  promoted corpus. It does not synthesize new conversations.

The active model-development release used by the final write-up contains
`train`, `validation`, and `test` partitions. Older diagnostic partitions, if
present in archival artifacts, should be treated as provenance or supplementary
analysis rather than as the active training package.

## Reproducibility Notes

- Raw generated candidates, provider caches, large logs, and local credentials
  are intentionally excluded from this repository.
- Provider-backed generation requires local credentials such as
  `VERTEX_PROJECT_ID` or `GEMINI_API_KEY`; no credentials are committed here.
- Final model-development records are stored separately from the end-to-end
  evaluation benchmark. They may share taxonomy and pattern contracts, but the
  runtime benchmark is not loaded by these training-data tools.
