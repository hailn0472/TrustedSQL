# TrustedSQL DataTrain Generation

This package implements the offline dataset-construction workflow used during
TrustedSQL model development. The reviewer-facing name is **TrustedSQL
DataTrain Generation**; `data_synthesis` remains the stable Python import path
for supplementary scripts and frozen release manifests.

This package is not imported by the TrustedSQL runtime. It is kept under
`tools/data_synthesis/` so reviewers can inspect how the Conversation-Risk Model
corpus was generated, validated, audited, converted to graph artifacts, and
repackaged.

## Scientific Workflow

The methodology presents five phases. The executable pipeline retains seven
fine-grained stage identifiers because those names already appear in artifacts.

| Phase | Implementation stages | Responsibility |
| --- | --- | --- |
| 1. Policy and Pattern Grounding | `expert_fewshot`, `target_condition` | Load schema, policy, role/context, taxonomy, pattern, and target constraints. |
| 2. Quota and Slot Planning | `slot` | Convert release quotas into deterministic generation jobs. |
| 3. Controlled Candidate Generation | `generate_raw` | Generate complete candidates and retain prompts, raw outputs, repairs, usage, and errors. |
| 4. Labeling and Verification | `label`, `validate_verify` | Canonicalize, label-check, reject invalid/duplicate rows, and verify quota and coverage. |
| 5. Release Construction | `finalize` | Select accepted rows and export the final dataset, reports, manifests, splits, and graph artifacts where applicable. |

The machine-readable mapping is defined in `workflow.py`. Existing consumers of
`PIPELINE_PHASES` remain supported; the clearer name `PIPELINE_STAGES` is
available for new code.

## Dataset Families

| Family | Module | Construction mode | Primary controlled dimensions |
| --- | --- | --- | --- |
| Policy-compliant conversations | `benign_dataset/` | Constrained LLM generation | turn type, authenticated role |
| Single-turn prompt injection | `singleturn_prompt_injection/` | Constrained LLM generation | injection type, RBAC violation |
| Dynamic multi-turn attacks | `multiturn_dynamic/` | Constrained LLM generation | MT pattern, role, target condition |
| Policy-grounded GNN corpus | `gnn_dataset/generator.py` | Constrained LLM generation plus graph export | pattern, role, policy target, protocol |
| Intent conversation corpus | `gnn_dataset/execution_v2.py` | Deterministic task-contract generation | category, intent, transition, security transition |

`gnn_dataset/datatrain_v1_builder.py` is deliberately separate from synthesis:
it verifies and repackages an already promoted frozen corpus. It does not create
new conversations. Likewise, `gnn_dataset/release_v2_packager.py` transforms
conversation records into the sequence-level release schema. The active
model-development package contains `train`, `validation`, and `test` partitions;
older diagnostic partitions should be treated as supplementary provenance only
when they appear in archival reports.

## Package Layout

```text
data_synthesis/
|-- workflow.py                  # scientific phases and stage mapping
|-- registry.py                  # immutable dataset-family definitions
|-- api.py                       # lazy public entry points
|-- common/                      # shared contracts, runners, validation, I/O
|-- benign_dataset/              # benign family
|-- singleturn_prompt_injection/ # single-turn adversarial family
|-- multiturn_dynamic/           # dynamic multi-turn family
|-- gnn_dataset/                 # policy-grounded, graph, split, and release tools
`-- gemini_client.py             # optional model-provider adapter
```

When running from the repository checkout, use:

```powershell
cd tools/data_synthesis
python -m data_synthesis.gnn_dataset.datatrain_v1_builder --config config/datatrain_final_v1.example.json
```

## Public Interface

Contract metadata can be inspected without loading model-provider dependencies:

```python
from data_synthesis import dataset_family_summary, workflow_summary

workflow = workflow_summary()
families = dataset_family_summary()
```

Generation entry points use lazy imports:

```python
from data_synthesis import generate_benign, generate_multi_turn

benign_result = generate_benign(...)
multiturn_result = generate_multi_turn(...)
```

Family-specific functions such as `generate_benign_dataset` and
`generate_gnn_dataset` remain available at their original module paths.

## Reproducibility Contract

- Planning quotas define the intended release distribution; over-generation
  only provides replacement candidates.
- Raw candidates and audit evidence are stored separately from final records.
- The intended slot label is verified, not silently rewritten by the label step.
- Structural, taxonomy, policy, duplicate, and coverage gates run before release.
- Split and graph exporters operate only after record-level validation.
- Frozen DataTrain packages are verified and manifested independently from the
  procedural development generators.
- End-to-end evaluation benchmark records are not loaded by the training-data
  generation, packaging, or verification pipeline.

Detailed inputs, quotas, labels, and outputs are documented in each family
README. The canonical field and artifact contract is implemented in
`common/pipeline_contract.py`.
