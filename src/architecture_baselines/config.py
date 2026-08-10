from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_path(value: str | Path, base: Path = PROJECT_ROOT) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


@dataclass
class AppConfig:
    raw: dict[str, Any]
    project_root: Path = PROJECT_ROOT

    @property
    def policy_path(self) -> Path:
        return _resolve_path(self.raw["paths"]["policy_path"], self.project_root)

    @property
    def role_access_matrix_path(self) -> Path:
        return _resolve_path(self.raw["paths"]["role_access_matrix_path"], self.project_root)

    @property
    def ddl_path(self) -> Path:
        return _resolve_path(self.raw["paths"]["ddl_path"], self.project_root)

    @property
    def output_dir(self) -> Path:
        return _resolve_path(self.raw["paths"]["output_dir"], self.project_root)

    @property
    def database_url(self) -> str:
        return str(self.raw["database"].get("url") or "")

    @property
    def vertex(self) -> dict[str, Any]:
        return dict(self.raw.get("vertex", {}))

    @property
    def llm(self) -> dict[str, Any]:
        return dict(self.raw.get("llm", self.raw.get("vertex", {})))

    @property
    def architectures(self) -> dict[str, Any]:
        return dict(self.raw.get("architectures", {}))

    @property
    def modules(self) -> dict[str, Any]:
        return dict(self.raw.get("modules", {}))

    @property
    def datasets(self) -> dict[str, Any]:
        return dict(self.raw.get("datasets", {}))

    def validate(
        self,
        require_runtime: bool = True,
        *,
        require_database: bool | None = None,
        require_vertex: bool | None = None,
    ) -> None:
        if require_database is None:
            require_database = require_runtime
        if require_vertex is None:
            require_vertex = require_runtime
        missing: list[str] = []
        for name, path in [
            ("policy_path", self.policy_path),
            ("role_access_matrix_path", self.role_access_matrix_path),
            ("ddl_path", self.ddl_path),
        ]:
            if not path.exists():
                missing.append(f"{name}: {path}")
        for ds_name, ds_cfg in self.datasets.items():
            ds_path = _resolve_path(ds_cfg["path"], self.project_root)
            if not ds_path.exists():
                missing.append(f"dataset {ds_name}: {ds_path}")
        if require_database and not self.database_url:
            missing.append("DATABASE_URL/database.url")
        if require_vertex:
            provider = str(self.llm.get("provider", "vertex")).lower()
            if provider == "vertex" and not self.llm.get("project_id"):
                missing.append("VERTEX_PROJECT_ID/llm.project_id")
        if missing:
            joined = "\n  - ".join(missing)
            raise ValueError(f"Missing required configuration:\n  - {joined}")


def load_config(
    config_dir: Path | None = None,
    env_file: Path | None = None,
    datasets_file: Path | None = None,
    modules_file: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> AppConfig:
    config_dir = config_dir or PROJECT_ROOT / "configs"
    env_file = env_file or PROJECT_ROOT / ".env"

    raw: dict[str, Any] = {}
    config_files = [
        "runtime/default.yaml",
    ]
    for filename in config_files:
        raw = _deep_merge(raw, _load_yaml(config_dir / filename))
    raw = _deep_merge(raw, _load_yaml(datasets_file and _resolve_path(datasets_file, PROJECT_ROOT) or _first_existing(config_dir / "datasets.yaml", config_dir / "datasets" / "v3_full.yaml")))
    raw = _deep_merge(raw, _load_yaml(modules_file and _resolve_path(modules_file, PROJECT_ROOT) or _first_existing(config_dir / "providers" / "modules.yaml", config_dir / "providers" / "gemini_25_flash.yaml")))
    architectures = _load_yaml(config_dir / "architectures" / "architectures.yaml")
    if not architectures:
        systems = _load_yaml(config_dir / "systems" / "architecture_baselines.yaml").get("systems", {})
        architectures = {
            "architectures": {
                system_id: {
                    key: value
                    for key, value in system.items()
                    if key in {"enabled", "label", "description", "modules"}
                }
                for system_id, system in systems.items()
            }
        }
    raw = _deep_merge(raw, architectures)

    env = {**_load_env_file(env_file), **os.environ}
    database_url = env.get("TRUSTEDSQL_DATABASE_URL") or env.get("DATABASE_URL")
    if database_url:
        raw.setdefault("database", {})["url"] = database_url
    vertex_project = env.get("TRUSTEDSQL_VERTEX_PROJECT_ID") or env.get("VERTEX_PROJECT_ID")
    if vertex_project:
        raw.setdefault("vertex", {})["project_id"] = vertex_project
        raw.setdefault("llm", {})["project_id"] = vertex_project
    vertex_location = env.get("TRUSTEDSQL_VERTEX_LOCATION") or env.get("VERTEX_LOCATION")
    if vertex_location:
        raw.setdefault("vertex", {})["location"] = vertex_location
        raw.setdefault("llm", {})["location"] = vertex_location
    if env.get("GOOGLE_APPLICATION_CREDENTIALS"):
        raw.setdefault("vertex", {})["google_application_credentials"] = env["GOOGLE_APPLICATION_CREDENTIALS"]
        raw.setdefault("llm", {})["google_application_credentials"] = env["GOOGLE_APPLICATION_CREDENTIALS"]
    if env.get("EVALUATE_ARCH_OUTPUT_DIR"):
        raw.setdefault("paths", {})["output_dir"] = env["EVALUATE_ARCH_OUTPUT_DIR"]

    if cli_overrides:
        raw = _deep_merge(raw, cli_overrides)
    return AppConfig(raw=raw)


def _first_existing(*paths: Path) -> Path:
    for path in paths:
        if path.exists():
            return path
    return paths[-1]

