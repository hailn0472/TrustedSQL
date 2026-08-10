from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch

from trustedsql_gnn.contracts import IntentGraph, IntentSample
from trustedsql_gnn.taxonomy import IntentTaxonomy


@dataclass
class EncodedGraph:
    x: torch.Tensor
    edge_indices: list[torch.Tensor]
    current_node_idx: int
    targets: dict[str, torch.Tensor]
    metadata: dict


class IntentGraphEncoder:
    def __init__(
        self,
        *,
        taxonomy: IntentTaxonomy,
        concept_catalog_path: str | Path,
        graph_config_path: str | Path,
        text_encoder,
    ):
        self.taxonomy = taxonomy
        self.text_encoder = text_encoder
        graph_config = json.loads(Path(graph_config_path).read_text(encoding="utf-8"))
        self.node_types = graph_config["node_types"]
        base_edges = graph_config["edge_types"]
        self.edge_types = [*base_edges, *[f"{item}__rev" for item in base_edges]]
        concept_payload = json.loads(Path(concept_catalog_path).read_text(encoding="utf-8"))
        self.concepts = sorted(concept_payload["concepts"])
        self.roles = taxonomy.roles.values

    @property
    def input_dim(self) -> int:
        return (
            self.text_encoder.dimension
            + len(self.node_types)
            + len(self.roles)
            + len(self.taxonomy.semantic_intents.values)
            + len(self.taxonomy.scopes.values)
            + len(self.taxonomy.target_relations.values)
            + 4
        )

    def encode(self, graph: IntentGraph, sample: IntentSample, device: str = "cpu") -> EncodedGraph:
        texts = [node.text or node.label for node in graph.nodes]
        embeddings = self.text_encoder.encode(texts)
        if embeddings.shape[1] != self.text_encoder.dimension:
            self.text_encoder.dimension = int(embeddings.shape[1])
        node_index = {node.node_id: idx for idx, node in enumerate(graph.nodes)}
        features = [
            self._node_features(node, embeddings[idx], graph.current_turn_id)
            for idx, node in enumerate(graph.nodes)
        ]
        edge_pairs: list[list[list[int]]] = [[] for _ in self.edge_types]
        edge_map = {value: idx for idx, value in enumerate(self.edge_types)}
        for edge in graph.edges:
            relation = edge_map.get(edge.edge_type)
            source = node_index.get(edge.source)
            target = node_index.get(edge.target)
            if relation is not None and source is not None and target is not None:
                edge_pairs[relation].append([source, target])
        edge_indices = [
            torch.tensor(pairs, dtype=torch.long, device=device).t().contiguous()
            if pairs
            else torch.empty((2, 0), dtype=torch.long, device=device)
            for pairs in edge_pairs
        ]
        targets = self._encode_targets(sample, device)
        return EncodedGraph(
            x=torch.tensor(np.asarray(features), dtype=torch.float32, device=device),
            edge_indices=edge_indices,
            current_node_idx=node_index[f"turn:{graph.current_turn_id}"],
            targets=targets,
            metadata={"sample_id": sample.sample_id, "concepts": self.concepts},
        )

    def _encode_targets(self, sample: IntentSample, device: str) -> dict[str, torch.Tensor]:
        labels = sample.labels
        if labels is None:
            return {}
        reference_distance = 0
        if labels.reference_targets:
            target_turn = max(item.target_turn for item in labels.reference_targets)
            reference_distance = min(sample.current_turn_id - target_turn, 8)
        return {
            "intent": torch.tensor(self.taxonomy.semantic_intents.index(labels.semantic_intent), device=device),
            "operation": torch.tensor(self.taxonomy.operations.index(labels.operation), device=device),
            "scope": torch.tensor(self.taxonomy.scopes.index(labels.scope), device=device),
            "target_relation": torch.tensor(
                self.taxonomy.target_relations.index(labels.target_relation),
                device=device,
            ),
            "transition": torch.tensor(self.taxonomy.transitions.index(labels.transition), device=device),
            "security_transition": torch.tensor(
                self.taxonomy.security_transitions.index(labels.security_transition),
                device=device,
            ),
            "reference_distance": torch.tensor(reference_distance, device=device),
            "concepts": torch.tensor(
                [1.0 if concept in labels.target_concepts else 0.0 for concept in self.concepts],
                dtype=torch.float32,
                device=device,
            ),
        }

    def _node_features(self, node, embedding: np.ndarray, current_turn_id: int) -> list[float]:
        node_type = _one_hot(self.node_types, node.node_type)
        role = _one_hot(self.roles, node.label if node.node_type == "Role" else None)
        attrs = node.attrs
        previous_intent = _one_hot(
            self.taxonomy.semantic_intents.values,
            attrs.get("semantic_intent") if node.node_type == "PreviousSemanticState" else None,
        )
        previous_scope = _one_hot(
            self.taxonomy.scopes.values,
            attrs.get("scope") if node.node_type == "PreviousSemanticState" else None,
        )
        previous_target = _one_hot(
            self.taxonomy.target_relations.values,
            attrs.get("target_relation") if node.node_type == "PreviousSemanticState" else None,
        )
        relative_turn = 0.0
        if node.turn_id is not None:
            relative_turn = max(-8, min(0, node.turn_id - current_turn_id)) / 8.0
        scalars = [
            1.0 if attrs.get("current") else 0.0,
            relative_turn,
            float(attrs.get("confidence", 1.0)),
            1.0 if node.node_type == "ReferenceExpression" else 0.0,
        ]
        return [
            *embedding.astype(np.float32).tolist(),
            *node_type,
            *role,
            *previous_intent,
            *previous_scope,
            *previous_target,
            *scalars,
        ]


def _one_hot(values: list[str], selected: str | None) -> list[float]:
    return [1.0 if value == selected else 0.0 for value in values]

