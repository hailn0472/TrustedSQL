from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any


def canonical_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        try:
            if stripped.isdigit() or (stripped.startswith("-") and stripped[1:].isdigit()):
                return int(stripped)
            return float(stripped)
        except ValueError:
            return stripped
    if isinstance(value, (list, tuple)):
        return tuple(canonical_value(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((str(key), canonical_value(item)) for key, item in value.items()))
    return value


def rows_from_result(result: Any) -> list[tuple[Any, ...]]:
    if result is None:
        return []
    if isinstance(result, dict):
        if "rows" in result:
            result = result["rows"]
        else:
            result = [result]
    if not isinstance(result, list):
        result = [result]
    rows: list[tuple[Any, ...]] = []
    for row in result:
        if isinstance(row, dict):
            values = [canonical_value(v) for v in row.values()]
        elif isinstance(row, (list, tuple)):
            values = [canonical_value(v) for v in row]
        else:
            values = [canonical_value(row)]
        rows.append(tuple(values))
    return rows


def ex_match(predicted: Any, expected: Any) -> bool:
    return set(rows_from_result(predicted)) == set(rows_from_result(expected))


def result_columns(result: Any) -> list[str]:
    """Return result column names in stable first-seen order."""
    if isinstance(result, dict) and "rows" in result:
        result = result["rows"]
    if isinstance(result, dict):
        result = [result]
    if not isinstance(result, list):
        return []
    columns: list[str] = []
    for row in result:
        if not isinstance(row, dict):
            continue
        for column in row:
            normalized = _normalize_column(column)
            if normalized and normalized not in columns:
                columns.append(normalized)
    return columns


def soft_f1(predicted: Any, expected: Any) -> float:
    predicted_rows = rows_from_result(predicted)
    ground_truth_rows = rows_from_result(expected)
    if not predicted_rows and not ground_truth_rows:
        return 1.0
    predicted_rows = list(dict.fromkeys(predicted_rows))
    ground_truth_rows = list(dict.fromkeys(ground_truth_rows))
    match_scores: list[float] = []
    pred_only_scores: list[float] = []
    truth_only_scores: list[float] = []
    for index, ground_truth_row in enumerate(ground_truth_rows):
        if index >= len(predicted_rows):
            match_scores.append(0.0)
            truth_only_scores.append(1.0)
            continue
        match, pred_only, truth_only = _row_match(predicted_rows[index], ground_truth_row)
        match_scores.append(match)
        pred_only_scores.append(pred_only)
        truth_only_scores.append(truth_only)
    for _ in range(len(predicted_rows) - len(ground_truth_rows)):
        match_scores.append(0.0)
        pred_only_scores.append(1.0)
        truth_only_scores.append(0.0)
    true_positive = sum(match_scores)
    false_positive = sum(pred_only_scores)
    false_negative = sum(truth_only_scores)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _row_match(predicted_row: tuple[Any, ...], ground_truth_row: tuple[Any, ...]) -> tuple[float, float, float]:
    total_columns = len(ground_truth_row)
    if total_columns == 0:
        return (1.0, 0.0, 0.0) if not predicted_row else (0.0, 1.0, 0.0)
    matches = sum(value in ground_truth_row for value in predicted_row)
    pred_only = sum(value not in ground_truth_row for value in predicted_row)
    truth_only = sum(value not in predicted_row for value in ground_truth_row)
    return (
        matches / total_columns,
        pred_only / total_columns,
        truth_only / total_columns,
    )


def _normalize_column(column: Any) -> str:
    return str(column).strip().strip('"').lower()

