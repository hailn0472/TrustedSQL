from __future__ import annotations

import subprocess
from pathlib import Path


FORBIDDEN_SUFFIXES = {".sqlite3", ".safetensors"}
FORBIDDEN_PARTS = {"outputs", ".cache", "embedding_cache", "__pycache__"}
MAX_TRACKED_BYTES = 20 * 1024 * 1024
PROMOTED_CHECKPOINT = "artifacts/models/intent_gnn/v1/best.pt"
CREDENTIAL_MARKERS = (
    '"private_key"',
    '"private_key_id"',
    '"client_email"',
    "-----begin private key-----",
)


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=root, check=True, capture_output=True, text=True
    )
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    violations: list[str] = []
    candidates = set(tracked.stdout.splitlines()) | set(untracked.stdout.splitlines())
    for raw in sorted(candidates):
        path = root / raw
        if not path.exists():
            continue
        parts = set(Path(raw).parts)
        if parts & FORBIDDEN_PARTS:
            violations.append(f"forbidden tracked path: {raw}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            violations.append(f"forbidden tracked artifact: {raw}")
        if path.suffix.lower() == ".pt" and Path(raw).as_posix() != PROMOTED_CHECKPOINT:
            violations.append(f"checkpoint is not the promoted runtime model: {raw}")
        if path.exists() and path.stat().st_size > MAX_TRACKED_BYTES:
            violations.append(f"tracked file exceeds 20 MiB: {raw}")
        if path.resolve() != Path(__file__).resolve() and path.stat().st_size <= 5 * 1024 * 1024:
            try:
                text = path.read_text(encoding="utf-8-sig", errors="ignore").lower()
            except OSError:
                text = ""
            if any(marker in text for marker in CREDENTIAL_MARKERS):
                violations.append(f"credential-like content: {raw}")
    if violations:
        print("\n".join(violations))
        return 1
    print("Repository artifact policy passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
