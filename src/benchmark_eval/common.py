from __future__ import annotations

import math
from typing import Any


def rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": int(numerator),
        "denominator": int(denominator),
        "value": numerator / denominator if denominator else 0.0,
    }


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def percentile_nearest_rank(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def usage(row: dict[str, Any], key: str) -> int:
    return int((row.get("llm_usage") or {}).get(key) or 0)

