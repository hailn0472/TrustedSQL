from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from trustedsql_gnn.contracts import IntentConversation, TurnLabels


@dataclass(frozen=True)
class LabelSpace:
    values: list[str]

    def index(self, value: str) -> int:
        try:
            return self.values.index(value)
        except ValueError as exc:
            raise ValueError(f"unknown_label:{value}") from exc


class IntentTaxonomy:
    def __init__(self, payload: dict):
        self.payload = payload
        self.semantic_intents = LabelSpace(payload["semantic_intents"])
        self.operations = LabelSpace(payload["operations"])
        self.scopes = LabelSpace(payload["scopes"])
        self.target_relations = LabelSpace(payload["target_relations"])
        self.transitions = LabelSpace(payload["transitions"])
        self.security_transitions = LabelSpace(payload["security_transitions"])
        self.roles = LabelSpace(payload["roles"])

    @classmethod
    def load(cls, path: str | Path) -> "IntentTaxonomy":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def validate_labels(self, labels: TurnLabels) -> None:
        self.semantic_intents.index(labels.semantic_intent)
        self.operations.index(labels.operation)
        self.scopes.index(labels.scope)
        self.target_relations.index(labels.target_relation)
        self.transitions.index(labels.transition)
        self.security_transitions.index(labels.security_transition)

    def validate_conversation(self, conversation: IntentConversation) -> None:
        self.roles.index(conversation.role)
        for turn in conversation.turns:
            self.validate_labels(turn.labels)

    def as_label_maps(self) -> dict[str, dict[str, int]]:
        return {
            "semantic_intent": {value: idx for idx, value in enumerate(self.semantic_intents.values)},
            "operation": {value: idx for idx, value in enumerate(self.operations.values)},
            "scope": {value: idx for idx, value in enumerate(self.scopes.values)},
            "target_relation": {value: idx for idx, value in enumerate(self.target_relations.values)},
            "transition": {value: idx for idx, value in enumerate(self.transitions.values)},
            "security_transition": {value: idx for idx, value in enumerate(self.security_transitions.values)},
        }

