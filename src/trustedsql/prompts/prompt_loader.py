from __future__ import annotations

from pathlib import Path
import json
from string import Template
from typing import Any

from trustedsql.utils.io import to_jsonable


PROMPT_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    path = PROMPT_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, **values: Any) -> str:
    safe_values = {key: _stringify(value) for key, value in values.items()}
    return Template(load_prompt(name)).safe_substitute(safe_values)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(to_jsonable(value), ensure_ascii=False, indent=2)
    return repr(value)
