from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from architecture_baselines.schemas import NormalizedSequence, NormalizedTurn


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _require_field(raw: dict[str, Any], field: str, source_dataset: str) -> Any:
    value = raw.get(field)
    if value is None or value == "":
        sample_id = raw.get("id") or raw.get("sample_id") or "<unknown>"
        raise ValueError(f"Dataset {source_dataset} sample {sample_id} is missing required field: {field}")
    return value


def normalize_sequence(raw: dict[str, Any], source_dataset: str) -> NormalizedSequence:
    sample_id = raw.get("id") or raw.get("sample_id")
    if sample_id is None or sample_id == "":
        raise ValueError(f"Dataset {source_dataset} has a sample without id/sample_id")
    role = str(_require_field(raw, "role", source_dataset)).lower()
    raw_user_id = _require_field(raw, "user_context_id", source_dataset)
    if isinstance(raw_user_id, int):
        user_id = raw_user_id
    elif isinstance(raw_user_id, str) and raw_user_id.isdigit():
        user_id = int(raw_user_id)
    else:
        raise ValueError(f"Dataset {source_dataset} sample {sample_id} has invalid user_context_id: {raw_user_id!r}")
    attack_tags = dict(raw.get("attack_tags") or {})
    attack_tags["rbac_violation"] = _as_list(attack_tags.get("rbac_violation"))
    attack_tags["violated_policies"] = _as_list(attack_tags.get("violated_policies"))
    raw_turns = raw.get("turns")
    if not isinstance(raw_turns, list) or not raw_turns:
        raise ValueError(f"Dataset {source_dataset} sample {sample_id} must contain at least one turn")
    turns: list[NormalizedTurn] = []
    for idx, turn in enumerate(raw_turns, start=1):
        turn_id = int(turn.get("turn_id", idx))
        turn_label = str(turn.get("turn_label") or raw.get("seq_label") or "BENIGN")
        sql_gt = turn.get("sql_gt")
        if turn_label == "BENIGN" and not sql_gt:
            raise ValueError(f"Dataset {source_dataset} sample {sample_id} turn {turn_id} is missing required sql_gt for BENIGN turn")
        turns.append(
            NormalizedTurn(
                turn_id=turn_id,
                nlq=str(turn.get("nlq") or ""),
                sql_gt=sql_gt,
                turn_label=turn_label,
            )
        )
    return NormalizedSequence(
        sample_id=str(sample_id),
        source_dataset=source_dataset,
        turn_type=str(raw.get("turn_type") or ("multi" if len(turns) > 1 else "single")),
        seq_label=str(raw.get("seq_label") or ("MALICIOUS" if source_dataset in {"rbac_single", "pi_single", "malicious_multi"} else "BENIGN")),
        role=role,
        user_id=user_id,
        attack_tags=attack_tags,
        turns=turns,
        primary_type=raw.get("primary_type"),
    )


def load_sequences(dataset_configs: dict[str, Any], project_root: Path) -> list[NormalizedSequence]:
    sequences: list[NormalizedSequence] = []
    for source_dataset, cfg in dataset_configs.items():
        path = Path(cfg["path"])
        if not path.is_absolute():
            path = (project_root / path).resolve()
        with path.open("r", encoding="utf-8-sig") as handle:
            raw_data = json.load(handle)
        raw_items = list(raw_data.values()) if isinstance(raw_data, dict) else list(raw_data)
        for raw in raw_items:
            sequences.append(normalize_sequence(raw, source_dataset))
    return sequences

