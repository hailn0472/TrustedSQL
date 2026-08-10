from __future__ import annotations

from pathlib import Path
from typing import Any

from trustedsql.schemas import NormalizedSequence, NormalizedTurn
from trustedsql.utils.io import read_json


def _as_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        return list(raw.values())
    if isinstance(raw, list):
        return raw
    raise ValueError("Dataset file must contain a list or object")


def _dataset_kind(name: str) -> str:
    if "benign_single" in name:
        return "benign_single"
    if "benign_multi" in name:
        return "benign_multi"
    if "rbac" in name:
        return "rbac_single"
    if "pi" in name or "prompt" in name:
        return "pi_single"
    if "malicious_multi" in name or "multi_malicious" in name:
        return "malicious_multi"
    return name


def load_sequences(dataset_configs: dict[str, Any], project_root: Path, max_samples: int | None = None) -> list[NormalizedSequence]:
    sequences: list[NormalizedSequence] = []
    configs = dataset_configs.get("datasets", dataset_configs)
    for name, cfg in configs.items():
        if cfg is None or cfg.get("enabled", True) is False:
            continue
        path = Path(cfg.get("path", cfg.get("file", "")))
        if not path.is_absolute():
            path = project_root / path
        raw = read_json(path)
        source_dataset = cfg.get("source_dataset") or _dataset_kind(name)
        count = 0
        for item in _as_items(raw):
            if max_samples is not None and count >= max_samples:
                break
            turns_raw = item.get("turns") or [
                {
                    "turn_id": item.get("turn_id", 1),
                    "nlq": item.get("nlq") or item.get("question") or item.get("query", ""),
                    "sql_gt": item.get("sql_gt"),
                    "turn_label": item.get("turn_label"),
                }
            ]
            turns = [
                NormalizedTurn(
                    turn_id=int(turn.get("turn_id", index + 1)),
                    nlq=str(turn.get("nlq") or turn.get("question") or ""),
                    sql_gt=turn.get("sql_gt"),
                    turn_label=str(turn.get("turn_label") or ("MALICIOUS" if source_dataset in {"rbac_single", "pi_single"} else "BENIGN")),
                )
                for index, turn in enumerate(turns_raw)
            ]
            seq_label = str(item.get("seq_label") or ("MALICIOUS" if source_dataset in {"rbac_single", "pi_single", "malicious_multi"} else "BENIGN"))
            sequences.append(
                NormalizedSequence(
                    sample_id=str(item.get("id") or item.get("sample_id") or f"{source_dataset}-{count + 1:04d}"),
                    source_dataset=source_dataset,
                    turn_type=str(item.get("turn_type") or ("multi" if len(turns) > 1 else "single")),
                    seq_label=seq_label,
                    role=str(item.get("role") or item.get("user_role") or "student"),
                    user_id=int(item.get("user_context_id") or item.get("user_id") or item.get("context_user_id") or 0),
                    attack_tags=dict(item.get("attack_tags") or {}),
                    turns=turns,
                    primary_type=item.get("primary_type") or (item.get("attack_tags") or {}).get("mt_pattern"),
                )
            )
            count += 1
    return sequences

