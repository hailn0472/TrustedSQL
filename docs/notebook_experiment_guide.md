# Notebook Experiment Guide

This guide explains how to use `evaluation/notebooks/evaluate_trustedsql_vi.ipynb` to run TrustedSQL experiments in a reproducible way.

## Purpose

The notebook is a thin orchestration layer over the experiment runner:

```powershell
python evaluation/run_experiment.py --experiment <experiment_yaml> --phase <runtime|evaluate|all>
```

It should be used for interactive experiment execution and inspection. The source of truth remains the YAML files under `configs/experiments/`, `configs/systems/`, `configs/providers/`, and `configs/datasets/`.

## Notebook Location

```text
evaluation/notebooks/evaluate_trustedsql_vi.ipynb
```

The notebook is intentionally placed under `evaluation/notebooks/` because it belongs to experiment execution and metric inspection, not runtime source code.

## Main Config Cell

The first code cell controls the experiment:

```python
EXPERIMENT_FILE = PROJECT_ROOT / "configs" / "experiments" / "ex1_main_full_results.yaml"
EXPERIMENT_RUN_ID = f"ex1_main_full_results__{datetime.now().strftime('%Y%m%d_%H%M%S')}"
SYSTEMS = ["full_trustedsql"]
PROVIDERS = ["gemini_25_flash"]
MAX_SAMPLES = None
```

### Fields

| Field | Meaning |
|---|---|
| `PROJECT_ROOT` | Root of the TrustedSQL repository. Can be controlled by `TRUSTEDSQL_PROJECT_ROOT`. |
| `EXPERIMENT_FILE` | YAML file defining the experiment protocol. |
| `EXPERIMENT_RUN_ID` | Folder name under `outputs/experiments/`; keep it fixed when running runtime and evaluate in separate sessions. |
| `SYSTEMS` | Optional system filter. Use `None` or `[]` to run all systems in the experiment YAML. |
| `PROVIDERS` | Optional provider filter. Use `None` or `[]` to run all providers in the experiment YAML. |
| `MAX_SAMPLES` | Optional debug limit per dataset split. Use `None` for full runs. |

## Common Experiment Choices

### EX1 Main Full Results

Purpose: run full TrustedSQL on all selected model providers.

```python
EXPERIMENT_FILE = PROJECT_ROOT / "configs" / "experiments" / "ex1_main_full_results.yaml"
SYSTEMS = ["full_trustedsql"]
PROVIDERS = ["gemini_25_flash"]       # or ["fci_oss20b"], ["fci_oss120b"], or all providers
MAX_SAMPLES = None
```

### EX2 Baseline Comparison

Purpose: compare generator-only, previous architecture baseline, and full TrustedSQL on Gemini 2.5 Flash.

```python
EXPERIMENT_FILE = PROJECT_ROOT / "configs" / "experiments" / "ex2_baseline_comparison.yaml"
SYSTEMS = []                          # run all systems in EX2
PROVIDERS = ["gemini_25_flash"]
MAX_SAMPLES = None
```

To run only one baseline:

```python
SYSTEMS = ["generator_only_control"]
```

### EX3 Ablation Study

Purpose: evaluate contribution of TrustedSQL module groups.

```python
EXPERIMENT_FILE = PROJECT_ROOT / "configs" / "experiments" / "ex3_ablation.yaml"
SYSTEMS = []                          # run all ablation settings
PROVIDERS = ["gemini_25_flash"]
MAX_SAMPLES = None
```

To run one ablation only:

```python
SYSTEMS = ["trustedsql_minus_m3_m4_m5"]
```

## Recommended Smoke Test

Before a full run, use a small smoke run:

```python
MAX_SAMPLES = 1
```

Then run:

1. `Experiment Config Summary`
2. `Command Preview`
3. `Phase RUNTIME`
4. `Phase EVALUATE`
5. `Load Metrics`

If the smoke run succeeds, set `MAX_SAMPLES = None` and create a new `EXPERIMENT_RUN_ID` for the full run.

## Runtime Phase

The runtime cell runs:

```python
run_command(experiment_args("runtime", ["--rerun-api-429"]))
```

This creates:

```text
outputs/experiments/<experiment_run_id>/
  experiment_manifest.json
  run_index.csv
  resolved_configs/

outputs/runs/<run_id>/
  run_manifest.json
  runtime/
```

`--rerun-api-429` retries only API 429 failures according to runtime config. It does not change the scientific metric logic.

## Evaluate Phase

The evaluate cell runs:

```python
run_command(experiment_args("evaluate"))
```

Important: keep the same `EXPERIMENT_RUN_ID` used in the runtime phase. The evaluator reads `outputs/experiments/<experiment_run_id>/run_index.csv` and evaluates those exact runtime runs.

The evaluator writes:

```text
outputs/runs/<run_id>/evaluation/
  evidence/
    turn_utility_evidence.jsonl
    sequence_security_evidence.jsonl
  metrics/
    benchmark_metrics.json
    *.csv
```

## Config Tracking Cells

The notebook contains two important tracking cells.

### Experiment Config Summary

Shows the planned experiment before execution:

- experiment id;
- dataset profile and dataset files;
- selected systems;
- module path for each system;
- selected providers;
- provider model, temperature, and API key environment variable;
- `MAX_SAMPLES`.

Use this cell before every run to avoid running the wrong model or wrong dataset.

### Runtime Config Snapshot

Shows the actual runtime provenance after execution:

- run id;
- runtime kind;
- module settings or architecture settings;
- resolved module models;
- selected sequence/turn count.

This is the post-run source of truth. If this snapshot disagrees with the notebook config cell, trust the runtime snapshot.

## Reading Metrics

The notebook loads:

```text
benchmark_metrics.json
```

The main sections are:

```python
metrics["utility"]
metrics["single_turn_security"]
metrics["multi_turn_security"]
metrics["performance"]
```

## Safe Usage Notes

- Do not edit generated resolved configs under `outputs/experiments/.../resolved_configs/`.
- Do not reuse an old `EXPERIMENT_RUN_ID` for a new runtime unless you intentionally want to resume/evaluate that same experiment.
- For copied remote runs, evaluate using the same `EXPERIMENT_RUN_ID` and ensure benchmark files match the runtime snapshot.
- `ERROR` is never counted as a correct refusal.
- The notebook does not run human review or adjudication.

## Quick Commands Without Notebook

The same workflow can be run directly:

```powershell
python evaluation/run_experiment.py --experiment configs/experiments/ex1_main_full_results.yaml --phase runtime --providers gemini_25_flash --systems full_trustedsql
python evaluation/run_experiment.py --experiment configs/experiments/ex1_main_full_results.yaml --phase evaluate --providers gemini_25_flash --systems full_trustedsql --experiment-run-id <same_experiment_run_id>
```

For smoke:

```powershell
python evaluation/run_experiment.py --experiment configs/experiments/ex2_baseline_comparison.yaml --phase all --max-samples 1
```
