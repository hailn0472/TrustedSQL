from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import torch

from trustedsql_gnn.contracts import IntentResolution, IntentSample
from trustedsql_gnn.data.io import read_jsonl, write_json, write_jsonl
from trustedsql_gnn.evaluation.metrics import calculate_metrics
from trustedsql_gnn.graph.builder import IntentGraphBuilder
from trustedsql_gnn.graph.encoder import IntentGraphEncoder
from trustedsql_gnn.integration.legacy_adapter import LegacyIntentAdapter
from trustedsql_gnn.model.gnn import RelationAwareIntentGNN
from trustedsql_gnn.taxonomy import IntentTaxonomy


SINGLE_LABEL_SPACES = {
    "intent": "semantic_intents",
    "operation": "operations",
    "scope": "scopes",
    "target_relation": "target_relations",
    "transition": "transitions",
    "security_transition": "security_transitions",
}

LOGIT_HEADS = {
    "intent": "intent_logits",
    "scope": "scope_logits",
    "transition": "transition_logits",
    "security_transition": "security_transition_logits",
}


def diagnose_split(
    *,
    root: str | Path,
    release_dir: str | Path,
    split: str,
    checkpoint_path: str | Path,
    runner,
    output_prefix: str,
    top_k: int = 3,
) -> dict:
    root_path = Path(root)
    release_path = Path(release_dir)
    split_map = json.loads(
        (release_path / "split_manifest.json").read_text(encoding="utf-8")
    )["conversation_splits"]
    samples = [
        sample
        for sample in read_jsonl(release_path / "intent_samples.jsonl", IntentSample)
        if split_map.get(sample.conversation_id) == split
    ]
    checkpoint = torch.load(checkpoint_path, map_location=runner.device, weights_only=False)
    model = RelationAwareIntentGNN(**checkpoint["model_config"]).to(runner.device)
    model.load_state_dict(checkpoint["model_state"])
    model.eval()

    records = diagnostic_records(
        samples=samples,
        model=model,
        taxonomy=runner.taxonomy,
        builder=runner.builder,
        encoder=runner.encoder,
        legacy_adapter=runner.legacy_adapter,
        device=runner.device,
        top_k=top_k,
    )
    numeric_records = [record["numeric_record"] for record in records]
    summary = summarize_diagnostics(
        records=records,
        numeric_records=numeric_records,
        root=root_path,
        split=split,
    )
    reports_dir = root_path / "artifacts" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    prediction_path = reports_dir / f"{output_prefix}_predictions.jsonl"
    summary_path = reports_dir / f"{output_prefix}_error_summary.json"
    markdown_path = reports_dir / f"{output_prefix}_error_report.md"
    write_jsonl(prediction_path, [_strip_numeric_record(record) for record in records])
    write_json(summary_path, summary)
    markdown_path.write_text(
        render_markdown_report(summary, records),
        encoding="utf-8",
    )
    return {
        "split": split,
        "sample_count": len(samples),
        "prediction_path": str(prediction_path.resolve()),
        "summary_path": str(summary_path.resolve()),
        "markdown_path": str(markdown_path.resolve()),
        "metrics": summary["metrics"],
    }


def diagnostic_records(
    *,
    samples: list[IntentSample],
    model: RelationAwareIntentGNN,
    taxonomy: IntentTaxonomy,
    builder: IntentGraphBuilder,
    encoder: IntentGraphEncoder,
    legacy_adapter: LegacyIntentAdapter,
    device: str,
    top_k: int = 3,
) -> list[dict]:
    records: list[dict] = []
    with torch.no_grad():
        for sample in samples:
            graph = builder.build(sample)
            encoded = encoder.encode(graph, sample, device)
            outputs = model(encoded.x, encoded.edge_indices, encoded.current_node_idx)
            prediction = _decode_indices(outputs)
            truth = _truth_from_encoded(encoded)
            gold_labels = decode_label_dict(truth, taxonomy, encoder.concepts)
            predicted_labels = decode_label_dict(prediction, taxonomy, encoder.concepts)
            wrong_heads = wrong_prediction_heads(gold_labels, predicted_labels)
            metadata = _sample_metadata(sample)
            numeric_record = {
                "sample_id": sample.sample_id,
                "category": sample.category,
                "metadata": metadata,
                "truth": truth,
                "prediction": prediction,
                "truth_legacy": _legacy_from_labels(
                    sample,
                    truth,
                    taxonomy,
                    encoder.concepts,
                    legacy_adapter,
                ),
                "prediction_legacy": _legacy_from_labels(
                    sample,
                    prediction,
                    taxonomy,
                    encoder.concepts,
                    legacy_adapter,
                ),
                "predicted_sequence_class": (
                    "BENIGN_MULTI_TURN"
                    if predicted_labels["security_transition"] == "NONE"
                    else "MALICIOUS_MULTI_TURN"
                ),
            }
            records.append(
                {
                    "sample_id": sample.sample_id,
                    "conversation_id": sample.conversation_id,
                    "category": sample.category,
                    "mt_id": metadata.get("mt_id"),
                    "micro_pattern_id": metadata.get("micro_pattern_id"),
                    "turns": _sample_turns(sample),
                    "gold_labels": gold_labels,
                    "predicted_labels": predicted_labels,
                    "wrong_heads": wrong_heads,
                    "legacy_gold": numeric_record["truth_legacy"],
                    "legacy_pred": numeric_record["prediction_legacy"],
                    "top_k": {
                        head: _topk(outputs[logit_key], _space_values(taxonomy, head), top_k)
                        for head, logit_key in LOGIT_HEADS.items()
                    },
                    "graph_evidence": graph_evidence_summary(graph, sample),
                    "metadata": metadata,
                    "numeric_record": numeric_record,
                }
            )
    return records


def decode_label_dict(
    labels: dict[str, Any],
    taxonomy: IntentTaxonomy,
    concepts: list[str],
) -> dict[str, Any]:
    output = {
        head: _space_values(taxonomy, head)[int(labels[head])]
        for head in SINGLE_LABEL_SPACES
    }
    output["reference_distance"] = int(labels["reference_distance"])
    output["target_concepts"] = [
        concept for concept, enabled in zip(concepts, labels["concepts"]) if int(enabled)
    ]
    return output


def wrong_prediction_heads(gold_labels: dict[str, Any], predicted_labels: dict[str, Any]) -> list[str]:
    heads = [
        "intent",
        "operation",
        "scope",
        "target_relation",
        "transition",
        "reference_distance",
        "security_transition",
    ]
    wrong = [head for head in heads if gold_labels[head] != predicted_labels[head]]
    if set(gold_labels["target_concepts"]) != set(predicted_labels["target_concepts"]):
        wrong.append("target_concepts")
    return wrong


def graph_evidence_summary(graph, sample: IntentSample) -> dict:
    nodes_by_id = {node.node_id: node for node in graph.nodes}
    concept_by_turn: defaultdict[int, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if edge.edge_type != "represents":
            continue
        source = nodes_by_id.get(edge.source)
        target = nodes_by_id.get(edge.target)
        if source is None or target is None or source.turn_id is None:
            continue
        concept_by_turn[source.turn_id].add(target.label)
    reference_nodes = [node for node in graph.nodes if node.node_type == "ReferenceExpression"]
    scope_candidates = sorted(
        {
            node.label
            for node in graph.nodes
            if node.node_type == "ScopeCandidate"
        }
    )
    target_candidates = sorted(
        {
            node.label
            for node in graph.nodes
            if node.node_type == "TargetCandidate"
        }
    )
    current_concepts = sorted(concept_by_turn.get(sample.current_turn_id, set()))
    history_concepts = {
        str(turn_id): sorted(concepts)
        for turn_id, concepts in sorted(concept_by_turn.items())
        if turn_id != sample.current_turn_id
    }
    return {
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "current_turn_concepts": current_concepts,
        "history_turn_concepts": history_concepts,
        "has_reference_expression": bool(reference_nodes),
        "reference_surfaces": [node.label for node in reference_nodes],
        "scope_candidates": scope_candidates,
        "target_candidates": target_candidates,
        "missing_reference_evidence": _looks_referential(sample.current_text) and not reference_nodes,
        "missing_concept_evidence": not current_concepts,
        "ambiguous_scope_candidates": len(scope_candidates) > 1,
    }


def summarize_diagnostics(
    *,
    records: list[dict],
    numeric_records: list[dict],
    root: Path,
    split: str,
) -> dict:
    metrics = calculate_metrics(numeric_records)
    wrong_head_counts = Counter(
        head for record in records for head in record["wrong_heads"]
    )
    intent_confusion = Counter(
        (
            record["gold_labels"]["intent"],
            record["predicted_labels"]["intent"],
        )
        for record in records
    )
    failed_groups = _failed_groups(records)
    test_metrics = _load_test_metrics(root)
    return {
        "split": split,
        "sample_count": len(records),
        "metrics": metrics,
        "test_metric_delta": _metric_delta(test_metrics, metrics),
        "wrong_head_counts": dict(wrong_head_counts),
        "intent_confusion": [
            {"gold": gold, "predicted": predicted, "count": count}
            for (gold, predicted), count in intent_confusion.most_common()
        ],
        "failed_groups": failed_groups,
        "graph_evidence_flags": {
            "missing_reference_evidence": sum(
                int(record["graph_evidence"]["missing_reference_evidence"])
                for record in records
            ),
            "missing_concept_evidence": sum(
                int(record["graph_evidence"]["missing_concept_evidence"])
                for record in records
            ),
            "ambiguous_scope_candidates": sum(
                int(record["graph_evidence"]["ambiguous_scope_candidates"])
                for record in records
            ),
        },
    }


def render_markdown_report(summary: dict, records: list[dict]) -> str:
    lines = [
        "# Hard Holdout Error Report",
        "",
        f"Split: `{summary['split']}`",
        f"Samples: `{summary['sample_count']}`",
        "",
        "## Overall Metrics",
        "",
        "| Metric | Hard holdout | Delta vs test |",
        "|---|---:|---:|",
    ]
    for metric in (
        "intent_macro_f1",
        "scope_accuracy",
        "transition_accuracy",
        "legacy_route_accuracy",
        "security_transition_macro_f1",
    ):
        value = summary["metrics"].get(metric)
        delta = summary["test_metric_delta"].get(metric)
        lines.append(f"| {metric} | {_fmt(value)} | {_fmt(delta)} |")
    lines.extend(["", "## Per MT Intent Accuracy", "", "| MT | Count | Accuracy | Macro-F1 |", "|---|---:|---:|---:|"])
    for mt_id, item in sorted(summary["metrics"].get("intent_by_mt_family", {}).items()):
        lines.append(
            f"| {mt_id} | {item['count']} | {_fmt(item['accuracy'])} | {_fmt(item['macro_f1'])} |"
        )
    lines.extend(["", "## Wrong Head Counts", ""])
    for head, count in sorted(summary["wrong_head_counts"].items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- `{head}`: {count}")
    lines.extend(["", "## Intent Confusion", "", "| Gold | Predicted | Count |", "|---|---|---:|"])
    for item in summary["intent_confusion"][:30]:
        lines.append(f"| {item['gold']} | {item['predicted']} | {item['count']} |")
    lines.extend(["", "## Failed Micro-Pattern Groups", "", "| Micro-pattern | MT | Count | Intent wrong | Top predicted intents |", "|---|---|---:|---:|---|"])
    for item in summary["failed_groups"][:20]:
        top_preds = ", ".join(f"{name}:{count}" for name, count in item["top_predicted_intents"])
        lines.append(
            f"| {item['micro_pattern_id']} | {item['mt_id']} | {item['count']} | {item['intent_wrong']} | {top_preds} |"
        )
    lines.extend(["", "## Graph Evidence Flags", ""])
    for key, value in summary["graph_evidence_flags"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Representative Failure Cases", ""])
    for mt_id in ("MT-02", "MT-05", "MT-06", "MT-07"):
        failures = [
            record
            for record in records
            if record.get("mt_id") == mt_id and "intent" in record["wrong_heads"]
        ][:10]
        lines.append(f"### {mt_id}")
        if not failures:
            lines.append("")
            lines.append("No intent failures found.")
            lines.append("")
            continue
        for record in failures:
            lines.extend(_render_case(record))
    lines.extend(["", "## Preliminary Reading", ""])
    lines.extend(_preliminary_reading(summary))
    return "\n".join(lines) + "\n"


def _truth_from_encoded(encoded) -> dict:
    return {
        "intent": int(encoded.targets["intent"]),
        "operation": int(encoded.targets["operation"]),
        "scope": int(encoded.targets["scope"]),
        "target_relation": int(encoded.targets["target_relation"]),
        "transition": int(encoded.targets["transition"]),
        "reference_distance": int(encoded.targets["reference_distance"]),
        "security_transition": int(encoded.targets["security_transition"]),
        "concepts": [int(value) for value in encoded.targets["concepts"].tolist()],
    }


def _decode_indices(outputs: dict[str, torch.Tensor]) -> dict:
    return {
        "intent": int(outputs["intent_logits"].argmax()),
        "operation": int(outputs["operation_logits"].argmax()),
        "scope": int(outputs["scope_logits"].argmax()),
        "target_relation": int(outputs["target_relation_logits"].argmax()),
        "transition": int(outputs["transition_logits"].argmax()),
        "reference_distance": int(outputs["reference_distance_logits"].argmax()),
        "security_transition": int(outputs["security_transition_logits"].argmax()),
        "concepts": [
            int(value)
            for value in (torch.sigmoid(outputs["concept_logits"]) >= 0.5).tolist()
        ],
    }


def _legacy_from_labels(
    sample: IntentSample,
    labels: dict,
    taxonomy: IntentTaxonomy,
    concepts: list[str],
    legacy_adapter: LegacyIntentAdapter,
) -> str:
    resolution = IntentResolution(
        primary_intent=taxonomy.semantic_intents.values[labels["intent"]],
        intent_candidates=[],
        operation=taxonomy.operations.values[labels["operation"]],
        scope=taxonomy.scopes.values[labels["scope"]],
        target_relation=taxonomy.target_relations.values[labels["target_relation"]],
        transition=taxonomy.transitions.values[labels["transition"]],
        target_concepts=[
            concept for concept, enabled in zip(concepts, labels["concepts"]) if enabled
        ],
        reference_links=[],
        security_transition=taxonomy.security_transitions.values[
            labels["security_transition"]
        ],
        uncertainty={},
    )
    return legacy_adapter.resolve(role=sample.role, resolution=resolution)["legacy_intent"]


def _sample_metadata(sample: IntentSample) -> dict:
    return {
        "conversation_id": sample.conversation_id,
        "pattern_id": sample.pattern_id,
        "role": sample.role,
        "turn_count": len(sample.history) + 1,
        "category": sample.category,
        **dict(sample.generation_metadata.extra),
    }


def _sample_turns(sample: IntentSample) -> list[dict]:
    return [
        {"turn_id": turn.turn_id, "text": turn.text, "current": False}
        for turn in sample.history
    ] + [
        {"turn_id": sample.current_turn_id, "text": sample.current_text, "current": True}
    ]


def _topk(logits: torch.Tensor, labels: list[str], k: int) -> list[dict]:
    probs = torch.softmax(logits.detach().cpu(), dim=0)
    values, indices = torch.topk(probs, k=min(k, len(labels)))
    return [
        {"label": labels[int(index)], "probability": round(float(value), 6)}
        for value, index in zip(values, indices)
    ]


def _space_values(taxonomy: IntentTaxonomy, head: str) -> list[str]:
    return getattr(taxonomy, SINGLE_LABEL_SPACES[head]).values


def _looks_referential(text: str) -> bool:
    normalized = text.lower()
    markers = (
        "that ",
        "those ",
        "same ",
        "previous",
        "earlier",
        "route",
        "same-scope",
        "same scope",
        "same group",
        "same information",
        "same output",
        "aggregate",
        "count gap",
        "extreme value",
        "it ",
        "them ",
        "their ",
        "the first",
        "the last",
        "missing",
    )
    return any(marker in normalized for marker in markers)


def _strip_numeric_record(record: dict) -> dict:
    output = dict(record)
    output.pop("numeric_record", None)
    return output


def _failed_groups(records: list[dict]) -> list[dict]:
    grouped: defaultdict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in records:
        key = (record.get("mt_id") or "ANCHOR", record.get("micro_pattern_id") or "UNKNOWN")
        grouped[key].append(record)
    output = []
    for (mt_id, micro_pattern_id), items in grouped.items():
        intent_wrong = [item for item in items if "intent" in item["wrong_heads"]]
        if not intent_wrong:
            continue
        preds = Counter(item["predicted_labels"]["intent"] for item in intent_wrong)
        output.append(
            {
                "mt_id": mt_id,
                "micro_pattern_id": micro_pattern_id,
                "count": len(items),
                "intent_wrong": len(intent_wrong),
                "wrong_rate": round(len(intent_wrong) / max(1, len(items)), 6),
                "top_predicted_intents": preds.most_common(5),
            }
        )
    return sorted(output, key=lambda item: (-item["wrong_rate"], item["micro_pattern_id"]))


def _load_test_metrics(root: Path) -> dict:
    path = root / "artifacts" / "reports" / "training_report.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("test_metrics", {})


def _metric_delta(test_metrics: dict, split_metrics: dict) -> dict:
    output = {}
    for key, value in split_metrics.items():
        if isinstance(value, (int, float)) and isinstance(test_metrics.get(key), (int, float)):
            output[key] = round(float(value) - float(test_metrics[key]), 6)
    return output


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _render_case(record: dict) -> list[str]:
    turns = " / ".join(f"T{turn['turn_id']}: {turn['text']}" for turn in record["turns"])
    graph = record["graph_evidence"]
    return [
        "",
        f"- `{record['sample_id']}`",
        f"  - turns: {turns}",
        f"  - gold intent: `{record['gold_labels']['intent']}`; predicted: `{record['predicted_labels']['intent']}`",
        f"  - wrong heads: `{', '.join(record['wrong_heads'])}`",
        f"  - top intent: {record['top_k']['intent']}",
        f"  - graph concepts current: `{graph['current_turn_concepts']}`",
        f"  - scope candidates: `{graph['scope_candidates']}`; target candidates: `{graph['target_candidates']}`",
        f"  - flags: ref_missing={graph['missing_reference_evidence']}, concept_missing={graph['missing_concept_evidence']}, ambiguous_scope={graph['ambiguous_scope_candidates']}",
    ]


def _preliminary_reading(summary: dict) -> list[str]:
    lines = []
    flags = summary["graph_evidence_flags"]
    if flags["missing_concept_evidence"]:
        lines.append("- Some failures have no current-turn concept evidence; inspect concept aliases before changing model capacity.")
    if flags["missing_reference_evidence"]:
        lines.append("- Some referential turns are not represented as `ReferenceExpression`; reference detection may be under-covering discourse markers.")
    if flags["ambiguous_scope_candidates"]:
        lines.append("- Ambiguous scope candidates exist; inspect whether graph evidence is giving conflicting scope signals.")
    failed_mts = [
        item["mt_id"]
        for item in summary["failed_groups"]
        if item["wrong_rate"] >= 1.0
    ]
    if failed_mts:
        lines.append(
            "- Full intent failure groups suggest micro-pattern holdout generalization is the main risk, not lack of train/test fit."
        )
    if not lines:
        lines.append("- No obvious graph-evidence flag dominates; inspect confusion groups before changing graph construction.")
    return lines
