from __future__ import annotations

import random
from collections import Counter

from trustedsql_gnn.contracts import IntentSample


def training_samples_for_epoch(
    samples: list[IntentSample],
    *,
    mode: str,
    seed: int,
    epoch: int,
    max_weight: float = 3.0,
) -> list[IntentSample]:
    rng = random.Random(f"{seed}:{epoch}:{mode}")
    if mode == "shuffle":
        output = list(samples)
        rng.shuffle(output)
        return output
    if mode != "family_micro_balanced":
        raise ValueError(f"unknown_sampling_mode:{mode}")
    if not samples:
        return []

    keys = [_sampling_group(sample) for sample in samples]
    counts = Counter(keys)
    average_group_size = len(samples) / max(1, len(counts))
    weights = [
        min(max_weight, max(1.0 / max_weight, average_group_size / counts[key]))
        for key in keys
    ]
    return rng.choices(samples, weights=weights, k=len(samples))


def sampling_distribution(samples: list[IntentSample]) -> dict[str, int]:
    return dict(Counter(_sampling_group(sample) for sample in samples))


def _sampling_group(sample: IntentSample) -> str:
    metadata = sample.generation_metadata.extra
    mt_id = metadata.get("mt_id") or "ANCHOR"
    pattern = (
        metadata.get("micro_pattern_id")
        or metadata.get("anchor_id")
        or sample.pattern_id
    )
    return f"{mt_id}:{pattern}"
