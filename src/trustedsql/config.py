from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class TrustedSqlConfig:
    project_root: Path
    raw: dict[str, Any]
    datasets: dict[str, Any]
    modules: dict[str, Any]
    method: dict[str, Any]

    @property
    def output_dir(self) -> Path:
        return self.resolve_path(self.raw.get("output_dir") or self.raw.get("paths", {}).get("output_dir", "outputs"))

    @property
    def ddl_path(self) -> Path:
        schema_ddl = self.raw.get("schema", {}).get("ddl_path")
        paths_ddl = self.raw.get("paths", {}).get("ddl_path") if isinstance(self.raw.get("paths"), dict) else None
        return self.resolve_path(schema_ddl or paths_ddl or "resources/schema/ddl.md")

    @property
    def compact_schema_path(self) -> Path:
        schema_compact = self.raw.get("schema", {}).get("compact_schema_path")
        return self.resolve_path(schema_compact or "artifacts/schema/compact/v1/compact_schema.txt")

    @property
    def policy_index_path(self) -> Path:
        policy_pip = self.raw.get("policy", {}).get("policy_index_path")
        paths_policy = self.raw.get("paths", {}).get("policy_path") if isinstance(self.raw.get("paths"), dict) else None
        return self.resolve_path(policy_pip or paths_policy or "resources/policy/policy_index.json")

    @property
    def role_access_matrix_path(self) -> Path:
        policy_ram = self.raw.get("policy", {}).get("role_access_matrix_path")
        paths_ram = self.raw.get("paths", {}).get("role_access_matrix_path") if isinstance(self.raw.get("paths"), dict) else None
        return self.resolve_path(policy_ram or paths_ram or "resources/policy/role_access_matrix.json")

    def resolve_path(self, value: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.project_root / path
        return path.resolve()

    def enabled_settings(self, selected: list[str] | None = None) -> dict[str, Any]:
        settings = self.method.get("settings", {})
        if selected:
            missing = [name for name in selected if name not in settings]
            if missing:
                raise KeyError(f"Unknown method settings: {missing}")
            return {name: settings[name] for name in selected}
        return {k: v for k, v in settings.items() if v.get("enabled", True)}

    def module_vertex_config(self, module_id: str) -> dict[str, Any]:
        vertex = dict(self.raw.get("vertex", {}))
        module_cfg = self.modules.get(module_id, {})
        vertex.update(module_cfg.get("vertex", {}) or {})
        return vertex

    def module_llm_config(self, module_id: str) -> dict[str, Any]:
        module_cfg = self.modules.get(module_id, {}) or {}
        if module_cfg.get("llm"):
            merged = dict(self.raw.get("llm", {}) or {})
            merged.update(module_cfg.get("llm") or {})
            provider = str(_env_value(merged.get("provider"), merged.get("provider_env")) or "vertex").lower()
            merged["provider"] = provider
            provider_models = merged.get("models")
            if isinstance(provider_models, dict) and not _env_value(None, merged.get("model_env")):
                if provider in provider_models:
                    merged["model"] = provider_models[provider]
            merged["model"] = _env_value(merged.get("model"), merged.get("model_env"))
            if provider == "vertex":
                vertex = dict(self.raw.get("vertex", {}) or {})
                vertex.update(merged)
                return vertex
            return merged
        return self.module_vertex_config(module_id)

    def module_models(self) -> dict[str, str | None]:
        return {
            module_id: self.module_llm_config(module_id).get("model")
            for module_id in sorted(self.modules)
            if self.module_llm_config(module_id).get("model")
        }

    def validate(self, require_database: bool = False, require_vertex: bool = False) -> None:
        for path in [self.ddl_path, self.compact_schema_path, self.policy_index_path, self.role_access_matrix_path]:
            if not path.exists():
                raise FileNotFoundError(path)
        if require_database and not self.raw.get("database", {}).get("url"):
            raise ValueError("database.url is required")
        vertex = self.raw.get("vertex", {})
        if require_vertex and not vertex.get("model"):
            raise ValueError("vertex.model is required")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as handle:
        return yaml.safe_load(handle) or {}


def _load_env(path: Path | None) -> None:
    if not path or not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


def _env_value(value: Any, env_name: Any) -> Any:
    if env_name and os.environ.get(str(env_name)):
        return os.environ[str(env_name)]
    return value


def load_config(
    config_dir: Path,
    env_file: Path | None = None,
    datasets_file: Path | None = None,
    modules_file: Path | None = None,
    project_root: Path | None = None,
) -> TrustedSqlConfig:
    project_root = (project_root or config_dir.parent).resolve()
    _load_env(env_file or (project_root / ".env"))
    raw = _read_yaml(config_dir / "runtime" / "default.yaml")
    datasets = _read_yaml(datasets_file or _first_existing(config_dir / "datasets.yaml", config_dir / "datasets" / "v3_full.yaml"))
    modules_raw = _read_yaml(modules_file or _first_existing(config_dir / "providers" / "modules.yaml", config_dir / "providers" / "gemini_25_flash.yaml"))
    modules = modules_raw.get("modules", modules_raw)
    method = _read_yaml(config_dir / "method" / "method.yaml")
    if not method:
        systems = _read_yaml(config_dir / "systems" / "trustedsql.yaml")
        method = {"settings": systems.get("systems", {})}
    database_url = os.environ.get("TRUSTEDSQL_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if database_url:
        raw.setdefault("database", {})["url"] = database_url
    vertex_env_map = {
        "project_id": ("TRUSTEDSQL_VERTEX_PROJECT_ID", "VERTEX_PROJECT_ID"),
        "location": ("TRUSTEDSQL_VERTEX_LOCATION", "VERTEX_LOCATION"),
    }
    for key, env_keys in vertex_env_map.items():
        for env_key in env_keys:
            if os.environ.get(env_key):
                raw.setdefault("vertex", {})[key] = os.environ[env_key]
                break
    return TrustedSqlConfig(project_root=project_root, raw=raw, datasets=datasets, modules=modules, method=method)


def _first_existing(*paths: Path) -> Path:
    for path in paths:
        if path.exists():
            return path
    return paths[-1]
