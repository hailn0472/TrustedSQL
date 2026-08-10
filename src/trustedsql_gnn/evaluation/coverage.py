from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from trustedsql_gnn.contracts import IntentSample
from trustedsql_gnn.data.io import read_jsonl, write_json
from trustedsql_gnn.evaluation.diagnostics import graph_evidence_summary


def graph_coverage_report(*, release_dir: str | Path, builder, splits: list[str]) -> dict:
    release_path = Path(release_dir)
    split_map = json.loads(
        (release_path / "split_manifest.json").read_text(encoding="utf-8")
    )["conversation_splits"]
    samples = read_jsonl(release_path / "intent_samples.jsonl", IntentSample)
    by_split = {
        split: [sample for sample in samples if split_map.get(sample.conversation_id) == split]
        for split in splits
    }
    return {
        "release_dir": str(release_path.resolve()),
        "splits": {
            split: _summarize_split(items, builder)
            for split, items in by_split.items()
        },
    }


def write_graph_coverage_report(*, report: dict, output_path: str | Path) -> None:
    write_json(output_path, report)


def _summarize_split(samples: list[IntentSample], builder) -> dict:
    flags = Counter()
    concept_counts = Counter()
    scope_counts = Counter()
    target_counts = Counter()
    by_mt: defaultdict[str, Counter] = defaultdict(Counter)
    by_micro: defaultdict[str, Counter] = defaultdict(Counter)
    for sample in samples:
        graph = builder.build(sample)
        summary = graph_evidence_summary(graph, sample)
        metadata = sample.generation_metadata.extra
        mt_id = metadata.get("mt_id") or "ANCHOR"
        micro_id = metadata.get("micro_pattern_id") or metadata.get("anchor_id") or "UNKNOWN"
        if summary["missing_reference_evidence"]:
            flags["missing_reference_evidence"] += 1
            by_mt[mt_id]["missing_reference_evidence"] += 1
            by_micro[micro_id]["missing_reference_evidence"] += 1
        if summary["missing_concept_evidence"]:
            flags["missing_concept_evidence"] += 1
            by_mt[mt_id]["missing_concept_evidence"] += 1
            by_micro[micro_id]["missing_concept_evidence"] += 1
        if summary["ambiguous_scope_candidates"]:
            flags["ambiguous_scope_candidates"] += 1
            by_mt[mt_id]["ambiguous_scope_candidates"] += 1
            by_micro[micro_id]["ambiguous_scope_candidates"] += 1
        concept_counts.update(summary["current_turn_concepts"])
        scope_counts.update(summary["scope_candidates"])
        target_counts.update(summary["target_candidates"])
        by_mt[mt_id]["count"] += 1
        by_micro[micro_id]["count"] += 1
    return {
        "sample_count": len(samples),
        "flags": dict(flags),
        "top_current_concepts": concept_counts.most_common(40),
        "scope_candidate_counts": dict(scope_counts),
        "target_candidate_counts": dict(target_counts),
        "by_mt": {
            key: dict(value)
            for key, value in sorted(by_mt.items())
        },
        "worst_micro_patterns": [
            {"micro_pattern_id": key, **dict(value)}
            for key, value in sorted(
                by_micro.items(),
                key=lambda item: (
                    -item[1].get("missing_reference_evidence", 0),
                    -item[1].get("missing_concept_evidence", 0),
                    item[0],
                ),
            )[:30]
        ],
    }
