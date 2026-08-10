from __future__ import annotations

import json
from pathlib import Path


class Checkpoint:
    def __init__(self, path: Path, resume: bool = True, run_fingerprint: str | None = None):
        self.path = path
        self.run_fingerprint = run_fingerprint
        self.completed: set[str] = set()
        if resume and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            stored_fingerprint = data.get("run_fingerprint")
            if run_fingerprint and stored_fingerprint != run_fingerprint:
                raise ValueError(
                    f"Checkpoint fingerprint mismatch for {path}. "
                    "Use a new run_id or remove stale runtime outputs/checkpoints before resuming."
                )
            self.completed = set(data.get("completed", []))

    def key(self, architecture_id: str, source_dataset: str, sample_id: str, turn_id: int) -> str:
        return f"{architecture_id}::{source_dataset}::{sample_id}::{turn_id}"

    def is_done(self, architecture_id: str, source_dataset: str, sample_id: str, turn_id: int) -> bool:
        return self.key(architecture_id, source_dataset, sample_id, turn_id) in self.completed

    def mark_done(self, architecture_id: str, source_dataset: str, sample_id: str, turn_id: int) -> None:
        self.completed.add(self.key(architecture_id, source_dataset, sample_id, turn_id))
        self.write()

    def remove_keys(self, keys: set[str]) -> None:
        self.completed -= keys
        self.write()

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"run_fingerprint": self.run_fingerprint, "completed": sorted(self.completed)}, ensure_ascii=False, indent=2), encoding="utf-8")

