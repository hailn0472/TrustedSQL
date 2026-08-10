# TrustedSQL Intent GNN Model Card

## Model Purpose

The Intent GNN checkpoint is used by the TrustedSQL M2 Intent Risk Guard to
predict conversation-level intent, risk categories, target scope, and security
state transitions. Runtime code converts these predictions into compact security
signals consumed by the TrustedSQL pipeline. The GNN is advisory: final
authorization is still enforced by deterministic policy, resource-contract,
row-scope, SQL-validation, and read-only execution modules.

## Runtime Integration

Runtime loader:

```text
src/trustedsql_gnn/inference/phase.py
src/trustedsql_gnn/inference/runtime.py
src/trustedsql_gnn/inference/predictor.py
```

Runtime consumer:

```text
src/trustedsql/modules/intent_risk_guard.py
```

Promoted checkpoint:

```text
artifacts/models/intent_gnn/v1/best.pt
```

## Model Architecture

The model is a custom relation-aware message-passing GNN implemented in:

```text
src/trustedsql_gnn/model/gnn.py
```

For this checkpoint, the recorded architecture is:

| Field | Value |
|---|---:|
| Input dimension | 431 |
| Hidden dimension | 128 |
| Message-passing layers | 3 |
| Relation types | 18 |
| Dropout | 0.2 |
| Max reference distance | 8 |

The network projects node features into a hidden space, applies relation-specific
linear message transforms across graph edges, uses ReLU activations and dropout,
then combines an attention-weighted graph pool with the current-turn node
representation. Task-specific heads predict semantic intent, operation, scope,
target relation, transition, reference distance, security transition, and
multi-label concepts.

Training uses the multi-task loss implemented in:

```text
src/trustedsql_gnn/training/losses.py
```

The architecture configuration is also recorded in `model_manifest.json` and
`training_config.origin.json`.

## Input Graph Representation

Each conversation is represented as a graph built by:

```text
src/trustedsql_gnn/graph/builder.py
```

The graph contains the following node types:

```text
Role
UserTurn
EntityMention
SemanticConceptCandidate
ReferenceExpression
PreviousSemanticState
ScopeCandidate
TargetCandidate
```

The configured edge types are:

```text
follows
mentions
represents
refers_to_candidate
has_previous_intent
has_previous_scope
supports_scope
supports_target
continues_context
```

Reverse edges are enabled. The graph keeps up to 8 turns and a maximum reference
distance of 8. Current-turn ground-truth labels, SQL, policy decisions, and graph
labels are explicitly forbidden as current-turn features. Textual features are
encoded with `sentence-transformers/all-MiniLM-L6-v2` at revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`; hashes for the encoder weights and
vocabulary are recorded in `encoder_manifest.json`.

Exact graph construction parameters are defined in:

```text
configs/gnn/graph_config.json
configs/gnn/concept_catalog_v1.json
```

## Label Taxonomy

The model predicts a multi-task label set:

| Task | Type | Label Source |
|---|---|---|
| Semantic intent | Multi-class | `configs/gnn/intent_taxonomy_v1.json` |
| Operation | Multi-class | `configs/gnn/intent_taxonomy_v1.json` |
| Scope | Multi-class | `configs/gnn/intent_taxonomy_v1.json` |
| Target relation | Multi-class | `configs/gnn/intent_taxonomy_v1.json` |
| Conversation transition | Multi-class | `configs/gnn/intent_taxonomy_v1.json` |
| Reference distance | Multi-class ordinal bucket | model config |
| Security transition | Multi-class | `configs/gnn/intent_taxonomy_v1.json` |
| Concepts | Multi-label | `configs/gnn/concept_catalog_v1.json` |

Legacy labels are mapped through:

```text
configs/gnn/legacy_intent_mapping_v1.json
```

Unknown or ambiguous cases are represented through explicit `UNKNOWN` labels
where the taxonomy defines them. Runtime policy uses calibrated security signals
derived from these outputs rather than treating the model as the sole authority.

## Training Data Provenance

The source release includes the active model-development corpus under:

```text
data/training/intent_gnn/v1
```

This package contains the train, validation, and internal test partitions used
for the promoted Conversation-Risk Model profile. Detailed human-curation,
promotion, retraining, and historical diagnostic reports are maintained as
supplementary review artifacts rather than runtime/source files.

Dataset version:

```text
3.0.0-0107-augmented
```

Recorded split hashes:

| Split | SHA-256 |
|---|---|
| train | `60D4A772F0C47CBF8C1B743F1D3F1A5B5A0A54AC0DC6FD6E0D3B04E8DD449ECF` |
| validation | `8FB1F098EC101DEA8471017EC8EB1DE166D8C7AA1EDCC008CACA73ED5875AB97` |
| test | `9D9188DFBEA47CEAB4F6BA7E321EC135F02E058C471E92AC08067CE77A2010A6` |

Instructions for validating or rebuilding the package metadata are provided in:

```text
docs/gnn_training_guide.md
tools/data_synthesis/
```

## Training Environment

The runtime repository records dependency constraints in `pyproject.toml` and
`requirements.txt`. The exact original training host environment was not fully
recorded in this repository. The promoted checkpoint should therefore be treated
as a fixed runtime artifact; detailed release-engineering evidence is maintained
in the supplementary review dossier.

Known framework constraints for current code:

| Component | Constraint |
|---|---|
| Python | `>=3.10` |
| PyTorch | `>=2.0.0` |
| sentence-transformers | `>=3.0.0` |
| scikit-learn | `>=1.4.0` for training/evaluation utilities |
| NumPy | `>=1.26.0` |

## Training Configuration

The training configuration snapshot is stored in:

```text
artifacts/models/intent_gnn/v1/training_config.origin.json
```

Summary:

| Field | Value |
|---|---:|
| Seed | 20260609 |
| Epochs | 30 |
| Batch size | 1 |
| Learning rate | 0.001 |
| Weight decay | 0.0001 |
| Early stopping patience | 5 |
| Gradient clipping norm | 1.0 |

Loss weights are multi-task and include intent, scope, target relation,
reference distance, operation, transition, concepts, and security transition.

## Checkpoint Selection

The promoted checkpoint records:

```text
best_epoch = 19
```

The exact checkpoint-selection rule is not stored as a separate field in the
runtime manifest. The available training record indicates the checkpoint was
promoted from the best recorded validation run and confirmed by the training
author. For submission, the supplementary training artifact should include the
complete training report and explicit best-checkpoint selection criterion.

## Evaluation Results

The runtime package includes the recorded validation metrics embedded in
`model_manifest.json`.

| Metric | Validation |
|---|---:|
| Sample count | 787 |
| Intent accuracy | 0.921220 |
| Intent macro-F1 | 0.928020 |
| Operation accuracy | 0.904701 |
| Operation macro-F1 | 0.902818 |
| Scope accuracy | 0.902160 |
| Scope macro-F1 | 0.886167 |
| Target relation accuracy | 0.923761 |
| Target relation macro-F1 | 0.936926 |
| Transition accuracy | 0.870394 |
| Transition macro-F1 | 0.882890 |
| Security transition accuracy | 0.921220 |
| Security transition macro-F1 | 0.869869 |
| Concept micro-F1 | 0.867523 |

The active internal-test split is included with the model-development corpus
under `data/training/intent_gnn/v1/`. Historical hard-holdout diagnostics and
large training reports should be supplied in the external supplementary
artifact for reviewer-side provenance.

## Artifact Identity

| Artifact | Identity |
|---|---|
| Checkpoint | `best.pt` |
| Checkpoint SHA-256 | `C4FBDA9D6518DB1EAA04D57FBEA85483C4925CCB1B5B468B5FA9D2DDABCF1141` |
| Encoder | `sentence-transformers/all-MiniLM-L6-v2` |
| Encoder revision | `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` |
| Training config hash | `EEDB436E35D21FA14FA72FBF976DE44BB0FC92895CF19734C912F6B3DB2E85CC` |

Use `SHA256SUMS` in this directory to verify promoted model artifacts.

## Limitations

- The model depends on the configured TrustedSQL taxonomy and concept catalog.
- Unseen attack categories or conversation patterns may be underrepresented.
- Text encoder changes may affect preprocessing and prediction behavior.
- The active model-development corpus is included, but historical candidates,
  hard/locked diagnostics, and large training reports are external
  supplementary artifacts.
- The GNN output is a risk signal, not the final authorization decision.
- Deterministic policy modules remain the primary enforcement layer.
