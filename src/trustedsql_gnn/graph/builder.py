from __future__ import annotations

import random
from dataclasses import dataclass

from trustedsql_gnn.contracts import GraphEdge, GraphNode, IntentGraph, IntentSample, Mention
from trustedsql_gnn.graph.concepts import ConceptExtractor


@dataclass(frozen=True)
class PreviousStatePolicy:
    use_probability: float = 1.0
    mask_probability: float = 0.0


class IntentGraphBuilder:
    def __init__(self, extractor: ConceptExtractor, *, max_turns: int = 8, reverse_edges: bool = True):
        self.extractor = extractor
        self.max_turns = max_turns
        self.reverse_edges = reverse_edges

    def build(
        self,
        sample: IntentSample,
        *,
        previous_state_policy: PreviousStatePolicy | None = None,
        seed: int = 0,
    ) -> IntentGraph:
        policy = previous_state_policy or PreviousStatePolicy()
        rng = random.Random(f"{seed}:{sample.sample_id}")
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        node_ids: set[str] = set()

        def add_node(node: GraphNode) -> None:
            if node.node_id not in node_ids:
                node_ids.add(node.node_id)
                nodes.append(node)

        def add_edge(source: str, target: str, edge_type: str, attrs: dict | None = None) -> None:
            edges.append(GraphEdge(source=source, target=target, edge_type=edge_type, attrs=attrs or {}))
            if self.reverse_edges:
                edges.append(
                    GraphEdge(
                        source=target,
                        target=source,
                        edge_type=f"{edge_type}__rev",
                        attrs=attrs or {},
                    )
                )

        role_id = f"role:{sample.role}"
        add_node(GraphNode(node_id=role_id, node_type="Role", label=sample.role))

        history = sample.history[-(self.max_turns - 1):]
        all_turns = [*history]
        previous_turn_id: int | None = None
        concept_nodes_by_turn: dict[int, list[tuple[str, str]]] = {}

        for history_turn in all_turns:
            turn_node_id = f"turn:{history_turn.turn_id}"
            add_node(
                GraphNode(
                    node_id=turn_node_id,
                    node_type="UserTurn",
                    label=f"Q{history_turn.turn_id}",
                    turn_id=history_turn.turn_id,
                    text=history_turn.text,
                    attrs={"current": False},
                )
            )
            add_edge(role_id, turn_node_id, "continues_context")
            if previous_turn_id is not None:
                add_edge(f"turn:{previous_turn_id}", turn_node_id, "follows")
            previous_turn_id = history_turn.turn_id
            mentions = history_turn.mentions or self.extractor.extract(history_turn.text)
            concept_nodes_by_turn[history_turn.turn_id] = self._add_mentions(
                turn_id=history_turn.turn_id,
                mentions=mentions,
                add_node=add_node,
                add_edge=add_edge,
            )
            state = history_turn.predicted_or_gold_state
            if (
                state is not None
                and rng.random() <= policy.use_probability
                and rng.random() > policy.mask_probability
            ):
                state_id = f"previous_state:{history_turn.turn_id}"
                add_node(
                    GraphNode(
                        node_id=state_id,
                        node_type="PreviousSemanticState",
                        label=state.semantic_intent,
                        turn_id=history_turn.turn_id,
                        attrs={
                            "semantic_intent": state.semantic_intent,
                            "scope": state.scope,
                            "target_relation": state.target_relation,
                            "transition": state.transition,
                        },
                    )
                )
                add_edge(turn_node_id, state_id, "has_previous_intent")

        current_id = f"turn:{sample.current_turn_id}"
        add_node(
            GraphNode(
                node_id=current_id,
                node_type="UserTurn",
                label=f"Q{sample.current_turn_id}",
                turn_id=sample.current_turn_id,
                text=sample.current_text,
                attrs={"current": True},
            )
        )
        add_edge(role_id, current_id, "continues_context")
        if previous_turn_id is not None:
            add_edge(f"turn:{previous_turn_id}", current_id, "follows")

        current_mentions = sample.current_mentions or self.extractor.extract(sample.current_text)
        concept_nodes_by_turn[sample.current_turn_id] = self._add_mentions(
            turn_id=sample.current_turn_id,
            mentions=current_mentions,
            add_node=add_node,
            add_edge=add_edge,
        )
        current_concepts = {mention.concept for mention in current_mentions}

        for scope, confidence in self.extractor.scope_candidates(current_concepts, sample.current_text):
            node_id = f"scope_candidate:{scope}"
            add_node(
                GraphNode(
                    node_id=node_id,
                    node_type="ScopeCandidate",
                    label=scope,
                    attrs={"confidence": confidence},
                )
            )
            add_edge(current_id, node_id, "supports_scope", {"confidence": confidence})

        for target, confidence in self.extractor.target_candidates(current_concepts, sample.current_text):
            node_id = f"target_candidate:{target}"
            add_node(
                GraphNode(
                    node_id=node_id,
                    node_type="TargetCandidate",
                    label=target,
                    attrs={"confidence": confidence},
                )
            )
            add_edge(current_id, node_id, "supports_target", {"confidence": confidence})

        reference_mentions = [
            mention
            for mention in current_mentions
            if mention.concept in {"REFERENCE_SINGULAR", "REFERENCE_PLURAL"}
        ]
        for index, mention in enumerate(reference_mentions):
            ref_id = f"reference:{sample.current_turn_id}:{index}"
            add_node(
                GraphNode(
                    node_id=ref_id,
                    node_type="ReferenceExpression",
                    label=mention.surface,
                    turn_id=sample.current_turn_id,
                    text=mention.surface,
                    attrs={"concept": mention.concept},
                )
            )
            add_edge(current_id, ref_id, "mentions")
            for prior_turn_id in sorted(concept_nodes_by_turn, reverse=True):
                if prior_turn_id >= sample.current_turn_id:
                    continue
                prior_concepts = concept_nodes_by_turn[prior_turn_id]
                if not prior_concepts:
                    continue
                target_id = f"turn:{prior_turn_id}"
                add_edge(
                    ref_id,
                    target_id,
                    "refers_to_candidate",
                    {"distance": sample.current_turn_id - prior_turn_id},
                )

        return IntentGraph(
            graph_id=f"graph:{sample.sample_id}",
            sample_id=sample.sample_id,
            current_turn_id=sample.current_turn_id,
            nodes=nodes,
            edges=edges,
            metadata={
                "category": sample.category,
                "pattern_id": sample.pattern_id,
                "role": sample.role,
                "sample_metadata": sample.generation_metadata.extra,
                "current_labels_excluded": True,
            },
        )

    @staticmethod
    def _add_mentions(*, turn_id: int, mentions: list[Mention], add_node, add_edge) -> list[tuple[str, str]]:
        output: list[tuple[str, str]] = []
        for index, mention in enumerate(mentions):
            mention_id = f"mention:{turn_id}:{index}"
            concept_id = f"concept:{mention.concept}"
            add_node(
                GraphNode(
                    node_id=mention_id,
                    node_type="EntityMention",
                    label=mention.surface,
                    turn_id=turn_id,
                    text=mention.surface,
                    attrs={"confidence": mention.confidence, "source": mention.source},
                )
            )
            add_node(
                GraphNode(
                    node_id=concept_id,
                    node_type="SemanticConceptCandidate",
                    label=mention.concept,
                )
            )
            add_edge(f"turn:{turn_id}", mention_id, "mentions")
            add_edge(mention_id, concept_id, "represents", {"confidence": mention.confidence})
            output.append((concept_id, mention.concept))
        return output

