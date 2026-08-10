from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from trustedsql_gnn.contracts import HistoryTurn, IntentConversation, IntentSample
from trustedsql_gnn.data.io import write_json, write_jsonl
from trustedsql_gnn.data.validation import (
    holdout_contamination_report,
    load_holdout_text_hashes,
    validate_conversations,
)
from trustedsql_gnn.taxonomy import IntentTaxonomy


def conversation_to_sample(conversation: IntentConversation) -> IntentSample:
    current = conversation.turns[-1]
    history = [
        HistoryTurn(
            turn_id=turn.turn_id,
            text=turn.text,
            predicted_or_gold_state=turn.labels,
            mentions=turn.mentions,
        )
        for turn in conversation.turns[:-1]
    ]
    return IntentSample(
        sample_id=f"{conversation.conversation_id}::turn_{current.turn_id}",
        conversation_id=conversation.conversation_id,
        pattern_id=conversation.pattern_id,
        category=conversation.category,
        role=conversation.role,
        current_turn_id=current.turn_id,
        history=history,
        current_text=current.text,
        current_mentions=current.mentions,
        labels=current.labels,
        generation_metadata=conversation.generation_metadata,
    )


def _group_key(conversation: IntentConversation) -> str:
    meta = conversation.generation_metadata
    if meta.contrastive_pair_id:
        return f"contrastive:{meta.contrastive_pair_id}"
    return f"surface:{meta.surface_variant_id}|entity:{meta.entity_seed}"


def assign_splits(
    conversations: list[IntentConversation],
    *,
    seed: int,
    ratios: tuple[float, float, float] = (0.7, 0.15, 0.15),
) -> dict[str, str]:
    groups: defaultdict[str, list[IntentConversation]] = defaultdict(list)
    for conversation in conversations:
        groups[_group_key(conversation)].append(conversation)
    keys = sorted(groups)
    random.Random(seed).shuffle(keys)
    total = len(keys)
    train_end = round(total * ratios[0])
    validation_end = train_end + round(total * ratios[1])
    split_by_group = {
        key: "train" if idx < train_end else "validation" if idx < validation_end else "test"
        for idx, key in enumerate(keys)
    }
    return {
        conversation.conversation_id: split_by_group[_group_key(conversation)]
        for conversation in conversations
    }


def prepare_release(
    *,
    conversations: list[IntentConversation],
    taxonomy: IntentTaxonomy,
    pattern_bank: dict,
    output_dir: str | Path,
    holdout_manifest: str | Path,
    seed: int = 20260609,
    graph_builder=None,
    enforce_quota: bool = True,
    known_concepts: set[str] | None = None,
) -> dict:
    output = Path(output_dir)
    patterns_by_id = {item["pattern_id"]: item for item in pattern_bank["patterns"]}
    if known_concepts is None and graph_builder is not None:
        known_concepts = set(graph_builder.extractor.catalog)
    validation = validate_conversations(
        conversations,
        taxonomy,
        patterns_by_id,
        known_concepts=known_concepts,
    )
    holdout = holdout_contamination_report(
        conversations,
        load_holdout_text_hashes(holdout_manifest),
    )
    if not validation["valid"] or not holdout["valid"]:
        write_json(output / "validation_report.json", validation)
        write_json(output / "leakage_report.json", holdout)
        raise ValueError("release_validation_failed")

    split_map = assign_splits(conversations, seed=seed)
    samples = [conversation_to_sample(item) for item in conversations]
    write_jsonl(output / "intent_conversations.jsonl", conversations)
    write_jsonl(output / "intent_samples.jsonl", samples)
    if graph_builder is not None:
        graphs = [graph_builder.build(sample) for sample in samples]
        write_jsonl(output / "intent_graphs.jsonl", graphs)
        write_jsonl(
            output / "intent_targets.jsonl",
            [
                {
                    "sample_id": sample.sample_id,
                    "conversation_id": sample.conversation_id,
                    "labels": sample.labels.model_dump(mode="json") if sample.labels else None,
                }
                for sample in samples
            ],
        )
    write_json(
        output / "split_manifest.json",
        {
            "seed": seed,
            "conversation_splits": split_map,
            "counts": dict(Counter(split_map.values())),
        },
    )
    category_counts = Counter(item.category for item in conversations)
    pattern_counts = Counter(item.pattern_id for item in conversations)
    coverage = {
        "conversation_count": len(conversations),
        "sample_count": len(samples),
        "category_counts": dict(category_counts),
        "pattern_counts": dict(pattern_counts),
        "required_quota": pattern_bank["release_quota"],
        "quota_delta": {
            category: category_counts.get(category, 0) - required
            for category, required in pattern_bank["release_quota"].items()
        },
    }
    write_json(output / "coverage_report.json", coverage)
    write_json(output / "duplicate_report.json", validation)
    write_json(output / "leakage_report.json", holdout)
    quota_valid = all(delta == 0 for delta in coverage["quota_delta"].values())
    if enforce_quota and not quota_valid:
        write_json(
            output / "release_summary.json",
            {
                "valid": False,
                "reason": "release_quota_mismatch",
                "sample_count": len(samples),
                "output_dir": str(output.resolve()),
            },
        )
        raise ValueError("release_quota_mismatch")
    release_hash = hashlib.sha256(
        "".join(sorted(item.sample_id for item in samples)).encode("utf-8")
    ).hexdigest()
    summary = {
        "valid": True,
        "release_hash": release_hash,
        "sample_count": len(samples),
        "quota_valid": quota_valid,
        "output_dir": str(output.resolve()),
    }
    write_json(output / "release_summary.json", summary)
    return summary
