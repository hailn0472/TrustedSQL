from __future__ import annotations

import torch
import torch.nn.functional as F


DEFAULT_LOSS_WEIGHTS = {
    "intent": 1.0,
    "scope": 0.5,
    "target_relation": 0.5,
    "reference_distance": 0.5,
    "operation": 0.3,
    "transition": 0.3,
    "concepts": 0.3,
    "security_transition": 0.2,
}


def multitask_loss(
    outputs: dict[str, torch.Tensor],
    targets: dict[str, torch.Tensor],
    *,
    weights: dict[str, float] | None = None,
    class_weights: dict[str, torch.Tensor] | None = None,
) -> tuple[torch.Tensor, dict[str, float]]:
    weights = {**DEFAULT_LOSS_WEIGHTS, **(weights or {})}
    class_weights = class_weights or {}
    losses = {
        "intent": F.cross_entropy(
            outputs["intent_logits"].unsqueeze(0),
            targets["intent"].unsqueeze(0),
            weight=class_weights.get("intent"),
        ),
        "operation": F.cross_entropy(
            outputs["operation_logits"].unsqueeze(0),
            targets["operation"].unsqueeze(0),
            weight=class_weights.get("operation"),
        ),
        "scope": F.cross_entropy(
            outputs["scope_logits"].unsqueeze(0),
            targets["scope"].unsqueeze(0),
            weight=class_weights.get("scope"),
        ),
        "target_relation": F.cross_entropy(
            outputs["target_relation_logits"].unsqueeze(0),
            targets["target_relation"].unsqueeze(0),
            weight=class_weights.get("target_relation"),
        ),
        "transition": F.cross_entropy(
            outputs["transition_logits"].unsqueeze(0),
            targets["transition"].unsqueeze(0),
            weight=class_weights.get("transition"),
        ),
        "reference_distance": F.cross_entropy(
            outputs["reference_distance_logits"].unsqueeze(0),
            targets["reference_distance"].unsqueeze(0),
        ),
        "security_transition": F.cross_entropy(
            outputs["security_transition_logits"].unsqueeze(0),
            targets["security_transition"].unsqueeze(0),
            weight=class_weights.get("security_transition"),
        ),
        "concepts": F.binary_cross_entropy_with_logits(
            outputs["concept_logits"],
            targets["concepts"],
        ),
    }
    total = sum(weights[name] * value for name, value in losses.items())
    return total, {name: float(value.detach().cpu()) for name, value in losses.items()}
