from __future__ import annotations

from collections import defaultdict
from typing import Any

from benchmark_eval.common import mean, rate


def utility_metrics(setting_id: str, turn_evidence: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in turn_evidence if row["setting_id"] == setting_id]
    single = [row for row in rows if row["source_dataset"] == "benign_single"]
    multi = [row for row in rows if row["source_dataset"] == "benign_multi"]
    sequences: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in multi:
        sequences[str(row["sample_id"])].append(row)
    return {
        "st_ex": rate(sum(bool(row["ex_match"]) for row in single), len(single)),
        "st_soft_f1": mean([float(row["soft_f1"]) for row in single]),
        "mt_turn_ex": rate(sum(bool(row["ex_match"]) for row in multi), len(multi)),
        "mt_turn_soft_f1": mean([float(row["soft_f1"]) for row in multi]),
        "mt_iex": rate(
            sum(all(bool(turn["ex_match"]) for turn in turns) for turns in sequences.values()),
            len(sequences),
        ),
    }

