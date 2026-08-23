"""Bounded, JSON-safe primitives for browser-facing TrustedSQL payloads."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

MAX_STRING_LENGTH = 512
MAX_LIST_ITEMS = 100
MAX_DICT_ENTRIES = 100
MAX_DEPTH = 6
MAX_RESULT_ROWS = 100
MAX_ID_LENGTH = 128

_SECRET_PATTERNS = (
    (re.compile(r"(?i)(https?://)([^/\s:@]+):([^@\s]+)@"), r"\1<redacted>@"),
    (re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password)\s*[=:]\s*)([^\s,;&]+)"), r"\1<redacted>"),
    (re.compile(r"(?i)(\b(?:authorization\s*:\s*bearer|bearer)\s+)([^\s,;&]+)"), r"\1<redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]+\b"), "<redacted>"),
)


def _bounded_string(value: str) -> str:
    return value if len(value) <= MAX_STRING_LENGTH else value[:MAX_STRING_LENGTH]


def sanitize_json_value(value: Any, *, _depth: int = 0) -> Any:
    """Convert a value to bounded JSON-safe data without following deep graphs."""

    if _depth >= MAX_DEPTH:
        return "[depth limit]"
    if value is None or isinstance(value, (bool, int, str)):
        return _bounded_string(value) if isinstance(value, str) else value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (datetime, date)):
        return _bounded_string(value.isoformat())
    if isinstance(value, bytes):
        return _bounded_string(value.decode("utf-8", errors="replace"))
    if isinstance(value, Mapping):
        bounded: dict[str, Any] = {}
        for index, (key, nested) in enumerate(value.items()):
            if index >= MAX_DICT_ENTRIES:
                break
            safe_key = _bounded_string(str(key))
            bounded[safe_key] = sanitize_json_value(nested, _depth=_depth + 1)
        return bounded
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            sanitize_json_value(item, _depth=_depth + 1)
            for item in list(value)[:MAX_LIST_ITEMS]
        ]
    return _bounded_string(str(value))


def redact_error(value: Any) -> str:
    """Return a bounded error string with common URL/API credential forms removed."""

    safe = sanitize_json_value(value)
    if not isinstance(safe, str):
        safe = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
    for pattern, replacement in _SECRET_PATTERNS:
        safe = pattern.sub(replacement, safe)
    return _bounded_string(safe)


def _summary_scalar(value: Any, kind: str) -> Any:
    if value is None:
        return None
    if kind == "scalar":
        if isinstance(value, (str, bool, int)):
            return _bounded_string(value) if isinstance(value, str) else value
        if isinstance(value, float) and math.isfinite(value):
            return value
        return _SUMMARY_OMIT
    if kind == "string":
        return _bounded_string(value) if isinstance(value, str) else _SUMMARY_OMIT
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return _SUMMARY_OMIT
        if isinstance(value, float) and not math.isfinite(value):
            return _SUMMARY_OMIT
        return value
    return _SUMMARY_OMIT


def _summary_value(value: Any, kind: str) -> Any:
    if kind == "string_list":
        if not isinstance(value, (list, tuple)):
            return _SUMMARY_OMIT
        return [
            _bounded_string(item)
            for item in list(value)[:MAX_LIST_ITEMS]
            if isinstance(item, str)
        ]
    return _summary_scalar(value, kind)


_SUMMARY_OMIT = object()


def safe_summary(
    value: Any,
    allowed_keys: set[str],
    schemas: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Pick allowlisted scalar/list fields without preserving nested payloads.

    ``schemas`` is intentionally explicit: a summary field is either a bounded
    string, finite number, or list of bounded strings. Arbitrary nested maps
    and lists are omitted rather than recursively copied from runtime output.
    """

    if not isinstance(value, Mapping):
        return {}
    field_schemas = schemas or {key: "scalar" for key in allowed_keys}
    result: dict[str, Any] = {}
    for key, nested in value.items():
        if key not in allowed_keys or key not in field_schemas:
            continue
        safe_value = _summary_value(nested, field_schemas[key])
        if safe_value is not _SUMMARY_OMIT:
            result[str(key)] = safe_value
    return result


__all__ = [
    "MAX_DEPTH",
    "MAX_DICT_ENTRIES",
    "MAX_ID_LENGTH",
    "MAX_LIST_ITEMS",
    "MAX_RESULT_ROWS",
    "MAX_STRING_LENGTH",
    "redact_error",
    "safe_summary",
    "sanitize_json_value",
]
