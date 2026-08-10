from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GNNPaths:
    project_root: Path
    config_dir: Path
    checkpoint_path: Path
    encoder_dir: Path
    embedding_cache_path: Path
    training_data_dir: Path
    training_output_dir: Path

    @classmethod
    def from_project_root(
        cls,
        project_root: str | Path,
        *,
        checkpoint_path: str | Path | None = None,
        training_output_dir: str | Path | None = None,
    ) -> "GNNPaths":
        root = Path(project_root).resolve()
        text_encoder_root = Path(
            os.environ.get(
                "TRUSTEDSQL_TEXT_ENCODER_DIR",
                root / "artifacts" / "text_encoder",
            )
        ).resolve()
        embedding_cache = Path(
            os.environ.get(
                "TRUSTEDSQL_EMBEDDING_CACHE_PATH",
                root / ".cache" / "trustedsql" / "embeddings" / "intent_gnn.sqlite3",
            )
        ).resolve()
        checkpoint = Path(
            checkpoint_path
            or root / "artifacts" / "models" / "intent_gnn" / "v1" / "best.pt"
        ).resolve()
        output = Path(
            training_output_dir or root / "outputs" / "training"
        ).resolve()
        return cls(
            project_root=root,
            config_dir=root / "configs" / "gnn",
            checkpoint_path=checkpoint,
            encoder_dir=text_encoder_root / "all-MiniLM-L6-v2",
            embedding_cache_path=embedding_cache,
            training_data_dir=root / "data" / "training" / "intent_gnn" / "v1",
            training_output_dir=output,
        )

    def require_runtime_assets(self) -> None:
        required = [
            self.config_dir / "graph_config.json",
            self.config_dir / "concept_catalog_v1.json",
            self.config_dir / "legacy_intent_mapping_v1.json",
            self.checkpoint_path,
            self.encoder_dir / "modules.json",
        ]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise FileNotFoundError(
                "Missing GNN runtime assets: " + ", ".join(missing)
            )
