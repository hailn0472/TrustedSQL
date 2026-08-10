from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from trustedsql_gnn.contracts import IntentSample
from trustedsql_gnn.data.io import read_jsonl, write_json, write_jsonl


LABEL_HEADS = [
    "intent",
    "operation",
    "scope",
    "target_relation",
    "transition",
    "security_transition",
]


def run_generalization_audit(
    *,
    release_dir: str | Path,
    predictions_path: str | Path,
    output_dir: str | Path,
    focus: list[str],
    max_cases_per_group: int = 10,
    nearest_k: int = 5,
) -> dict:
    release_path = Path(release_dir)
    output_path = Path(output_dir)
    split_map = json.loads(
        (release_path / "split_manifest.json").read_text(encoding="utf-8")
    )["conversation_splits"]
    samples = read_jsonl(release_path / "intent_samples.jsonl", IntentSample)
    samples_by_id = {sample.sample_id: sample for sample in samples}
    train_samples = [
        sample for sample in samples if split_map.get(sample.conversation_id) == "train"
    ]
    predictions = _read_jsonl(predictions_path)
    focus_records = [
        record
        for record in predictions
        if record.get("mt_id") in focus or record.get("micro_pattern_id") in focus
    ]
    cases = [
        _audit_case(
            record=record,
            sample=samples_by_id.get(record["sample_id"]),
            train_samples=train_samples,
            nearest_k=nearest_k,
        )
        for record in focus_records
    ]
    summary = _audit_summary(cases, train_samples)
    report = _render_markdown(summary, cases, max_cases_per_group=max_cases_per_group)

    output_path.mkdir(parents=True, exist_ok=True)
    cases_path = output_path / "generalization_audit_cases.jsonl"
    summary_path = output_path / "generalization_audit_summary.json"
    report_path = output_path / "generalization_audit_report.md"
    write_jsonl(cases_path, cases)
    write_json(summary_path, summary)
    report_path.write_text(report, encoding="utf-8")
    return {
        "sample_count": len(focus_records),
        "case_path": str(cases_path.resolve()),
        "summary_path": str(summary_path.resolve()),
        "report_path": str(report_path.resolve()),
        "recommended_next_action": summary["recommended_next_action"],
        "recommendation_reasons": summary["recommendation_reasons"],
    }


def _audit_case(
    *,
    record: dict,
    sample: IntentSample | None,
    train_samples: list[IntentSample],
    nearest_k: int,
) -> dict:
    final_text = record["turns"][-1]["text"]
    same_mt_train = [
        item
        for item in train_samples
        if item.generation_metadata.extra.get("mt_id") == record.get("mt_id")
    ]
    nearest = _nearest_samples(final_text, same_mt_train, nearest_k)
    label_flags = _label_suspect_flags(record)
    nearest_conflicts = [
        item
        for item in nearest
        if item["similarity"] >= 0.45
        and item["labels"]["intent"] != record["gold_labels"]["intent"]
    ]
    same_gold_neighbors = [
        item for item in nearest if item["labels"]["intent"] == record["gold_labels"]["intent"]
    ]
    coverage_gap = (
        not same_gold_neighbors
        or max((item["similarity"] for item in same_gold_neighbors), default=0.0) < 0.35
    )
    model_objective_risk = (
        not label_flags
        and not coverage_gap
        and "intent" in record["wrong_heads"]
        and _top_probability(record, "intent") >= 0.9
    )
    return {
        "sample_id": record["sample_id"],
        "conversation_id": record["conversation_id"],
        "mt_id": record.get("mt_id"),
        "micro_pattern_id": record.get("micro_pattern_id"),
        "category": record["category"],
        "turns": record["turns"],
        "final_text": final_text,
        "gold_labels": record["gold_labels"],
        "predicted_labels": record["predicted_labels"],
        "wrong_heads": record["wrong_heads"],
        "top_k": record.get("top_k", {}),
        "graph_evidence": record.get("graph_evidence", {}),
        "label_suspect": bool(label_flags),
        "label_suspect_flags": label_flags,
        "possible_label_conflict": bool(nearest_conflicts),
        "coverage_gap": coverage_gap,
        "model_objective_risk": model_objective_risk,
        "nearest_train_neighbors": nearest,
        "sample_metadata": sample.generation_metadata.extra if sample else record.get("metadata", {}),
    }


def _audit_summary(cases: list[dict], train_samples: list[IntentSample]) -> dict:
    by_micro: defaultdict[str, list[dict]] = defaultdict(list)
    for case in cases:
        by_micro[case["micro_pattern_id"]].append(case)
    micro_reports = {}
    for micro, items in sorted(by_micro.items()):
        micro_reports[micro] = {
            "mt_id": items[0]["mt_id"],
            "count": len(items),
            "intent_wrong": sum("intent" in item["wrong_heads"] for item in items),
            "label_suspect_count": sum(item["label_suspect"] for item in items),
            "possible_label_conflict_count": sum(item["possible_label_conflict"] for item in items),
            "coverage_gap_count": sum(item["coverage_gap"] for item in items),
            "model_objective_risk_count": sum(item["model_objective_risk"] for item in items),
            "gold_distribution": _label_distribution(items, "gold_labels"),
            "predicted_distribution": _label_distribution(items, "predicted_labels"),
            "top_final_ngrams": _top_ngrams([item["final_text"] for item in items]),
            "top_current_concepts": Counter(
                concept
                for item in items
                for concept in item.get("graph_evidence", {}).get("current_turn_concepts", [])
            ).most_common(20),
        }
    label_suspect_total = sum(item["label_suspect"] for item in cases)
    conflict_total = sum(item["possible_label_conflict"] for item in cases)
    coverage_gap_total = sum(item["coverage_gap"] for item in cases)
    objective_total = sum(item["model_objective_risk"] for item in cases)
    action, reasons = _recommend_action(
        label_suspect_total=label_suspect_total,
        conflict_total=conflict_total,
        coverage_gap_total=coverage_gap_total,
        objective_total=objective_total,
        case_count=len(cases),
    )
    return {
        "case_count": len(cases),
        "train_sample_count": len(train_samples),
        "micro_patterns": micro_reports,
        "global_flags": {
            "label_suspect_total": label_suspect_total,
            "possible_label_conflict_total": conflict_total,
            "coverage_gap_total": coverage_gap_total,
            "model_objective_risk_total": objective_total,
        },
        "recommended_next_action": action,
        "recommendation_reasons": reasons,
    }


def _recommend_action(
    *,
    label_suspect_total: int,
    conflict_total: int,
    coverage_gap_total: int,
    objective_total: int,
    case_count: int,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if label_suspect_total >= max(10, math.ceil(case_count * 0.1)) or conflict_total >= max(10, math.ceil(case_count * 0.1)):
        reasons.append("Label suspicion/conflict is high enough that new training may reinforce inconsistent labels.")
        if label_suspect_total:
            reasons.append(f"label_suspect_total={label_suspect_total}")
        if conflict_total:
            reasons.append(f"possible_label_conflict_total={conflict_total}")
        return "fix_labels_first", reasons
    if coverage_gap_total >= max(10, math.ceil(case_count * 0.2)):
        reasons.append("Hard holdout cases lack close same-intent train neighbors; targeted contrastive data is likely needed.")
        reasons.append(f"coverage_gap_total={coverage_gap_total}")
        return "generate_more_data", reasons
    if objective_total:
        reasons.append("Close train neighbors exist but high-confidence wrong predictions remain.")
        reasons.append(f"model_objective_risk_total={objective_total}")
        return "change_model_objective", reasons
    reasons.append("Legacy/security route may be sufficient for current objective; no major action indicated by audit.")
    return "no_action", reasons


def _nearest_samples(text: str, candidates: list[IntentSample], k: int) -> list[dict]:
    query_tokens = _tokens(text)
    scored = []
    for sample in candidates:
        score = _jaccard(query_tokens, _tokens(sample.current_text))
        labels = sample.labels
        scored.append(
            {
                "sample_id": sample.sample_id,
                "micro_pattern_id": sample.pattern_id,
                "category": sample.category,
                "similarity": round(score, 6),
                "final_text": sample.current_text,
                "labels": {
                    "intent": labels.semantic_intent if labels else None,
                    "operation": labels.operation if labels else None,
                    "scope": labels.scope if labels else None,
                    "target_relation": labels.target_relation if labels else None,
                    "transition": labels.transition if labels else None,
                    "security_transition": labels.security_transition if labels else None,
                    "target_concepts": labels.target_concepts if labels else [],
                },
            }
        )
    return sorted(scored, key=lambda item: (-item["similarity"], item["sample_id"]))[:k]


def _label_suspect_flags(record: dict) -> list[str]:
    text = record["turns"][-1]["text"].lower()
    gold = record["gold_labels"]
    micro = record.get("micro_pattern_id") or ""
    flags: list[str] = []
    if any(term in text for term in ("lecturer", "teacher", "instructor")) and gold["intent"] != "LECTURER_LOOKUP":
        flags.append("lecturer_surface_not_lecturer_intent")
    if "LECTURER" in micro and gold["intent"] != "LECTURER_LOOKUP":
        flags.append("lecturer_micro_pattern_not_lecturer_intent")
    if "SCHEDULE" in micro and gold["intent"] == "ATTENDANCE_LOOKUP":
        flags.append("schedule_micro_pattern_labeled_attendance_review")
    if any(term in text for term in ("schedule", "session", "room", "slot", "start time")) and gold["intent"] == "ATTENDANCE_LOOKUP":
        flags.append("schedule_surface_labeled_attendance_review")
    if (
        any(term in text for term in ("all sections", "all classes", "all student records", "export", "system"))
        and gold["scope"] == "GLOBAL"
        and gold["intent"] not in {"ADMIN_DATA_QUERY", "ROSTER_LOOKUP"}
    ):
        flags.append("global_export_surface_with_fine_private_intent_review")
    return flags


def _label_distribution(items: list[dict], field: str) -> dict:
    output = {}
    for head in LABEL_HEADS:
        output[head] = dict(Counter(item[field][head] for item in items))
    output["target_concepts"] = dict(
        Counter(concept for item in items for concept in item[field]["target_concepts"])
    )
    return output


def _top_ngrams(texts: list[str], n: int = 2, limit: int = 25) -> list[tuple[str, int]]:
    counts = Counter()
    for text in texts:
        toks = list(_tokens(text))
        for idx in range(0, max(0, len(toks) - n + 1)):
            gram = " ".join(toks[idx:idx + n])
            if gram not in _STOP_NGRAMS:
                counts[gram] += 1
    return counts.most_common(limit)


def _top_probability(record: dict, head: str) -> float:
    values = record.get("top_k", {}).get(head) or []
    return float(values[0]["probability"]) if values else 0.0


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z0-9_]+", text.lower())
        if token not in _STOP_WORDS and len(token) > 1
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _read_jsonl(path: str | Path) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _render_markdown(summary: dict, cases: list[dict], *, max_cases_per_group: int) -> str:
    lines = [
        "# Generalization Audit Report",
        "",
        f"Cases: `{summary['case_count']}`",
        f"Recommended next action: `{summary['recommended_next_action']}`",
        "",
        "## Recommendation Reasons",
        "",
    ]
    lines.extend(f"- {reason}" for reason in summary["recommendation_reasons"])
    lines.extend(["", "## Global Flags", ""])
    for key, value in summary["global_flags"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Micro-Pattern Summary", ""])
    lines.append("| Micro-pattern | MT | Count | Intent wrong | Label suspect | Conflicts | Coverage gaps | Objective risk |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for micro, item in summary["micro_patterns"].items():
        lines.append(
            f"| {micro} | {item['mt_id']} | {item['count']} | {item['intent_wrong']} | "
            f"{item['label_suspect_count']} | {item['possible_label_conflict_count']} | "
            f"{item['coverage_gap_count']} | {item['model_objective_risk_count']} |"
        )
    by_micro: defaultdict[str, list[dict]] = defaultdict(list)
    for case in cases:
        by_micro[case["micro_pattern_id"]].append(case)
    lines.extend(["", "## Representative Cases", ""])
    for micro, items in sorted(by_micro.items()):
        lines.append(f"### {micro}")
        micro_summary = summary["micro_patterns"][micro]
        lines.append("")
        lines.append(f"- Top ngrams: `{micro_summary['top_final_ngrams'][:8]}`")
        lines.append(f"- Top concepts: `{micro_summary['top_current_concepts'][:8]}`")
        for case in items[:max_cases_per_group]:
            lines.extend(_render_case(case))
        lines.append("")
    return "\n".join(lines) + "\n"


def _render_case(case: dict) -> list[str]:
    neighbors = "; ".join(
        f"{item['micro_pattern_id']}:{item['labels']['intent']}@{item['similarity']}"
        for item in case["nearest_train_neighbors"][:3]
    )
    return [
        "",
        f"- `{case['sample_id']}`",
        f"  - final: {case['final_text']}",
        f"  - gold: `{case['gold_labels']['intent']}` / pred: `{case['predicted_labels']['intent']}`",
        f"  - wrong heads: `{', '.join(case['wrong_heads'])}`",
        f"  - label flags: `{case['label_suspect_flags']}`",
        f"  - coverage_gap={case['coverage_gap']}, possible_label_conflict={case['possible_label_conflict']}, model_objective_risk={case['model_objective_risk']}",
        f"  - nearest train: {neighbors}",
    ]


_STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "only", "from", "into", "show",
    "use", "now", "same", "previous", "as", "to", "in", "of", "it", "my", "me",
    "is", "are", "be", "not", "just", "first", "then", "than", "up", "on",
}

_STOP_NGRAMS = {"as safe", "safe scoped", "scoped refinement", "in the", "for the"}
