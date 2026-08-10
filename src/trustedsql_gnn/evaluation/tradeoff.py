from __future__ import annotations

from collections import Counter


def compare_prediction_records(
    baseline: list[dict],
    candidate: list[dict],
    *,
    correctness_field: str = "legacy_intent_correct",
) -> dict:
    baseline_by_id = {item["sample_id"]: item for item in baseline}
    candidate_by_id = {item["sample_id"]: item for item in candidate}
    shared = sorted(set(baseline_by_id) & set(candidate_by_id))
    groups = {
        "improved_cases": [],
        "regressed_cases": [],
        "unchanged_correct_cases": [],
        "unchanged_incorrect_cases": [],
        "ambiguous_tradeoff_cases": [],
    }
    for sample_id in shared:
        before = baseline_by_id[sample_id]
        after = candidate_by_id[sample_id]
        before_correct = before.get(correctness_field)
        after_correct = after.get(correctness_field)
        case = {"sample_id": sample_id, "baseline": before, "candidate": after}
        if before_correct is None or after_correct is None:
            groups["ambiguous_tradeoff_cases"].append(case)
        elif not before_correct and after_correct:
            groups["improved_cases"].append(case)
        elif before_correct and not after_correct:
            groups["regressed_cases"].append(case)
        elif before_correct:
            groups["unchanged_correct_cases"].append(case)
        else:
            groups["unchanged_incorrect_cases"].append(case)
    return {
        "shared_sample_count": len(shared),
        "missing_from_candidate": sorted(set(baseline_by_id) - set(candidate_by_id)),
        "new_in_candidate": sorted(set(candidate_by_id) - set(baseline_by_id)),
        "group_counts": {name: len(items) for name, items in groups.items()},
        "transition_counts": dict(
            Counter(
                f"{baseline_by_id[item]['predicted_legacy_intent']} -> "
                f"{candidate_by_id[item]['predicted_legacy_intent']}"
                for item in shared
                if baseline_by_id[item].get("predicted_legacy_intent")
                != candidate_by_id[item].get("predicted_legacy_intent")
            )
        ),
        **groups,
        "decision": "researcher_review_required",
    }
