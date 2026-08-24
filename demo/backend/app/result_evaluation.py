"""Post-runtime dataset result comparison for the interactive demo.

Ground truth is loaded and executed only here, after the runtime has produced a
final result. It is never included in runtime prompts, policy context, or model
inputs.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from benchmark_eval.ex_metrics import ex_match, rows_from_result, soft_f1
except ModuleNotFoundError as exc:
    if exc.name != "benchmark_eval":
        raise
    # `python -m demo.backend.app.main` from the repository root does not add
    # `<repo>/src` to sys.path, while editable/package installs do.
    source_root = Path(__file__).resolve().parents[3] / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from benchmark_eval.ex_metrics import ex_match, rows_from_result, soft_f1
from trustedsql.db.executor import DatabaseExecutor

from .paths import parent_resource_path


_DATASET_FILES = (
    "data/benchmark/v3/full/Multiturn_Benign_records.json",
    "data/benchmark/v3/full/Multiturn_Malicious_records.json",
    "data/benchmark/v3/full/SingleTurn_Benign_records.json",
    "data/benchmark/v3/full/SingleTurn_PromptInjection_Malicious_records.json",
    "data/benchmark/v3/full/SingleTurn_RBAC_Violation_records.json",
)
_MAX_DIFF_ROWS = 5


@dataclass(frozen=True)
class _ExpectedTurn:
    dataset_id: str
    turn_type: str
    turn_id: int
    sql_gt: str | None
    normalized_prefix: tuple[str, ...]


def _normalize_nlq(value: str) -> str:
    return " ".join(value.split()).casefold()


def _json_row(row: tuple[Any, ...]) -> list[Any]:
    def convert(value: Any) -> Any:
        if isinstance(value, tuple):
            return [convert(item) for item in value]
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, Mapping):
            return {str(key): convert(item) for key, item in value.items()}
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    return [convert(value) for value in row]


def _column_key(value: str) -> str:
    return value.strip().strip('"').casefold()


class DatasetResultEvaluator:
    """Match a dataset turn and compare runtime rows with its SQL ground truth."""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self._turns_by_query = self._load_turn_index()

    def _load_turn_index(self) -> dict[str, list[_ExpectedTurn]]:
        index: dict[str, list[_ExpectedTurn]] = {}
        for relative_path in _DATASET_FILES:
            path = parent_resource_path(self.repo_root, relative_path)
            try:
                records = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(records, list):
                continue
            for record in records:
                if (
                    not isinstance(record, Mapping)
                    or record.get("role") != "student"
                    or record.get("user_context_id") != 40
                    or record.get("turn_type") not in {"single", "multi"}
                    or not isinstance(record.get("id"), str)
                    or not isinstance(record.get("turns"), list)
                ):
                    continue
                prefix: list[str] = []
                for raw_turn in record["turns"]:
                    if (
                        not isinstance(raw_turn, Mapping)
                        or type(raw_turn.get("turn_id")) is not int
                        or not isinstance(raw_turn.get("nlq"), str)
                    ):
                        break
                    normalized = _normalize_nlq(raw_turn["nlq"])
                    prefix.append(normalized)
                    sql_gt = raw_turn.get("sql_gt")
                    candidate = _ExpectedTurn(
                        dataset_id=record["id"],
                        turn_type=record["turn_type"],
                        turn_id=raw_turn["turn_id"],
                        sql_gt=sql_gt if isinstance(sql_gt, str) and sql_gt.strip() else None,
                        normalized_prefix=tuple(prefix),
                    )
                    index.setdefault(normalized, []).append(candidate)
        return index

    def _match_turn(self, nlq: str, history: Sequence[Mapping[str, Any]]) -> _ExpectedTurn | None:
        candidates = self._turns_by_query.get(_normalize_nlq(nlq), [])
        if not candidates:
            return None
        history_nlqs = tuple(
            _normalize_nlq(str(item["nlq"]))
            for item in history
            if isinstance(item.get("nlq"), str)
        )
        ranked: list[tuple[int, _ExpectedTurn]] = []
        for candidate in candidates:
            prior = candidate.normalized_prefix[:-1]
            if prior and (len(history_nlqs) < len(prior) or history_nlqs[-len(prior):] != prior):
                continue
            ranked.append((len(prior), candidate))
        if not ranked:
            return None
        best_score = max(score for score, _ in ranked)
        best = [candidate for score, candidate in ranked if score == best_score]
        if len(best) == 1 or len({candidate.sql_gt for candidate in best}) == 1:
            return best[0]
        return None

    def compare(
        self,
        *,
        nlq: str,
        history: Sequence[Mapping[str, Any]],
        runtime_columns: Sequence[Any],
        runtime_rows: Sequence[Any],
    ) -> dict[str, Any] | None:
        candidate = self._match_turn(nlq, history)
        if candidate is None:
            return None
        metric = "ST-EX" if candidate.turn_type == "single" else "MT-Turn-EX"
        identity = {
            "available": False,
            "metric": metric,
            "datasetId": candidate.dataset_id,
            "datasetTurn": candidate.turn_id,
            "rule": "Canonical result-set equality (the EX primitive used by ST-EX)",
        }
        if candidate.sql_gt is None:
            return {**identity, "reason": "This dataset turn has no executable expected result."}

        database_url = os.environ.get("TRUSTEDSQL_DATABASE_URL") or os.environ.get("DATABASE_URL")
        executor = DatabaseExecutor(database_url, statement_timeout_ms=5_000, max_rows=100)
        try:
            expected = executor.execute_read_only(candidate.sql_gt)
        finally:
            executor.close()
        if not expected.executed:
            return {**identity, "reason": "The expected dataset result could not be executed safely."}

        predicted_rows = list(rows_from_result(list(runtime_rows)))
        expected_rows = list(rows_from_result(expected.rows))
        predicted_set = set(predicted_rows)
        expected_set = set(expected_rows)
        canonical_predicted = sorted(predicted_set, key=repr)
        canonical_expected = sorted(expected_set, key=repr)
        matched = sorted(predicted_set & expected_set, key=repr)
        missing = sorted(expected_set - predicted_set, key=repr)
        unexpected = sorted(predicted_set - expected_set, key=repr)
        exact = ex_match(list(runtime_rows), expected.rows)

        runtime_names = [str(column) for column in runtime_columns]
        expected_names = [str(column) for column in (expected.columns or [])]
        runtime_by_key = {_column_key(column): column for column in runtime_names}
        expected_by_key = {_column_key(column): column for column in expected_names}
        shared_column_keys = runtime_by_key.keys() & expected_by_key.keys()

        return {
            **identity,
            "available": True,
            "exactMatch": exact,
            "score": 1.0 if exact else 0.0,
            "softF1": round(float(soft_f1(list(runtime_rows), expected.rows)), 4),
            "runtimeRowCount": len(runtime_rows),
            "expectedRowCount": len(expected.rows),
            "canonicalRuntimeRowCount": len(predicted_set),
            "canonicalExpectedRowCount": len(expected_set),
            "runtimeColumns": runtime_names,
            "expectedColumns": expected_names,
            "matchedColumns": [expected_by_key[key] for key in expected_by_key if key in shared_column_keys],
            "missingColumns": [expected_by_key[key] for key in expected_by_key if key not in runtime_by_key],
            "unexpectedColumns": [runtime_by_key[key] for key in runtime_by_key if key not in expected_by_key],
            "matchedRowCount": len(matched),
            "runtimePreviewRows": [_json_row(row) for row in canonical_predicted[:_MAX_DIFF_ROWS]],
            "expectedPreviewRows": [_json_row(row) for row in canonical_expected[:_MAX_DIFF_ROWS]],
            "matchedRows": [_json_row(row) for row in matched[:_MAX_DIFF_ROWS]],
            "missingRows": [_json_row(row) for row in missing[:_MAX_DIFF_ROWS]],
            "unexpectedRows": [_json_row(row) for row in unexpected[:_MAX_DIFF_ROWS]],
            "differencesTruncated": len(matched) > _MAX_DIFF_ROWS or len(missing) > _MAX_DIFF_ROWS or len(unexpected) > _MAX_DIFF_ROWS,
        }
