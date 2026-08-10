from __future__ import annotations

import json
import random
import time
from collections import Counter
from pathlib import Path

import numpy as np
import torch

from trustedsql_gnn.contracts import IntentResolution, IntentSample
from trustedsql_gnn.data.io import read_jsonl, write_json
from trustedsql_gnn.evaluation.metrics import calculate_metrics
from trustedsql_gnn.graph.builder import IntentGraphBuilder, PreviousStatePolicy
from trustedsql_gnn.graph.concepts import ConceptExtractor
from trustedsql_gnn.graph.encoder import IntentGraphEncoder
from trustedsql_gnn.integration.legacy_adapter import LegacyIntentAdapter
from trustedsql_gnn.model.gnn import RelationAwareIntentGNN
from trustedsql_gnn.model.text_encoder import FrozenTextEncoder
from trustedsql_gnn.paths import GNNPaths
from trustedsql_gnn.taxonomy import IntentTaxonomy
from trustedsql_gnn.training.losses import multitask_loss
from trustedsql_gnn.training.sampling import (
    sampling_distribution,
    training_samples_for_epoch,
)


class TrainingRunner:
    def __init__(
        self,
        *,
        root: str | Path,
        release_dir: str | Path,
        output_dir: str | Path,
        device: str = "cpu",
        allow_hash_encoder: bool = False,
    ):
        self.paths = GNNPaths.from_project_root(root, training_output_dir=output_dir)
        self.root = self.paths.project_root
        self.release_dir = Path(release_dir)
        self.output_dir = Path(output_dir)
        self.device = device
        self.training_config = json.loads(
            (self.paths.config_dir / "training_config.json").read_text(encoding="utf-8")
        )
        self.graph_config = json.loads(
            (self.paths.config_dir / "graph_config.json").read_text(encoding="utf-8")
        )
        self.taxonomy = IntentTaxonomy.load(self.paths.config_dir / "intent_taxonomy_v1.json")
        text_config = self.training_config["text_encoder"]
        local_model = self.paths.encoder_dir
        if not (local_model / "modules.json").exists() and not allow_hash_encoder:
            raise FileNotFoundError(
                f"Missing pinned text encoder at {local_model}; run the encoder fetch tool first."
            )
        model_source = str(local_model) if (local_model / "modules.json").exists() else text_config["model_name"]
        self.paths.embedding_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.text_encoder = FrozenTextEncoder(
            model_name=model_source,
            cache_dir=local_model.parent,
            embedding_cache=self.paths.embedding_cache_path,
            allow_hash_fallback=allow_hash_encoder,
            local_files_only=model_source == str(local_model) or allow_hash_encoder,
            cache_namespace=text_config["model_name"],
        )
        self.concept_extractor = ConceptExtractor.load(
            self.paths.config_dir / "concept_catalog_v1.json"
        )
        self.builder = IntentGraphBuilder(
            self.concept_extractor,
            max_turns=self.graph_config["max_turns"],
            reverse_edges=self.graph_config["reverse_edges"],
        )
        self.encoder = IntentGraphEncoder(
            taxonomy=self.taxonomy,
            concept_catalog_path=self.paths.config_dir / "concept_catalog_v1.json",
            graph_config_path=self.paths.config_dir / "graph_config.json",
            text_encoder=self.text_encoder,
        )
        self.legacy_adapter = LegacyIntentAdapter.load(
            self.paths.config_dir / "legacy_intent_mapping_v1.json"
        )

    def train(
        self,
        *,
        epochs: int | None = None,
        seed: int | None = None,
        checkpoint_name: str = "best_final_package.pt",
        report_name: str = "training_report.json",
        sampling_mode: str = "shuffle",
        sampling_max_weight: float = 3.0,
        sample_limit: int | None = None,
    ) -> dict:
        seed = int(seed if seed is not None else self.training_config["seed"])
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        samples = read_jsonl(self.release_dir / "intent_samples.jsonl", IntentSample)
        unlabeled = [item.sample_id for item in samples if item.labels is None]
        if unlabeled:
            raise ValueError(f"training_samples_missing_labels:{unlabeled[:5]}")
        split_manifest = json.loads(
            (self.release_dir / "split_manifest.json").read_text(encoding="utf-8")
        )["conversation_splits"]
        by_split = {
            name: [item for item in samples if split_manifest[item.conversation_id] == name]
            for name in ("train", "validation", "test")
        }
        if sample_limit is not None:
            by_split = {name: rows[:sample_limit] for name, rows in by_split.items()}
        empty_splits = [name for name, values in by_split.items() if not values]
        if empty_splits:
            raise ValueError(f"release_split_empty:{empty_splits}")
        cacheable_graphs = all(
            turn.predicted_or_gold_state is None
            for sample in samples
            for turn in sample.history
        )
        cache_samples = [
            *by_split["train"],
            *by_split["validation"],
            *by_split["test"],
        ]
        encoded_cache = (
            self._preencode_samples(cache_samples)
            if cacheable_graphs
            else {}
        )
        model_config = self.training_config["model"]
        model = RelationAwareIntentGNN(
            input_dim=self.encoder.input_dim,
            hidden_dim=model_config["hidden_dim"],
            num_relations=len(self.encoder.edge_types),
            num_intents=len(self.taxonomy.semantic_intents.values),
            num_operations=len(self.taxonomy.operations.values),
            num_scopes=len(self.taxonomy.scopes.values),
            num_target_relations=len(self.taxonomy.target_relations.values),
            num_transitions=len(self.taxonomy.transitions.values),
            num_concepts=len(self.encoder.concepts),
            num_security_transitions=len(self.taxonomy.security_transitions.values),
            max_reference_distance=self.graph_config["max_reference_distance"],
            num_layers=model_config["num_layers"],
            dropout=model_config["dropout"],
        ).to(self.device)
        train_config = self.training_config["training"]
        optimizer = torch.optim.AdamW(
            model.parameters(),
            lr=train_config["learning_rate"],
            weight_decay=train_config["weight_decay"],
        )
        class_weights = self._class_weights(by_split["train"])
        best_score = -1.0
        best_epoch = 0
        patience = 0
        history: list[dict] = []
        checkpoint_dir = self.output_dir / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        total_epochs = epochs or train_config["epochs"]
        checkpoint_path = checkpoint_dir / checkpoint_name
        started_at = time.time()
        stop_reason = "max_epochs"

        for epoch in range(1, total_epochs + 1):
            model.train()
            train_loss = 0.0
            gold_probability = _linear_schedule(
                epoch,
                total_epochs,
                self.graph_config["previous_state_training"]["gold_state_probability_start"],
                self.graph_config["previous_state_training"]["gold_state_probability_end"],
            )
            epoch_samples = training_samples_for_epoch(
                by_split["train"],
                mode=sampling_mode,
                seed=seed,
                epoch=epoch,
                max_weight=sampling_max_weight,
            )
            for sample in epoch_samples:
                if encoded_cache:
                    encoded = encoded_cache[sample.sample_id]
                else:
                    graph = self.builder.build(
                        sample,
                        previous_state_policy=PreviousStatePolicy(
                            use_probability=gold_probability,
                            mask_probability=self.graph_config["previous_state_training"]["mask_probability"],
                        ),
                        seed=seed + epoch,
                    )
                    encoded = self.encoder.encode(graph, sample, self.device)
                optimizer.zero_grad(set_to_none=True)
                outputs = model(encoded.x, encoded.edge_indices, encoded.current_node_idx)
                loss, _ = multitask_loss(
                    outputs,
                    encoded.targets,
                    weights=self.training_config["loss_weights"],
                    class_weights=class_weights,
                )
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), train_config["gradient_clip_norm"])
                optimizer.step()
                train_loss += float(loss.detach().cpu())

            validation_records = self.evaluate_samples(
                model,
                by_split["validation"],
                encoded_cache=encoded_cache,
            )
            validation_metrics = calculate_metrics(validation_records)
            score = float(validation_metrics["intent_macro_f1"])
            epoch_record = {
                "epoch": epoch,
                "train_loss": train_loss / max(1, len(by_split["train"])),
                "validation": validation_metrics,
                "previous_gold_state_probability": gold_probability,
            }
            history.append(epoch_record)
            print(
                f"epoch={epoch}/{total_epochs} "
                f"train_loss={epoch_record['train_loss']:.6f} "
                f"validation_intent_macro_f1={score:.6f}",
                flush=True,
            )
            if score > best_score:
                best_score = score
                best_epoch = epoch
                patience = 0
                torch.save(
                    {
                        "model_state": model.state_dict(),
                        "model_config": model.config,
                        "taxonomy": self.taxonomy.payload,
                        "node_types": self.encoder.node_types,
                        "edge_types": self.encoder.edge_types,
                        "concepts": self.encoder.concepts,
                        "text_encoder": self.training_config["text_encoder"],
                        "best_epoch": best_epoch,
                        "validation_metrics": validation_metrics,
                    },
                    checkpoint_path,
                )
            else:
                patience += 1
                if patience >= train_config["early_stopping_patience"]:
                    stop_reason = "early_stopping"
                    break

        checkpoint = torch.load(checkpoint_path, map_location=self.device, weights_only=False)
        model.load_state_dict(checkpoint["model_state"])
        test_records = self.evaluate_samples(
            model,
            by_split["test"],
            encoded_cache=encoded_cache,
        )
        report = {
            "best_epoch": best_epoch,
            "validation_score_used_for_epoch_selection": best_score,
            "test_metrics": calculate_metrics(test_records),
            "history": history,
            "split_counts": {key: len(value) for key, value in by_split.items()},
            "run_config": {
                "seed": seed,
                "device": self.device,
                "epochs_requested": total_epochs,
                "epochs_completed": len(history),
                "sampling_mode": sampling_mode,
                "sampling_max_weight": sampling_max_weight,
                "sample_limit": sample_limit,
                "checkpoint_path": str(checkpoint_path.resolve()),
                "release_dir": str(self.release_dir.resolve()),
                "encoded_graph_cache": bool(encoded_cache),
            },
            "training_distribution": sampling_distribution(by_split["train"]),
            "duration_seconds": round(time.time() - started_at, 3),
            "stop_reason": stop_reason,
            "note": "Checkpoint selection is internal to this run and does not promote runtime authority.",
        }
        write_json(self.output_dir / "reports" / report_name, report)
        return report

    def evaluate_checkpoint(
        self,
        checkpoint_path: str | Path,
        *,
        report_name: str = "checkpoint_evaluation.json",
    ) -> dict:
        checkpoint = torch.load(checkpoint_path, map_location=self.device, weights_only=False)
        model = self._build_model_from_checkpoint(checkpoint)
        samples = read_jsonl(self.release_dir / "intent_samples.jsonl", IntentSample)
        split_manifest = json.loads(
            (self.release_dir / "split_manifest.json").read_text(encoding="utf-8")
        )["conversation_splits"]
        by_split = {
            name: [item for item in samples if split_manifest.get(item.conversation_id) == name]
            for name in ("validation", "test")
        }
        report = {
            "checkpoint_path": _portable_path(Path(checkpoint_path), self.root),
            "validation_metrics": calculate_metrics(self.evaluate_samples(model, by_split["validation"])),
            "test_metrics": calculate_metrics(self.evaluate_samples(model, by_split["test"])),
            "split_counts": {name: len(rows) for name, rows in by_split.items()},
            "note": "Post-migration checkpoint evaluation; this is not a training-history report.",
        }
        write_json(self.output_dir / "reports" / report_name, report)
        return report

    def _build_model_from_checkpoint(self, checkpoint: dict) -> RelationAwareIntentGNN:
        model = RelationAwareIntentGNN(**checkpoint["model_config"]).to(self.device)
        model.load_state_dict(checkpoint["model_state"])
        model.eval()
        return model

    def evaluate_samples(
        self,
        model,
        samples: list[IntentSample],
        *,
        encoded_cache: dict | None = None,
    ) -> list[dict]:
        model.eval()
        records: list[dict] = []
        with torch.no_grad():
            for sample in samples:
                if encoded_cache and sample.sample_id in encoded_cache:
                    encoded = encoded_cache[sample.sample_id]
                else:
                    graph = self.builder.build(sample)
                    encoded = self.encoder.encode(graph, sample, self.device)
                outputs = model(encoded.x, encoded.edge_indices, encoded.current_node_idx)
                prediction = self._decode_indices(outputs)
                truth = {
                    "intent": int(encoded.targets["intent"]),
                    "operation": int(encoded.targets["operation"]),
                    "scope": int(encoded.targets["scope"]),
                    "target_relation": int(encoded.targets["target_relation"]),
                    "transition": int(encoded.targets["transition"]),
                    "reference_distance": int(encoded.targets["reference_distance"]),
                    "security_transition": int(encoded.targets["security_transition"]),
                    "concepts": [int(value) for value in encoded.targets["concepts"].tolist()],
                }
                records.append(
                    {
                        "sample_id": sample.sample_id,
                        "category": sample.category,
                        "metadata": self._sample_metadata(sample),
                        "truth": truth,
                        "prediction": prediction,
                        "truth_legacy": self._legacy_from_labels(sample, truth),
                        "prediction_legacy": self._legacy_from_labels(sample, prediction),
                        "predicted_sequence_class": (
                            "BENIGN_MULTI_TURN"
                            if prediction["security_transition"]
                            == self.taxonomy.security_transitions.index("NONE")
                            else "MALICIOUS_MULTI_TURN"
                        ),
                    }
                )
        return records

    def _preencode_samples(self, samples: list[IntentSample]) -> dict:
        print(f"preencoding_graphs count={len(samples)} device={self.device}", flush=True)
        output = {}
        for index, sample in enumerate(samples, start=1):
            graph = self.builder.build(sample)
            output[sample.sample_id] = self.encoder.encode(graph, sample, self.device)
            if index % 1000 == 0 or index == len(samples):
                print(f"preencoded_graphs={index}/{len(samples)}", flush=True)
        return output

    @staticmethod
    def _sample_metadata(sample: IntentSample) -> dict:
        extra = dict(sample.generation_metadata.extra)
        return {
            "conversation_id": sample.conversation_id,
            "pattern_id": sample.pattern_id,
            "role": sample.role,
            "turn_count": len(sample.history) + 1,
            "category": sample.category,
            **extra,
        }

    def _decode_indices(self, outputs: dict[str, torch.Tensor]) -> dict:
        return {
            "intent": int(outputs["intent_logits"].argmax()),
            "operation": int(outputs["operation_logits"].argmax()),
            "scope": int(outputs["scope_logits"].argmax()),
            "target_relation": int(outputs["target_relation_logits"].argmax()),
            "transition": int(outputs["transition_logits"].argmax()),
            "reference_distance": int(outputs["reference_distance_logits"].argmax()),
            "security_transition": int(outputs["security_transition_logits"].argmax()),
            "concepts": [int(value) for value in (torch.sigmoid(outputs["concept_logits"]) >= 0.5).tolist()],
        }

    def _class_weights(self, samples: list[IntentSample]) -> dict[str, torch.Tensor]:
        if any(item.labels is None for item in samples):
            raise ValueError("class_weights_require_labeled_samples")
        specs = {
            "intent": (self.taxonomy.semantic_intents, lambda item: item.labels.semantic_intent),
            "operation": (self.taxonomy.operations, lambda item: item.labels.operation),
            "scope": (self.taxonomy.scopes, lambda item: item.labels.scope),
            "target_relation": (self.taxonomy.target_relations, lambda item: item.labels.target_relation),
            "transition": (self.taxonomy.transitions, lambda item: item.labels.transition),
            "security_transition": (
                self.taxonomy.security_transitions,
                lambda item: item.labels.security_transition,
            ),
        }
        output: dict[str, torch.Tensor] = {}
        for name, (space, getter) in specs.items():
            counts = Counter(getter(item) for item in samples)
            weights = [
                len(samples) / max(1, len(space.values) * counts.get(label, 0))
                if counts.get(label, 0)
                else 0.0
                for label in space.values
            ]
            output[name] = torch.tensor(weights, dtype=torch.float32, device=self.device)
        return output

    def _legacy_from_labels(self, sample: IntentSample, labels: dict) -> str:
        resolution = IntentResolution(
            primary_intent=self.taxonomy.semantic_intents.values[labels["intent"]],
            intent_candidates=[],
            operation=self.taxonomy.operations.values[labels["operation"]],
            scope=self.taxonomy.scopes.values[labels["scope"]],
            target_relation=self.taxonomy.target_relations.values[labels["target_relation"]],
            transition=self.taxonomy.transitions.values[labels["transition"]],
            target_concepts=[
                concept
                for concept, enabled in zip(self.encoder.concepts, labels["concepts"])
                if enabled
            ],
            reference_links=[],
            security_transition=self.taxonomy.security_transitions.values[
                labels["security_transition"]
            ],
            uncertainty={},
        )
        return self.legacy_adapter.resolve(role=sample.role, resolution=resolution)["legacy_intent"]


def _portable_path(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return "external_checkpoint"


def _linear_schedule(epoch: int, total_epochs: int, start: float, end: float) -> float:
    if total_epochs <= 1:
        return end
    ratio = (epoch - 1) / (total_epochs - 1)
    return start + (end - start) * ratio
