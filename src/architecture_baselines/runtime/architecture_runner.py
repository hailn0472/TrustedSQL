from __future__ import annotations

import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from architecture_baselines.reporting import RunWriter
from architecture_baselines.runtime.checkpoint import Checkpoint
from architecture_baselines.runtime.registry import ModuleRegistry
from architecture_baselines.schemas import ArchitectureTurnOutput, ModuleDecision, ModuleResult, NormalizedSequence, RuntimeTurnInput, TurnDecision, TurnHistoryItem
from architecture_baselines.utils.jsonl import append_jsonl, read_jsonl, write_jsonl


def _usage_merge(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    for key, value in source.items():
        if isinstance(value, (int, float)):
            target[key] = target.get(key, 0) + value
        else:
            target[key] = value
    return target


class ArchitectureRunner:
    def __init__(
        self,
        run_id: str,
        architecture_id: str,
        module_order: list[str],
        registry: ModuleRegistry,
        writer: RunWriter,
        checkpoint: Checkpoint,
        retry_config: dict[str, Any] | None = None,
    ):
        self.run_id = run_id
        self.architecture_id = architecture_id
        self.module_order = module_order
        self.registry = registry
        self.writer = writer
        self.checkpoint = checkpoint
        self.retry_config = dict(retry_config or {})
        self.previous_outputs = {
            (row.get("source_dataset"), row.get("sample_id"), int(row.get("turn_id"))): row
            for row in writer.load_turn_outputs(architecture_id)
            if row.get("source_dataset") is not None and row.get("sample_id") is not None and row.get("turn_id") is not None
        }

    def run_sequence(self, sequence: NormalizedSequence) -> list[ArchitectureTurnOutput]:
        turn_keys = {
            self.checkpoint.key(self.architecture_id, sequence.source_dataset, sequence.sample_id, turn.turn_id)
            for turn in sequence.turns
        }
        if self._api_429_retry_enabled():
            existing_done = turn_keys & self.checkpoint.completed
            if existing_done and existing_done != turn_keys:
                self._compact_runtime_outputs_for_retry(turn_keys, set())
                self.previous_outputs = {
                    (row.get("source_dataset"), row.get("sample_id"), int(row.get("turn_id"))): row
                    for row in self.writer.load_turn_outputs(self.architecture_id)
                    if row.get("source_dataset") is not None and row.get("sample_id") is not None and row.get("turn_id") is not None
                }

        outputs: list[ArchitectureTurnOutput] = []
        history: list[TurnHistoryItem] = []
        if self._api_429_retry_enabled() and all(
            self.checkpoint.is_done(self.architecture_id, sequence.source_dataset, sequence.sample_id, turn.turn_id)
            and self.previous_outputs.get((sequence.source_dataset, sequence.sample_id, turn.turn_id))
            for turn in sequence.turns
        ):
            for turn in sequence.turns:
                previous = self.previous_outputs.get((sequence.source_dataset, sequence.sample_id, turn.turn_id))
                if previous:
                    history.append(self._history_from_output(previous))
            self.writer.append_sequence_output(self.architecture_id, {"run_id": self.run_id, "architecture_id": self.architecture_id, "sample_id": sequence.sample_id, "source_dataset": sequence.source_dataset, "turn_count": len(sequence.turns), "output_count": 0})
            return outputs

        if self._api_429_retry_enabled():
            outputs = self._run_sequence_with_api_429_retry(sequence)
            for output in outputs:
                self.writer.append_turn_output(output)
                self.checkpoint.mark_done(self.architecture_id, sequence.source_dataset, sequence.sample_id, output.turn_id)
                history.append(self._history_from_output(output.to_dict()))
            self.writer.append_sequence_output(self.architecture_id, {"run_id": self.run_id, "architecture_id": self.architecture_id, "sample_id": sequence.sample_id, "source_dataset": sequence.source_dataset, "turn_count": len(sequence.turns), "output_count": len(outputs)})
            return outputs

        for turn in sequence.turns:
            previous = self.previous_outputs.get((sequence.source_dataset, sequence.sample_id, turn.turn_id))
            if self.checkpoint.is_done(self.architecture_id, sequence.source_dataset, sequence.sample_id, turn.turn_id) and previous:
                history.append(self._history_from_output(previous))
                continue
            turn_input = RuntimeTurnInput(self.run_id, self.architecture_id, sequence.sample_id if sequence.turn_type == "multi" else None, sequence.sample_id, sequence.source_dataset, turn.turn_id, sequence.role, sequence.user_id, turn.nlq, history)
            output = self.run_turn(turn_input, seq_label=sequence.seq_label, turn_label=turn.turn_label, attack_tags=sequence.attack_tags, primary_type=sequence.primary_type)
            outputs.append(output)
            self.writer.append_turn_output(output)
            self.checkpoint.mark_done(self.architecture_id, sequence.source_dataset, sequence.sample_id, turn.turn_id)
            history.append(self._history_from_output(output.to_dict()))
        self.writer.append_sequence_output(self.architecture_id, {"run_id": self.run_id, "architecture_id": self.architecture_id, "sample_id": sequence.sample_id, "source_dataset": sequence.source_dataset, "turn_count": len(sequence.turns), "output_count": len(outputs)})
        return outputs

    def rerun_existing_api_429(self, sequences: list[NormalizedSequence]) -> int:
        retry_keys = self._api_429_retry_keys()
        if not retry_keys:
            return 0
        expanded = self._expand_retry_keys_to_downstream_turns(retry_keys, sequences)
        self._compact_runtime_outputs_for_retry(expanded, retry_keys)
        self.previous_outputs = {
            (row.get("source_dataset"), row.get("sample_id"), int(row.get("turn_id"))): row
            for row in self.writer.load_turn_outputs(self.architecture_id)
            if row.get("source_dataset") is not None and row.get("sample_id") is not None and row.get("turn_id") is not None
        }
        return len(expanded)

    def write_runtime_error_summary(self) -> dict[str, Any]:
        arch_dir = self.writer.architecture_dir(self.architecture_id)
        rows = read_jsonl(arch_dir / "raw_turn_outputs.jsonl")
        retry_events = read_jsonl(arch_dir / "retry_events.jsonl")
        errors = [row for row in rows if row.get("decision") == TurnDecision.ERROR.value or row.get("error")]
        api_429 = [row for row in errors if _is_api_429_row(row)]
        summary = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "run_id": self.run_id,
            "architecture_id": self.architecture_id,
            "runtime_rows": len(rows),
            "runtime_error_count": len(errors),
            "api_429_error_count": len(api_429),
            "api_429_retry_event_count": len(retry_events),
            "api_429_retry_sequence_count": len({f"{event.get('architecture_id')}|{event.get('sample_id')}" for event in retry_events}),
            "api_429_errors": [
                {
                    "sample_id": row.get("sample_id"),
                    "turn_id": row.get("turn_id"),
                    "source_dataset": row.get("source_dataset"),
                    "blocked_at": row.get("blocked_at"),
                    "error": row.get("error"),
                }
                for row in api_429
            ],
            "error_groups": _error_groups(errors),
        }
        (arch_dir / "runtime_error_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return summary

    def _history_from_output(self, output: dict[str, Any]) -> TurnHistoryItem:
        result = output.get("execution_result_json")
        return TurnHistoryItem(int(output["turn_id"]), str(output.get("nlq") or ""), str(output.get("decision") or "ERROR"), output.get("raw_sql"), output.get("final_sql"), bool(output.get("executed")), result, output.get("blocked_at"))

    def _api_429_retry_enabled(self) -> bool:
        return bool(self.retry_config.get("enabled", False))

    def _run_sequence_with_api_429_retry(self, sequence: NormalizedSequence) -> list[ArchitectureTurnOutput]:
        max_attempts = max(1, int(self.retry_config.get("max_sequence_attempts", 1)))
        backoff_seconds = list(self.retry_config.get("backoff_seconds") or [])
        last_outputs: list[ArchitectureTurnOutput] = []
        for attempt in range(1, max_attempts + 1):
            outputs = self._run_sequence_attempt(sequence)
            last_outputs = outputs
            api_429_output = next((output for output in outputs if _is_api_429_output(output)), None)
            if api_429_output is None:
                return outputs
            event = {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "run_id": self.run_id,
                "architecture_id": self.architecture_id,
                "sequence_id": sequence.sample_id if sequence.turn_type == "multi" else None,
                "sample_id": sequence.sample_id,
                "source_dataset": sequence.source_dataset,
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
                f"architecture={self.architecture_id} sample={sequence.sample_id} "
                f"turn={api_429_output.turn_id} module={api_429_output.blocked_at} "
                f"attempt={attempt}/{max_attempts}"
            )
            if attempt < max_attempts:
                wait_seconds = _backoff_for_attempt(backoff_seconds, attempt)
                event["backoff_seconds"] = wait_seconds
                append_jsonl(self.writer.architecture_dir(self.architecture_id) / "retry_events.jsonl", event)
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                continue
            append_jsonl(self.writer.architecture_dir(self.architecture_id) / "retry_events.jsonl", event)
            return _complete_sequence_after_api_429_exhausted(self.run_id, self.architecture_id, sequence, outputs, api_429_output)
        return last_outputs

    def _run_sequence_attempt(self, sequence: NormalizedSequence) -> list[ArchitectureTurnOutput]:
        history: list[TurnHistoryItem] = []
        outputs: list[ArchitectureTurnOutput] = []
        for turn in sequence.turns:
            turn_input = RuntimeTurnInput(self.run_id, self.architecture_id, sequence.sample_id if sequence.turn_type == "multi" else None, sequence.sample_id, sequence.source_dataset, turn.turn_id, sequence.role, sequence.user_id, turn.nlq, history)
            output = self.run_turn(turn_input, seq_label=sequence.seq_label, turn_label=turn.turn_label, attack_tags=sequence.attack_tags, primary_type=sequence.primary_type)
            outputs.append(output)
            history.append(self._history_from_output(output.to_dict()))
            if _is_api_429_output(output):
                break
        return outputs

    def _api_429_retry_keys(self) -> set[str]:
        retry_keys: set[str] = set()
        for row in self.writer.load_turn_outputs(self.architecture_id):
            if _is_api_429_row(row):
                retry_keys.add(_checkpoint_key_from_row(row))
        return retry_keys

    def _expand_retry_keys_to_downstream_turns(
        self,
        retry_keys: set[str],
        sequences: list[NormalizedSequence],
    ) -> set[str]:
        if not retry_keys:
            return set()
        expanded = set(retry_keys)
        retry_by_sample: dict[tuple[str, str], int] = {}
        for key in retry_keys:
            architecture_id, source_dataset, sample_id, turn_id_text = key.split("::", 3)
            if architecture_id != self.architecture_id:
                continue
            try:
                turn_id = int(turn_id_text)
            except ValueError:
                continue
            current = retry_by_sample.get((source_dataset, sample_id))
            retry_by_sample[(source_dataset, sample_id)] = turn_id if current is None else min(current, turn_id)
        for sequence in sequences:
            first_retry_turn = retry_by_sample.get((sequence.source_dataset, sequence.sample_id))
            if first_retry_turn is None:
                continue
            for turn in sequence.turns:
                expanded.add(self.checkpoint.key(self.architecture_id, sequence.source_dataset, sequence.sample_id, turn.turn_id))
        return expanded

    def _compact_runtime_outputs_for_retry(self, retry_keys: set[str], direct_retry_keys: set[str]) -> None:
        arch_dir = self.writer.architecture_dir(self.architecture_id)
        raw_path = arch_dir / "raw_turn_outputs.jsonl"
        if raw_path.exists():
            rows = [row for row in read_jsonl(raw_path) if _checkpoint_key_from_row(row) not in retry_keys]
            write_jsonl(raw_path, rows)

        events_path = arch_dir / "module_events.jsonl"
        if events_path.exists():
            rows = [row for row in read_jsonl(events_path) if _checkpoint_key_from_row(row) not in retry_keys]
            write_jsonl(events_path, rows)

        csv_path = arch_dir / "turn_runtime.csv"
        if csv_path.exists():
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fieldnames = list(reader.fieldnames or [])
                rows = [row for row in reader if _checkpoint_key_from_row(row) not in retry_keys]
            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

        sequence_path = arch_dir / "sequence_runtime.jsonl"
        if sequence_path.exists():
            retry_samples = {_sample_key_from_checkpoint_key(key) for key in retry_keys}
            rows = [
                row
                for row in read_jsonl(sequence_path)
                if f"{row.get('source_dataset')}::{row.get('sample_id')}" not in retry_samples
            ]
            write_jsonl(sequence_path, rows)

        self.checkpoint.remove_keys(retry_keys)
        manifest = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "direct_api_429_key_count": len(direct_retry_keys),
            "rerun_key_count": len(retry_keys),
            "downstream_key_count": len(retry_keys - direct_retry_keys),
            "merge_policy": "in_place_compaction",
            "rerun_keys": sorted(retry_keys),
            "policy": "Rows with API 429 errors and downstream turns in the same architecture/sequence are removed from the current runtime artifacts, then regenerated into the same run_id.",
        }
        (arch_dir / "rerun_api_429_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def run_turn(self, turn_input: RuntimeTurnInput, *, seq_label: str, turn_label: str, attack_tags: dict[str, Any], primary_type: str | None = None) -> ArchitectureTurnOutput:
        start = time.perf_counter()
        trace: list[ModuleResult] = []
        usage: dict[str, Any] = {}
        context = None
        raw_sql = final_sql = None
        scoped_schema = None
        executed = False
        result_json = None
        error = None

        def stop(decision: str, blocked: str | None = None, err: str | None = None) -> ArchitectureTurnOutput:
            return ArchitectureTurnOutput(turn_input.run_id, turn_input.architecture_id, turn_input.sequence_id, turn_input.sample_id, turn_input.source_dataset, seq_label, turn_input.turn_id, turn_label, turn_input.role, turn_input.user_id, turn_input.nlq, decision, blocked, raw_sql, final_sql, executed, result_json, trace, (time.perf_counter() - start) * 1000.0, usage, err, attack_tags, primary_type)

        for module_id in self.module_order:
            module = self.registry.get(module_id)
            if module_id == "C0":
                module_result, context = module.run(turn_input)
            elif module_id == "D1":
                module_result = module.run(turn_input.nlq, context)
            elif module_id == "D2":
                module_result = module.run(turn_input.nlq, context)
                scoped_schema = module_result.artifact.get("scoped_schema_ddl")
            elif module_id == "G1":
                module_result = module.run(turn_input.nlq, context, scoped_schema)
                raw_sql = module_result.artifact.get("raw_sql")
                final_sql = raw_sql
            elif module_id == "D3":
                module_result = module.run(raw_sql or "")
            elif module_id == "D4":
                module_result = module.run(raw_sql or "", context)
                final_sql = module_result.artifact.get("final_sql")
            elif module_id == "X1":
                module_result = module.run((final_sql or raw_sql) or "")
                executed = bool(module_result.artifact.get("executed"))
                result_json = module_result.artifact.get("rows")
                if module_result.decision == ModuleDecision.ERROR.value:
                    error = module_result.error
            else:
                raise ValueError(f"Unknown module {module_id}")
            trace.append(module_result)
            _usage_merge(usage, module_result.llm_usage)
            if module_result.decision == ModuleDecision.DENY.value:
                return stop(TurnDecision.DENY.value, module_id)
            if module_result.decision == ModuleDecision.ERROR.value:
                error = module_result.error or f"{module_id} returned ERROR"
                return stop(TurnDecision.ERROR.value, module_id, error)
        if executed:
            return stop(TurnDecision.ALLOW.value)
        return stop(TurnDecision.ERROR.value, None, error or "Pipeline finished without execution.")


def _checkpoint_key_from_row(row: dict[str, Any]) -> str:
    return f"{row.get('architecture_id')}::{row.get('source_dataset')}::{row.get('sample_id')}::{row.get('turn_id')}"


def _sample_key_from_checkpoint_key(key: str) -> str:
    parts = key.split("::", 3)
    if len(parts) < 4:
        return key
    return f"{parts[1]}::{parts[2]}"


def _is_api_429_output(output: ArchitectureTurnOutput) -> bool:
    return _is_api_429_row(output.to_dict())


def _is_api_429_row(row: dict[str, Any]) -> bool:
    texts: list[str] = []
    if row.get("error") is not None:
        texts.append(str(row.get("error")))
    for event in row.get("module_trace") or []:
        if isinstance(event, dict) and event.get("error") is not None:
            texts.append(str(event.get("error")))
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


def _api_429_error_text(output: ArchitectureTurnOutput) -> str | None:
    row = output.to_dict()
    if row.get("error"):
        return str(row.get("error"))
    for event in row.get("module_trace") or []:
        if isinstance(event, dict) and event.get("error"):
            text = str(event.get("error"))
            if _is_api_429_text(text):
                return text
    return None


def _backoff_for_attempt(backoff_seconds: list[Any], attempt: int) -> float:
    if not backoff_seconds:
        return 0.0
    index = max(0, min(attempt - 1, len(backoff_seconds) - 1))
    try:
        return max(0.0, float(backoff_seconds[index]))
    except (TypeError, ValueError):
        return 0.0


def _complete_sequence_after_api_429_exhausted(
    run_id: str,
    architecture_id: str,
    sequence: NormalizedSequence,
    outputs: list[ArchitectureTurnOutput],
    failed_output: ArchitectureTurnOutput,
) -> list[ArchitectureTurnOutput]:
    completed_turn_ids = {output.turn_id for output in outputs}
    completed = list(outputs)
    for turn in sequence.turns:
        if turn.turn_id in completed_turn_ids:
            continue
        completed.append(
            ArchitectureTurnOutput(
                run_id=run_id,
                architecture_id=architecture_id,
                sequence_id=sequence.sample_id if sequence.turn_type == "multi" else None,
                sample_id=sequence.sample_id,
                source_dataset=sequence.source_dataset,
                seq_label=sequence.seq_label,
                turn_id=turn.turn_id,
                turn_label=turn.turn_label,
                role=sequence.role,
                user_id=sequence.user_id,
                nlq=turn.nlq,
                decision=TurnDecision.ERROR.value,
                blocked_at="API_429_SEQUENCE_ABORT",
                executed=False,
                module_trace=[],
                latency_ms=0.0,
                llm_usage={},
                error=(
                    "API_429_SEQUENCE_ABORT: prior turn exhausted API 429 retry attempts; "
                    f"failed_turn_id={failed_output.turn_id}; failed_module={failed_output.blocked_at}"
                ),
                attack_tags=sequence.attack_tags,
                primary_type=sequence.primary_type,
            )
        )
    return sorted(completed, key=lambda output: output.turn_id)


def _error_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], int] = {}
    for row in rows:
        kind = _error_kind(row)
        key = (kind, str(row.get("blocked_at") or ""), str(row.get("source_dataset") or ""))
        groups[key] = groups.get(key, 0) + 1
    return [
        {"kind": kind, "blocked_at": blocked_at, "source_dataset": source_dataset, "count": count}
        for (kind, blocked_at, source_dataset), count in sorted(groups.items(), key=lambda item: (-item[1], item[0]))
    ]


def _error_kind(row: dict[str, Any]) -> str:
    if _is_api_429_row(row):
        return "API_429"
    text = str(row.get("error") or "").lower()
    if "getaddrinfo" in text or "name resolution" in text or "dns" in text:
        return "API_CONNECTION"
    if "psycopg2" in text or "sqlalchemy" in text:
        return "DB_EXEC_ERROR"
    if "valid json" in text or "valid json object" in text:
        return "LLM_JSON_INVALID"
    if row.get("decision") == TurnDecision.ERROR.value:
        return "OTHER_ERROR"
    return "OK"

