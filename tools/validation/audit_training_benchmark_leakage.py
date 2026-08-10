from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


SIMILARITY_THRESHOLD = 0.9


def normalize(text: Any) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).lower().strip()
    return re.sub(r"\s+", " ", value)


def template(text: Any) -> str:
    value = normalize(text)
    value = re.sub(r"\b[a-z]{2,}\d+[a-z0-9-]*\b", "<entity>", value)
    value = re.sub(r"\b\d+(?:\.\d+)?\b", "<number>", value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit GNN training-to-benchmark text leakage")
    parser.add_argument("--project-root", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    root = Path(args.project_root).resolve() if args.project_root else Path(__file__).resolve().parents[2]
    train_dir = root / "data" / "training" / "intent_gnn" / "v1"
    benchmark_dir = root / "data" / "benchmark" / "v1" / "full"
    train_ids: set[str] = set()
    train_turns: set[str] = set()
    train_templates: set[str] = set()
    for filename in ("train.jsonl", "validation.jsonl", "test.jsonl"):
        for row in _jsonl(train_dir / filename):
            train_ids.add(str(row.get("conversation_id")))
            for turn in row.get("turns") or []:
                text = turn.get("user_utterance") or turn.get("nlq") or turn.get("text")
                train_turns.add(normalize(text))
                train_templates.add(template(text))
    benchmark_ids: set[str] = set()
    benchmark_turns: set[str] = set()
    benchmark_templates: set[str] = set()
    for path in benchmark_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        rows = payload.values() if isinstance(payload, dict) else payload
        for row in rows:
            benchmark_ids.add(str(row.get("sequence_id") or row.get("sample_id") or row.get("id")))
            turns = row.get("turns") if isinstance(row.get("turns"), list) else [row]
            for turn in turns:
                text = turn.get("nlq") or turn.get("user_utterance") or turn.get("question") or turn.get("text")
                benchmark_turns.add(normalize(text))
                benchmark_templates.add(template(text))
    train_turns.discard("")
    benchmark_turns.discard("")
    similar_templates = _similar_template_pairs(
        train_templates,
        benchmark_templates,
        threshold=SIMILARITY_THRESHOLD,
    )
    report = {
        "training_conversation_count": len(train_ids),
        "benchmark_record_count": len(benchmark_ids),
        "conversation_id_overlap_count": len(train_ids & benchmark_ids),
        "exact_turn_overlap_count": len(train_turns & benchmark_turns),
        "normalized_template_overlap_count": len(train_templates & benchmark_templates),
        "template_similarity_threshold": SIMILARITY_THRESHOLD,
        "similar_template_overlap_count": len(similar_templates),
        "exact_turn_overlap_examples": sorted(train_turns & benchmark_turns)[:20],
        "template_overlap_examples": sorted(train_templates & benchmark_templates)[:20],
        "similar_template_overlap_examples": similar_templates[:20],
        "pass": not any(
            (
                train_ids & benchmark_ids,
                train_turns & benchmark_turns,
                train_templates & benchmark_templates,
                similar_templates,
            )
        ),
    }
    output = Path(args.output).resolve() if args.output else root / "artifacts" / "generated" / "training_benchmark_leakage.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["pass"] else 1


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _similar_template_pairs(
    training: set[str],
    benchmark: set[str],
    *,
    threshold: float,
) -> list[dict[str, Any]]:
    training_rows = [
        (text, _token_bigrams(text)) for text in sorted(training) if text
    ]
    inverted: dict[tuple[str, str], set[int]] = {}
    for index, (_, grams) in enumerate(training_rows):
        for gram in grams:
            inverted.setdefault(gram, set()).add(index)

    matches: list[dict[str, Any]] = []
    for benchmark_text in sorted(text for text in benchmark if text):
        benchmark_grams = _token_bigrams(benchmark_text)
        candidates: set[int] = set()
        for gram in benchmark_grams:
            candidates.update(inverted.get(gram, set()))
        for index in candidates:
            training_text, training_grams = training_rows[index]
            if training_text == benchmark_text:
                continue
            union = training_grams | benchmark_grams
            score = len(training_grams & benchmark_grams) / len(union) if union else 0.0
            if score >= threshold:
                matches.append(
                    {
                        "training_template": training_text,
                        "benchmark_template": benchmark_text,
                        "token_bigram_jaccard": round(score, 6),
                    }
                )
    return sorted(
        matches,
        key=lambda row: (
            -row["token_bigram_jaccard"],
            row["training_template"],
            row["benchmark_template"],
        ),
    )


def _token_bigrams(text: str) -> set[tuple[str, str]]:
    tokens = re.findall(r"[a-z0-9_<>=-]+", text)
    if len(tokens) < 2:
        return {(tokens[0], "<end>")} if tokens else set()
    return set(zip(tokens, tokens[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
