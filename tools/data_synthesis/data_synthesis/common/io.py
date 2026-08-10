from __future__ import annotations

import json
import os
from typing import Any, Dict


class JsonOutputError(ValueError):
    """Raised when a model response cannot be parsed as a JSON object."""


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_model_json(text: str) -> Dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```json"):
        candidate = candidate[7:].strip()
    elif candidate.startswith("```"):
        candidate = candidate[3:].strip()
    if candidate.endswith("```"):
        candidate = candidate[:-3].strip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(candidate[start : end + 1])

    if not isinstance(parsed, dict):
        raise JsonOutputError("Model output must be a JSON object.")
    return parsed

