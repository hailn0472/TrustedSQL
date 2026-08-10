import pytest

from trustedsql.providers.client import extract_json_object
from trustedsql.providers.output_schemas import PromptIntegrityOutput


def test_extract_json_object_accepts_markdown_fence() -> None:
    data = extract_json_object('```json\n{"decision":"ALLOW","reason":"normal request"}\n```')
    parsed = PromptIntegrityOutput.model_validate(data)
    assert parsed.decision == "ALLOW"


def test_extract_json_object_rejects_truncated_json() -> None:
    with pytest.raises(ValueError):
        extract_json_object('```json\n{"decision":"ALLOW","reason":"unterminated"\n```')


