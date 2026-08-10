from __future__ import annotations

import json
from pathlib import Path

from trustedsql_gnn.contracts import IntentResolution


class LegacyIntentAdapter:
    def __init__(self, rules: list[dict]):
        self.rules = sorted(rules, key=lambda item: int(item["priority"]))

    @classmethod
    def load(cls, path: str | Path) -> "LegacyIntentAdapter":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(payload["rules"])

    def resolve(self, *, role: str, resolution: IntentResolution) -> dict:
        if resolution.scope in {"EXTERNAL_INDIVIDUAL", "EXTERNAL_COHORT"}:
            return {
                "legacy_intent": "UNKNOWN",
                "status": "requires_risk_and_policy_review",
                "reason": "external_scope_not_routable_by_intent_adapter",
                "matched_rule": None,
            }
        concepts = set(resolution.target_concepts)
        matches: list[dict] = []
        for rule in self.rules:
            if role not in rule["roles"]:
                continue
            if resolution.primary_intent != rule["semantic_intent"]:
                continue
            if resolution.scope not in rule["scopes"]:
                continue
            required = set(rule.get("target_concepts", []))
            if required and not required.issubset(concepts):
                continue
            matches.append(rule)
        if not matches:
            return {
                "legacy_intent": "UNKNOWN",
                "status": "unmapped",
                "reason": "no_compatible_legacy_rule",
                "matched_rule": None,
            }
        intents = sorted({item["legacy_intent"] for item in matches})
        if len(intents) != 1:
            return {
                "legacy_intent": "UNKNOWN",
                "status": "ambiguous",
                "reason": "multiple_compatible_legacy_routes",
                "matched_rule": None,
                "candidates": intents,
            }
        matched = min(matches, key=lambda item: int(item["priority"]))
        return {
            "legacy_intent": intents[0],
            "status": "mapped",
            "reason": None,
            "matched_rule": matched,
        }

