# Architecture to Code Mapping

This document maps the paper notation to the runtime implementation. It is intended for reviewers who want to inspect the contribution without inferring the pipeline from the source tree.

## TrustedSQL Pipeline

| Paper component | Runtime responsibility | Primary implementation |
|---|---|---|
| `C0` | Build runtime context from schema, policy, role/user identity, conversation history, and benchmark input. It does not make security decisions. | `src/trustedsql/modules/context_builder.py` |
| `M1` | Prompt-integrity guard for prompt-injection and policy-bypass intent. | `src/trustedsql/modules/prompt_integrity_guard.py` |
| `M2` | Intent-GNN risk guard for conversational risk signals. | `src/trustedsql/modules/intent_risk_guard.py`, `src/trustedsql_gnn/` |
| `M3` | Policy-grounded resource planner. | `src/trustedsql/modules/access_planner.py` |
| `M4` | Deterministic table/column access validator and resource-contract builder. | `src/trustedsql/modules/table_column_access_validator.py` |
| `M5` | Deterministic row-scope proof verifier. | `src/trustedsql/modules/row_scope_verifier.py` |
| `M6` | SQL generator constrained by the verified runtime context. | `src/trustedsql/modules/sql_generator.py` |
| `M7` | SQL conformance validator. | `src/trustedsql/modules/sql_conformance_validator.py` |
| `X1` | Database-enforced read-only SQL executor with timeout and row-limit enforcement. | `src/trustedsql/modules/readonly_executor.py`, `src/trustedsql/db/executor.py` |

The full method path is:

```text
C0 -> M1 -> M2 -> M3 -> M4 -> M5 -> M6 -> M7 -> X1
```

## Supporting Runtime Packages

| Area | Implementation |
|---|---|
| Runtime orchestration and module contracts | `src/trustedsql/runtime/`, `src/trustedsql/contracts.py` |
| Provider adapters and LLM clients | `src/trustedsql/providers/` |
| Prompts used by runtime modules | `src/trustedsql/prompts/` |
| Policy loading and checks | `src/trustedsql/policy/`, `resources/policy/` |
| Database schema and SQL safety helpers | `src/trustedsql/db/`, `src/trustedsql/sql/`, `resources/schema/` |
| GNN graph construction, inference, and training utilities | `src/trustedsql_gnn/` |
| Automatic evaluation metrics | `src/benchmark_eval/` |
| Architecture baselines for EX2 | `src/architecture_baselines/` |

## Experiment Entry Points

| Purpose | Entry point |
|---|---|
| Run a single TrustedSQL runtime pass | `run_trustedsql.py run` |
| Evaluate one completed runtime run | `evaluation/run_evaluation.py evaluate` |
| Run paper experiments EX1, EX2, and EX3 | `evaluation/run_experiment.py` |
| Inspect, train, evaluate, or promote Intent GNN assets | `trustedsql-gnn` |
| Validate repository artifacts | `tools/validation/check_repository_artifacts.py` |

## Paper Experiment Mapping

| Paper experiment | Main configs | Runtime implementations |
|---|---|---|
| EX1 main full-result tables | `configs/experiments/ex1_main_full_results.yaml` | `src/trustedsql/` |
| EX2 baseline comparison | `configs/experiments/ex2_baseline_comparison.yaml` | `src/trustedsql/`, `src/architecture_baselines/` |
| EX3 ablation study | `configs/experiments/ex3_ablation.yaml` | `src/trustedsql/` |

## Runtime Boundary

Runtime modules do not receive `sql_gt`, expected result rows, dataset labels, attack tags, human-review labels, training labels, or evaluator evidence. Ground-truth SQL is loaded by `src/benchmark_eval/` only after the runtime phase has completed.
