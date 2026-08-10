from __future__ import annotations

from collections import defaultdict

import numpy as np
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score


SINGLE_LABEL_HEADS = [
    "intent",
    "operation",
    "scope",
    "target_relation",
    "transition",
    "reference_distance",
    "security_transition",
]


def calculate_metrics(records: list[dict]) -> dict:
    if not records:
        return {"sample_count": 0, "error": "no_records"}
    metrics: dict[str, float | dict] = {}
    metrics["sample_count"] = len(records)
    for head in SINGLE_LABEL_HEADS:
        truth = [record["truth"][head] for record in records]
        prediction = [record["prediction"][head] for record in records]
        metrics[f"{head}_accuracy"] = round(float(accuracy_score(truth, prediction)), 6)
        metrics[f"{head}_macro_f1"] = round(
            float(f1_score(truth, prediction, average="macro", zero_division=0)),
            6,
        )
    concept_truth = np.asarray([record["truth"]["concepts"] for record in records])
    concept_prediction = np.asarray([record["prediction"]["concepts"] for record in records])
    metrics["concept_micro_f1"] = round(
        float(f1_score(concept_truth, concept_prediction, average="micro", zero_division=0)),
        6,
    )
    by_category: defaultdict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_category[record["category"]].append(record)
    metrics["intent_by_category"] = {
        category: {
            "count": len(items),
            "accuracy": round(
                float(
                    accuracy_score(
                        [item["truth"]["intent"] for item in items],
                        [item["prediction"]["intent"] for item in items],
                    )
                ),
                6,
            ),
        }
        for category, items in by_category.items()
    }
    role_groups = _group_by_metadata(records, "role")
    if role_groups:
        metrics["intent_by_role"] = _group_accuracy(role_groups, "intent")
    for field, metric_name in (
        ("mt_id", "intent_by_mt_family"),
        ("micro_pattern_id", "intent_by_micro_pattern"),
    ):
        grouped = _group_by_metadata(records, field)
        if grouped:
            metrics[metric_name] = _group_accuracy(grouped, "intent")
            if field == "micro_pattern_id":
                metrics[metric_name] = dict(
                    sorted(
                        metrics[metric_name].items(),
                        key=lambda item: (-item[1]["count"], item[0]),
                    )
                )
    if all("truth_legacy" in record and "prediction_legacy" in record for record in records):
        metrics["legacy_route_accuracy"] = round(
            float(
                accuracy_score(
                    [record["truth_legacy"] for record in records],
                    [record["prediction_legacy"] for record in records],
                )
            ),
            6,
        )
    metrics["reference_link_presence_f1"] = round(
        float(
            f1_score(
                [int(record["truth"]["reference_distance"] > 0) for record in records],
                [int(record["prediction"]["reference_distance"] > 0) for record in records],
                zero_division=0,
            )
        ),
        6,
    )
    multi_turn = [
        record
        for record in records
        if record["category"] in {"BENIGN_MULTI_TURN", "MALICIOUS_MULTI_TURN"}
        and "predicted_sequence_class" in record
    ]
    if multi_turn:
        labels = ["BENIGN_MULTI_TURN", "MALICIOUS_MULTI_TURN"]
        matrix = confusion_matrix(
            [record["category"] for record in multi_turn],
            [record["predicted_sequence_class"] for record in multi_turn],
            labels=labels,
        )
        metrics["benign_malicious_multiturn_confusion"] = {
            "labels": labels,
            "matrix": matrix.tolist(),
        }
    metrics["confusion_matrices"] = {
        head: confusion_matrix(
            [record["truth"][head] for record in records],
            [record["prediction"][head] for record in records],
        ).tolist()
        for head in SINGLE_LABEL_HEADS
    }
    template_groups = _group_by_metadata(records, "repeated_final_template")
    if template_groups:
        metrics["repeated_template_slice"] = _group_accuracy(template_groups, "intent")
    return metrics


def _group_by_metadata(records: list[dict], field: str) -> dict[str, list[dict]]:
    grouped: defaultdict[str, list[dict]] = defaultdict(list)
    for record in records:
        metadata = record.get("metadata") or {}
        value = metadata.get(field)
        if value is None:
            continue
        grouped[str(value)].append(record)
    return dict(grouped)


def _group_accuracy(grouped: dict[str, list[dict]], head: str) -> dict[str, dict]:
    output: dict[str, dict] = {}
    for value, items in grouped.items():
        output[value] = {
            "count": len(items),
            "accuracy": round(
                float(
                    accuracy_score(
                        [item["truth"][head] for item in items],
                        [item["prediction"][head] for item in items],
                    )
                ),
                6,
            ),
            "macro_f1": round(
                float(
                    f1_score(
                        [item["truth"][head] for item in items],
                        [item["prediction"][head] for item in items],
                        average="macro",
                        zero_division=0,
                    )
                ),
                6,
            ),
        }
    return output
