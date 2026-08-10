from __future__ import annotations

import torch
from torch import nn
import torch.nn.functional as F


class RelationAwareIntentGNN(nn.Module):
    def __init__(
        self,
        *,
        input_dim: int,
        hidden_dim: int,
        num_relations: int,
        num_intents: int,
        num_operations: int,
        num_scopes: int,
        num_target_relations: int,
        num_transitions: int,
        num_concepts: int,
        num_security_transitions: int,
        max_reference_distance: int = 8,
        num_layers: int = 3,
        dropout: float = 0.2,
    ):
        super().__init__()
        self.config = {
            "input_dim": input_dim,
            "hidden_dim": hidden_dim,
            "num_relations": num_relations,
            "num_intents": num_intents,
            "num_operations": num_operations,
            "num_scopes": num_scopes,
            "num_target_relations": num_target_relations,
            "num_transitions": num_transitions,
            "num_concepts": num_concepts,
            "num_security_transitions": num_security_transitions,
            "max_reference_distance": max_reference_distance,
            "num_layers": num_layers,
            "dropout": dropout,
        }
        self.input_projection = nn.Linear(input_dim, hidden_dim)
        self.self_layers = nn.ModuleList(nn.Linear(hidden_dim, hidden_dim) for _ in range(num_layers))
        self.relation_layers = nn.ModuleList(
            nn.ModuleList(nn.Linear(hidden_dim, hidden_dim, bias=False) for _ in range(num_relations))
            for _ in range(num_layers)
        )
        self.attention = nn.Linear(hidden_dim, 1)
        self.readout = nn.Sequential(
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        self.intent_head = nn.Linear(hidden_dim, num_intents)
        self.operation_head = nn.Linear(hidden_dim, num_operations)
        self.scope_head = nn.Linear(hidden_dim, num_scopes)
        self.target_relation_head = nn.Linear(hidden_dim, num_target_relations)
        self.transition_head = nn.Linear(hidden_dim, num_transitions)
        self.concept_head = nn.Linear(hidden_dim, num_concepts)
        self.reference_distance_head = nn.Linear(hidden_dim, max_reference_distance + 1)
        self.security_transition_head = nn.Linear(hidden_dim, num_security_transitions)
        self.dropout = dropout
        self.num_layers = num_layers

    def forward(
        self,
        x: torch.Tensor,
        edge_indices: list[torch.Tensor],
        current_node_idx: int,
    ) -> dict[str, torch.Tensor]:
        h = F.relu(self.input_projection(x))
        for layer_index in range(self.num_layers):
            new_h = self.self_layers[layer_index](h)
            degree = torch.ones((h.size(0), 1), dtype=h.dtype, device=h.device)
            for relation_index, edge_index in enumerate(edge_indices):
                if edge_index.numel() == 0:
                    continue
                source, target = edge_index
                messages = self.relation_layers[layer_index][relation_index](h[source])
                new_h.index_add_(0, target, messages)
                degree.index_add_(
                    0,
                    target,
                    torch.ones((target.numel(), 1), dtype=h.dtype, device=h.device),
                )
            h = F.relu(new_h / degree.clamp_min(1.0))
            h = F.dropout(h, p=self.dropout, training=self.training)

        attention_weights = torch.softmax(self.attention(h).squeeze(-1), dim=0)
        graph_pool = torch.sum(h * attention_weights.unsqueeze(-1), dim=0)
        current_index = min(max(int(current_node_idx), 0), h.size(0) - 1)
        current = h[current_index]
        representation = self.readout(torch.cat([current, graph_pool], dim=-1))
        return {
            "intent_logits": self.intent_head(representation),
            "operation_logits": self.operation_head(representation),
            "scope_logits": self.scope_head(representation),
            "target_relation_logits": self.target_relation_head(representation),
            "transition_logits": self.transition_head(representation),
            "concept_logits": self.concept_head(representation),
            "reference_distance_logits": self.reference_distance_head(representation),
            "security_transition_logits": self.security_transition_head(representation),
            "attention_weights": attention_weights,
        }

