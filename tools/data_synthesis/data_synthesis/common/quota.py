from __future__ import annotations

import math
from typing import Dict, Optional


def scale_counts(base_counts: Dict[str, int], total: Optional[int] = None, *, label: str = "total") -> Dict[str, int]:
    base_total = sum(base_counts.values())
    if total is None:
        return dict(base_counts)
    if total <= 0:
        raise ValueError(f"{label} must be a positive integer.")
    if total == base_total:
        return dict(base_counts)

    raw_allocations = {
        key: (count * total) / base_total
        for key, count in base_counts.items()
    }
    scaled = {
        key: int(value)
        for key, value in raw_allocations.items()
    }
    remainder = total - sum(scaled.values())
    ranked_keys = sorted(
        raw_allocations,
        key=lambda key: (raw_allocations[key] - scaled[key], base_counts[key], key),
        reverse=True,
    )
    for key in ranked_keys[:remainder]:
        scaled[key] += 1
    return scaled


def apply_overgenerate_buffer(counts: Dict[str, int], buffer_ratio: float = 0.0) -> Dict[str, int]:
    """Return per-slot generation quotas with deterministic overgeneration."""
    if buffer_ratio <= 0:
        return dict(counts)
    return {
        key: max(value, int(math.ceil(value * (1.0 + buffer_ratio))))
        for key, value in counts.items()
    }
