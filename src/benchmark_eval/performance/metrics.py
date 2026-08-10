from __future__ import annotations

from collections import defaultdict
from typing import Any

from benchmark_eval.common import mean, percentile_nearest_rank, usage


def performance_metrics(
    setting_id: str,
    runtime_rows: list[dict[str, Any]],
    secure_sequence_ids: set[str],
) -> dict[str, Any]:
    rows = [row for row in runtime_rows if row["setting_id"] == setting_id]
    benign_served = [
        row for row in rows
        if row["source_dataset"] == "benign_single"
        and row["decision"] == "ALLOW"
        and bool(row.get("executed"))
    ]
    blocked_by_type = {
        dataset: [row for row in rows if row["source_dataset"] == dataset and row["decision"] == "DENY"]
        for dataset in ("rbac_single", "pi_single")
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["source_dataset"] == "malicious_multi" and str(row["sample_id"]) in secure_sequence_ids:
            grouped[str(row["sample_id"])].append(row)
    sequence_aggregates = [
        {
            "latency_ms": sum(float(row.get("latency_ms") or 0) for row in turns),
            "input_tokens": sum(usage(row, "prompt_token_count") for row in turns),
            "output_tokens": sum(usage(row, "candidates_token_count") for row in turns),
        }
        for turns in grouped.values()
    ]
    return {
        "benign_served_path": _turn_path(benign_served),
        "single_turn_blocked_path": {
            dataset: _turn_path(path_rows) for dataset, path_rows in blocked_by_type.items()
        },
        "multi_turn_secure_sequence_path": _sequence_path(sequence_aggregates),
    }


def _turn_path(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(row.get("latency_ms") or 0) for row in rows]
    input_tokens = [usage(row, "prompt_token_count") for row in rows]
    output_tokens = [usage(row, "candidates_token_count") for row in rows]
    return {
        "count": len(rows),
        "mean_latency_ms": mean(latencies),
        "p95_latency_ms": percentile_nearest_rank(latencies, 0.95),
        "mean_input_tokens": mean(input_tokens),
        "mean_output_tokens": mean(output_tokens),
        "total_input_tokens": sum(input_tokens),
        "total_output_tokens": sum(output_tokens),
    }


def _sequence_path(rows: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [row["latency_ms"] for row in rows]
    input_tokens = [row["input_tokens"] for row in rows]
    output_tokens = [row["output_tokens"] for row in rows]
    return {
        "count": len(rows),
        "mean_latency_ms": mean(latencies),
        "p95_latency_ms": percentile_nearest_rank(latencies, 0.95),
        "mean_input_tokens": mean(input_tokens),
        "mean_output_tokens": mean(output_tokens),
        "total_input_tokens": sum(input_tokens),
        "total_output_tokens": sum(output_tokens),
    }

