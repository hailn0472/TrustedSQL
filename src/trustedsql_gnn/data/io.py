from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, TypeVar

from pydantic import BaseModel


T = TypeVar("T", bound=BaseModel)


def read_jsonl(path: str | Path, model: type[T]) -> list[T]:
    output: list[T] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                output.append(model.model_validate_json(raw))
            except Exception as exc:
                raise ValueError(f"{path}:{line_number}:{exc}") from exc
    return output


def write_jsonl(path: str | Path, values: Iterable[BaseModel | dict]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for value in values:
            payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def write_json(path: str | Path, payload: dict | list) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
