from __future__ import annotations

import argparse
import json
import os
import shutil
from hashlib import sha256
from pathlib import Path


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch and verify the pinned GNN text encoder")
    parser.add_argument("--project-root", default=os.environ.get("TRUSTEDSQL_PROJECT_ROOT"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    root = Path(args.project_root).resolve() if args.project_root else Path(__file__).resolve().parents[2]
    manifest_path = root / "artifacts" / "models" / "intent_gnn" / "v1" / "encoder_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    encoder_root = Path(
        os.environ.get("TRUSTEDSQL_TEXT_ENCODER_DIR", root / "artifacts" / "text_encoder")
    ).resolve()
    target = encoder_root / manifest["local_subpath"]
    weights = target / "model.safetensors"
    vocab = target / "vocab.txt"
    expected_weights = manifest["model_safetensors_sha256"].upper()
    expected_vocab = manifest["vocab_txt_sha256"].upper()
    if (
        weights.exists()
        and vocab.exists()
        and file_hash(weights) == expected_weights
        and file_hash(vocab) == expected_vocab
        and not args.force
    ):
        print(f"Encoder already verified: {target}")
        return 0
    if target.exists():
        shutil.rmtree(target)
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=manifest["model_id"],
        revision=manifest["revision"],
        local_dir=target,
    )
    actual_weights = file_hash(weights)
    actual_vocab = file_hash(vocab)
    if actual_weights != expected_weights or actual_vocab != expected_vocab:
        raise RuntimeError(
            "Encoder hash mismatch: "
            f"weights expected={expected_weights}, actual={actual_weights}; "
            f"vocab expected={expected_vocab}, actual={actual_vocab}"
        )
    print(f"Encoder downloaded and verified: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
