"""Safe, deterministic prompt library for the interactive TrustedSQL demo."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .paths import parent_resource_path


class ScenarioCatalogError(ValueError):
    """Raised when the multiturn prompt library cannot be loaded safely."""


_APPROVED_SCENARIOS = (
    {
        "key": "multiturn",
        "canonical_id": "MT-MAL-420",
        "source": "data/benchmark/v3/full/Multiturn_Malicious_records.json",
        "title": "Multiturn prompt library",
        "description": "Six reference prompts that can be copied into the live TrustedSQL chat.",
        "expected_turn_labels": (
            "BENIGN",
            "BENIGN",
            "BENIGN",
            "BENIGN",
            "BENIGN",
            "MALICIOUS",
        ),
    },
)

_SEARCHABLE_DATASETS = (
    "data/benchmark/v3/full/Multiturn_Benign_records.json",
    "data/benchmark/v3/full/Multiturn_Malicious_records.json",
    "data/benchmark/v3/full/SingleTurn_Benign_records.json",
    "data/benchmark/v3/full/SingleTurn_PromptInjection_Malicious_records.json",
    "data/benchmark/v3/full/SingleTurn_RBAC_Violation_records.json",
)
_DATASET_SCENARIO_ID = re.compile(r"^(?:MT-(?:BEN|MAL)|ST-(?:BENIGN|PI|RBAC))-[0-9]{3}$")
_MAX_SEARCH_RESULTS = 20
_MAX_DATASET_TURNS = 20
_MAX_QUERY_CHARS = 2_000


def _load_approved_record(repository_root: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    source_path = parent_resource_path(repository_root, scenario["source"])
    try:
        with source_path.open("r", encoding="utf-8") as source_file:
            records = json.load(source_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioCatalogError(
            f"could not load approved scenario source {scenario['source']}"
        ) from exc

    if not isinstance(records, list):
        raise ScenarioCatalogError(f"scenario source {scenario['source']} is not a record list")

    matches = [record for record in records if isinstance(record, dict) and record.get("id") == scenario["canonical_id"]]
    if len(matches) != 1:
        raise ScenarioCatalogError(
            f"approved scenario {scenario['canonical_id']} must resolve to exactly one record"
        )
    return matches[0]


def _validated_turns(record: dict[str, Any], scenario: dict[str, Any]) -> list[dict[str, Any]]:
    turns = record.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ScenarioCatalogError(f"scenario {scenario['key']} has no turns")

    expected_ids = list(range(1, len(turns) + 1))
    actual_ids = [turn.get("turn_id") if isinstance(turn, dict) else None for turn in turns]
    if any(type(turn_id) is not int for turn_id in actual_ids) or actual_ids != expected_ids:
        raise ScenarioCatalogError(
            f"scenario {scenario['key']} has missing or noncontiguous turn IDs"
        )
    if any(
        not isinstance(turn, dict)
        or not isinstance(turn.get("nlq"), str)
        or not isinstance(turn.get("turn_label"), str)
        for turn in turns
    ):
        raise ScenarioCatalogError(f"scenario {scenario['key']} has malformed turn data")

    expected_labels = list(scenario["expected_turn_labels"])
    actual_labels = [turn["turn_label"] for turn in turns]
    if actual_labels != expected_labels:
        raise ScenarioCatalogError(
            f"scenario {scenario['key']} has unexpected turn labels"
        )
    return turns


def _validated_record(repository_root: Path, scenario: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    record = _load_approved_record(repository_root, scenario)
    if record.get("turn_type") not in {"single", "multi"}:
        raise ScenarioCatalogError(f"scenario {scenario['key']} has an invalid turn type")
    if record.get("role") != "lecturer" or record.get("user_context_id") != 1:
        raise ScenarioCatalogError(
            f"approved scenario {scenario['key']} must use lecturer user 1"
        )
    turns = _validated_turns(record, scenario)
    return record, turns


def load_scenario_catalog(repo_root: Path) -> dict[str, dict[str, Any]]:
    """Load the single multiturn record as a copy-only prompt library."""

    repository_root = Path(repo_root)
    catalog: dict[str, dict[str, Any]] = {}
    for scenario in _APPROVED_SCENARIOS:
        record, turns = _validated_record(repository_root, scenario)
        catalog[scenario["key"]] = {
            "key": scenario["key"],
            "canonical_id": scenario["canonical_id"],
            "title": scenario["title"],
            "description": scenario["description"],
            "role": record["role"],
            "user_id": record["user_context_id"],
            "turn_type": record["turn_type"],
            "turns": [
                {
                    "turn_id": turn["turn_id"],
                    "nlq": turn["nlq"],
                    "turn_label": turn["turn_label"],
                }
                for turn in turns
            ],
        }
    return catalog


def load_dataset_prompt_catalog(repo_root: Path) -> dict[str, dict[str, Any]]:
    """Build a read-only index of every approved benchmark dataset."""

    repository_root = Path(repo_root)
    catalog: dict[str, dict[str, Any]] = {}
    for relative_source in _SEARCHABLE_DATASETS:
        source_path = parent_resource_path(repository_root, relative_source)
        try:
            records = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ScenarioCatalogError(f"could not load dataset prompt source {relative_source}") from exc
        if not isinstance(records, list):
            raise ScenarioCatalogError(f"dataset prompt source {relative_source} is not a record list")

        for record in records:
            if not isinstance(record, dict):
                continue
            scenario_id = record.get("id")
            turns = record.get("turns")
            role = record.get("role")
            user_id = record.get("user_context_id")
            if (
                not isinstance(scenario_id, str)
                or not _DATASET_SCENARIO_ID.fullmatch(scenario_id)
                or record.get("turn_type") not in {"single", "multi"}
                or role not in {"lecturer", "student"}
                or type(user_id) is not int
                or not isinstance(turns, list)
                or not 1 <= len(turns) <= _MAX_DATASET_TURNS
            ):
                continue
            safe_turns: list[dict[str, Any]] = []
            for expected_turn, turn in enumerate(turns, start=1):
                if not isinstance(turn, dict):
                    safe_turns = []
                    break
                turn_id = turn.get("turn_id")
                nlq = turn.get("nlq")
                turn_label = turn.get("turn_label")
                if (
                    turn_id != expected_turn
                    or not isinstance(nlq, str)
                    or not nlq.strip()
                    or len(nlq) > _MAX_QUERY_CHARS
                    or turn_label not in {"BENIGN", "MALICIOUS"}
                ):
                    safe_turns = []
                    break
                safe_turns.append(
                    {"turn_id": turn_id, "nlq": nlq.strip(), "turn_label": turn_label}
                )
            if not safe_turns:
                continue
            if scenario_id in catalog:
                raise ScenarioCatalogError(f"duplicate dataset scenario id {scenario_id}")
            catalog[scenario_id] = {
                "id": scenario_id,
                "source_file": source_path.name,
                "role": role,
                "user_id": user_id,
                "turn_type": record["turn_type"],
                "turns": safe_turns,
            }
    return catalog


def search_dataset_prompt_catalog(
    catalog: Mapping[str, Mapping[str, Any]],
    query: str,
    limit: int = 12,
    role: str | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(query, str) or len(query) > 120:
        raise ValueError("search query is invalid")
    if type(limit) is not int or not 1 <= limit <= _MAX_SEARCH_RESULTS:
        raise ValueError("search limit is invalid")
    if role not in {None, "student", "lecturer"}:
        raise ValueError("search role is invalid")
    needle = query.strip().casefold()

    def score(item: tuple[str, Mapping[str, Any]]) -> tuple[int, str]:
        scenario_id, record = item
        folded_id = scenario_id.casefold()
        folded_file = str(record.get("source_file", "")).casefold()
        if folded_id == needle:
            rank = 0
        elif folded_id.startswith(needle):
            rank = 1
        elif needle in folded_id:
            rank = 2
        elif folded_file.startswith(needle):
            rank = 3
        else:
            rank = 4
        return rank, scenario_id

    matches = [
        (scenario_id, record)
        for scenario_id, record in catalog.items()
        if (role is None or record.get("role") == role)
        and (
            not needle
            or needle in scenario_id.casefold()
            or needle in str(record.get("source_file", "")).casefold()
        )
    ]
    matches.sort(key=score)
    return [
        {
            "id": scenario_id,
            "source_file": record["source_file"],
            "role": record["role"],
            "user_id": record["user_id"],
            "turn_count": len(record["turns"]),
        }
        for scenario_id, record in matches[:limit]
    ]


def get_dataset_prompt_scenario(
    catalog: Mapping[str, Mapping[str, Any]], scenario_id: str
) -> dict[str, Any]:
    if not isinstance(scenario_id, str) or not _DATASET_SCENARIO_ID.fullmatch(scenario_id):
        raise ValueError("scenario id is invalid")
    record = catalog.get(scenario_id)
    if record is None:
        raise KeyError(scenario_id)
    return {
        "key": f"dataset-{scenario_id.lower()}",
        "canonical_id": scenario_id,
        "title": f"Dataset {record['turn_type']}-turn scenario",
        "description": f"Copy-only prompts loaded from {record['source_file']}. Runtime identity is unchanged.",
        "source_file": record["source_file"],
        "role": record["role"],
        "user_id": record["user_id"],
        "turn_type": record["turn_type"],
        "turn_count": len(record["turns"]),
        "turns": [dict(turn) for turn in record["turns"]],
    }


__all__ = [
    "ScenarioCatalogError",
    "get_dataset_prompt_scenario",
    "load_dataset_prompt_catalog",
    "load_scenario_catalog",
    "search_dataset_prompt_catalog",
]
