from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from trustedsql_gnn.data.v2 import SPLIT_FILENAMES


def test_promoted_model_manifest_is_portable(project_root: Path) -> None:
    model_dir = project_root / "artifacts" / "models" / "intent_gnn" / "v1"
    manifest = json.loads((model_dir / "model_manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["checkpoint"]["path"] == "artifacts/models/intent_gnn/v1/best.pt"
    assert manifest["checkpoint"]["sha256"] == _sha256(model_dir / "best.pt")
    serialized = json.dumps(manifest)
    assert "D:\\" not in serialized
    assert manifest["source"]["training_git_commit"] == "not_recorded"
    assert manifest["source"]["training_history"] == "not_recorded"


def test_model_development_release_has_only_three_partitions() -> None:
    assert SPLIT_FILENAMES == {
        "train": "train.jsonl",
        "validation": "validation.jsonl",
        "test": "test.jsonl",
    }


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()

