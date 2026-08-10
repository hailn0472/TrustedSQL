from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from architecture_baselines.schemas import ArchitectureTurnOutput, ModuleResult
from architecture_baselines.utils.jsonl import append_jsonl, read_jsonl


class RunWriter:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def architecture_dir(self, architecture_id: str) -> Path:
        path = self.run_dir / "architectures" / architecture_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        (self.run_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def append_turn_output(self, output: ArchitectureTurnOutput) -> None:
        arch_dir = self.architecture_dir(output.architecture_id)
        append_jsonl(arch_dir / "raw_turn_outputs.jsonl", output.to_dict())
        self._append_turn_csv(arch_dir / "turn_runtime.csv", output)
        for module in output.module_trace:
            self.append_module_event(output.architecture_id, output, module)

    def append_module_event(self, architecture_id: str, output: ArchitectureTurnOutput, module: ModuleResult) -> None:
        row = {"run_id": output.run_id, "architecture_id": architecture_id, "sample_id": output.sample_id, "source_dataset": output.source_dataset, "turn_id": output.turn_id, **module.to_dict()}
        append_jsonl(self.architecture_dir(architecture_id) / "module_events.jsonl", row)

    def append_sequence_output(self, architecture_id: str, row: dict[str, Any]) -> None:
        append_jsonl(self.architecture_dir(architecture_id) / "sequence_runtime.jsonl", row)

    def load_turn_outputs(self, architecture_id: str) -> list[dict[str, Any]]:
        return read_jsonl(self.architecture_dir(architecture_id) / "raw_turn_outputs.jsonl")

    def _append_turn_csv(self, path: Path, output: ArchitectureTurnOutput) -> None:
        fields = ["run_id", "architecture_id", "source_dataset", "sample_id", "turn_id", "turn_label", "role", "user_id", "decision", "blocked_at", "executed", "raw_sql", "final_sql", "latency_ms", "error"]
        exists = path.exists()
        with path.open("a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            if not exists:
                writer.writeheader()
            data = output.to_dict()
            writer.writerow({field: data.get(field, "") for field in fields})

