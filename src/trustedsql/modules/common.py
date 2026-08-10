from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from trustedsql.schemas import ModuleResult


def timed_module(module_id: str, stage: str, fn: Callable[[], ModuleResult]) -> ModuleResult:
    started = time.perf_counter()
    try:
        result = fn()
        result.latency_ms = (time.perf_counter() - started) * 1000
        return result
    except Exception as exc:  # noqa: BLE001
        return ModuleResult(
            module_id=module_id,
            stage=stage,
            decision="ERROR",
            artifact={},
            audit={},
            latency_ms=(time.perf_counter() - started) * 1000,
            error=str(exc),
        )


def merge_usage(target: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    for key, value in (usage or {}).items():
        if isinstance(value, (int, float)):
            target[key] = target.get(key, 0) + value
        elif key not in target:
            target[key] = value
    return target

