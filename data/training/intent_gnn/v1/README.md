# TrustedSQL GNN Model-Development Corpus

This is the primary Conversation-Risk Model development corpus included with the
TrustedSQL source release. It contains only the active train, validation, and
internal test partitions needed to reproduce the promoted GNN checkpoint
workflow.

This package is a frozen promoted corpus, not the direct output of the
procedural `EXEC-*` development generator. Its expected conversation ID
families are `FULLMT-*`, `ANCHOR-*`, `0107-*`, and `AUGMT-*`.

Detailed curation, promotion, retraining, and historical diagnostic reports are
kept as supplementary review artifacts rather than runtime/source files. This
keeps the repository focused on runnable source and the active model-development
package.

## Counts

| Split | File | Count | Purpose |
|---|---|---:|---|
| Train | `train.jsonl` | 3384 | Model optimization |
| Validation | `validation.jsonl` | 787 | Model selection / early stopping |
| Test | `test.jsonl` | 615 | Internal component reporting after checkpoint selection |

The model-development corpus contains 4786 conversations: train + validation + test.

## 0107 Augmentation

Dataset-0107 augmentation is included only in the training and validation partitions.

| 0107 split | Benign | Malicious | Total |
|---|---:|---:|---:|
| Train augmentation | 269 | 245 | 514 |
| Validation augmentation | 26 | 86 | 112 |

The 269 benign train augmentation examples include 245 original 0107 benign examples plus
24 deterministic hard-benign v3 examples. These hard-benign examples target benign boundary
patterns that v2 over-blocked.

## Quality Notes

- No duplicate conversation IDs across train, validation, and test.
- No duplicate normalized final turns across those splits.
- Benign audit risk count is 0.
- Original `test.jsonl` is kept unchanged from `best_final`.
