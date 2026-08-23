import json
from datetime import datetime

from demo.backend.app.sanitization import (
    MAX_DEPTH,
    MAX_DICT_ENTRIES,
    MAX_LIST_ITEMS,
    MAX_STRING_LENGTH,
    redact_error,
    sanitize_json_value,
)


def test_sanitizer_bounds_strings_lists_maps_and_depth():
    value = {
        f"key-{index}": ["x" * (MAX_STRING_LENGTH + 20) for _ in range(MAX_LIST_ITEMS + 5)]
        for index in range(MAX_DICT_ENTRIES + 5)
    }

    sanitized = sanitize_json_value(value)

    assert len(sanitized) <= MAX_DICT_ENTRIES
    assert all(len(items) <= MAX_LIST_ITEMS for items in sanitized.values())
    assert all(len(item) <= MAX_STRING_LENGTH for items in sanitized.values() for item in items)

    nested = current = {}
    for _ in range(MAX_DEPTH + 3):
        current["next"] = {}
        current = current["next"]
    bounded = sanitize_json_value(nested)
    cursor = bounded
    for _ in range(MAX_DEPTH + 1):
        if not isinstance(cursor, dict) or "next" not in cursor:
            break
        cursor = cursor["next"]
    assert cursor == "[depth limit]"


def test_sanitizer_converts_non_json_values_to_bounded_strings():
    sanitized = sanitize_json_value({"when": datetime(2026, 8, 23, 15, 25, 49), "bytes": b"abc"})

    assert sanitized["when"].startswith("2026-08-23T15:25:49")
    assert sanitized["bytes"] == "abc"
    json.dumps(sanitized)


def test_error_redaction_removes_common_provider_secrets_and_bounds_message():
    message = (
        "Request failed: https://alice:super-secret@example.test/v1?api_key=sk-live-123 "
        "Authorization: Bearer very-secret-token " + "!" * (MAX_STRING_LENGTH + 50)
    )

    safe = redact_error(message)

    assert "super-secret" not in safe
    assert "sk-live-123" not in safe
    assert "very-secret-token" not in safe
    assert len(safe) <= MAX_STRING_LENGTH
    json.dumps(safe)
