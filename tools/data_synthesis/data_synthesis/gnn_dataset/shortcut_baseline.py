from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Sequence

from .graph_export import record_to_feature_graph, record_to_target


def build_shortcut_baseline_report(
    records: Sequence[Dict[str, Any]],
    *,
    protocol: str = "balanced",
    fail_threshold: float = 0.80,
) -> Dict[str, Any]:
    rows = []
    for record in records:
        graph = record_to_feature_graph(record)
        target = record_to_target(record)
        predicted = "MALICIOUS" if _has_multiturn_shortcut(graph) else "BENIGN"
        actual = str(target.get("graph_label") or "")
        split = str(
            (((record.get("protocol_assignments") or {}).get(protocol) or {}).get("split"))
            or "unassigned"
        )
        rows.append(
            {
                "id": record.get("id"),
                "split": split,
                "actual": actual,
                "predicted": predicted,
                "correct": predicted == actual,
                "turn_count": _turn_count(graph),
                "has_next_turn": _has_next_turn(graph),
            }
        )
    by_split = {
        split: _metrics([row for row in rows if row["split"] == split])
        for split in sorted({row["split"] for row in rows})
    }
    overall = _metrics(rows)
    blocking_splits = [
        split
        for split in ("validation", "test")
        if by_split.get(split, {}).get("accuracy", 0.0) >= fail_threshold
    ]
    multi_subset = _metrics([row for row in rows if int(row["turn_count"]) > 1])
    return {
        "baseline_name": "turn_count_or_next_turn_implies_malicious",
        "protocol": protocol,
        "fail_threshold": fail_threshold,
        "ok": not blocking_splits,
        "blocking_splits": blocking_splits,
        "overall": overall,
        "by_split": by_split,
        "multi_turn_subset": multi_subset,
        "confusion": _confusion(rows),
    }


def _has_multiturn_shortcut(graph: Dict[str, Any]) -> bool:
    return _turn_count(graph) > 1 or _has_next_turn(graph)


def _turn_count(graph: Dict[str, Any]) -> int:
    return sum(1 for node in graph.get("nodes") or [] if node.get("type") == "Turn")


def _has_next_turn(graph: Dict[str, Any]) -> bool:
    return any(edge.get("type") == "next_turn" for edge in graph.get("edges") or [])


def _metrics(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    correct = sum(1 for row in rows if row["correct"])
    labels = Counter(str(row["actual"]) for row in rows)
    predictions = Counter(str(row["predicted"]) for row in rows)
    return {
        "total": total,
        "accuracy": correct / total if total else 0.0,
        "label_counts": dict(labels),
        "prediction_counts": dict(predictions),
    }


def _confusion(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    counts = Counter(f"{row['actual']}->{row['predicted']}" for row in rows)
    return dict(counts)
