from __future__ import annotations

import json
import re
from pathlib import Path

from trustedsql_gnn.contracts import Mention


class ConceptExtractor:
    def __init__(
        self,
        catalog: dict[str, list[str]],
        *,
        scope_candidate_rules: list[dict] | None = None,
        target_candidate_rules: list[dict] | None = None,
    ):
        self.catalog = catalog
        self.scope_candidate_rules = scope_candidate_rules or []
        self.target_candidate_rules = target_candidate_rules or []

    @classmethod
    def load(cls, path: str | Path) -> "ConceptExtractor":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            payload["concepts"],
            scope_candidate_rules=payload.get("scope_candidate_rules"),
            target_candidate_rules=payload.get("target_candidate_rules"),
        )

    def extract(self, text: str) -> list[Mention]:
        normalized = text.lower()
        matches: list[Mention] = []
        occupied: set[tuple[int, int, str]] = set()
        for concept, aliases in self.catalog.items():
            for alias in sorted(aliases, key=len, reverse=True):
                for match in re.finditer(rf"(?<!\w){re.escape(alias.lower())}(?!\w)", normalized):
                    key = (match.start(), match.end(), concept)
                    if key in occupied:
                        continue
                    occupied.add(key)
                    matches.append(
                        Mention(
                            surface=text[match.start():match.end()],
                            concept=concept,
                            start=match.start(),
                            end=match.end(),
                            source="catalog",
                            confidence=1.0,
                        )
                    )
        return sorted(matches, key=lambda item: (item.start or 0, -(item.end or 0)))

    def scope_candidates(self, concepts: set[str], text: str) -> list[tuple[str, float]]:
        return _candidate_rules(self.scope_candidate_rules, concepts)

    def target_candidates(self, concepts: set[str], text: str) -> list[tuple[str, float]]:
        return _candidate_rules(self.target_candidate_rules, concepts)


def _dedupe_candidates(values: list[tuple[str, float]]) -> list[tuple[str, float]]:
    best: dict[str, float] = {}
    for label, confidence in values:
        best[label] = max(confidence, best.get(label, 0.0))
    return sorted(best.items())


def _candidate_rules(rules: list[dict], concepts: set[str]) -> list[tuple[str, float]]:
    output: list[tuple[str, float]] = []
    for rule in rules:
        any_required = set(rule.get("concepts_any", []))
        all_required = set(rule.get("concepts_all", []))
        if any_required and not concepts.intersection(any_required):
            continue
        if all_required and not all_required.issubset(concepts):
            continue
        if not any_required and not all_required:
            continue
        output.append((rule["label"], float(rule["confidence"])))
    return _dedupe_candidates(output)

