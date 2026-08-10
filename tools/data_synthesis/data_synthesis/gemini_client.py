"""Gemini inference adapter used by DataTrain generation workflows.

Setup:
    pip install google-genai

Usage:
    Set environment variable GEMINI_API_KEY before running any synthesis script.
    Example: export GEMINI_API_KEY="your-api-key-here"
"""

import os
import time
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, TypeVar

from google import genai
from google.genai import types

from data_synthesis.common.usage import extract_usage_metadata, estimate_cost


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "gemini-2.5-flash-lite"
MAX_OUTPUT_TOKENS = 8192
TEMPERATURE = 0.0
TOP_P = 0.95

# Conservative rate-limit guard for Vertex/Gemini paid-tier runs.
# Adjust this delay if your Vertex quota allows higher throughput.
REQUEST_DELAY_SECONDS = 1.0
_CLIENT_LOCK = threading.Lock()
_THREAD_LOCAL = threading.local()
_ProgressItem = TypeVar("_ProgressItem")


def _progress(iterable: Iterable[_ProgressItem], **kwargs: Any) -> Iterable[_ProgressItem]:
    """Wrap an iterable with tqdm when the optional dependency is installed."""

    try:
        from tqdm import tqdm
    except ImportError:
        return iterable
    return tqdm(iterable, **kwargs)


def _get_client() -> genai.Client:
    """Create and return a Gemini client using Vertex AI."""
    try:
        from dotenv import load_dotenv
        env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
        load_dotenv(dotenv_path=env_path)
    except ImportError:
        pass

    project_id = os.environ.get("VERTEX_PROJECT_ID")
    location = os.environ.get("VERTEX_REGION", "us-central1")
    
    if not project_id:
        raise EnvironmentError(
            "VERTEX_PROJECT_ID environment variable is not set.\n"
            "Please add it to your .env file."
        )

    thread_cache = getattr(_THREAD_LOCAL, "client_cache", None)
    if (
        thread_cache is not None
        and thread_cache["project_id"] == project_id
        and thread_cache["location"] == location
    ):
        return thread_cache["client"]

    # Use one Vertex client per worker thread to avoid sharing a mutable
    # transport object across concurrent requests.
    with _CLIENT_LOCK:
        client = genai.Client(
            vertexai=True,
            project=project_id,
            location=location,
        )
    _THREAD_LOCAL.client_cache = {
        "client": client,
        "project_id": project_id,
        "location": location,
    }
    return client


def generate_single(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = TEMPERATURE,
    top_p: float = TOP_P,
    max_tokens: int = MAX_OUTPUT_TOKENS,
    retries: int = 3,
) -> str:
    """Send a single prompt to Gemini and return the text response."""
    return generate_single_with_usage(
        prompt,
        model=model,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        retries=retries,
    )["output"]


def generate_single_with_usage(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = TEMPERATURE,
    top_p: float = TOP_P,
    max_tokens: int = MAX_OUTPUT_TOKENS,
    retries: int = 3,
    response_schema: Optional[Dict[str, Any]] = None,
) -> Dict:
    """Send a single prompt to Gemini and return text plus timing, token, and cost metadata."""
    client = _get_client()
    config_kwargs: Dict[str, Any] = dict(
        temperature=temperature,
        top_p=top_p,
        max_output_tokens=max_tokens,
    )
    if response_schema is not None:
        config_kwargs.update(
            response_mime_type="application/json",
            response_json_schema=response_schema,
        )
    config = types.GenerateContentConfig(**config_kwargs)

    started_at = _utc_now_iso()
    start_perf = time.perf_counter()
    attempts = []
    for attempt in range(1, retries + 1):
        attempt_started_at = _utc_now_iso()
        attempt_start_perf = time.perf_counter()
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
            attempt_latency = time.perf_counter() - attempt_start_perf
            output = response.text or ""
            usage = extract_usage_metadata(response)
            cost = estimate_cost(usage, model=model)
            attempts.append(
                {
                    "attempt": attempt,
                    "started_at_utc": attempt_started_at,
                    "finished_at_utc": _utc_now_iso(),
                    "latency_seconds": attempt_latency,
                    "ok": True,
                    "error": None,
                }
            )
            finished_at = _utc_now_iso()
            return {
                "input": prompt,
                "output": output,
                "model": model,
                "timing": {
                    "started_at_utc": started_at,
                    "finished_at_utc": finished_at,
                    "latency_seconds": time.perf_counter() - start_perf,
                    "attempt_count": attempt,
                },
                "usage": usage,
                "cost": cost,
                "attempts": attempts,
            }
        except Exception as e:
            attempt_latency = time.perf_counter() - attempt_start_perf
            attempts.append(
                {
                    "attempt": attempt,
                    "started_at_utc": attempt_started_at,
                    "finished_at_utc": _utc_now_iso(),
                    "latency_seconds": attempt_latency,
                    "ok": False,
                    "error": str(e),
                }
            )
            print(f"  [Gemini] Attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                wait = 2 ** attempt
                print(f"  [Gemini] Retrying in {wait}s ...")
                time.sleep(wait)
            else:
                print(f"  [Gemini] All retries exhausted. Returning empty string.")
                usage = {
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
                return {
                    "input": prompt,
                    "output": "",
                    "model": model,
                    "timing": {
                        "started_at_utc": started_at,
                        "finished_at_utc": _utc_now_iso(),
                        "latency_seconds": time.perf_counter() - start_perf,
                        "attempt_count": attempt,
                    },
                    "usage": usage,
                    "cost": estimate_cost(usage, model=model),
                    "attempts": attempts,
                }


def generate_batch(
    prompts: List[str],
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = TEMPERATURE,
    top_p: float = TOP_P,
    max_tokens: int = MAX_OUTPUT_TOKENS,
    delay: float = REQUEST_DELAY_SECONDS,
    max_workers: int = 1,
    save_callback=None,
    save_every: int = 50,
    response_schemas: Optional[List[Optional[Dict[str, Any]]]] = None,
    resume_results: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict]:
    """
    Process a list of prompts through Gemini API.

    Returns a list of dicts: [{"input": <prompt>, "output": <response>}, ...]

    Parameters
    ----------
    save_callback : callable, optional
        A function(results_so_far) called every ``save_every`` items so you
        can persist partial progress.
    save_every : int
        How often to call ``save_callback``.
    max_workers : int
        Number of concurrent worker threads. Use 1 for sequential execution.
    delay : float
        Sequential mode sleeps after each completed request. Concurrent mode
        sleeps between task submissions to avoid a burst at startup.
    """
    max_workers = max(1, int(max_workers or 1))
    if response_schemas is not None and len(response_schemas) != len(prompts):
        raise ValueError("response_schemas must have the same length as prompts.")
    if max_workers == 1 or len(prompts) <= 1:
        return _generate_batch_sequential(
            prompts,
            model=model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            delay=delay,
            save_callback=save_callback,
            save_every=save_every,
            response_schemas=response_schemas,
            resume_results=resume_results,
        )

    results: List[Optional[Dict]] = [None] * len(prompts)
    for position, result in enumerate(resume_results or []):
        try:
            index = int(result.get("batch_index", position))
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(results):
            results[index] = result
    completed = sum(result is not None for result in results)

    def run_one(index: int, prompt: str) -> tuple[int, Dict]:
        result = generate_single_with_usage(
            prompt,
            model=model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            response_schema=response_schemas[index] if response_schemas is not None else None,
        )
        result["batch_index"] = index
        return index, result

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for index, prompt in enumerate(prompts):
            if results[index] is not None:
                continue
            futures.append(executor.submit(run_one, index, prompt))
            if delay > 0 and index < len(prompts) - 1:
                time.sleep(delay)

        for future in _progress(
            as_completed(futures),
            total=len(futures),
            desc="Gemini inference",
            unit="prompt",
        ):
            index, result = future.result()
            results[index] = result
            completed += 1
            if save_callback and completed % save_every == 0:
                save_callback(_completed_results_in_order(results))

    if save_callback:
        save_callback(_completed_results_in_order(results))
    return _require_complete_results(results)


def _generate_batch_sequential(
    prompts: List[str],
    *,
    model: str,
    temperature: float,
    top_p: float,
    max_tokens: int,
    delay: float,
    save_callback=None,
    save_every: int,
    response_schemas: Optional[List[Optional[Dict[str, Any]]]],
    resume_results: Optional[List[Dict[str, Any]]],
) -> List[Dict]:
    resumed_by_index = {}
    for position, result in enumerate(resume_results or []):
        try:
            index = int(result.get("batch_index", position))
        except (TypeError, ValueError):
            continue
        resumed_by_index[index] = result
    results: List[Dict] = []

    for i, prompt in enumerate(_progress(prompts, desc="Gemini inference", unit="prompt")):
        result = resumed_by_index.get(i)
        if result is None:
            result = generate_single_with_usage(
                prompt,
                model=model,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                response_schema=response_schemas[i] if response_schemas is not None else None,
            )
            result["batch_index"] = i
        results.append(result)

        # Periodic save
        if save_callback and (i + 1) % save_every == 0:
            save_callback(results)

        # Rate-limit pause
        if delay > 0:
            time.sleep(delay)

    if save_callback:
        save_callback(results)
    return results


def _completed_results_in_order(results: List[Optional[Dict]]) -> List[Dict]:
    return [result for result in results if result is not None]


def _require_complete_results(results: List[Optional[Dict]]) -> List[Dict]:
    missing = [index for index, result in enumerate(results) if result is None]
    if missing:
        raise RuntimeError(f"Gemini batch incomplete; missing result indexes: {missing[:20]}")
    return [result for result in results if result is not None]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
