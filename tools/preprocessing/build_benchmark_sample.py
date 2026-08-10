from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable


FILES = {
    "benign_single": "SingleTurn_Benign_records.json",
    "benign_multi": "Multiturn_Benign_records.json",
    "pi_single": "SingleTurn_PromptInjection_Malicious_records.json",
    "rbac_single": "SingleTurn_RBAC_Violation_records.json",
    "malicious_multi": "Multiturn_Malicious_records.json",
}
ROLES = ("student", "lecturer")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic role-balanced benchmark sample")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--source", default="data/benchmark/v1/full")
    parser.add_argument("--output", default="artifacts/generated/benchmark_sample")
    parser.add_argument("--seed", type=int, default=20260702)
    args = parser.parse_args()

    root = args.project_root.resolve()
    source_dir = _resolve(root, args.source)
    output_dir = _resolve(root, args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    source = {name: _read_records(source_dir / filename) for name, filename in FILES.items()}
    selected = {
        "benign_single": _sample_by_role(source["benign_single"], 50, rng),
        "benign_multi": _sample_by_role(source["benign_multi"], 50, rng),
        "pi_single": _sample_by_type_and_role(source["pi_single"], _primary_type, 5, rng),
        "rbac_single": _sample_by_type_and_role(source["rbac_single"], _primary_type, 5, rng),
        "malicious_multi": _sample_by_type_and_role(source["malicious_multi"], _mt_pattern, 5, rng),
    }

    for name, records in selected.items():
        _validate_unique_ids(records, name)
        _write_json(output_dir / FILES[name], records)

    manifest = {
        "seed": args.seed,
        "sampling_unit": {
            "single_turn": "record",
            "multi_turn": "complete_sequence",
        },
        "selection": {
            "benign_single": "50 records per role",
            "benign_multi": "50 complete sequences per role",
            "pi_single": "5 records per role for each primary_type",
            "rbac_single": "5 records per role for each primary_type",
            "malicious_multi": "5 complete sequences per role for each attack_tags.mt_pattern",
        },
        "sources": {
            filename: {
                "sha256": _sha256(source_dir / filename),
                "record_count": len(source[name]),
            }
            for name, filename in FILES.items()
        },
        "outputs": {
            FILES[name]: _summary(name, records)
            for name, records in selected.items()
        },
    }
    _write_json(output_dir / "sample_manifest.json", manifest)
    print(json.dumps(manifest["outputs"], ensure_ascii=False, indent=2))
    return 0


def _sample_by_role(records: list[dict[str, Any]], count: int, rng: random.Random) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for role in ROLES:
        candidates = [record for record in records if record.get("role") == role]
        selected.extend(_sample(candidates, count, rng, f"role={role}"))
    return _source_order(selected, records)


def _sample_by_type_and_role(
    records: list[dict[str, Any]],
    type_getter: Callable[[dict[str, Any]], str],
    count_per_role: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        group = type_getter(record)
        if not group:
            raise ValueError(f"Record {record.get('id')} has no sampling type")
        grouped[group].append(record)
    selected: list[dict[str, Any]] = []
    for group in sorted(grouped):
        for role in ROLES:
            candidates = [record for record in grouped[group] if record.get("role") == role]
            selected.extend(
                _sample(candidates, count_per_role, rng, f"type={group}, role={role}")
            )
    return _source_order(selected, records)


def _sample(
    candidates: list[dict[str, Any]],
    count: int,
    rng: random.Random,
    label: str,
) -> list[dict[str, Any]]:
    if len(candidates) < count:
        raise ValueError(f"Insufficient records for {label}: required={count}, available={len(candidates)}")
    return rng.sample(candidates, count)


def _source_order(selected: list[dict[str, Any]], source: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected_ids = {str(record.get("id")) for record in selected}
    return [record for record in source if str(record.get("id")) in selected_ids]


def _primary_type(record: dict[str, Any]) -> str:
    return str(record.get("primary_type") or "")


def _mt_pattern(record: dict[str, Any]) -> str:
    return str((record.get("attack_tags") or {}).get("mt_pattern") or "")


def _summary(name: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "record_count": len(records),
        "turn_count": sum(len(record.get("turns") or []) for record in records),
        "roles": dict(sorted(Counter(str(record.get("role")) for record in records).items())),
    }
    if name in {"pi_single", "rbac_single"}:
        summary["primary_types"] = dict(sorted(Counter(_primary_type(record) for record in records).items()))
    if name == "malicious_multi":
        summary["mt_patterns"] = dict(sorted(Counter(_mt_pattern(record) for record in records).items()))
    return summary


def _validate_unique_ids(records: list[dict[str, Any]], name: str) -> None:
    ids = [str(record.get("id")) for record in records]
    duplicates = sorted(item for item, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise ValueError(f"Duplicate IDs in {name}: {duplicates[:10]}")


def _read_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected a JSON list: {path}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


if __name__ == "__main__":
    raise SystemExit(main())
