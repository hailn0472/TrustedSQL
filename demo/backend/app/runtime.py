"""Interactive TrustedSQL runtime adapter for the isolated demo.

The parent TrustedSQL runner remains the source of runtime behavior.  This module
normalizes a user-authored chat history, supplies it to the parent runner, and
confines its persistence to ``demo/runs``.
"""

from __future__ import annotations

import json
import re
import sys
import time
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .paths import IsolationBoundaryError, parent_resource_path, run_path

REQUIRED_MODULES = ("C0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "X1")
TRUSTEDSQL_MODULES = REQUIRED_MODULES
TRUSTEDSQL_MODE = "trustedsql"
DIRECT_MODE = "direct"
TRUSTEDSQL_SETTING_ID = "full_trustedsql"
DIRECT_SETTING_ID = "direct_sql"
DIRECT_MODULES = ("C0", "M6", "X1")
INTERACTIVE_SCENARIO_KEY = "multiturn"
INTERACTIVE_SAMPLE_ID = "interactive-multiturn"
INTERACTIVE_ROLE = "student"
INTERACTIVE_USER_ID = 40
MAX_CHAT_TURNS = 20
MAX_CHAT_QUERY_CHARS = 2_000
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _clean_direct_sql(text: str) -> str | None:
    """Normalize a naive text-to-SQL response without applying policy checks."""

    stripped = text.strip()
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", stripped, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    lines = [line for line in stripped.splitlines() if not line.strip().startswith("--")]
    return "\n".join(lines).strip() or None


class RuntimeAdapterError(RuntimeError):
    """Raised when the demo adapter cannot honor its runtime contract."""


@dataclass(frozen=True)
class ReadinessResult:
    """Safe readiness information; it intentionally contains no paths or values."""

    ready: bool
    checks: Mapping[str, bool]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "checks": dict(self.checks),
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class ExecutionResult:
    """Small backend-facing handle for one completed incremental turn."""

    run_id: str
    scenario_key: str
    sample_id: str
    through_turn: int
    output_dir: Path
    manifest_path: Path
    runtime_dir: Path
    final_output: Mapping[str, Any]
    error_summary: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario_key": self.scenario_key,
            "sample_id": self.sample_id,
            "through_turn": self.through_turn,
            "output_dir": str(self.output_dir),
            "manifest_path": str(self.manifest_path),
            "runtime_dir": str(self.runtime_dir),
            "final_output": dict(self.final_output),
            "error_summary": dict(self.error_summary),
        }


ConfigLoader = Callable[[Path, Path], Any]
RunnerFactory = Callable[..., Any]


def _ensure_parent_import_path(repo_root: Path) -> None:
    source_root = str((repo_root / "src").resolve())
    if source_root not in sys.path:
        sys.path.insert(0, source_root)


def _default_config_loader(repo_root: Path, provider_config: Path) -> Any:
    """Load demo config while making every parent input explicit and read-only."""

    _ensure_parent_import_path(repo_root)
    from trustedsql.config import load_config

    demo_config = repo_root / "demo" / "configs"
    datasets = parent_resource_path(repo_root, "configs/datasets/v3_full.yaml")
    return load_config(
        demo_config,
        datasets_file=datasets,
        modules_file=provider_config,
        project_root=repo_root,
    )


def _default_runner_factory(config: Any, run_id: str, *, output_dir: Path) -> Any:
    _ensure_parent_import_path(Path(config.project_root))
    from trustedsql.runtime.runner import MethodRunner

    return MethodRunner(config, run_id, output_dir=output_dir)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    return rows


class TrustedSqlRuntimeAdapter:
    """Facade for one interactive, multi-turn TrustedSQL conversation.

    ``config_loader`` and ``runner_factory`` are injected so tests can exercise
    orchestration without constructing provider clients or database connections.
    """

    def __init__(
        self,
        repo_root: str | Path,
        provider_config: str | Path | None,
        *,
        config_loader: ConfigLoader | None = None,
        runner_factory: RunnerFactory | None = None,
    ) -> None:
        if provider_config is None:
            raise ValueError("provider_config is required; provider fallback is disabled")
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.provider_config = self._resolve_provider_config(provider_config)
        self.config_loader = config_loader or _default_config_loader
        self.runner_factory = runner_factory or _default_runner_factory
        self._config_cache: Any | None = None

    def _resolve_provider_config(self, provider_config: str | Path) -> Path:
        requested = Path(provider_config).expanduser()
        if requested.is_absolute():
            return requested.resolve()
        return parent_resource_path(self.repo_root, requested)

    def _load_config(self) -> Any:
        if self._config_cache is None:
            if not self.provider_config.is_file():
                raise RuntimeAdapterError("provider config does not exist")
            try:
                self._config_cache = self.config_loader(self.repo_root, self.provider_config)
            except RuntimeAdapterError:
                raise
            except Exception as exc:  # noqa: BLE001 - never expose config/provider details
                raise RuntimeAdapterError("TrustedSQL demo config could not be loaded") from exc
        return self._config_cache

    def _validate_run_id(self, run_id: str) -> None:
        if not isinstance(run_id, str) or not _SAFE_RUN_ID.fullmatch(run_id):
            raise RuntimeAdapterError("run_id must be a safe single path segment")

    def _prepare_run_dir(self, run_id: str) -> Path:
        self._validate_run_id(run_id)
        try:
            output_dir = run_path(self.repo_root, run_id)
        except IsolationBoundaryError as exc:
            raise RuntimeAdapterError("run output path is outside demo/runs") from exc
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            output_dir.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise RuntimeAdapterError(
                "non-empty or already reserved run directory cannot be reused"
            ) from exc
        return output_dir

    @staticmethod
    def _setting(config: Any) -> dict[str, Any]:
        settings = getattr(config, "method", {}).get("settings", {})
        setting = settings.get("full_trustedsql") if isinstance(settings, Mapping) else None
        if not isinstance(setting, Mapping):
            raise RuntimeAdapterError("full_trustedsql setting is missing")
        modules = tuple(setting.get("modules", ()))
        if modules != REQUIRED_MODULES:
            raise RuntimeAdapterError("full_trustedsql module list does not match required module list")
        return dict(setting)

    @staticmethod
    def _normalized_sequence(prefix: Mapping[str, Any]) -> Any:
        from trustedsql.schemas import NormalizedSequence, NormalizedTurn

        turns = [
            NormalizedTurn(
                turn_id=int(turn["turn_id"]),
                nlq=str(turn["nlq"]),
                sql_gt=None,
                turn_label="",
            )
            for turn in prefix["turns"]
        ]
        return NormalizedSequence(
            sample_id=str(prefix["canonical_id"]),
            source_dataset="interactive_chat",
            turn_type=str(prefix["turn_type"]),
            seq_label="",
            role=str(prefix["role"]),
            user_id=int(prefix["user_id"]),
            attack_tags={},
            turns=turns,
            primary_type=None,
        )

    @staticmethod
    def _interactive_prefix(turns: Sequence[str]) -> dict[str, Any]:
        """Build a label-free sequence from user-authored chat history."""

        if isinstance(turns, (str, bytes)) or not isinstance(turns, Sequence):
            raise RuntimeAdapterError("turns must be a sequence of chat messages")
        if not 1 <= len(turns) <= MAX_CHAT_TURNS:
            raise RuntimeAdapterError(f"turns must contain between 1 and {MAX_CHAT_TURNS} messages")
        normalized: list[dict[str, Any]] = []
        for turn_id, value in enumerate(turns, start=1):
            if not isinstance(value, str):
                raise RuntimeAdapterError("every chat message must be a string")
            nlq = value.strip()
            if not nlq or len(nlq) > MAX_CHAT_QUERY_CHARS or "\x00" in nlq:
                raise RuntimeAdapterError("chat message is empty or exceeds the bounded limit")
            normalized.append({"turn_id": turn_id, "nlq": nlq})
        return {
            "key": INTERACTIVE_SCENARIO_KEY,
            "canonical_id": INTERACTIVE_SAMPLE_ID,
            "role": INTERACTIVE_ROLE,
            "user_id": INTERACTIVE_USER_ID,
            "turn_type": "multi",
            "turns": normalized,
        }

    @staticmethod
    def _trusted_history(history: Sequence[Mapping[str, Any]], turn_number: int) -> list[Any]:
        """Rebuild parent history objects from server-owned conversation state."""

        from trustedsql.schemas import TurnHistoryItem

        if type(turn_number) is not int or not 1 <= turn_number <= MAX_CHAT_TURNS:
            raise RuntimeAdapterError("turn_number is outside the bounded chat range")
        if isinstance(history, (str, bytes)) or not isinstance(history, Sequence):
            raise RuntimeAdapterError("history must be a sequence")
        if len(history) != turn_number - 1:
            raise RuntimeAdapterError("history length does not match the current turn")

        result: list[Any] = []
        for expected_turn, item in enumerate(history, start=1):
            if not isinstance(item, Mapping) or item.get("turn_id") != expected_turn:
                raise RuntimeAdapterError("history turn identity is invalid")
            nlq = item.get("nlq")
            decision = item.get("decision")
            raw_sql = item.get("raw_sql")
            final_sql = item.get("final_sql")
            executed = item.get("executed")
            blocked_at = item.get("blocked_at")
            if (
                not isinstance(nlq, str)
                or not nlq.strip()
                or len(nlq) > MAX_CHAT_QUERY_CHARS
                or "\x00" in nlq
                or decision not in {"ALLOW", "DENY", "ERROR"}
                or type(executed) is not bool
                or (raw_sql is not None and not isinstance(raw_sql, str))
                or (final_sql is not None and not isinstance(final_sql, str))
                or (blocked_at is not None and blocked_at not in REQUIRED_MODULES)
            ):
                raise RuntimeAdapterError("history contains an invalid trusted turn")
            result.append(
                TurnHistoryItem(
                    turn_id=expected_turn,
                    nlq=nlq.strip(),
                    decision=decision,
                    raw_sql=raw_sql,
                    final_sql=final_sql,
                    executed=executed,
                    execution_result_json=item.get("execution_result_json"),
                    blocked_at=blocked_at,
                )
            )
        return result

    def check_readiness(self) -> ReadinessResult:
        checks: dict[str, bool] = {
            "provider_config": self.provider_config.is_file(),
            "database_url": False,
            "schema_assets": False,
            "policy_assets": False,
            "compact_schema": False,
            "gnn_assets": False,
            "model_assets": False,
            "temperatures": False,
        }
        errors: list[str] = []
        if not checks["provider_config"]:
            errors.append("provider_config_missing")
            return ReadinessResult(False, checks, tuple(errors))

        try:
            config = self._load_config()
        except RuntimeAdapterError:
            errors.append("config_load_failed")
            return ReadinessResult(False, checks, tuple(errors))

        raw = getattr(config, "raw", {})
        database = raw.get("database", {}) if isinstance(raw, Mapping) else {}
        checks["database_url"] = bool(isinstance(database, Mapping) and database.get("url"))
        if not checks["database_url"]:
            errors.append("database_url_missing")

        schema_paths = [getattr(config, "ddl_path", None)]
        policy_paths = [
            getattr(config, "policy_index_path", None),
            getattr(config, "role_access_matrix_path", None),
        ]
        checks["schema_assets"] = all(isinstance(path, Path) and path.is_file() for path in schema_paths)
        checks["policy_assets"] = all(isinstance(path, Path) and path.is_file() for path in policy_paths)
        checks["compact_schema"] = isinstance(getattr(config, "compact_schema_path", None), Path) and config.compact_schema_path.is_file()
        if not checks["schema_assets"]:
            errors.append("schema_asset_missing")
        if not checks["policy_assets"]:
            errors.append("policy_asset_missing")
        if not checks["compact_schema"]:
            errors.append("compact_schema_asset_missing")

        checks["temperatures"], temperature_errors = self._temperature_check(config)
        errors.extend(temperature_errors)
        checks["model_assets"] = self._model_check(config)
        if not checks["model_assets"]:
            errors.append("model_asset_missing")
        checks["gnn_assets"] = self._gnn_check(config)
        if not checks["gnn_assets"]:
            errors.append("gnn_asset_missing")

        # Validate parent-owned paths/database, but not its global raw.vertex model:
        # TrustedSQL resolves provider models per module (M1/M3/M6).
        validator = getattr(config, "validate", None)
        if callable(validator):
            try:
                validator(require_database=True, require_vertex=False)
            except Exception:  # noqa: BLE001 - readiness must not disclose values
                errors.append("parent_config_validation_failed")

        unique_errors = tuple(dict.fromkeys(errors))
        return ReadinessResult(not unique_errors, checks, unique_errors)

    @staticmethod
    def _temperature_check(config: Any) -> tuple[bool, list[str]]:
        """Require an explicit zero temperature for every effective LLM stage."""

        errors: list[str] = []
        checked_stage = False
        raw = getattr(config, "raw", {})
        raw_llm = raw.get("llm") if isinstance(raw, Mapping) else None
        if isinstance(raw_llm, Mapping):
            checked_stage = True
            value = raw_llm.get("temperature")
            if type(value) not in (int, float) or value != 0.0:
                errors.append("temperature_not_pinned")

        modules = getattr(config, "modules", {})
        required_modules = ("M1", "M3", "M6")
        stages: list[Mapping[str, Any]] = []
        for module_id in required_modules:
            effective_stage = TrustedSqlRuntimeAdapter._effective_llm_config(config, module_id)
            stages.append(effective_stage)
        if isinstance(modules, Mapping):
            for module in modules.values():
                if not isinstance(module, Mapping):
                    continue
                for stage_name in ("llm", "vertex"):
                    configured_stage = module.get(stage_name)
                    if isinstance(configured_stage, Mapping):
                        stages.append(configured_stage)
        for effective_stage in stages:
            if not isinstance(effective_stage, Mapping):
                continue
            checked_stage = True
            value = effective_stage.get("temperature")
            if type(value) not in (int, float) or value != 0.0:
                errors.append("temperature_not_pinned")

        if not checked_stage:
            errors.append("temperature_not_pinned")
        return not errors, errors

    @staticmethod
    def _model_check(config: Any) -> bool:
        return all(
            bool(TrustedSqlRuntimeAdapter._effective_llm_config(config, module_id).get("model"))
            for module_id in ("M1", "M3", "M6")
        )

    @staticmethod
    def _effective_llm_config(config: Any, module_id: str) -> Mapping[str, Any]:
        resolver = getattr(config, "module_llm_config", None)
        if callable(resolver):
            try:
                resolved = resolver(module_id)
                if isinstance(resolved, Mapping):
                    return resolved
            except Exception:  # noqa: BLE001 - readiness reports safe reason codes
                pass

        raw = getattr(config, "raw", {})
        merged: dict[str, Any] = {}
        if isinstance(raw, Mapping):
            for key in ("vertex", "llm"):
                value = raw.get(key)
                if isinstance(value, Mapping):
                    merged.update(value)
        modules = getattr(config, "modules", {})
        module = modules.get(module_id) if isinstance(modules, Mapping) else None
        if isinstance(module, Mapping):
            for key in ("vertex", "llm"):
                value = module.get(key)
                if isinstance(value, Mapping):
                    merged.update(value)
        return merged

    @staticmethod
    def _selected_config(config: Any, output_dir: Path, prefix: Mapping[str, Any]) -> Any:
        """Create a config view whose manifest can fingerprint only this prefix."""

        dataset_path = output_dir / "selected-sequence.json"
        dataset_payload = [
            {
                "id": str(prefix["canonical_id"]),
                "turn_type": str(prefix["turn_type"]),
                "role": str(prefix["role"]),
                "user_context_id": int(prefix["user_id"]),
                "turns": [
                    {"turn_id": int(turn["turn_id"]), "nlq": str(turn["nlq"])}
                    for turn in prefix["turns"]
                ],
            }
        ]
        dataset_path.write_text(
            json.dumps(dataset_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            dataset_ref = str(dataset_path.relative_to(Path(config.project_root).resolve()))
        except ValueError:
            dataset_ref = str(dataset_path)
        config_view = copy(config)
        object.__setattr__(
            config_view,
            "datasets",
            {
                "datasets": {
                    "selected_sequence": {
                        "path": dataset_ref,
                        "turn_type": str(prefix["turn_type"]),
                        "enabled": True,
                    }
                }
            },
        )
        return config_view

    @staticmethod
    def _gnn_check(config: Any) -> bool:
        try:
            from trustedsql_gnn.paths import GNNPaths

            root = getattr(config, "project_root")
            module = getattr(config, "modules", {}).get("M2", {})
            engine = module.get("intent_gnn", {}) if isinstance(module, Mapping) else {}
            checkpoint = engine.get("checkpoint_path") if isinstance(engine, Mapping) else None
            paths = GNNPaths.from_project_root(root, checkpoint_path=checkpoint)
            paths.require_runtime_assets()
            extra = [
                root / "artifacts/models/intent_gnn/v1/encoder_manifest.json",
                paths.encoder_dir / "model.safetensors",
                paths.encoder_dir / "vocab.txt",
            ]
            return all(path.is_file() for path in extra)
        except Exception:  # noqa: BLE001 - readiness returns safe reason codes
            return False

    def execute(self, turns: Sequence[str], run_id: str) -> ExecutionResult:
        """Legacy exact-sequence entrypoint retained for offline demo tooling."""

        if not self.provider_config.is_file():
            raise RuntimeAdapterError("provider config does not exist")
        prefix = self._interactive_prefix(turns)
        output_dir = self._prepare_run_dir(run_id)
        config = self._load_config()
        setting = self._setting(config)
        sequence = self._normalized_sequence(prefix)
        config = self._selected_config(config, output_dir, prefix)
        runner = None
        error_summary: Mapping[str, Any] = {}
        primary_error: BaseException | None = None
        try:
            runner = self.runner_factory(config, run_id, output_dir=output_dir)
            runner.write_run_manifest(
                settings={"full_trustedsql": setting},
                sequences=[sequence],
                max_samples=1,
            )
            outputs = runner._run_sequence_full(
                "full_trustedsql",
                sequence,
                seq_key=sequence.sample_id,
                history=[],
                modules=list(REQUIRED_MODULES),
            )
            if not isinstance(outputs, list):
                raise RuntimeAdapterError("runner returned an invalid output collection")
            completed: set[str] = set()
            for output in outputs:
                runner._write_output(output)
                completed.add(f"full_trustedsql|{sequence.sample_id}|{int(output.turn_id)}")
                runner._save_checkpoint(completed)
        except BaseException as exc:
            primary_error = exc
        finally:
            if runner is not None:
                try:
                    error_summary = runner._write_runtime_error_summary()
                except Exception:
                    if primary_error is None:
                        primary_error = RuntimeAdapterError("runtime error summary could not be persisted")
        if primary_error is not None:
            raise primary_error

        expected_turns = [turn.turn_id for turn in sequence.turns]
        rows = _read_jsonl(output_dir / "runtime" / "raw_turn_outputs.jsonl")
        actual_keys = [
            (row.get("setting_id"), row.get("sample_id"), row.get("turn_id"))
            for row in rows
        ]
        expected_keys = [("full_trustedsql", sequence.sample_id, turn_id) for turn_id in expected_turns]
        if actual_keys != expected_keys or not rows or rows[-1].get("turn_id") != expected_turns[-1]:
            raise RuntimeAdapterError("final selected turn is missing; partial sequence completion rejected")

        return ExecutionResult(
            run_id=run_id,
            scenario_key=INTERACTIVE_SCENARIO_KEY,
            sample_id=sequence.sample_id,
            through_turn=len(sequence.turns),
            output_dir=output_dir,
            manifest_path=output_dir / "run_manifest.json",
            runtime_dir=output_dir / "runtime",
            final_output=rows[-1],
            error_summary=error_summary,
        )

    def execute_turn(
        self,
        message: str,
        turn_number: int,
        history: Sequence[Mapping[str, Any]],
        run_id: str,
    ) -> ExecutionResult:
        """Execute only the new turn using authoritative server-side history."""

        if not self.provider_config.is_file():
            raise RuntimeAdapterError("provider config does not exist")
        if not isinstance(message, str):
            raise RuntimeAdapterError("message must be a string")
        nlq = message.strip()
        if not nlq or len(nlq) > MAX_CHAT_QUERY_CHARS or "\x00" in nlq:
            raise RuntimeAdapterError("chat message is empty or exceeds the bounded limit")

        trusted_history = self._trusted_history(history, turn_number)
        prefix = {
            "key": INTERACTIVE_SCENARIO_KEY,
            "canonical_id": INTERACTIVE_SAMPLE_ID,
            "role": INTERACTIVE_ROLE,
            "user_id": INTERACTIVE_USER_ID,
            "turn_type": "multi",
            "turns": [{"turn_id": turn_number, "nlq": nlq}],
        }
        output_dir = self._prepare_run_dir(run_id)
        config = self._load_config()
        setting = self._setting(config)
        sequence = self._normalized_sequence(prefix)
        config = self._selected_config(config, output_dir, prefix)
        runner = None
        error_summary: Mapping[str, Any] = {}
        primary_error: BaseException | None = None
        try:
            runner = self.runner_factory(config, run_id, output_dir=output_dir)
            runner.write_run_manifest(
                settings={"full_trustedsql": setting},
                sequences=[sequence],
                max_samples=1,
            )
            output = runner._run_turn_for_sequence(
                setting_id="full_trustedsql",
                sequence=sequence,
                turn=sequence.turns[0],
                seq_key=sequence.sample_id,
                history=trusted_history,
                modules=list(REQUIRED_MODULES),
            )
            runner._write_output(output)
            runner._save_checkpoint(
                {f"full_trustedsql|{sequence.sample_id}|{int(output.turn_id)}"}
            )
        except BaseException as exc:
            primary_error = exc
        finally:
            if runner is not None:
                try:
                    error_summary = runner._write_runtime_error_summary()
                except Exception:
                    if primary_error is None:
                        primary_error = RuntimeAdapterError(
                            "runtime error summary could not be persisted"
                        )
        if primary_error is not None:
            raise primary_error

        rows = _read_jsonl(output_dir / "runtime" / "raw_turn_outputs.jsonl")
        if (
            len(rows) != 1
            or rows[0].get("setting_id") != "full_trustedsql"
            or rows[0].get("sample_id") != sequence.sample_id
            or rows[0].get("turn_id") != turn_number
        ):
            raise RuntimeAdapterError("incremental turn output is missing or has invalid identity")

        return ExecutionResult(
            run_id=run_id,
            scenario_key=INTERACTIVE_SCENARIO_KEY,
            sample_id=sequence.sample_id,
            through_turn=turn_number,
            output_dir=output_dir,
            manifest_path=output_dir / "run_manifest.json",
            runtime_dir=output_dir / "runtime",
            final_output=rows[0],
            error_summary=error_summary,
        )

    def execute_direct_turn(
        self,
        message: str,
        turn_number: int,
        history: Sequence[Mapping[str, Any]],
        run_id: str,
    ) -> ExecutionResult:
        """Run the comparison path: prompt -> SQL generation -> read-only execution.

        No TrustedSQL policy, intent, scope, schema-authorization, or SQL
        conformance module participates.  The database connection remains
        read-only as an environment safety boundary for the demo.
        """

        _ensure_parent_import_path(self.repo_root)
        from trustedsql.modules import readonly_executor
        from trustedsql.schemas import MethodTurnOutput, ModuleResult, RuntimeTurnInput

        if not self.provider_config.is_file():
            raise RuntimeAdapterError("provider config does not exist")
        if not isinstance(message, str):
            raise RuntimeAdapterError("message must be a string")
        nlq = message.strip()
        if not nlq or len(nlq) > MAX_CHAT_QUERY_CHARS or "\x00" in nlq:
            raise RuntimeAdapterError("chat message is empty or exceeds the bounded limit")

        memory_started = time.perf_counter()
        trusted_history = self._trusted_history(history, turn_number)
        memory_latency_ms = (time.perf_counter() - memory_started) * 1000
        prefix = {
            "key": INTERACTIVE_SCENARIO_KEY,
            "canonical_id": INTERACTIVE_SAMPLE_ID,
            "role": INTERACTIVE_ROLE,
            "user_id": INTERACTIVE_USER_ID,
            "turn_type": "multi",
            "turns": [{"turn_id": turn_number, "nlq": nlq}],
        }
        output_dir = self._prepare_run_dir(run_id)
        config = self._load_config()
        sequence = self._normalized_sequence(prefix)
        config = self._selected_config(config, output_dir, prefix)
        runner = None
        error_summary: Mapping[str, Any] = {}
        primary_error: BaseException | None = None
        try:
            runner = self.runner_factory(config, run_id, output_dir=output_dir)
            direct_setting = {
                "enabled": True,
                "label": "Direct SQL (security bypassed)",
                "runtime_kind": "direct_sql",
                "modules": list(DIRECT_MODULES),
            }
            runner.write_run_manifest(
                settings={DIRECT_SETTING_ID: direct_setting},
                sequences=[sequence],
                max_samples=1,
            )

            runtime_turn = RuntimeTurnInput(
                run_id=run_id,
                setting_id=DIRECT_SETTING_ID,
                sequence_id=sequence.sample_id,
                sample_id=sequence.sample_id,
                turn_id=turn_number,
                role=sequence.role,
                user_id=sequence.user_id,
                nlq=nlq,
                history=trusted_history,
            )
            history_payload = [item.to_dict() for item in trusted_history]
            memory_result = ModuleResult(
                module_id="C0",
                stage="direct_context_memory",
                decision="ALLOW",
                artifact={
                    "role": sequence.role,
                    "user_id": sequence.user_id,
                    "history_count": len(history_payload),
                    "security_modules_bypassed": True,
                    "memory_source": "server_conversation",
                },
                audit={"mode": DIRECT_MODE},
                latency_ms=memory_latency_ms,
            )
            runner._write_module_event_log(
                runtime_turn,
                "C0",
                {
                    "history_turn_count": len(history_payload),
                    "memory_source": "server_conversation",
                    "security_modules_bypassed": True,
                },
                memory_result,
            )
            full_schema = runner.compact_schema or runner.schema_graph.ddl
            prompt = (
                "Convert the current natural-language request into exactly one PostgreSQL "
                "SELECT query. Return SQL only, without Markdown or explanation.\n\n"
                "Use the conversation history to resolve references. The session identity is "
                "context only; do not apply access-control or policy rules.\n\n"
                f"Session role: {sequence.role}\n"
                f"Session user id: {sequence.user_id}\n"
                f"Conversation history: {json.dumps(history_payload, ensure_ascii=False)}\n"
                f"Current request: {nlq}\n\n"
                f"Full database schema:\n{full_schema}"
            )

            generation_started = time.perf_counter()
            generation_error: str | None = None
            raw_sql: str | None = None
            usage: dict[str, Any] = {}
            try:
                llm = runner._thread_llms().get("M6")
                if llm is None:
                    generation_error = "LLM_NOT_CONFIGURED"
                else:
                    llm_config = self._effective_llm_config(config, "M6")
                    response = llm.generate_text(
                        prompt,
                        temperature=float(llm_config.get("temperature", 0.0)),
                        max_output_tokens=int(llm_config.get("max_output_tokens", 1200)),
                    )
                    raw_sql = _clean_direct_sql(response.text)
                    response_usage = getattr(response, "usage", {})
                    if isinstance(response_usage, Mapping):
                        usage = dict(response_usage)
                    if raw_sql is None:
                        generation_error = "EMPTY_SQL"
            except Exception:  # noqa: BLE001 - provider details stay server-side
                generation_error = "DIRECT_SQL_GENERATION_FAILED"

            generation_result = ModuleResult(
                module_id="M6",
                stage="direct_sql_generator",
                decision="ALLOW" if raw_sql else "ERROR",
                artifact={
                    "raw_sql": raw_sql,
                    "raw_sql_chars": len(raw_sql) if raw_sql else 0,
                    "schema_scope": "full",
                    "history_turn_count": len(history_payload),
                    "security_modules_bypassed": True,
                },
                audit={"mode": DIRECT_MODE},
                latency_ms=(time.perf_counter() - generation_started) * 1000,
                llm_usage=usage,
                error=generation_error,
            )
            runner._write_module_event_log(
                runtime_turn,
                "M6",
                {
                    "role": sequence.role,
                    "user_id": sequence.user_id,
                    "nlq": nlq,
                    "history_turn_count": len(history_payload),
                    "schema_scope": "full",
                    "security_modules_bypassed": True,
                },
                generation_result,
            )

            trace = [memory_result, generation_result]
            execution_result = None
            final_sql = raw_sql
            blocked_at = None
            decision = "ALLOW"
            error = None
            executed = False
            rows: Any = None
            columns: list[str] = []
            if generation_result.decision == "ERROR":
                decision = "ERROR"
                blocked_at = "M6"
                error = generation_result.error
            else:
                execution_result = readonly_executor.run(final_sql, runner.db)
                execution_result.stage = "direct_sql_executor"
                runner._write_module_event_log(
                    runtime_turn,
                    "X1",
                    {"raw_sql": final_sql, "security_modules_bypassed": True},
                    execution_result,
                )
                trace.append(execution_result)
                executed = bool(execution_result.artifact.get("executed"))
                rows = execution_result.artifact.get("rows")
                columns = list(execution_result.artifact.get("columns") or [])
                if execution_result.decision == "ERROR":
                    decision = "ERROR"
                    blocked_at = "X1"
                    error = execution_result.error

            output = MethodTurnOutput(
                run_id=run_id,
                setting_id=DIRECT_SETTING_ID,
                sequence_id=sequence.sample_id,
                sample_id=sequence.sample_id,
                turn_id=turn_number,
                role=sequence.role,
                user_id=sequence.user_id,
                nlq=nlq,
                decision=decision,
                blocked_at=blocked_at,
                raw_sql=raw_sql,
                final_sql=final_sql,
                executed=executed,
                execution_result_json=rows,
                execution_columns=columns,
                module_trace=trace,
                latency_ms=sum(float(item.latency_ms or 0.0) for item in trace),
                llm_usage=usage,
                error=error,
            )
            runner._write_output(output)
            runner._save_checkpoint(
                {f"{DIRECT_SETTING_ID}|{sequence.sample_id}|{turn_number}"}
            )
        except BaseException as exc:
            primary_error = exc
        finally:
            if runner is not None:
                try:
                    error_summary = runner._write_runtime_error_summary()
                except Exception:
                    if primary_error is None:
                        primary_error = RuntimeAdapterError(
                            "runtime error summary could not be persisted"
                        )
        if primary_error is not None:
            raise primary_error

        rows = _read_jsonl(output_dir / "runtime" / "raw_turn_outputs.jsonl")
        if (
            len(rows) != 1
            or rows[0].get("setting_id") != DIRECT_SETTING_ID
            or rows[0].get("sample_id") != sequence.sample_id
            or rows[0].get("turn_id") != turn_number
        ):
            raise RuntimeAdapterError("direct turn output is missing or has invalid identity")

        return ExecutionResult(
            run_id=run_id,
            scenario_key=INTERACTIVE_SCENARIO_KEY,
            sample_id=sequence.sample_id,
            through_turn=turn_number,
            output_dir=output_dir,
            manifest_path=output_dir / "run_manifest.json",
            runtime_dir=output_dir / "runtime",
            final_output=rows[0],
            error_summary=error_summary,
        )


__all__ = [
    "DIRECT_MODE",
    "DIRECT_MODULES",
    "DIRECT_SETTING_ID",
    "ExecutionResult",
    "INTERACTIVE_SAMPLE_ID",
    "INTERACTIVE_SCENARIO_KEY",
    "MAX_CHAT_QUERY_CHARS",
    "MAX_CHAT_TURNS",
    "REQUIRED_MODULES",
    "ReadinessResult",
    "RuntimeAdapterError",
    "TRUSTEDSQL_MODULES",
    "TRUSTEDSQL_MODE",
    "TRUSTEDSQL_SETTING_ID",
    "TrustedSqlRuntimeAdapter",
]
