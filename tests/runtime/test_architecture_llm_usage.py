from architecture_baselines.llm.client import _normalize_openai_usage


def test_normalize_openai_usage_adds_trustedsql_token_keys() -> None:
    usage = _normalize_openai_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }
    )

    assert usage["prompt_token_count"] == 100
    assert usage["candidates_token_count"] == 50
    assert usage["total_token_count"] == 150
    assert usage["prompt_tokens"] == 100
    assert usage["completion_tokens"] == 50
    assert usage["total_tokens"] == 150


def test_normalize_openai_usage_preserves_existing_trustedsql_token_keys() -> None:
    usage = _normalize_openai_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "prompt_token_count": 90,
            "candidates_token_count": 40,
            "total_token_count": 130,
        }
    )

    assert usage["prompt_token_count"] == 90
    assert usage["candidates_token_count"] == 40
    assert usage["total_token_count"] == 130
