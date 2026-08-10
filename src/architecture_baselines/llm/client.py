from __future__ import annotations

import json
import os
import random
import time
from urllib import error, request
from dataclasses import dataclass
from typing import Any, Protocol


class LLMClient(Protocol):
    def generate_text(self, system: str, prompt: str, *, response_json: bool = False) -> tuple[str, dict[str, Any]]:
        ...

    def generate_json(self, system: str, prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
        ...


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient errors where a retry may help."""
    from google.genai.errors import ClientError, ServerError

    if isinstance(exc, ServerError):
        return True
    if isinstance(exc, ClientError):
        return getattr(exc, "code", None) == 429
    return True


def _backoff(attempt: int, exc: BaseException | None, config: dict[str, Any]) -> float:
    """Compute sleep seconds with exponential backoff + jitter.

    Honours the ``Retry-After`` header in 429 responses when present.
    """
    from google.genai.errors import ClientError

    if isinstance(exc, ClientError) and getattr(exc, "code", None) == 429:
        response = getattr(exc, "response", None)
        if response is not None:
            retry_after = getattr(response, "headers", {}).get("Retry-After")
            if retry_after is not None:
                try:
                    return float(retry_after)
                except (ValueError, TypeError):
                    pass

    base = float(config.get("retry_backoff_base_s", 1.0))
    cap = float(config.get("retry_backoff_cap_s", 60.0))
    jitter = random.uniform(0, float(config.get("retry_jitter_s", 1.0)))
    return min(base * (2 ** attempt) + jitter, cap)


@dataclass
class VertexGeminiClient:
    config: dict[str, Any]

    def __post_init__(self) -> None:
        credentials = self.config.get("google_application_credentials")
        if credentials:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials)

    def _client(self) -> Any:
        from google import genai

        return genai.Client(
            vertexai=True,
            project=self.config.get("project_id"),
            location=self.config.get("location", "global"),
        )

    def generate_text(self, system: str, prompt: str, *, response_json: bool = False) -> tuple[str, dict[str, Any]]:
        from google.genai import types

        client = self._client()
        model = self.config.get("model", "gemini-2.5-flash-lite")
        max_retries = int(self.config.get("max_retries", 2))
        last_error: BaseException | None = None
        config_kwargs = {
            "system_instruction": system,
            "temperature": float(self.config.get("temperature", 0.0)),
            "top_p": float(self.config.get("top_p", 1.0)),
            "max_output_tokens": int(self.config.get("max_output_tokens", 2048)),
            "response_mime_type": "application/json" if response_json else "text/plain",
        }
        thinking_config = self.config.get("thinking_config")
        if isinstance(thinking_config, dict) and thinking_config:
            config_kwargs["thinking_config"] = thinking_config
        generation_config = types.GenerateContentConfig(**config_kwargs)
        for attempt in range(max_retries + 1):
            try:
                response = client.models.generate_content(model=model, contents=prompt, config=generation_config)
                usage = {}
                if getattr(response, "usage_metadata", None):
                    usage = response.usage_metadata.model_dump(exclude_none=True)
                return response.text or "", usage
            except Exception as exc:
                last_error = exc
                if attempt < max_retries and _is_retryable(exc):
                    delay = _backoff(attempt, exc, self.config)
                    time.sleep(delay)
                    continue
                if not _is_retryable(exc):
                    raise RuntimeError(f"Gemini call failed (non-retryable): {exc}") from exc
        raise RuntimeError(f"Gemini call failed after {max_retries + 1} attempts: {last_error}") from last_error

    def generate_json(self, system: str, prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
        max_json_retries = int(self.config.get("max_json_retries", 1))
        last_error: Exception | None = None
        for attempt in range(max_json_retries + 1):
            text, usage = self.generate_text(system, prompt, response_json=True)
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                if cleaned.lower().startswith("json"):
                    cleaned = cleaned[4:].strip()
            try:
                return json.loads(cleaned), usage
            except json.JSONDecodeError as exc:
                last_error = exc
                if attempt < max_json_retries:
                    continue
        raise ValueError(f"LLM did not return valid JSON after {max_json_retries + 1} attempts: {text[:500]}") from last_error


@dataclass
class OpenAICompatibleClient:
    config: dict[str, Any]

    def __post_init__(self) -> None:
        self.model = str(_env_value(self.config.get("model"), self.config.get("model_env")) or "")
        self.api_url = str(_env_value(self.config.get("api_url"), self.config.get("api_url_env")) or "")
        self.api_key = str(_api_key_for_model(self.config, self.model) or "")

    def generate_text(self, system: str, prompt: str, *, response_json: bool = False) -> tuple[str, dict[str, Any]]:
        if not self.model or not self.api_url or not self.api_key:
            raise RuntimeError("OpenAI-compatible LLM is missing model, API URL, or API key")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": float(self.config.get("temperature", 0.0)),
            "max_tokens": int(self.config.get("max_output_tokens", 2048)),
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
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=float(self.config.get("timeout_s", 240))) as response:  # noqa: S310
                data = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI-compatible API HTTP {exc.code}: {detail[:1000]}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"OpenAI-compatible API connection error: {exc}") from exc
        choices = data.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        usage = _normalize_openai_usage(data.get("usage") or {})
        usage.setdefault("model", self.model)
        usage.setdefault("provider", self.config.get("provider", "openai_compatible"))
        return str(message.get("content") or ""), usage

    def generate_json(self, system: str, prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
        text, usage = self.generate_text(system, prompt, response_json=True)
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
        return json.loads(cleaned), usage


def create_llm_client(config: dict[str, Any]) -> LLMClient:
    provider = str(_env_value(config.get("provider"), config.get("provider_env")) or "vertex").lower()
    if provider in {"openai_compatible", "fci", "fpt_cloud"}:
        return OpenAICompatibleClient(config)
    return VertexGeminiClient(config)


def _normalize_openai_usage(raw_usage: Any) -> dict[str, Any]:
    usage = dict(raw_usage or {})
    if "prompt_token_count" not in usage and "prompt_tokens" in usage:
        usage["prompt_token_count"] = usage["prompt_tokens"]
    if "candidates_token_count" not in usage and "completion_tokens" in usage:
        usage["candidates_token_count"] = usage["completion_tokens"]
    if "total_token_count" not in usage and "total_tokens" in usage:
        usage["total_token_count"] = usage["total_tokens"]
    return usage


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

