from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from trustedsql_gnn.contracts import (
    Category,
    GenerationMetadata,
    HistoryTurn,
    IntentSample,
    ReferenceTarget,
    StrictModel,
    TurnLabels,
)
from trustedsql_gnn.data.io import read_jsonl, write_json, write_jsonl
from trustedsql_gnn.taxonomy import IntentTaxonomy


SPLIT_FILENAMES = {
    "train": "train.jsonl",
    "validation": "validation.jsonl",
    "test": "test.jsonl",
}


class V2ReferenceTarget(StrictModel):
    from_turn: int = Field(ge=1)
    to_turn: int = Field(ge=1)
    target_concept: str
    surface: str | None = None


class V2Turn(StrictModel):
    turn_id: int = Field(ge=1)
    user_utterance: str = Field(min_length=1)


class V2Labels(StrictModel):
    semantic_intent: str
    operation: str
    scope: str
    target_relation: str
    transition: str
    target_concepts: list[str] = Field(default_factory=list)
    security_transition: str = "NONE"
    reference_targets: list[V2ReferenceTarget] = Field(default_factory=list)


class V2Conversation(StrictModel):
    conversation_id: str
    category: Category
    role: Literal["student", "lecturer", "admin"]
    turns: list[V2Turn] = Field(min_length=1, max_length=8)
    labels: V2Labels
    entity_seed: dict[str, Any] | str | None = None
    mt_id: str | None = None
    micro_pattern_id: str | None = None
    contrastive_pair_id: str | None = None
    anchor_id: str | None = None

    @model_validator(mode="after")
    def validate_turns_and_references(self) -> "V2Conversation":
        ids = [turn.turn_id for turn in self.turns]
        if ids != list(range(1, len(ids) + 1)):
            raise ValueError("turn_ids_must_be_contiguous_from_one")
        current_turn_id = self.turns[-1].turn_id
        for reference in self.labels.reference_targets:
            if reference.from_turn != current_turn_id:
                raise ValueError("reference_from_turn_must_be_current_turn")
            if reference.to_turn >= reference.from_turn:
                raise ValueError("reference_target_must_precede_source_turn")
        if self.category == "BENIGN_SINGLE_TURN" and len(self.turns) != 1:
            raise ValueError("benign_single_turn_must_have_exactly_one_turn")
        if self.category != "BENIGN_SINGLE_TURN" and len(self.turns) < 2:
            raise ValueError("multi_turn_category_requires_at_least_two_turns")
        return self


class LabelOverride(StrictModel):
    override_id: str
    reason: str
    micro_pattern_id: str | None = None
    conversation_id: str | None = None
    set: dict[str, Any]

    @model_validator(mode="after")
    def validate_selector(self) -> "LabelOverride":
        if not self.micro_pattern_id and not self.conversation_id:
            raise ValueError("label_override_requires_micro_pattern_id_or_conversation_id")
        if self.micro_pattern_id and self.conversation_id:
            raise ValueError("label_override_selector_must_be_unique")
        return self


def read_v2_jsonl(path: str | Path) -> list[V2Conversation]:
    return read_jsonl(path, V2Conversation)


def read_label_overrides(path: str | Path | None) -> list[LabelOverride]:
    if path is None:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [LabelOverride.model_validate(item) for item in payload.get("overrides", [])]


def v2_conversation_to_sample(
    conversation: V2Conversation,
    *,
    label_overrides: list[LabelOverride] | None = None,
) -> IntentSample:
    current = conversation.turns[-1]
    references = [
        ReferenceTarget(
            target_turn=reference.to_turn,
            target_concept=reference.target_concept,
            surface=reference.surface,
        )
        for reference in conversation.labels.reference_targets
    ]
    label_payload = {
        "semantic_intent": conversation.labels.semantic_intent,
        "operation": conversation.labels.operation,
        "scope": conversation.labels.scope,
        "target_relation": conversation.labels.target_relation,
        "transition": conversation.labels.transition,
        "target_concepts": conversation.labels.target_concepts,
        "security_transition": conversation.labels.security_transition,
    }
    applied_overrides = _matching_overrides(conversation, label_overrides or [])
    for item in applied_overrides:
        label_payload.update(item.set)
    labels = TurnLabels(
        semantic_intent=label_payload["semantic_intent"],
        operation=label_payload["operation"],
        scope=label_payload["scope"],
        target_relation=label_payload["target_relation"],
        transition=label_payload["transition"],
        target_concepts=label_payload["target_concepts"],
        reference_targets=references,
        security_transition=label_payload["security_transition"],
    )
    pattern_id = (
        conversation.micro_pattern_id
        or conversation.anchor_id
        or conversation.mt_id
        or "V2_UNSPECIFIED_PATTERN"
    )
    entity_seed = (
        conversation.entity_seed
        if isinstance(conversation.entity_seed, str)
        else json.dumps(conversation.entity_seed or {}, ensure_ascii=True, sort_keys=True)
    )
    metadata = GenerationMetadata(
        generator_version="gnn_method_v1_execution_v2",
        surface_variant_id=pattern_id,
        contrastive_pair_id=conversation.contrastive_pair_id,
        entity_seed=entity_seed,
        pattern_revision="v2",
        extra={
            "dataset_schema": "conversation_v2",
            "category": conversation.category,
            "mt_id": conversation.mt_id,
            "micro_pattern_id": conversation.micro_pattern_id,
            "contrastive_pair_id": conversation.contrastive_pair_id,
            "anchor_id": conversation.anchor_id,
            "role": conversation.role,
            "turn_count": len(conversation.turns),
            "reference_distance": _reference_distance(current.turn_id, references),
            "reference_from_turns": [
                reference.from_turn for reference in conversation.labels.reference_targets
            ],
            "scope": conversation.labels.scope,
            "transition": conversation.labels.transition,
            "security_transition": conversation.labels.security_transition,
            "label_override_applied": bool(applied_overrides),
            "label_override_ids": [item.override_id for item in applied_overrides],
            "original_labels": {
                "semantic_intent": conversation.labels.semantic_intent,
                "operation": conversation.labels.operation,
                "scope": conversation.labels.scope,
                "target_relation": conversation.labels.target_relation,
                "transition": conversation.labels.transition,
                "target_concepts": conversation.labels.target_concepts,
                "security_transition": conversation.labels.security_transition,
            },
        },
    )
    return IntentSample(
        sample_id=f"{conversation.conversation_id}::turn_{current.turn_id}",
        conversation_id=conversation.conversation_id,
        pattern_id=pattern_id,
        category=conversation.category,
        role=conversation.role,
        current_turn_id=current.turn_id,
        history=[
            HistoryTurn(
                turn_id=turn.turn_id,
                text=turn.user_utterance,
                predicted_or_gold_state=None,
            )
            for turn in conversation.turns[:-1]
        ],
        current_text=current.user_utterance,
        labels=labels,
        generation_metadata=metadata,
    )


def validate_v2_conversations(
    conversations: list[V2Conversation],
    *,
    taxonomy: IntentTaxonomy,
    known_concepts: set[str],
) -> dict:
    errors: list[dict] = []
    seen_ids: set[str] = set()
    for conversation in conversations:
        if conversation.conversation_id in seen_ids:
            errors.append(
                {
                    "conversation_id": conversation.conversation_id,
                    "error": "duplicate_conversation_id",
                }
            )
        seen_ids.add(conversation.conversation_id)
        try:
            labels = TurnLabels(
                semantic_intent=conversation.labels.semantic_intent,
                operation=conversation.labels.operation,
                scope=conversation.labels.scope,
                target_relation=conversation.labels.target_relation,
                transition=conversation.labels.transition,
                target_concepts=conversation.labels.target_concepts,
                security_transition=conversation.labels.security_transition,
                reference_targets=[
                    ReferenceTarget(
                        target_turn=reference.to_turn,
                        target_concept=reference.target_concept,
                        surface=reference.surface,
                    )
                    for reference in conversation.labels.reference_targets
                ],
            )
            taxonomy.validate_labels(labels)
        except Exception as exc:
            errors.append(
                {
                    "conversation_id": conversation.conversation_id,
                    "error": "taxonomy_error",
                    "detail": str(exc),
                }
            )
        unknown_concepts = sorted(
            concept
            for concept in [
                *conversation.labels.target_concepts,
                *[
                    reference.target_concept
                    for reference in conversation.labels.reference_targets
                ],
            ]
            if concept not in known_concepts
        )
        if unknown_concepts:
            errors.append(
                {
                    "conversation_id": conversation.conversation_id,
                    "error": "unknown_concepts",
                    "concepts": unknown_concepts,
                }
            )
    return {
        "valid": not errors,
        "conversation_count": len(conversations),
        "error_count": len(errors),
        "errors": errors[:100],
    }


def prepare_v2_release(
    *,
    split_dir: str | Path,
    output_dir: str | Path,
    taxonomy: IntentTaxonomy,
    known_concepts: set[str],
    label_overrides: list[LabelOverride] | None = None,
) -> dict:
    source = Path(split_dir)
    output = Path(output_dir)
    conversations_by_split: dict[str, list[V2Conversation]] = {}
    for split, filename in SPLIT_FILENAMES.items():
        path = source / filename
        if path.exists():
            conversations_by_split[split] = read_v2_jsonl(path)
    missing = [split for split in SPLIT_FILENAMES if split not in conversations_by_split]
    if missing:
        raise FileNotFoundError(f"missing_v2_split_files:{missing}")

    all_conversations = [
        conversation
        for split in SPLIT_FILENAMES
        for conversation in conversations_by_split[split]
    ]
    validation = validate_v2_conversations(
        all_conversations,
        taxonomy=taxonomy,
        known_concepts=known_concepts,
    )
    if not validation["valid"]:
        write_json(output / "dataset_v2_validation_report.json", validation)
        raise ValueError("dataset_v2_validation_failed")

    samples_by_split = {
        split: [
            v2_conversation_to_sample(item, label_overrides=label_overrides)
            for item in conversations
        ]
        for split, conversations in conversations_by_split.items()
    }
    all_samples = [sample for split in SPLIT_FILENAMES for sample in samples_by_split[split]]
    override_validation = _validate_sample_labels(
        all_samples,
        taxonomy=taxonomy,
        known_concepts=known_concepts,
    )
    if not override_validation["valid"]:
        write_json(output / "dataset_v2_override_validation_report.json", override_validation)
        raise ValueError("dataset_v2_override_validation_failed")
    split_map = {
        conversation.conversation_id: split
        for split, conversations in conversations_by_split.items()
        for conversation in conversations
    }
    counts = Counter(split_map.values())
    summary = _dataset_summary(conversations_by_split, all_samples)
    release_hash = hashlib.sha256(
        "\n".join(
            sorted(item.model_dump_json() for item in all_samples)
        ).encode("utf-8")
    ).hexdigest()
    summary.update(
        {
            "valid": True,
            "release_hash": release_hash,
            "source_split_dir": str(source.resolve()),
            "output_dir": str(output.resolve()),
            "previous_state_mode": "none",
            "label_override_count": len(label_overrides or []),
            "label_overrides_applied": sum(
                1 for sample in all_samples if sample.generation_metadata.extra.get("label_override_applied")
            ),
            "note": (
                "Dataset v2 labels are conversation-level final-turn labels; "
                "history turns intentionally do not carry PreviousSemanticState."
            ),
        }
    )

    write_jsonl(output / "intent_samples.jsonl", all_samples)
    write_json(
        output / "split_manifest.json",
        {
            "source": "gnn_method_v1_execution_best_final",
            "conversation_splits": split_map,
            "counts": dict(counts),
        },
    )
    write_json(output / "dataset_v2_summary.json", summary)
    write_json(output / "dataset_v2_validation_report.json", validation)
    write_json(output / "dataset_v2_override_validation_report.json", override_validation)
    return summary


def _matching_overrides(
    conversation: V2Conversation,
    overrides: list[LabelOverride],
) -> list[LabelOverride]:
    output = []
    for item in overrides:
        if item.conversation_id and item.conversation_id == conversation.conversation_id:
            output.append(item)
        elif item.micro_pattern_id and item.micro_pattern_id == conversation.micro_pattern_id:
            output.append(item)
    return output


def _validate_sample_labels(
    samples: list[IntentSample],
    *,
    taxonomy: IntentTaxonomy,
    known_concepts: set[str],
) -> dict:
    errors = []
    for sample in samples:
        if sample.labels is None:
            errors.append({"sample_id": sample.sample_id, "error": "missing_labels"})
            continue
        try:
            taxonomy.validate_labels(sample.labels)
        except Exception as exc:
            errors.append(
                {
                    "sample_id": sample.sample_id,
                    "error": "taxonomy_error",
                    "detail": str(exc),
                }
            )
        unknown = sorted(
            concept for concept in sample.labels.target_concepts if concept not in known_concepts
        )
        if unknown:
            errors.append(
                {
                    "sample_id": sample.sample_id,
                    "error": "unknown_target_concepts",
                    "concepts": unknown,
                }
            )
    return {
        "valid": not errors,
        "sample_count": len(samples),
        "error_count": len(errors),
        "errors": errors[:100],
    }


def _reference_distance(current_turn_id: int, references: list[ReferenceTarget]) -> int:
    if not references:
        return 0
    return min(current_turn_id - max(reference.target_turn for reference in references), 8)


def _dataset_summary(
    conversations_by_split: dict[str, list[V2Conversation]],
    samples: list[IntentSample],
) -> dict:
    category_counts = Counter(sample.category for sample in samples)
    mt_counts = Counter(
        sample.generation_metadata.extra.get("mt_id") or "ANCHOR"
        for sample in samples
    )
    micro_counts = Counter(
        sample.generation_metadata.extra.get("micro_pattern_id")
        or sample.generation_metadata.extra.get("anchor_id")
        or "UNKNOWN"
        for sample in samples
    )
    split_category_counts: dict[str, dict[str, int]] = {}
    split_mt_counts: dict[str, dict[str, int]] = {}
    for split, conversations in conversations_by_split.items():
        split_samples = [v2_conversation_to_sample(item) for item in conversations]
        split_category_counts[split] = dict(Counter(item.category for item in split_samples))
        split_mt_counts[split] = dict(
            Counter(
                item.generation_metadata.extra.get("mt_id") or "ANCHOR"
                for item in split_samples
            )
        )
    repeated_template_risk = _repeated_final_template_risk(samples)
    return {
        "schema": "method_v1_release_v2",
        "sample_count": len(samples),
        "split_counts": {
            split: len(conversations) for split, conversations in conversations_by_split.items()
        },
        "category_counts": dict(category_counts),
        "mt_counts": dict(mt_counts),
        "micro_pattern_count": len(micro_counts),
        "micro_pattern_counts": dict(micro_counts),
        "split_category_counts": split_category_counts,
        "split_mt_counts": split_mt_counts,
        "repeated_template_risk": repeated_template_risk,
    }


def _repeated_final_template_risk(samples: list[IntentSample]) -> dict:
    normalized: defaultdict[str, list[str]] = defaultdict(list)
    for sample in samples:
        text = sample.current_text.lower()
        try:
            seed_values = json.loads(sample.generation_metadata.entity_seed or "{}").values()
        except json.JSONDecodeError:
            seed_values = []
        for value in seed_values:
            if isinstance(value, str) and value:
                text = text.replace(value.lower(), "<entity>")
        normalized[" ".join(text.split())].append(sample.sample_id)
    repeated = {
        key: values for key, values in normalized.items() if len(values) > 1
    }
    return {
        "repeated_normalized_final_template_count": len(repeated),
        "max_repeat_count": max((len(values) for values in repeated.values()), default=0),
        "examples": [
            {"normalized_final": key, "count": len(values), "sample_ids": values[:5]}
            for key, values in sorted(
                repeated.items(),
                key=lambda item: len(item[1]),
                reverse=True,
            )[:20]
        ],
    }
