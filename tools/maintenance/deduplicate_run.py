from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


def runtime_key(row: dict[str, Any]) -> str:
    return "|".join(
        str(row.get(field) or "")
        for field in ("setting_id", "sample_id", "turn_id")
    )


def keep_last(rows: Iterable[dict[str, Any]], *, include_module: bool = False) -> list[dict[str, Any]]:
    kept: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for row in rows:
        key = runtime_key(row)
        if include_module:
            key = f"{key}|{row.get('module_id') or ''}"
        if key in kept:
            del kept[key]
        kept[key] = row
    return list(kept.values())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def backup_artifacts(run_dir: Path, backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=False)
    for relative in (
        "runtime",
        "evaluation",
    ):
        source = run_dir / relative
        if source.exists():
            shutil.copytree(source, backup_dir / relative)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deduplicate one TrustedSQL run in place.")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    runtime_dir = run_dir / "runtime"
    raw_path = runtime_dir / "raw_turn_outputs.jsonl"
    csv_path = runtime_dir / "turn_runtime.csv"
    if not raw_path.exists() or not csv_path.exists():
        raise FileNotFoundError("The run does not contain canonical runtime outputs.")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = run_dir / f"_duplicate_cleanup_backup_{stamp}"
    backup_artifacts(run_dir, backup_dir)
    evaluation_dir = run_dir / "evaluation"
    if evaluation_dir.exists():
        shutil.rmtree(evaluation_dir)

    raw_before = read_jsonl(raw_path)
    raw_after = keep_last(raw_before)
    write_jsonl(raw_path, raw_after)

    csv_before, fieldnames = read_csv(csv_path)
    csv_after = keep_last(csv_before)
    write_csv(csv_path, csv_after, fieldnames)

    module_events_path = runtime_dir / "module_events.jsonl"
    module_events_before = read_jsonl(module_events_path)
    module_events_after = keep_last(module_events_before, include_module=True)
    write_jsonl(module_events_path, module_events_after)

    module_log_counts: dict[str, dict[str, int]] = {}
    module_log_dir = runtime_dir / "module_logs"
    if module_log_dir.exists():
        for path in sorted(module_log_dir.glob("*.jsonl")):
            before = read_jsonl(path)
            after = keep_last(before, include_module=True)
            write_jsonl(path, after)
            module_log_counts[path.name] = {"before": len(before), "after": len(after)}

    completed = sorted(
        f"{row.get('setting_id')}|{row.get('sample_id')}|{row.get('turn_id')}"
        for row in raw_after
    )
    with (runtime_dir / "checkpoint.json").open("w", encoding="utf-8") as handle:
        json.dump({"completed": completed}, handle, ensure_ascii=False, indent=2)

    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "policy": "keep_last_by_setting_sample_turn",
        "backup_dir": str(backup_dir),
        "raw_turn_outputs": {"before": len(raw_before), "after": len(raw_after)},
        "turn_runtime": {"before": len(csv_before), "after": len(csv_after)},
        "module_events": {
            "before": len(module_events_before),
            "after": len(module_events_after),
        },
        "module_logs": module_log_counts,
    }
    with (runtime_dir / "deduplication_manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
