from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Template


@dataclass(frozen=True)
class PromptTemplate:
    system: str
    prompt: Template


def load_prompt_template(filename: str) -> PromptTemplate:
    path = Path(__file__).resolve().parent / filename
    text = path.read_text(encoding="utf-8")
    system_marker = "---SYSTEM---"
    prompt_marker = "---PROMPT---"
    if system_marker not in text or prompt_marker not in text:
        raise ValueError(f"Prompt file must contain {system_marker} and {prompt_marker}: {path}")
    system_part, prompt_part = text.split(prompt_marker, 1)
    system = system_part.replace(system_marker, "", 1).strip()
    return PromptTemplate(system=system, prompt=Template(prompt_part.strip()))

