from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib import error, request


@dataclass
class LlmResponse:
    text: str
    usage: dict[str, Any]


class GeminiClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.model = _env_value(config.get("model"), config.get("model_env"))
        self.enabled = bool(self.model and config.get("enabled", True))
        self._client: Any = None
        self._types: Any = None

    def generate_text(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        max_output_tokens: int = 1024,
        response_json: bool = False,
    ) -> LlmResponse:
        if not self.enabled:
            raise RuntimeError("LLM is disabled or model is not configured")
        config_kwargs = _build_config_kwargs(
            self.config,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_json=response_json,
        )
        config = self._load_types().GenerateContentConfig(**config_kwargs)
        response = self._load().models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )
        return LlmResponse(
            text=getattr(response, "text", "") or "",
            usage=_extract_usage(response, self.model),
        )

    def generate_json(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        max_output_tokens: int = 1024,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        response = self.generate_text(
            prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_json=True,
        )
        return extract_json_object(response.text), response.usage

    def _load(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google import genai
            from google.genai import types
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"google-genai is not available: {exc}") from exc
        project = self.config.get("project_id")
        location = self.config.get("location", "us-central1")
        timeout_ms = int(self.config.get("timeout_ms", 120_000))
        self._types = types
        http_opts = types.HttpOptions(timeout=timeout_ms)
        self._client = (
            genai.Client(vertexai=True, project=project, location=location, http_options=http_opts)
            if project
            else genai.Client(http_options=http_opts)
        )
        return self._client

    def _load_types(self) -> Any:
        self._load()
        return self._types


class OpenAICompatibleClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.model = str(_env_value(config.get("model"), config.get("model_env")) or "")
        self.api_url = str(_env_value(config.get("api_url"), config.get("api_url_env")) or "")
        self.api_key = str(_api_key_for_model(config, self.model) or "")
        self.enabled = bool(self.model and self.api_url and self.api_key and config.get("enabled", True))

    def generate_text(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        max_output_tokens: int = 1024,
        response_json: bool = False,
    ) -> LlmResponse:
        return self._chat(
            [{"role": "user", "content": prompt}],
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_json=response_json,
        )

    def generate_json(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        max_output_tokens: int = 1024,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        response = self.generate_text(
            prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_json=True,
        )
        return extract_json_object(response.text), response.usage

    def _chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_output_tokens: int,
        response_json: bool,
    ) -> LlmResponse:
        if not self.enabled:
            raise RuntimeError("OpenAI-compatible LLM is disabled or missing model/api_url/api_key")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }
        if response_json or self.config.get("response_format_json"):
            payload["response_format"] = {"type": "json_object"}
        req = request.Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "TrustedSQL/0.1 urllib-openai-compatible",
            },
            method="POST",
        )
        timeout_s = float(self.config.get("timeout_s") or self.config.get("timeout") or 120)
        try:
            with request.urlopen(req, timeout=timeout_s) as response:  # noqa: S310
                data = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI-compatible API HTTP {exc.code}: {detail[:1000]}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"OpenAI-compatible API connection error: {exc}") from exc
        choices = data.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        return LlmResponse(
            text=message.get("content") or "",
            usage=_extract_openai_usage(
                data,
                self.model,
                self.config.get("provider", "openai_compatible"),
            ),
        )


def create_llm_client(config: dict[str, Any]) -> GeminiClient | OpenAICompatibleClient:
    provider = str(_env_value(config.get("provider"), config.get("provider_env")) or "vertex").lower()
    if provider in {"openai_compatible", "fci", "fpt_cloud"}:
        return OpenAICompatibleClient(config)
    return GeminiClient(config)


def _env_value(value: Any, env_name: Any) -> Any:
    if env_name and os.environ.get(str(env_name)):
        return os.environ[str(env_name)]
    return value


def _api_key_for_model(config: dict[str, Any], model: str) -> Any:
    env_by_model = config.get("api_key_env_by_model")
    if isinstance(env_by_model, dict):
        env_name = env_by_model.get(model)
        if env_name and os.environ.get(str(env_name)):
            return os.environ[str(env_name)]
    return _env_value(config.get("api_key"), config.get("api_key_env"))


def _build_config_kwargs(
    source: dict[str, Any],
    *,
    temperature: float,
    max_output_tokens: int,
    response_json: bool,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    if "top_p" in source:
        kwargs["top_p"] = float(source.get("top_p", 1.0))
    thinking_config = source.get("thinking_config")
    if isinstance(thinking_config, dict) and thinking_config:
        kwargs["thinking_config"] = thinking_config
    if not kwargs.get("thinking_config"):
        kwargs["thinking_config"] = {"thinking_budget": 0, "include_thoughts": False}
    if response_json:
        kwargs["response_mime_type"] = "application/json"
    return kwargs


def _extract_usage(response: Any, model: str) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    usage_meta = getattr(response, "usage_metadata", None)
    if usage_meta:
        if hasattr(usage_meta, "model_dump"):
            usage = usage_meta.model_dump(exclude_none=True)
        else:
            usage = {
                "prompt_token_count": getattr(usage_meta, "prompt_token_count", None),
                "candidates_token_count": getattr(usage_meta, "candidates_token_count", None),
                "total_token_count": getattr(usage_meta, "total_token_count", None),
            }
    candidates = getattr(response, "candidates", None) or []
    finish_reasons = [
        str(getattr(candidate, "finish_reason", ""))
        for candidate in candidates
        if getattr(candidate, "finish_reason", None)
    ]
    if finish_reasons:
        usage["finish_reasons"] = finish_reasons
    usage["model"] = model
    return usage


def _extract_openai_usage(data: dict[str, Any], model: str, provider: Any) -> dict[str, Any]:
    raw = data.get("usage") or {}
    return {
        "prompt_token_count": raw.get("prompt_tokens") or raw.get("prompt_token_count") or 0,
        "candidates_token_count": raw.get("completion_tokens") or raw.get("candidates_token_count") or 0,
        "total_token_count": raw.get("total_tokens") or raw.get("total_token_count") or 0,
        "model": model,
        "provider": provider,
    }


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = _strip_code_fence(text.strip())
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError(f"LLM did not return a valid JSON object: {cleaned[:500]}")


def _strip_code_fence(text: str) -> str:
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text
