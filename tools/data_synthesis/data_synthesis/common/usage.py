from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

USD_PER_MILLION = 1_000_000


GEMINI_25_FLASH_LITE_PAID_STANDARD = {
    "pricing_source": "google_gemini_api_pricing_paid_standard",
    "tier": "paid_standard",
    "model": "gemini-2.5-flash-lite",
    "currency": "USD",
    "unit": "per_1m_tokens",
    "pricing_url": "https://ai.google.dev/gemini-api/docs/pricing",
    "usage_metadata_url": "https://googleapis.github.io/js-genai/release_docs/interfaces/types.UsageMetadata.html",
    "input_text_image_video_per_1m": 0.10,
    "output_including_thinking_per_1m": 0.40,
    "cached_input_text_image_video_per_1m": 0.01,
    "cache_storage_per_1m_tokens_per_hour": 1.00,
    "google_search_grounding_free_rpd": 1500,
    "google_search_grounding_after_free_per_1000_prompts": 35.00,
    "google_maps_grounding_free_rpd": 10000,
    "google_maps_grounding_after_free_per_1000_prompts": 25.00,
}

DEFAULT_PRICING = GEMINI_25_FLASH_LITE_PAID_STANDARD


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_usage_metadata(response: Any) -> Dict[str, Any]:
    usage = _get_attr(response, "usage_metadata") or _get_attr(response, "usageMetadata")
    if usage is None:
        return {
            "usage_available": False,
            "prompt_token_count": None,
            "cached_content_token_count": None,
            "response_token_count": None,
            "candidates_token_count": None,
            "thoughts_token_count": None,
            "tool_use_prompt_token_count": None,
            "total_token_count": None,
            "accounted_token_count": None,
            "unaccounted_token_count": None,
            "billable_input_tokens": None,
            "billable_cached_input_tokens": None,
            "billable_output_tokens": None,
        }

    prompt_tokens = _as_int(_get_attr(usage, "prompt_token_count", "promptTokenCount"))
    cached_tokens = _as_int(_get_attr(usage, "cached_content_token_count", "cachedContentTokenCount"))
    response_tokens = _as_int(
        _get_attr(
            usage,
            "response_token_count",
            "responseTokenCount",
            "candidates_token_count",
            "candidatesTokenCount",
        )
    )
    thought_tokens = _as_int(_get_attr(usage, "thoughts_token_count", "thoughtsTokenCount"))
    tool_use_prompt_tokens = _as_int(
        _get_attr(usage, "tool_use_prompt_token_count", "toolUsePromptTokenCount")
    )
    total_tokens = _as_int(_get_attr(usage, "total_token_count", "totalTokenCount"))

    billable_cached_input = min(cached_tokens or 0, prompt_tokens or 0) if prompt_tokens is not None else cached_tokens
    billable_input = None
    if prompt_tokens is not None:
        billable_input = max(prompt_tokens - (billable_cached_input or 0), 0) + (tool_use_prompt_tokens or 0)

    billable_output_tokens = None
    if response_tokens is not None or thought_tokens is not None:
        billable_output_tokens = (response_tokens or 0) + (thought_tokens or 0)

    accounted_tokens = None
    unaccounted_tokens = None
    if prompt_tokens is not None or response_tokens is not None or tool_use_prompt_tokens is not None:
        accounted_tokens = (prompt_tokens or 0) + (response_tokens or 0) + (tool_use_prompt_tokens or 0)
        if thoughts_tokens_in_total := _thoughts_in_total(total_tokens, accounted_tokens, thought_tokens):
            accounted_tokens += thoughts_tokens_in_total
        if total_tokens is not None:
            unaccounted_tokens = total_tokens - accounted_tokens

    return {
        "usage_available": True,
        "prompt_token_count": prompt_tokens,
        "cached_content_token_count": cached_tokens,
        "response_token_count": response_tokens,
        "candidates_token_count": response_tokens,
        "thoughts_token_count": thought_tokens,
        "tool_use_prompt_token_count": tool_use_prompt_tokens,
        "total_token_count": total_tokens,
        "accounted_token_count": accounted_tokens,
        "unaccounted_token_count": unaccounted_tokens,
        "prompt_tokens_details": _to_plain_dict(
            _get_attr(usage, "prompt_tokens_details", "promptTokensDetails")
        ),
        "cache_tokens_details": _to_plain_dict(
            _get_attr(usage, "cache_tokens_details", "cacheTokensDetails")
        ),
        "response_tokens_details": _to_plain_dict(
            _get_attr(
                usage,
                "response_tokens_details",
                "responseTokensDetails",
                "candidates_tokens_details",
                "candidatesTokensDetails",
            )
        ),
        "tool_use_prompt_tokens_details": _to_plain_dict(
            _get_attr(usage, "tool_use_prompt_tokens_details", "toolUsePromptTokensDetails")
        ),
        "traffic_type": str(_get_attr(usage, "traffic_type", "trafficType") or "") or None,
        "billable_input_tokens": billable_input,
        "billable_cached_input_tokens": billable_cached_input,
        "billable_output_tokens": billable_output_tokens,
        "raw_usage_metadata": _to_plain_dict(usage),
    }


def estimate_cost(
    usage: Dict[str, Any],
    *,
    model: str,
    pricing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pricing = pricing or DEFAULT_PRICING
    billable_input = usage.get("billable_input_tokens")
    cached_input = usage.get("billable_cached_input_tokens")
    billable_output = usage.get("billable_output_tokens")
    pricing_tier = _pricing_tier_for_prompt(usage, pricing)
    input_rate = _tiered_rate(
        pricing,
        pricing_tier,
        le_key="input_text_image_video_per_1m_le_200k",
        gt_key="input_text_image_video_per_1m_gt_200k",
        fallback_key="input_text_image_video_per_1m",
    )
    cached_rate = _tiered_rate(
        pricing,
        pricing_tier,
        le_key="cached_input_text_image_video_per_1m_le_200k",
        gt_key="cached_input_text_image_video_per_1m_gt_200k",
        fallback_key="cached_input_text_image_video_per_1m",
    )
    output_rate = _tiered_rate(
        pricing,
        pricing_tier,
        le_key="output_including_thinking_per_1m_le_200k",
        gt_key="output_including_thinking_per_1m_gt_200k",
        fallback_key="output_including_thinking_per_1m",
    )

    input_cost = _token_cost(billable_input, input_rate)
    cached_cost = _token_cost(cached_input, cached_rate)
    output_cost = _token_cost(billable_output, output_rate)
    total_cost = _none_if_any_none(input_cost, cached_cost, output_cost)
    if total_cost is not None:
        total_cost = input_cost + cached_cost + output_cost

    pricing_model = pricing["model"]
    return {
        "pricing_model": pricing_model,
        "actual_model": model,
        "pricing_warning": None
        if model == pricing_model
        else f"Cost estimated with {pricing_model} rates from the user-provided table.",
        "pricing_tier": pricing_tier,
        "prompt_threshold_tokens": pricing.get("prompt_threshold_tokens"),
        "input_rate_per_1m": input_rate,
        "cached_input_rate_per_1m": cached_rate,
        "output_rate_per_1m": output_rate,
        "input_cost_usd": input_cost,
        "cached_input_cost_usd": cached_cost,
        "output_cost_usd": output_cost,
        "grounding_cost_usd": 0.0,
        "cache_storage_cost_usd": 0.0,
        "total_cost_usd": total_cost,
    }


def summarize_usage(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    records = list(records)
    usage_rows = [record.get("usage", {}) for record in records]
    cost_rows = [record.get("cost", {}) for record in records]
    timing_rows = [record.get("timing", {}) for record in records]
    return {
        "request_count": len(records),
        "usage_available_count": sum(1 for usage in usage_rows if usage.get("usage_available")),
        "prompt_token_count": _sum_optional(usage.get("prompt_token_count") for usage in usage_rows),
        "cached_content_token_count": _sum_optional(usage.get("cached_content_token_count") for usage in usage_rows),
        "response_token_count": _sum_optional(usage.get("response_token_count") for usage in usage_rows),
        "candidates_token_count": _sum_optional(usage.get("candidates_token_count") for usage in usage_rows),
        "thoughts_token_count": _sum_optional(usage.get("thoughts_token_count") for usage in usage_rows),
        "tool_use_prompt_token_count": _sum_optional(usage.get("tool_use_prompt_token_count") for usage in usage_rows),
        "total_token_count": _sum_optional(usage.get("total_token_count") for usage in usage_rows),
        "accounted_token_count": _sum_optional(usage.get("accounted_token_count") for usage in usage_rows),
        "unaccounted_token_count": _sum_optional(usage.get("unaccounted_token_count") for usage in usage_rows),
        "billable_input_tokens": _sum_optional(usage.get("billable_input_tokens") for usage in usage_rows),
        "billable_cached_input_tokens": _sum_optional(usage.get("billable_cached_input_tokens") for usage in usage_rows),
        "billable_output_tokens": _sum_optional(usage.get("billable_output_tokens") for usage in usage_rows),
        "input_cost_usd": _sum_optional(cost.get("input_cost_usd") for cost in cost_rows),
        "cached_input_cost_usd": _sum_optional(cost.get("cached_input_cost_usd") for cost in cost_rows),
        "output_cost_usd": _sum_optional(cost.get("output_cost_usd") for cost in cost_rows),
        "grounding_cost_usd": _sum_optional(cost.get("grounding_cost_usd") for cost in cost_rows),
        "cache_storage_cost_usd": _sum_optional(cost.get("cache_storage_cost_usd") for cost in cost_rows),
        "total_cost_usd": _sum_optional(cost.get("total_cost_usd") for cost in cost_rows),
        "total_latency_seconds": _sum_optional(timing.get("latency_seconds") for timing in timing_rows),
        "by_phase": {
            phase: _summarize_usage_no_phase([record for record in records if record.get("phase") == phase])
            for phase in sorted({str(record.get("phase")) for record in records})
        },
    }


def _summarize_usage_no_phase(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    records = list(records)
    usage_rows = [record.get("usage", {}) for record in records]
    cost_rows = [record.get("cost", {}) for record in records]
    timing_rows = [record.get("timing", {}) for record in records]
    return {
        "request_count": len(records),
        "usage_available_count": sum(1 for usage in usage_rows if usage.get("usage_available")),
        "prompt_token_count": _sum_optional(usage.get("prompt_token_count") for usage in usage_rows),
        "cached_content_token_count": _sum_optional(usage.get("cached_content_token_count") for usage in usage_rows),
        "response_token_count": _sum_optional(usage.get("response_token_count") for usage in usage_rows),
        "candidates_token_count": _sum_optional(usage.get("candidates_token_count") for usage in usage_rows),
        "thoughts_token_count": _sum_optional(usage.get("thoughts_token_count") for usage in usage_rows),
        "tool_use_prompt_token_count": _sum_optional(usage.get("tool_use_prompt_token_count") for usage in usage_rows),
        "total_token_count": _sum_optional(usage.get("total_token_count") for usage in usage_rows),
        "accounted_token_count": _sum_optional(usage.get("accounted_token_count") for usage in usage_rows),
        "unaccounted_token_count": _sum_optional(usage.get("unaccounted_token_count") for usage in usage_rows),
        "billable_input_tokens": _sum_optional(usage.get("billable_input_tokens") for usage in usage_rows),
        "billable_cached_input_tokens": _sum_optional(usage.get("billable_cached_input_tokens") for usage in usage_rows),
        "billable_output_tokens": _sum_optional(usage.get("billable_output_tokens") for usage in usage_rows),
        "input_cost_usd": _sum_optional(cost.get("input_cost_usd") for cost in cost_rows),
        "cached_input_cost_usd": _sum_optional(cost.get("cached_input_cost_usd") for cost in cost_rows),
        "output_cost_usd": _sum_optional(cost.get("output_cost_usd") for cost in cost_rows),
        "total_cost_usd": _sum_optional(cost.get("total_cost_usd") for cost in cost_rows),
        "total_latency_seconds": _sum_optional(timing.get("latency_seconds") for timing in timing_rows),
    }


def write_usage_logs(
    *,
    json_path: str,
    csv_path: str,
    records: List[Dict[str, Any]],
    pricing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pricing = pricing or DEFAULT_PRICING
    payload = {
        "pricing": pricing,
        "summary": summarize_usage(records),
        "records": records,
    }
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        import json

        json.dump(payload, f, ensure_ascii=False, indent=2)

    flat_rows = [_flatten_usage_record(record) for record in records]
    fieldnames = sorted({key for row in flat_rows for key in row})
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flat_rows)
    return payload


def _flatten_usage_record(record: Dict[str, Any]) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "phase": record.get("phase"),
        "index": record.get("index"),
        "id": record.get("id"),
        "model": record.get("model"),
        "prompt_chars": record.get("prompt_chars"),
        "output_chars": record.get("output_chars"),
    }
    for section in ("prompt_build", "timing", "usage", "cost"):
        data = record.get(section) or {}
        for key, value in data.items():
            if key == "raw_usage_metadata":
                continue
            row[f"{section}.{key}"] = value
    return row


def _get_attr(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _thoughts_in_total(
    total_tokens: Optional[int],
    accounted_without_thoughts: int,
    thought_tokens: Optional[int],
) -> int:
    """Include thoughts in reconciliation only when the API total demonstrably contains them."""
    if total_tokens is None or thought_tokens is None:
        return 0
    return thought_tokens if total_tokens >= accounted_without_thoughts + thought_tokens else 0


def _to_plain_dict(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [_to_plain_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {str(key): _to_plain_dict(value) for key, value in obj.items()}
    if hasattr(obj, "model_dump"):
        return _to_plain_dict(obj.model_dump())
    if hasattr(obj, "to_dict"):
        return _to_plain_dict(obj.to_dict())
    if hasattr(obj, "__dict__"):
        return {
            key: _to_plain_dict(value)
            for key, value in vars(obj).items()
            if not key.startswith("_")
        }
    return str(obj)


def _token_cost(tokens: Optional[int], rate_per_1m: float) -> Optional[float]:
    if tokens is None:
        return None
    return (tokens / USD_PER_MILLION) * rate_per_1m


def _pricing_tier_for_prompt(usage: Dict[str, Any], pricing: Dict[str, Any]) -> str:
    threshold = pricing.get("prompt_threshold_tokens")
    prompt_tokens = usage.get("prompt_token_count")
    if threshold is None or prompt_tokens is None:
        return "flat"
    return "gt_200k" if int(prompt_tokens) > int(threshold) else "le_200k"


def _tiered_rate(
    pricing: Dict[str, Any],
    pricing_tier: str,
    *,
    le_key: str,
    gt_key: str,
    fallback_key: str,
) -> float:
    if pricing_tier == "gt_200k":
        return float(pricing[gt_key])
    if pricing_tier == "le_200k":
        return float(pricing[le_key])
    if fallback_key in pricing:
        return float(pricing[fallback_key])
    return float(pricing[le_key])


def _none_if_any_none(*values: Optional[float]) -> Optional[float]:
    return None if any(value is None for value in values) else 0.0


def _sum_optional(values: Iterable[Any]) -> Optional[float]:
    total = 0.0
    seen = False
    for value in values:
        if value is None:
            continue
        total += float(value)
        seen = True
    return total if seen else None
