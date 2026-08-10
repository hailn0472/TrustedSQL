from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from trustedsql_gnn.taxonomy import IntentTaxonomy


class PatternBankValidator:
    def __init__(self, taxonomy: IntentTaxonomy, known_concepts: set[str] | None = None):
        self.taxonomy = taxonomy
        self.known_concepts = known_concepts

    def validate_path(self, path: str | Path) -> dict:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        errors: list[str] = []
        patterns = payload.get("patterns", [])
        ids = [item.get("pattern_id") for item in patterns]
        duplicates = [item for item, count in Counter(ids).items() if count > 1]
        if duplicates:
            errors.append(f"duplicate_pattern_ids:{duplicates}")

        quotas: Counter[str] = Counter()
        known_ids = set(ids)
        for item in patterns:
            pattern_id = item.get("pattern_id", "<missing>")
            category = item.get("category")
            quotas[category] += int(item.get("generation", {}).get("target_samples", 0))
            blueprint = item.get("conversation", {}).get("turn_blueprint", [])
            if not blueprint:
                errors.append(f"{pattern_id}:empty_blueprint")
                continue
            for turn in blueprint:
                for key, space in (
                    ("semantic_intent", self.taxonomy.semantic_intents),
                    ("scope", self.taxonomy.scopes),
                    ("target_relation", self.taxonomy.target_relations),
                    ("transition", self.taxonomy.transitions),
                ):
                    if turn.get(key) not in space.values:
                        errors.append(f"{pattern_id}:unknown_{key}:{turn.get(key)}")
                if self.known_concepts is not None:
                    for concept in turn.get("concepts", []):
                        if concept not in self.known_concepts:
                            errors.append(f"{pattern_id}:unknown_concept:{concept}")
            expected = item.get("expected_resolution", {})
            for key, space in (
                ("semantic_intent", self.taxonomy.semantic_intents),
                ("operation", self.taxonomy.operations),
                ("scope", self.taxonomy.scopes),
                ("target_relation", self.taxonomy.target_relations),
                ("transition", self.taxonomy.transitions),
                ("security_transition", self.taxonomy.security_transitions),
            ):
                if expected.get(key) not in space.values:
                    errors.append(f"{pattern_id}:unknown_expected_{key}:{expected.get(key)}")
            if self.known_concepts is not None:
                for concept in expected.get("target_concepts", []):
                    if concept not in self.known_concepts:
                        errors.append(f"{pattern_id}:unknown_expected_concept:{concept}")
            for other in item.get("contrastive_patterns", []):
                if other not in known_ids:
                    errors.append(f"{pattern_id}:unknown_contrastive_pattern:{other}")

        declared = payload.get("release_quota", {})
        actual = dict(quotas)
        if declared != actual:
            errors.append(f"release_quota_mismatch:declared={declared}:actual={actual}")
        total = sum(quotas.values())
        if total != int(payload.get("required_release_total", -1)):
            errors.append("required_release_total_mismatch")
        return {
            "valid": not errors,
            "errors": errors,
            "pattern_count": len(patterns),
            "quota": actual,
            "total": total,
        }
