from __future__ import annotations

import json
from typing import Any

from architecture_baselines.prompts.prompt_loader import load_prompt_template


_D1_TEMPLATE = load_prompt_template("d1_input_attack_guard.txt")
_D2_TEMPLATE = load_prompt_template("d2_resource_extractor.txt")
_G1_TEMPLATE = load_prompt_template("g1_text2sql_generator.txt")

INPUT_ATTACK_SYSTEM = _D1_TEMPLATE.system
RESOURCE_EXTRACT_SYSTEM = _D2_TEMPLATE.system
TEXT2SQL_SYSTEM = _G1_TEMPLATE.system


def history_text(history: list[Any], max_items: int = 6, *, include_sql: bool = False) -> str:
    if not history:
        return "No previous turns."
    lines: list[str] = []
    for item in history[-max_items:]:
        data = item.to_dict() if hasattr(item, "to_dict") else dict(item)
        preview = data.get("execution_result_preview")
        result_text = json.dumps(preview, ensure_ascii=False, default=str) if preview is not None else "null"
        sql_text = ""
        if include_sql and data.get("executed") and data.get("final_sql"):
            sql_text = f"; final_sql={str(data.get('final_sql'))[:1200]}"
        lines.append(f"Turn {data.get('turn_id')}: nlq={data.get('nlq')!r}; decision={data.get('decision')}{sql_text}; execution_result_json={result_text}")
    return "\n".join(lines)


def input_attack_prompt(nlq: str, role: str, user_id: int, history: list[Any]) -> str:
    return _D1_TEMPLATE.prompt.substitute(
        role=role,
        user_id=user_id,
        history=history_text(history),
        nlq=nlq,
    )


def resource_extract_prompt(nlq: str, role: str, user_id: int, schema_ddl: str, history: list[Any]) -> str:
    return _D2_TEMPLATE.prompt.substitute(
        role=role,
        user_id=user_id,
        schema_ddl=schema_ddl,
        history=history_text(history),
        nlq=nlq,
    )


def text2sql_prompt(nlq: str, role: str, user_id: int, scoped_schema_ddl: str, history: list[Any]) -> str:
    return _G1_TEMPLATE.prompt.substitute(
        role=role,
        user_id=user_id,
        history=history_text(history, include_sql=True),
        scoped_schema_ddl=scoped_schema_ddl,
        nlq=nlq,
    )

