from __future__ import annotations

import csv
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from trustedsql.config import TrustedSqlConfig
from trustedsql.datasets.loader import load_sequences
from trustedsql.db.executor import DatabaseExecutor
from trustedsql.providers.client import create_llm_client
from trustedsql.modules import (
    access_planner,
    context_builder,
    intent_risk_guard,
    prompt_integrity_guard,
    readonly_executor,
    row_scope_verifier,
    sql_conformance_validator,
    sql_generator,
    table_column_access_validator,
)
from trustedsql.modules.common import merge_usage
from trustedsql.policy.index import load_policy_index
from trustedsql.preprocessing.compact_schema import parse_compact_schema_examples
from trustedsql.schemas import GenerationInput, MethodTurnOutput, RuntimeTurnInput, TurnHistoryItem
from trustedsql.sql.schema import load_schema_graph, role_authorized_compact_schema
from trustedsql.utils.hash import sha256_file
from trustedsql.utils.io import append_jsonl, read_csv, read_json, read_jsonl, write_csv, write_json, write_jsonl
from trustedsql_gnn.paths import GNNPaths
_MODULE_ORDER = {
    "C0": "001_C0_context_builder",
    "M1": "002_M1_prompt_integrity_guard",
    "M2": "003_M2_intent_risk_guard",
    "M3": "004_M3_access_planner",
    "M4": "005_M4_table_column_access_validator",
    "M5": "006_M5_row_scope_verifier",
    "M6": "007_M6_sql_generator",
    "M7": "008_M7_sql_conformance_validator",
    "X1": "009_X1_readonly_executor",
}



class MethodRunner:
    def __init__(self, config: TrustedSqlConfig, run_id: str, output_dir: Path | None = None) -> None:
        self.config = config
        self.run_id = run_id
        self.run_dir = (output_dir or config.output_dir / run_id).resolve()
        self.runtime_dir = self.run_dir / "runtime"
        self.module_event_log_path = self.runtime_dir / "module_events.jsonl"
        self.schema_graph = load_schema_graph(config.ddl_path)
        self.compact_schema = _load_compact_schema(config)
        self.compact_schema_examples = parse_compact_schema_examples(self.compact_schema)
        self.policy_index = load_policy_index(config.policy_index_path, config.role_access_matrix_path)
        self._role_authorized_schemas: dict[str, str] = {}
        self._role_authorized_schema_stats: dict[str, dict[str, int]] = {}
        self._role_schema_lock = threading.Lock()
        # Per-thread LLM storage (genai.Client is NOT thread-safe)
        self._llm_local = threading.local()
        execution = config.raw.get("execution", {})
        self._execution_seq = 0
        # Thread safety
        parallel_cfg = config.raw.get("runtime", {}).get("parallel", {})
        self._max_workers = max(1, int(parallel_cfg.get("max_workers", 5)))
        self._execution_seq_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._checkpoint_lock = threading.Lock()
        self.db = DatabaseExecutor(
            config.raw.get("database", {}).get("url") or None,
            statement_timeout_ms=int(execution.get("statement_timeout_ms", 3000)),
            max_rows=int(execution.get("max_result_rows", 200)),
        )

    def _thread_llms(self) -> dict[str, Any]:
        """Return per-thread LLM instances (provider clients may not be thread-safe)."""
        llms = getattr(self._llm_local, "llms", None)
        if llms is None:
            llms = {
                module_id: create_llm_client(self.config.module_llm_config(module_id))
                for module_id in ("M1", "M2", "M3", "M6")
            }
            self._llm_local.llms = llms
        return llms

    def write_run_manifest(
        self,
        *,
        settings: dict[str, Any],
        sequences: list[Any],
        max_samples: int | None,
    ) -> dict[str, Any]:
        snapshot = {
            "run_id": self.run_id,
            "runtime_kind": "trustedsql",
            "protocol": "trustedsql-refusal-v1",
            "module_models": _resolved_module_models(self.config, settings),
            "resolved_config": {
                "runtime": _redact_config(self.config.raw),
                "datasets": _redact_config(self.config.datasets),
                "modules": _redact_config(_module_manifest_config(self.config.modules)),
                "settings": _redact_config(settings),
                "module_llm": {
                    module_id: _redact_config(self.config.module_llm_config(module_id))
                    for module_id in ("M1", "M3", "M6")
                    if any(module_id in setting.get("modules", []) for setting in settings.values())
                },
            },
            "resource_fingerprints": _runtime_resource_fingerprints(self.config, settings),
            "benchmark_selection": {
                "max_samples_per_dataset": max_samples,
                "sequence_count": len(sequences),
                "turn_count": sum(len(sequence.turns) for sequence in sequences),
                "expected_turns": [
                    {
                        "setting_id": setting_id,
                        "sample_id": str(sequence.sample_id),
                        "turn_id": int(turn.turn_id),
                    }
                    for setting_id in settings
                    for sequence in sequences
                    for turn in sequence.turns
                ],
            },
            "m2_intent_engine": _m2_intent_engine_manifest(self.config),
            "data_flow_log_manifest": {
                "description": "Module log files written under runtime/module_logs/, numbered by data flow execution order",
                "modules": [
                    {"order": 1, "file": "001_C0_context_builder.jsonl", "module": "C0", "description": "Runtime context: role, user_id, nlq, schema stats, policy stats"},
                    {"order": 2, "file": "002_M1_prompt_integrity_guard.jsonl", "module": "M1", "description": "Prompt integrity: heuristic regex hits, LLM classifier verdict"},
                    {"order": 3, "file": "003_M2_intent_risk_guard.jsonl", "module": "M2", "description": "Intent-GNN: primary_intent, scope, target_relation, security_signals, downstream_hint"},
                    {"order": 4, "file": "004_M3_access_planner.jsonl", "module": "M3", "description": "Resource planner: requested resources, scope, target and query predicates"},
                    {"order": 5, "file": "005_M4_table_column_access_validator.jsonl", "module": "M4", "description": "Deterministic table/column authorization and ResourceContract"},
                    {"order": 6, "file": "006_M5_row_scope_verifier.jsonl", "module": "M5", "description": "Target-only row-scope proof and VerifiedAuthorization"},
                    {"order": 7, "file": "007_M6_sql_generator.jsonl", "module": "M6", "description": "SQL generator using full runtime history, role-authorized schema, and optional guide"},
                    {"order": 8, "file": "008_M7_sql_conformance_validator.jsonl", "module": "M7", "description": "SQL SELECT-safety and role table/column conformance"},
                    {"order": 9, "file": "009_X1_readonly_executor.jsonl", "module": "X1", "description": "SQL executor: execution result, row_count, execution_time_ms"},
                ],
            },
            "module_event_log_path": "runtime/module_events.jsonl",
        }
        snapshot_path = self.run_dir / "run_manifest.json"
        if snapshot_path.exists():
            existing = read_json(snapshot_path)
            if existing != snapshot:
                raise RuntimeError(
                    f"Runtime configuration mismatch for existing run_id {self.run_id}. "
                    "Use a new run_id instead of replacing runtime provenance."
                )
            return existing
        raw_output = self.run_dir / "runtime" / "raw_turn_outputs.jsonl"
        if raw_output.exists() and raw_output.stat().st_size > 0:
            raise RuntimeError(
                f"Existing runtime evidence has no run_manifest.json: {raw_output}. "
                "Use a new run_id; legacy evidence must not be merged into the cache-free protocol."
            )
        write_json(snapshot_path, snapshot)
        return snapshot

    def run(
        self,
        selected_settings: list[str] | None = None,
        max_samples: int | None = None,
        resume: bool = True,
        rerun_api_429: bool = False,
    ) -> dict[str, Any]:
        sequences = load_sequences(self.config.datasets, self.config.project_root, max_samples=max_samples)
        settings = self.config.enabled_settings(selected_settings)
        self.write_run_manifest(settings=settings, sequences=sequences, max_samples=max_samples)
        completed: set[str] = self._completed_keys() if resume else set()
        completed_rows: dict[str, dict[str, Any]] = self._completed_rows() if resume else {}
        if resume and rerun_api_429:
            direct_retry_keys = self._api_429_retry_keys(completed_rows, set(settings))
            retry_keys = self._expand_retry_keys_to_downstream_turns(direct_retry_keys, sequences, set(settings))
            if retry_keys:
                self._compact_runtime_outputs_for_retry(retry_keys, direct_retry_keys)
                completed = self._completed_keys()
                completed_rows = self._completed_rows()
        for setting_id, setting in settings.items():
            modules = list(setting.get("modules", []))
            # Partition: sequences that need running vs already completed
            pending_sequences = []
            for sequence in sequences:
                turn_keys = [f"{setting_id}|{sequence.sample_id}|{turn.turn_id}" for turn in sequence.turns]
                if resume and all(key in completed for key in turn_keys):
                    continue
                pending_sequences.append(sequence)
            if not pending_sequences:
                continue
            # Parallel when >1 pending sequence and max_workers >1
            # API 429 retry is per-sequence â€” works inside worker threads too
            fresh_sequences = []
            partial_sequences = []
            for sequence in pending_sequences:
                turn_keys = [f"{setting_id}|{sequence.sample_id}|{turn.turn_id}" for turn in sequence.turns]
                target = partial_sequences if any(key in completed for key in turn_keys) else fresh_sequences
                target.append(sequence)

            if self._max_workers > 1 and len(fresh_sequences) > 1:
                self._run_sequences_parallel(setting_id, setting, fresh_sequences, modules, completed, completed_rows)
            elif fresh_sequences:
                self._run_sequences_serial(setting_id, setting, fresh_sequences, modules, completed, completed_rows)
            if partial_sequences:
                self._run_sequences_serial(setting_id, setting, partial_sequences, modules, completed, completed_rows)
        return self._write_runtime_error_summary()

    # ------------------------------------------------------------------
    # Serial path (single sequence, or API 429 retry)
    # ------------------------------------------------------------------

    def _run_sequences_serial(
        self,
        setting_id: str,
        setting: dict[str, Any],
        sequences: list[Any],
        modules: list[str],
        completed: set[str],
        completed_rows: dict[str, dict[str, Any]],
    ) -> None:
        history_by_sequence: dict[str, list[TurnHistoryItem]] = {}
        for sequence in sequences:
            turn_keys = [f"{setting_id}|{sequence.sample_id}|{turn.turn_id}" for turn in sequence.turns]
            seq_key = sequence.sample_id
            if self._api_429_retry_enabled():
                sequence_keys = set(turn_keys)
                if sequence_keys & completed:
                    self._compact_runtime_outputs_for_retry(sequence_keys, set())
                    completed = self._completed_keys()
                    completed_rows = self._completed_rows()
                outputs = self._run_sequence_with_api_429_retry(setting_id, setting, sequence, modules)
                history = history_by_sequence.setdefault(seq_key, [])
                for output in outputs:
                    self._write_output(output)
                    history.append(_history_item_from_output(output))
                    completed.add(f"{setting_id}|{sequence.sample_id}|{output.turn_id}")
                    self._save_checkpoint(completed)
                continue
            history = history_by_sequence.setdefault(seq_key, [])
            for turn in sequence.turns:
                key = f"{setting_id}|{sequence.sample_id}|{turn.turn_id}"
                if key in completed:
                    row = completed_rows.get(key)
                    if row:
                        history.append(_history_item_from_row(row, turn.nlq))
                    continue
                output = self._run_turn_for_sequence(
                    setting_id=setting_id,
                    sequence=sequence,
                    turn=turn,
                    seq_key=seq_key,
                    history=history,
                    modules=modules,
                )
                self._write_output(output)
                history.append(
                    TurnHistoryItem(
                        turn_id=turn.turn_id, nlq=turn.nlq, decision=output.decision,
                        raw_sql=output.raw_sql, final_sql=output.final_sql,
                        executed=output.executed, execution_result_json=output.execution_result_json,
                        blocked_at=output.blocked_at,
                    )
                )
                completed.add(key)
                self._save_checkpoint(completed)

    # ------------------------------------------------------------------
    # Parallel path â€” one sequence per thread (turns within sequence are serial)
    # ------------------------------------------------------------------

    def _run_sequences_parallel(
        self,
        setting_id: str,
        setting: dict[str, Any],
        sequences: list[Any],
        modules: list[str],
        completed: set[str],
        completed_rows: dict[str, dict[str, Any]],
    ) -> None:
        """Fan out sequences across threads. Each sequence processes its turns sequentially."""
        futures: dict[Any, tuple[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            for sequence in sequences:
                # Build initial history from prior completed rows (if resuming)
                history: list[TurnHistoryItem] = []
                for turn in sequence.turns:
                    key = f"{setting_id}|{sequence.sample_id}|{turn.turn_id}"
                    if key in completed:
                        row = completed_rows.get(key)
                        if row:
                            history.append(_history_item_from_row(row, turn.nlq))
                future = executor.submit(
                    self._run_sequence_full,
                    setting_id, sequence, seq_key=sequence.sample_id,
                    history=list(history), modules=modules,
                )
                futures[future] = (sequence.sample_id, sequence)

            for future in as_completed(futures):
                try:
                    outputs = future.result()
                    sample_id = futures[future][0]
                    for output in outputs:
                        self._write_output(output)
                        completed.add(f"{setting_id}|{sample_id}|{output.turn_id}")
                    self._save_checkpoint(completed)
                except Exception as exc:
                    print(f"[ParallelRunner] sequence {futures[future][0]} failed: {exc}")
                    # Write ERROR outputs for remaining turns
                    seq = futures[future][1]
                    for turn in seq.turns:
                        key = f"{setting_id}|{seq.sample_id}|{turn.turn_id}"
                        if key not in completed:
                            error_output = MethodTurnOutput(
                                run_id=self.run_id, setting_id=setting_id,
                                sequence_id=seq.sample_id if seq.turn_type == "multi" else None,
                                sample_id=seq.sample_id, turn_id=turn.turn_id,
                                role=seq.role, user_id=seq.user_id, nlq=turn.nlq,
                                decision="ERROR", blocked_at="PARALLEL_EXECUTOR", error=str(exc),
                            )
                            self._write_output(error_output)
                            completed.add(key)
                    self._save_checkpoint(completed)

    def _run_sequence_full(
        self,
        setting_id: str,
        sequence: Any,
        seq_key: str,
        history: list[TurnHistoryItem],
        modules: list[str],
    ) -> list[MethodTurnOutput]:
        """Process all turns of a sequence sequentially (with optional 429 retry)."""
        if self._api_429_retry_enabled():
            return self._run_sequence_with_api_429_retry(
                setting_id,
                {"modules": modules},
                sequence,
                modules,
            )
        outputs: list[MethodTurnOutput] = []
        for turn in sequence.turns:
            output = self._run_turn_for_sequence(
                setting_id=setting_id,
                sequence=sequence,
                turn=turn,
                seq_key=seq_key,
                history=list(history),
                modules=modules,
            )
            outputs.append(output)
            history.append(
                TurnHistoryItem(
                    turn_id=turn.turn_id, nlq=turn.nlq, decision=output.decision,
                    raw_sql=output.raw_sql, final_sql=output.final_sql,
                    executed=output.executed, execution_result_json=output.execution_result_json,
                    blocked_at=output.blocked_at,
                )
            )
            # A denied or failed turn remains in runtime history with no SQL/result.
            # Later user turns still run so serial, parallel, and retry paths have
            # identical conversation semantics.
            if output.decision == "ERROR":
                if _is_api_429_output(output):
                    break  # API quota exhausted â€” stop and retry
                # Non-429 errors: record and continue to next turn
                continue
        return outputs

    def _run_turn_for_sequence(
        self,
        setting_id: str,
        sequence: Any,
        turn: Any,
        seq_key: str,
        history: list[TurnHistoryItem],
        modules: list[str],
    ) -> MethodTurnOutput:
        """Build RuntimeTurnInput and delegate to _run_turn."""
        runtime_input = RuntimeTurnInput(
            run_id=self.run_id,
            setting_id=setting_id,
            sequence_id=sequence.sample_id if sequence.turn_type == "multi" else None,
            sample_id=sequence.sample_id,
            turn_id=turn.turn_id,
            role=sequence.role,
            user_id=sequence.user_id,
            nlq=turn.nlq,
            history=list(history),
        )
        return self._run_turn(runtime_input, modules)

    def _api_429_retry_enabled(self) -> bool:
        retry_cfg = self.config.raw.get("runtime", {}).get("api_429_retry", {})
        return bool(retry_cfg.get("enabled", False))

    def _api_429_retry_config(self) -> dict[str, Any]:
        retry_cfg = dict(self.config.raw.get("runtime", {}).get("api_429_retry", {}) or {})
        retry_cfg.setdefault("max_sequence_attempts", 1)
        retry_cfg.setdefault("backoff_seconds", [])
        return retry_cfg

    def _run_sequence_with_api_429_retry(
        self,
        setting_id: str,
        setting: dict[str, Any],
        sequence: Any,
        modules: list[str],
    ) -> list[MethodTurnOutput]:
        retry_cfg = self._api_429_retry_config()
        max_attempts = max(1, int(retry_cfg.get("max_sequence_attempts", 1)))
        backoff_seconds = list(retry_cfg.get("backoff_seconds") or [])
        last_outputs: list[MethodTurnOutput] = []
        for attempt in range(1, max_attempts + 1):
            outputs = self._run_sequence_attempt(setting_id, sequence, modules)
            last_outputs = outputs
            api_429_output = next((output for output in outputs if _is_api_429_output(output)), None)
            if api_429_output is None:
                return outputs
            event = {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "run_id": self.run_id,
                "setting_id": setting_id,
                "sequence_id": sequence.sample_id if sequence.turn_type == "multi" else None,
                "sample_id": sequence.sample_id,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "failed_turn_id": api_429_output.turn_id,
                "blocked_at": api_429_output.blocked_at,
                "error_type": "API_429",
                "error": _api_429_error_text(api_429_output),
                "action": "retry_sequence" if attempt < max_attempts else "max_attempts_exhausted",
            }
            print(
                "[API 429] "
                f"setting={setting_id} sample={sequence.sample_id} turn={api_429_output.turn_id} "
                f"module={api_429_output.blocked_at} attempt={attempt}/{max_attempts}"
            )
            if attempt < max_attempts:
                wait_seconds = _backoff_for_attempt(backoff_seconds, attempt)
                event["backoff_seconds"] = wait_seconds
                append_jsonl(self.run_dir / "runtime" / "retry_events.jsonl", event)
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                continue
            append_jsonl(self.run_dir / "runtime" / "retry_events.jsonl", event)
            return _complete_sequence_after_api_429_exhausted(
                run_id=self.run_id,
                setting_id=setting_id,
                sequence=sequence,
                outputs=outputs,
                failed_output=api_429_output,
            )
        return last_outputs

    def _run_sequence_attempt(
        self,
        setting_id: str,
        sequence: Any,
        modules: list[str],
    ) -> list[MethodTurnOutput]:
        history: list[TurnHistoryItem] = []
        outputs: list[MethodTurnOutput] = []
        for turn in sequence.turns:
            runtime_input = RuntimeTurnInput(
                run_id=self.run_id,
                setting_id=setting_id,
                sequence_id=sequence.sample_id if sequence.turn_type == "multi" else None,
                sample_id=sequence.sample_id,
                turn_id=turn.turn_id,
                role=sequence.role,
                user_id=sequence.user_id,
                nlq=turn.nlq,
                history=list(history),
            )
            output = self._run_turn(runtime_input, modules)
            outputs.append(output)
            history.append(_history_item_from_output(output))
            if _is_api_429_output(output):
                break
        return outputs

    def _run_turn(
        self,
        turn: RuntimeTurnInput,
        modules: list[str],
    ) -> MethodTurnOutput:
        started = time.perf_counter()
        trace = []
        usage: dict[str, Any] = {}
        blocked_at = None
        raw_sql = None
        final_sql = None
        executed = False
        rows: Any = None
        execution_columns: list[str] = []
        error = None
        context = None
        access_plan = None
        resource = None
        generation = None
        verified_authorization = None
        m2_hint = None
        decision = "ALLOW"
        for module_id in modules:
            if module_id == "X1" and final_sql is None:
                final_sql = raw_sql
            if module_id == "M6" and generation is None:
                generation = self._generation_input(turn.setting_id, turn.role, access_plan, verified_authorization)
            module_input = self._module_input_snapshot(
                module_id=module_id,
                turn=turn,
                context=context,
                raw_sql=raw_sql,
                final_sql=final_sql,
                access_plan=access_plan,
                resource=resource,
                generation=generation,
                m2_hint=m2_hint,
            )
            if module_id == "C0":
                context, result = context_builder.run(turn, self.schema_graph, self.policy_index, self.compact_schema)
            elif module_id == "M1":
                result = prompt_integrity_guard.run(context, self._thread_llms().get("M1"), self.config.modules.get("M1", {}))
            elif module_id == "M2":
                result = intent_risk_guard.run(context, self._thread_llms().get("M2"), self.config.modules.get("M2", {}))
                m2_hint = result.artifact.get("m2_downstream_hint") if result.artifact else None
            elif module_id == "M3":
                m3_config = dict(self.config.modules.get("M3", {}))
                if m3_config.get("log_prompts"):
                    m3_config["prompt_log_dir"] = str(self.run_dir / "debug" / "prompts" / "m3")
                access_plan, result = access_planner.run(context, self._thread_llms().get("M3"), m3_config, m2_hint=m2_hint)
            elif module_id == "M4":
                access_plan, resource, result = table_column_access_validator.run(
                    context,
                    access_plan,
                    m2_hint=m2_hint,
                )
            elif module_id == "M5":
                _, verified_authorization, result = row_scope_verifier.run(context, resource, self.db)
            elif module_id == "M6":
                raw_sql, result = sql_generator.run(context, generation, self._thread_llms().get("M6"), self.config.modules.get("M6", {}))
                result.audit["role_authorized_schema"] = dict(self._role_authorized_schema_stats[turn.role])
            elif module_id == "M7":
                validation, result = sql_conformance_validator.run(context, raw_sql, generation)
                final_sql = validation.final_sql if validation else None
            elif module_id == "X1":
                result = readonly_executor.run(final_sql, self.db)
                executed = bool(result.artifact.get("executed"))
                rows = result.artifact.get("rows")
                execution_columns = list(result.artifact.get("columns") or [])
            else:
                raise ValueError(f"Unknown module {module_id}")
            self._write_module_event_log(turn, module_id, module_input, result)
            trace.append(result)
            merge_usage(usage, result.llm_usage)
            if result.decision == "DENY":
                decision = "DENY"
                blocked_at = result.module_id
                break
            if result.decision == "ERROR":
                decision = "ERROR"
                blocked_at = result.module_id
                error = result.error
                break
        return MethodTurnOutput(
            run_id=self.run_id,
            setting_id=turn.setting_id,
            sequence_id=turn.sequence_id,
            sample_id=turn.sample_id,
            turn_id=turn.turn_id,
            role=turn.role,
            user_id=turn.user_id,
            nlq=turn.nlq,
            decision=decision,
            blocked_at=blocked_at,
            raw_sql=raw_sql,
            final_sql=final_sql,
            executed=executed,
            execution_result_json=rows,
            execution_columns=execution_columns,
            module_trace=trace,
            latency_ms=(time.perf_counter() - started) * 1000,
            llm_usage=usage,
            error=error,
        )

    def _generation_input(
        self,
        setting_id: str,
        role: str,
        access_plan: Any,
        verified_authorization: Any,
    ) -> GenerationInput:
        return GenerationInput(
            role_authorized_schema=self._role_authorized_schema(role),
            m5_guide=self._m5_generation_guide(verified_authorization),
        )

    def _m5_generation_guide(self, authorization: Any) -> dict[str, Any] | None:
        value = self._dataclass_dict(authorization)
        if not value:
            return None
        return {
            "scope_type": value.get("scope_type"),
            "mandatory_scope_predicates": list(value.get("current_user_bindings") or []),
            "verified_target_predicates": list(value.get("verified_targets") or []),
        }

    def _role_authorized_schema(self, role: str) -> str:
        with self._role_schema_lock:
            if role not in self._role_authorized_schemas:
                ddl = role_authorized_compact_schema(
                    self.schema_graph,
                    self.policy_index.role_access_matrix,
                    role,
                    examples=self.compact_schema_examples,
                )
                role_tables = self.policy_index.role_access_matrix.get(role, {})
                self._role_authorized_schemas[role] = ddl
                self._role_authorized_schema_stats[role] = {
                    "table_count": sum(1 for table in role_tables if self.schema_graph.has_table(table)),
                    "column_count": sum(
                        1
                        for table, columns in role_tables.items()
                        for column in columns
                        if self.schema_graph.has_column(table, column)
                    ),
                    "character_count": len(ddl),
                    "example_column_count": sum(
                        1
                        for table, columns in role_tables.items()
                        for column in columns
                        if (table.lower(), column.lower()) in self.compact_schema_examples
                    ),
                }
            return self._role_authorized_schemas[role]

    def _module_input_snapshot(
        self,
        *,
        module_id: str,
        turn: RuntimeTurnInput,
        context: Any,
        raw_sql: str | None,
        final_sql: str | None,
        access_plan: Any,
        resource: Any,
        generation: Any,
        m2_hint: dict[str, Any] | None,
    ) -> dict[str, Any]:
        common = {
            "run_id": turn.run_id,
            "setting_id": turn.setting_id,
            "sequence_id": turn.sequence_id,
            "sample_id": turn.sample_id,
            "turn_id": turn.turn_id,
            "role": turn.role,
            "user_id": turn.user_id,
            "nlq": turn.nlq,
            "history": [item.to_dict() for item in turn.history],
        }
        if module_id == "C0":
            return {
                **common,
                "schema": {
                    "ddl_path": str(self.config.ddl_path),
                    "compact_schema_path": str(self.config.compact_schema_path),
                    "compact_schema_chars": len(self.compact_schema),
                    "table_count": len(self.schema_graph.table_columns),
                    "foreign_key_count": len(self.schema_graph.foreign_keys),
                },
                "policy": {
                    "policy_index_path": str(self.config.policy_index_path),
                    "role_access_matrix_path": str(self.config.role_access_matrix_path),
                },
            }
        if module_id in {"M1", "M2"}:
            module_config = dict(self.config.modules.get(module_id, {}) or {})
            if module_id == "M1":
                module_config.pop("history_turn_limit", None)
            return {
                **self._context_input_snapshot(context),
                "module_config": module_config,
                "llm": self.config.module_llm_config(module_id),
            }
        if module_id == "M3":
            policy_summary = context.policy_index.policy_summary_for_role(context.role) if context else ""
            m3_config = dict(self.config.modules.get("M3", {}))
            if m3_config.get("log_prompts"):
                m3_config["prompt_log_dir"] = str(self.run_dir / "debug" / "prompts" / "m3")
            return {
                **self._context_input_snapshot(context, include_compact_schema=True),
                "m2_downstream_hint": m2_hint,
                "policy_summary": policy_summary,
                "module_config": m3_config,
                "llm": self.config.module_llm_config("M3"),
            }
        if module_id == "M4":
            return {
                **self._context_input_snapshot(context),
                "access_plan": self._dataclass_dict(access_plan),
                "m2_downstream_hint": m2_hint,
            }
        if module_id == "M5":
            return {
                **self._context_input_snapshot(context),
                "resource_contract": self._dataclass_dict(resource),
            }
        if module_id == "M6":
            return {
                **self._context_input_snapshot(context),
                "generation_input": self._dataclass_dict(generation),
                "module_config": self.config.modules.get("M6", {}),
                "llm": self.config.module_llm_config("M6"),
            }
        if module_id == "M7":
            return {
                **self._context_input_snapshot(context),
                "raw_sql": raw_sql,
                "generation_input": self._dataclass_dict(generation),
            }
        if module_id == "X1":
            return {
                **self._context_input_snapshot(context),
                "final_sql": final_sql,
            }
        return common

    def _context_input_snapshot(
        self,
        context: Any,
        include_compact_schema: bool = False,
    ) -> dict[str, Any]:
        if context is None:
            return {}
        snapshot = {
            "run_id": context.run_id,
            "setting_id": context.setting_id,
            "sequence_id": context.sequence_id,
            "sample_id": context.sample_id,
            "turn_id": context.turn_id,
            "role": context.role,
            "user_id": context.user_id,
            "nlq": context.nlq,
            "history": [item.to_dict() for item in context.history],
            "schema_ddl_chars": len(context.schema_ddl or ""),
            "compact_schema_chars": len(context.compact_schema or ""),
        }
        if include_compact_schema:
            snapshot["compact_schema"] = context.compact_schema
        return snapshot

    def _dataclass_dict(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "__dict__"):
            return dict(value.__dict__)
        return value

    def _write_module_event_log(
        self,
        turn: RuntimeTurnInput,
        module_id: str,
        module_input: dict[str, Any],
        result: Any,
    ) -> None:
        with self._write_lock:
            append_jsonl(
                self.module_event_log_path,
                {
                    "created_at": datetime.now().isoformat(timespec="microseconds"),
                    "run_id": self.run_id,
                    "setting_id": turn.setting_id,
                    "sequence_id": turn.sequence_id,
                    "sample_id": turn.sample_id,
                    "turn_id": turn.turn_id,
                    "module_id": module_id,
                    "input": module_input,
                    "output": result.to_dict() if hasattr(result, "to_dict") else result,
                },
            )

    def _next_execution_seq(self) -> int:
        with self._execution_seq_lock:
            self._execution_seq += 1
            return self._execution_seq

    def _save_checkpoint(self, completed: set[str]) -> None:
        with self._checkpoint_lock:
            write_json(self.run_dir / "runtime" / "checkpoint.json", {"completed": sorted(completed)})

    def _write_output(self, output: MethodTurnOutput) -> None:
        row = output.to_dict()
        module_log_dir = self.run_dir / "runtime" / "module_logs"
        shared = {
            "setting_id": output.setting_id,
            "sample_id": output.sample_id,
            "turn_id": output.turn_id,
            "role": output.role,
            "user_id": output.user_id,
        }
        turn_module_index: dict[str, int] = {}
        for event in row["module_trace"]:
            event = dict(event)
            event.update(shared)
            module_filename = _MODULE_ORDER.get(event["module_id"], f"999_{event['module_id']}")
            module_id = event["module_id"]
            turn_module_index.setdefault(module_id, 0)
            turn_module_index[module_id] += 1
            seq = self._next_execution_seq()
            log_row = {
                "execution_seq": f"{seq:03d}",
                "module_exec_order": module_filename[:3],
                "setting_id": event["setting_id"],
                "sample_id": event["sample_id"],
                "turn_id": event["turn_id"],
                "turn_module_ordinal": turn_module_index[module_id],
                "role": event.get("role"),
                "user_id": event.get("user_id"),
                "module_id": event["module_id"],
                "stage": event["stage"],
                "decision": event["decision"],
                "artifact": event.get("artifact", {}),
                "audit": event.get("audit", {}),
                "raw_objects": event.get("raw_objects", {}),
                "latency_ms": event.get("latency_ms"),
                "llm_usage": event.get("llm_usage", {}),
                "error": event.get("error"),
            }
            with self._write_lock:
                append_jsonl(module_log_dir / f"{module_filename}.jsonl", log_row)
        runtime_rows = [
            {
                "setting_id": output.setting_id,
                "sample_id": output.sample_id,
                "turn_id": output.turn_id,
                "decision": output.decision,
                "blocked_at": output.blocked_at,
                "executed": output.executed,
                "latency_ms": round(output.latency_ms, 3),
                "input_tokens": output.llm_usage.get("prompt_token_count"),
                "output_tokens": output.llm_usage.get("candidates_token_count"),
                "total_tokens": output.llm_usage.get("total_token_count"),
            }
        ]
        with self._write_lock:
            append_jsonl(self.run_dir / "runtime" / "raw_turn_outputs.jsonl", row)
            existing = []
            csv_path = self.run_dir / "runtime" / "turn_runtime.csv"
            if csv_path.exists():
                import csv
                with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    existing = list(csv.DictReader(handle))
            write_csv(csv_path, existing + runtime_rows)

    def _completed_keys(self) -> set[str]:
        path = self.run_dir / "runtime" / "checkpoint.json"
        if not path.exists():
            return set()
        try:
            return set(read_json(path).get("completed", []))
        except Exception:  # noqa: BLE001
            return set()

    def _completed_rows(self) -> dict[str, dict[str, Any]]:
        rows = read_jsonl(self.run_dir / "runtime" / "raw_turn_outputs.jsonl")
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = f"{row.get('setting_id')}|{row.get('sample_id')}|{row.get('turn_id')}"
            out[key] = row
        return out

    def _api_429_retry_keys(self, completed_rows: dict[str, dict[str, Any]], selected_settings: set[str]) -> set[str]:
        retry_keys: set[str] = set()
        for key, row in completed_rows.items():
            setting_id = str(row.get("setting_id") or "")
            if setting_id not in selected_settings:
                continue
            if _is_api_429_row(row):
                retry_keys.add(key)
        return retry_keys

    def _expand_retry_keys_to_downstream_turns(
        self,
        retry_keys: set[str],
        sequences: list[Any],
        selected_settings: set[str],
    ) -> set[str]:
        if not retry_keys:
            return set()
        expanded = set(retry_keys)
        retry_by_setting_sample: dict[tuple[str, str], int] = {}
        for key in retry_keys:
            setting_id, sample_id, turn_id_text = key.split("|", 2)
            if setting_id not in selected_settings:
                continue
            try:
                turn_id = int(turn_id_text)
            except ValueError:
                continue
            current = retry_by_setting_sample.get((setting_id, sample_id))
            retry_by_setting_sample[(setting_id, sample_id)] = turn_id if current is None else min(current, turn_id)
        for sequence in sequences:
            for setting_id in selected_settings:
                first_retry_turn = retry_by_setting_sample.get((setting_id, sequence.sample_id))
                if first_retry_turn is None:
                    continue
                for turn in sequence.turns:
                    expanded.add(f"{setting_id}|{sequence.sample_id}|{turn.turn_id}")
        return expanded

    def _compact_runtime_outputs_for_retry(self, retry_keys: set[str], direct_retry_keys: set[str]) -> None:
        runtime_dir = self.run_dir / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)

        raw_path = runtime_dir / "raw_turn_outputs.jsonl"
        if raw_path.exists():
            raw_rows = [row for row in read_jsonl(raw_path) if _runtime_key(row) not in retry_keys]
            write_jsonl(raw_path, raw_rows)

        events_path = runtime_dir / "module_events.jsonl"
        if events_path.exists():
            event_rows = [
                row for row in read_jsonl(events_path) if _runtime_key(row) not in retry_keys
            ]
            write_jsonl(events_path, event_rows)

        module_log_dir = runtime_dir / "module_logs"
        if module_log_dir.exists():
            for log_file in module_log_dir.glob("*.jsonl"):
                try:
                    existing_rows = [row for row in read_jsonl(log_file) if _runtime_key(row) not in retry_keys]
                    write_jsonl(log_file, existing_rows)
                except Exception:  # noqa: BLE001
                    pass

        csv_path = runtime_dir / "turn_runtime.csv"
        if csv_path.exists():
            rows = read_csv(csv_path)
            fieldnames: list[str] = []
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                fieldnames = list((csv.DictReader(handle).fieldnames or []))
            kept_rows = [row for row in rows if _runtime_key(row) not in retry_keys]
            write_csv(csv_path, kept_rows, fieldnames=fieldnames or None)

        completed = sorted(key for key in self._completed_keys() if key not in retry_keys)
        write_json(runtime_dir / "checkpoint.json", {"completed": completed})
        write_json(
            runtime_dir / "rerun_api_429_manifest.json",
            {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "direct_api_429_key_count": len(direct_retry_keys),
                "rerun_key_count": len(retry_keys),
                "downstream_key_count": len(retry_keys - direct_retry_keys),
                "merge_policy": "in_place_compaction",
                "rerun_keys": sorted(retry_keys),
                "policy": "Rows with API 429 errors and downstream turns in the same setting/sequence are removed from the current runtime artifacts, then regenerated into the same run_id.",
            },
        )

    def _write_runtime_error_summary(self) -> dict[str, Any]:
        rows = read_jsonl(self.run_dir / "runtime" / "raw_turn_outputs.jsonl")
        retry_events = read_jsonl(self.run_dir / "runtime" / "retry_events.jsonl")
        errors = [row for row in rows if row.get("decision") == "ERROR" or row.get("error")]
        api_429 = [row for row in errors if _is_api_429_row(row)]
        summary = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "run_id": self.run_id,
            "runtime_rows": len(rows),
            "runtime_error_count": len(errors),
            "api_429_error_count": len(api_429),
            "api_429_retry_event_count": len(retry_events),
            "api_429_retry_sequence_count": len({f"{event.get('setting_id')}|{event.get('sample_id')}" for event in retry_events}),
            "api_429_errors": [
                {
                    "setting_id": row.get("setting_id"),
                    "sample_id": row.get("sample_id"),
                    "turn_id": row.get("turn_id"),
                    "blocked_at": row.get("blocked_at"),
                    "error": row.get("error"),
                }
                for row in api_429
            ],
            "error_groups": _error_groups(errors),
        }
        write_json(self.run_dir / "runtime" / "runtime_error_summary.json", summary)
        return summary


def _runtime_resource_fingerprints(
    config: TrustedSqlConfig,
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    resources: list[tuple[str, Path]] = [
        ("policy_index", config.policy_index_path),
        ("role_access_matrix", config.role_access_matrix_path),
        ("ddl", config.ddl_path),
        ("compact_schema", config.compact_schema_path),
    ]
    dataset_configs = config.datasets.get("datasets", config.datasets)
    for name, dataset in dataset_configs.items():
        if not dataset or dataset.get("enabled", True) is False:
            continue
        resources.append((f"benchmark:{name}", config.resolve_path(dataset.get("path", dataset.get("file", "")))))
    used_modules = {
        str(module_id)
        for setting in settings.values()
        for module_id in setting.get("modules", [])
    }
    for module_id, filename in {
        "M1": "m1_prompt_integrity_guard.txt",
        "M3": "m3_access_planner.txt",
        "M6": "m6_sql_generator.txt",
    }.items():
        if module_id in used_modules:
            resources.append((f"prompt:{module_id}", config.project_root / "src" / "trustedsql" / "prompts" / filename))
    if "M2" in used_modules:
        checkpoint = _m2_checkpoint_path(config)
        gnn_paths = GNNPaths.from_project_root(config.project_root, checkpoint_path=checkpoint)
        resources.extend(
            [
                ("gnn_checkpoint", checkpoint),
                (
                    "gnn_encoder_manifest",
                    config.project_root / "artifacts" / "models" / "intent_gnn" / "v1" / "encoder_manifest.json",
                ),
                ("gnn_text_encoder_weights", gnn_paths.encoder_dir / "model.safetensors"),
                ("gnn_text_encoder_vocab", gnn_paths.encoder_dir / "vocab.txt"),
            ]
        )
    return [
        {
            "name": name,
            "path": _portable_path(path, config.project_root),
            "sha256": sha256_file(path),
        }
        for name, path in resources
    ]


def _resolved_module_models(
    config: TrustedSqlConfig,
    settings: dict[str, Any],
) -> dict[str, str]:
    used_modules = {
        str(module_id)
        for setting in settings.values()
        for module_id in setting.get("modules", [])
    }
    models: dict[str, str] = {}
    for module_id in ("M1", "M3", "M6"):
        if module_id not in used_modules:
            continue
        model = config.module_llm_config(module_id).get("model")
        if model:
            models[module_id] = str(model)
    return models


def _portable_path(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(resolved)


def _redact_config(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(token in lowered for token in ("password", "secret", "api_key", "credential")):
        return "<redacted>"
    if lowered == "url" and isinstance(value, str) and "@" in value:
        return "<redacted>"
    if isinstance(value, dict):
        return {str(item_key): _redact_config(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact_config(item) for item in value]
    return value


def _module_manifest_config(modules: dict[str, Any]) -> dict[str, Any]:
    snapshot = {
        module_id: dict(module_config) if isinstance(module_config, dict) else module_config
        for module_id, module_config in modules.items()
    }
    m1_config = snapshot.get("M1")
    if isinstance(m1_config, dict):
        m1_config.pop("history_turn_limit", None)
    return snapshot


def _m2_intent_engine_manifest(config: TrustedSqlConfig) -> dict[str, Any]:
    module_cfg = dict(config.modules.get("M2", {}) or {})
    engine_cfg = dict(module_cfg.get("intent_gnn", {}) or {})
    checkpoint_path = _m2_checkpoint_path(config)
    return {
        "engine": module_cfg.get("engine", "trustedsql_m2_intent_gnn"),
        "mode": module_cfg.get("mode", engine_cfg.get("mode", "calibrated")),
        "checkpoint": _portable_path(checkpoint_path, config.project_root),
        "checkpoint_sha256": sha256_file(checkpoint_path) if checkpoint_path and checkpoint_path.exists() else None,
        "device": engine_cfg.get("device", module_cfg.get("device", "cpu")),
        "allow_hash_encoder": bool(engine_cfg.get("allow_hash_encoder", module_cfg.get("allow_hash_encoder", False))),
        "gnn_authority": False,
    }


def _m2_checkpoint_path(config: TrustedSqlConfig) -> Path:
    module_cfg = dict(config.modules.get("M2", {}) or {})
    engine_cfg = dict(module_cfg.get("intent_gnn", {}) or {})
    project_root_value = engine_cfg.get("project_root") or engine_cfg.get("package_root")
    project_root = Path(str(project_root_value)).resolve() if project_root_value else config.project_root
    configured = engine_cfg.get("checkpoint_path") or module_cfg.get("checkpoint_path")
    if configured:
        path = Path(str(configured))
        return (path if path.is_absolute() else project_root / path).resolve()
    return (project_root / "artifacts" / "models" / "intent_gnn" / "v1" / "best.pt").resolve()


def _load_compact_schema(config: TrustedSqlConfig) -> str:
    path = config.compact_schema_path
    if not path.exists():
        raise FileNotFoundError(
            "Missing compact schema prompt file. Run "
            "tools/preprocessing/build_compact_schema_prompt_vi.ipynb "
            f"or trustedsql.preprocessing.compact_schema first: {path}"
        )
    return path.read_text(encoding="utf-8-sig")


def _runtime_key(row: dict[str, Any]) -> str:
    return f"{row.get('setting_id')}|{row.get('sample_id')}|{row.get('turn_id')}"


def _history_item_from_row(row: dict[str, Any], fallback_nlq: str) -> TurnHistoryItem:
    return TurnHistoryItem(
        turn_id=int(row["turn_id"]),
        nlq=str(row.get("nlq") or fallback_nlq),
        decision=str(row.get("decision") or "ERROR"),
        raw_sql=row.get("raw_sql"),
        final_sql=row.get("final_sql"),
        executed=bool(row.get("executed")),
        execution_result_json=row.get("execution_result_json"),
        blocked_at=row.get("blocked_at"),
    )


def _history_item_from_output(output: MethodTurnOutput) -> TurnHistoryItem:
    return TurnHistoryItem(
        turn_id=output.turn_id,
        nlq=output.nlq,
        decision=output.decision,
        raw_sql=output.raw_sql,
        final_sql=output.final_sql,
        executed=output.executed,
        execution_result_json=output.execution_result_json,
        blocked_at=output.blocked_at,
    )


def _is_api_429_output(output: MethodTurnOutput) -> bool:
    return _is_api_429_row(output.to_dict())


def _api_429_error_text(output: MethodTurnOutput) -> str | None:
    row = output.to_dict()
    if row.get("error"):
        return str(row.get("error"))
    for event in row.get("module_trace") or []:
        if isinstance(event, dict) and event.get("error"):
            text = str(event.get("error"))
            if _is_api_429_text(text):
                return text
    return None


def _complete_sequence_after_api_429_exhausted(
    run_id: str,
    setting_id: str,
    sequence: Any,
    outputs: list[MethodTurnOutput],
    failed_output: MethodTurnOutput,
) -> list[MethodTurnOutput]:
    completed_turn_ids = {output.turn_id for output in outputs}
    completed = list(outputs)
    for turn in sequence.turns:
        if turn.turn_id in completed_turn_ids:
            continue
        completed.append(
            MethodTurnOutput(
                run_id=run_id,
                setting_id=setting_id,
                sequence_id=sequence.sample_id if sequence.turn_type == "multi" else None,
                sample_id=sequence.sample_id,
                turn_id=turn.turn_id,
                role=sequence.role,
                user_id=sequence.user_id,
                nlq=turn.nlq,
                decision="ERROR",
                blocked_at="API_429_SEQUENCE_ABORT",
                executed=False,
                module_trace=[],
                latency_ms=0.0,
                llm_usage={},
                error=(
                    "API_429_SEQUENCE_ABORT: prior turn exhausted API 429 retry attempts; "
                    f"failed_turn_id={failed_output.turn_id}; failed_module={failed_output.blocked_at}"
                ),
            )
        )
    return sorted(completed, key=lambda output: output.turn_id)


def _backoff_for_attempt(backoff_seconds: list[Any], attempt: int) -> float:
    if not backoff_seconds:
        return 0.0
    index = max(0, min(attempt - 1, len(backoff_seconds) - 1))
    try:
        return max(0.0, float(backoff_seconds[index]))
    except (TypeError, ValueError):
        return 0.0


def _is_api_429_row(row: dict[str, Any]) -> bool:
    texts: list[str] = []
    for value in (row.get("error"),):
        if value is not None:
            texts.append(str(value))
    for event in row.get("module_trace") or []:
        if isinstance(event, dict):
            for value in (event.get("error"),):
                if value is not None:
                    texts.append(str(value))
    return _is_api_429_text(" ".join(texts))


def _is_api_429_text(text: str) -> bool:
    haystack = text.lower()
    return (
        "429" in haystack
        or "resource_exhausted" in haystack
        or "resource exhausted" in haystack
        or "quota exceeded" in haystack
        or "rate limit" in haystack
    )


def _error_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], int] = {}
    for row in rows:
        kind = _error_kind(row)
        key = (kind, str(row.get("blocked_at") or ""))
        groups[key] = groups.get(key, 0) + 1
    return [
        {"kind": kind, "blocked_at": blocked_at, "count": count}
        for (kind, blocked_at), count in sorted(groups.items(), key=lambda item: (-item[1], item[0]))
    ]


def _error_kind(row: dict[str, Any]) -> str:
    if _is_api_429_row(row):
        return "API_429"
    text = str(row.get("error") or "").lower()
    if "getaddrinfo" in text or "name resolution" in text or "dns" in text:
        return "API_CONNECTION"
    if "psycopg2" in text or "sqlalchemy" in text:
        return "DB_EXEC_ERROR"
    if "valid json object" in text:
        return "LLM_JSON_INVALID"
    if row.get("decision") == "ERROR":
        return "OTHER_ERROR"
    return "OK"

