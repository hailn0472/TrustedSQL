from __future__ import annotations

import json
import math
from pathlib import Path

import torch

from trustedsql_gnn.contracts import GenerationMetadata, IntentGraph, IntentResolution, IntentSample, RuntimeIntentRequest
from trustedsql_gnn.graph.builder import IntentGraphBuilder
from trustedsql_gnn.graph.concepts import ConceptExtractor
from trustedsql_gnn.graph.encoder import IntentGraphEncoder
from trustedsql_gnn.model.gnn import RelationAwareIntentGNN
from trustedsql_gnn.model.text_encoder import FrozenTextEncoder
from trustedsql_gnn.paths import GNNPaths
from trustedsql_gnn.taxonomy import IntentTaxonomy


class IntentPredictor:
    def __init__(
        self,
        *,
        root: str | Path,
        checkpoint_path: str | Path,
        device: str = "cpu",
        allow_hash_encoder: bool = False,
    ):
        self.paths = GNNPaths.from_project_root(root, checkpoint_path=checkpoint_path)
        self.paths.require_runtime_assets()
        self.root = self.paths.project_root
        self.device = device
        checkpoint = torch.load(self.paths.checkpoint_path, map_location=device, weights_only=False)
        self.taxonomy = IntentTaxonomy(checkpoint["taxonomy"])
        text_config = checkpoint["text_encoder"]
        local_model = self.paths.encoder_dir
        model_source = str(local_model)
        self.text_encoder = FrozenTextEncoder(
            model_name=model_source,
            cache_dir=local_model.parent,
            embedding_cache=None,
            allow_hash_fallback=allow_hash_encoder,
            local_files_only=True,
            cache_namespace=text_config["model_name"],
        )
        graph_config_path = self.paths.config_dir / "graph_config.json"
        graph_config = json.loads(graph_config_path.read_text(encoding="utf-8"))
        extractor = ConceptExtractor.load(self.paths.config_dir / "concept_catalog_v1.json")
        self.builder = IntentGraphBuilder(
            extractor,
            max_turns=graph_config["max_turns"],
            reverse_edges=graph_config["reverse_edges"],
        )
        self.encoder = IntentGraphEncoder(
            taxonomy=self.taxonomy,
            concept_catalog_path=self.paths.config_dir / "concept_catalog_v1.json",
            graph_config_path=graph_config_path,
            text_encoder=self.text_encoder,
        )
        self.model = RelationAwareIntentGNN(**checkpoint["model_config"]).to(device)
        self.model.load_state_dict(checkpoint["model_state"])
        self.model.eval()
        self.last_graph_debug: dict | None = None

    def predict(self, sample: IntentSample) -> IntentResolution:
        graph = self.builder.build(sample)
        self.last_graph_debug = _graph_debug_summary(graph)
        encoded = self.encoder.encode(graph, sample, self.device)
        with torch.no_grad():
            output = self.model(encoded.x, encoded.edge_indices, encoded.current_node_idx)
        intent_probs = torch.softmax(output["intent_logits"], dim=-1)
        top_values, top_indices = torch.topk(intent_probs, k=min(3, intent_probs.numel()))
        intent_candidates = [
            {
                "intent": self.taxonomy.semantic_intents.values[int(index)],
                "probability": round(float(value), 6),
            }
            for value, index in zip(top_values, top_indices)
        ]
        primary_intent = intent_candidates[0]["intent"]
        operation = self.taxonomy.operations.values[int(output["operation_logits"].argmax())]
        scope = self.taxonomy.scopes.values[int(output["scope_logits"].argmax())]
        target_relation = self.taxonomy.target_relations.values[
            int(output["target_relation_logits"].argmax())
        ]
        transition = self.taxonomy.transitions.values[int(output["transition_logits"].argmax())]
        security_transition = self.taxonomy.security_transitions.values[
            int(output["security_transition_logits"].argmax())
        ]
        concept_probs = torch.sigmoid(output["concept_logits"])
        concepts = [
            concept
            for concept, probability in zip(self.encoder.concepts, concept_probs)
            if float(probability) >= 0.5
        ]
        reference_distance = int(output["reference_distance_logits"].argmax())
        reference_links = []
        if reference_distance > 0 and sample.current_turn_id - reference_distance >= 1:
            reference_links.append(
                {
                    "source_turn": sample.current_turn_id,
                    "target_turn": sample.current_turn_id - reference_distance,
                    "confidence": round(
                        float(torch.softmax(output["reference_distance_logits"], dim=-1)[reference_distance]),
                        6,
                    ),
                }
            )
        probabilities = intent_probs.tolist()
        entropy = -sum(value * math.log(max(value, 1e-12)) for value in probabilities)
        margin = float(top_values[0] - top_values[1]) if top_values.numel() > 1 else 1.0
        head_conflicts: list[str] = []
        if scope == "SELF" and target_relation in {"SPECIFIC_EXTERNAL", "UNRESOLVED"}:
            head_conflicts.append("self_scope_external_target_conflict")
        if scope == "PUBLIC" and primary_intent in {
            "GRADE_DETAIL_LOOKUP",
            "RESULT_SUMMARY_LOOKUP",
            "ATTENDANCE_LOOKUP",
        }:
            head_conflicts.append("public_scope_private_intent_conflict")
        return IntentResolution(
            primary_intent=primary_intent,
            intent_candidates=intent_candidates,
            operation=operation,
            scope=scope,
            target_relation=target_relation,
            transition=transition,
            target_concepts=concepts,
            reference_links=reference_links,
            security_transition=security_transition,
            uncertainty={
                "intent_entropy": round(entropy, 6),
                "top1_top2_margin": round(margin, 6),
                "unresolved_reference": target_relation == "UNRESOLVED" and not reference_links,
                "head_conflicts": head_conflicts,
            },
        )

    def predict_turn(self, request: RuntimeIntentRequest) -> IntentResolution:
        sample = IntentSample(
            sample_id=f"{request.conversation_id}::turn_{request.current_turn_id}",
            conversation_id=request.conversation_id,
            pattern_id="RUNTIME",
            category="BENIGN_SINGLE_TURN" if not request.history else "BENIGN_MULTI_TURN",
            role=request.role,
            current_turn_id=request.current_turn_id,
            history=request.history,
            current_text=request.current_text,
            current_mentions=request.current_mentions,
            labels=None,
            generation_metadata=GenerationMetadata(
                generator_version="runtime",
                surface_variant_id="runtime",
                entity_seed=request.conversation_id,
            ),
        )
        return self.predict(sample)


def _graph_debug_summary(graph: IntentGraph) -> dict:
    nodes_by_type: dict[str, int] = {}
    edges_by_type: dict[str, int] = {}
    current_turn = None
    mentions: list[dict] = []
    concepts: list[dict] = []
    scope_candidates: list[dict] = []
    target_candidates: list[dict] = []
    reference_expressions: list[dict] = []

    for node in graph.nodes:
        nodes_by_type[node.node_type] = nodes_by_type.get(node.node_type, 0) + 1
        if node.node_type == "UserTurn" and node.attrs.get("current"):
            current_turn = {"turn_id": node.turn_id, "text": node.text}
        elif node.node_type == "EntityMention":
            mentions.append(
                {
                    "node_id": node.node_id,
                    "turn_id": node.turn_id,
                    "surface": node.label,
                    "source": node.attrs.get("source"),
                    "confidence": node.attrs.get("confidence"),
                }
            )
        elif node.node_type == "SemanticConceptCandidate":
            concepts.append({"node_id": node.node_id, "concept": node.label})
        elif node.node_type == "ScopeCandidate":
            scope_candidates.append({"scope": node.label, "confidence": node.attrs.get("confidence")})
        elif node.node_type == "TargetCandidate":
            target_candidates.append({"target": node.label, "confidence": node.attrs.get("confidence")})
        elif node.node_type == "ReferenceExpression":
            reference_expressions.append(
                {"turn_id": node.turn_id, "surface": node.label, "concept": node.attrs.get("concept")}
            )

    for edge in graph.edges:
        edges_by_type[edge.edge_type] = edges_by_type.get(edge.edge_type, 0) + 1

    return {
        "graph_id": graph.graph_id,
        "sample_id": graph.sample_id,
        "current_turn_id": graph.current_turn_id,
        "current_turn": current_turn,
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "nodes_by_type": nodes_by_type,
        "edges_by_type": edges_by_type,
        "mentions": mentions,
        "concepts": concepts,
        "scope_candidates": scope_candidates,
        "target_candidates": target_candidates,
        "reference_expressions": reference_expressions,
        "metadata": graph.metadata,
    }

