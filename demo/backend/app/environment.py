"""Safe dotenv loading shared by the local demo entrypoints."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path


_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_env_file(path: str | Path) -> bool:
    """Load literal dotenv assignments without executing shell content.

    Existing process variables always win so service launchers can override
    local demo defaults.
    """

    env_path = Path(path).expanduser().resolve()
    if not env_path.is_file():
        return False
    for line_number, raw_line in enumerate(env_path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not _ENV_KEY.fullmatch(key):
            raise ValueError(f"invalid environment assignment at {env_path.name}:{line_number}")
        lexer = shlex.shlex(raw_value, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        try:
            parts = list(lexer)
        except ValueError as exc:
            raise ValueError(f"invalid environment value at {env_path.name}:{line_number}") from exc
        if len(parts) > 1:
            raise ValueError(f"environment value must be quoted at {env_path.name}:{line_number}")
        os.environ.setdefault(key, parts[0] if parts else "")
    return True


__all__ = ["load_env_file"]
