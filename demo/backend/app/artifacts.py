"""Incremental, browser-safe readers for TrustedSQL runtime artifacts.

The stream deliberately treats JSONL as an append-only evidence source, while
also handling the runner's in-place API-429 compaction.  No raw runtime row is
returned to callers: module events and final rows pass through ``contracts``
normalizers before they leave this module.
"""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import ALLOWED_MODULE_IDS, BrowserEvent, BrowserFinalResult, normalize_event, normalize_final_result
from .paths import IsolationBoundaryError, run_path
from .sanitization import MAX_ID_LENGTH, MAX_STRING_LENGTH, redact_error, sanitize_json_value

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_LINE_BYTES = 512 * 1024
_TERMINAL_DECISIONS = {"deny", "denied", "blocked", "error"}
_DIRECT_MODULE_IDS = ("C0", "M6", "X1")


class ArtifactError(ValueError):
    """Base error for invalid or unsafe runtime artifacts."""


class ArtifactValidationError(ArtifactError):
    """Raised when an artifact is malformed or violates stream identity/order."""


class ArtifactConflictError(ArtifactValidationError):
    """Raised when an append introduces conflicting evidence for an identity."""


@dataclass
class _Cursor:
    identity: tuple[int, int] | None = None
    last_data: bytes = b""
    partial: bytes = b""
    initialized: bool = False


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _bounded_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_ID_LENGTH:
        raise ArtifactValidationError(f"invalid {label} identity")
    return value


def _safe_error(exc: BaseException) -> ArtifactValidationError:
    # Contract details are useful to tests/operators, but never copy arbitrary
    # exception/provider text into a browser-facing payload.
    reason = redact_error(str(exc))
    return ArtifactValidationError(reason[:MAX_STRING_LENGTH])


class ArtifactStream:
    """Thread-safe incremental reader for one reserved TrustedSQL run.

    ``runtime_dir`` must be the actual ``runtime`` directory returned by the
    runtime adapter.  Callers must pass the catalog/runtime ``turn_type``
    explicitly; a one-turn prefix may still belong to a multi-turn scenario.
    The constructor accepts aliases used by the job layer so callers can name
    the selected turn either ``selected_final_turn`` or ``selected_turn_id``.
    """

    def __init__(
        self,
        runtime_dir: str | Path,
        *,
        run_id: str,
        sample_id: str,
        allowed_turn_ids: Iterable[int] | None = None,
        expected_turn_ids: Iterable[int] | None = None,
        selected_final_turn: int | None = None,
        selected_turn_id: int | None = None,
        setting_id: str = "full_trustedsql",
        turn_type: str | None = None,
    ) -> None:
        self.run_id = _bounded_id(run_id, "run")
        self.runtime_dir = self._validate_runtime_dir(runtime_dir, self.run_id)
        self.sample_id = _bounded_id(sample_id, "sample")
        self.setting_id = _bounded_id(setting_id, "setting")
        self.module_ids = _DIRECT_MODULE_IDS if self.setting_id == "direct_sql" else ALLOWED_MODULE_IDS
        supplied_turns = allowed_turn_ids if allowed_turn_ids is not None else expected_turn_ids
        if supplied_turns is None:
            raise ValueError("allowed_turn_ids is required")
        turns = tuple(supplied_turns)
        if not turns or any(type(turn) is not int or turn < 0 for turn in turns) or len(set(turns)) != len(turns):
            raise ValueError("allowed_turn_ids must be unique non-negative integers")
        self.allowed_turn_ids = turns
        if turn_type not in {"single", "multi"}:
            raise ValueError("turn_type must be explicitly supplied as single or multi")
        self.turn_type = turn_type
        self.selected_final_turn = selected_final_turn if selected_final_turn is not None else selected_turn_id
        if self.selected_final_turn is None:
            self.selected_final_turn = turns[-1]
        if self.selected_final_turn not in turns:
            raise ValueError("selected final turn must be an allowed turn")
        self.expected_final_turns = turns[: turns.index(self.selected_final_turn) + 1]

        self.module_events_path = self.runtime_dir / "module_events.jsonl"
        self.final_rows_path = self.runtime_dir / "raw_turn_outputs.jsonl"
        self.retry_events_path = self.runtime_dir / "retry_events.jsonl"
        self.error_summary_path = self.runtime_dir / "runtime_error_summary.json"
        self._lock = threading.RLock()
        self._event_cursor = _Cursor()
        self._final_cursor = _Cursor()
        self._retry_cursor = _Cursor()
        self._seen: dict[tuple[str, str, int, str], dict[str, Any]] = {}
        self._seen_order: list[tuple[str, str, int, str]] = []
        self._revisions: dict[tuple[str, str, int, str], int] = {}
        self._final_rows: list[dict[str, Any]] = []
        self._final_result: BrowserFinalResult | None = None

    @staticmethod
    def _validate_runtime_dir(runtime_dir: str | Path, run_id: str) -> Path:
        # Keep both lexical and resolved forms.  Resolving first would turn a
        # symlinked run alias into a plausible canonical path.
        requested = Path(os.path.abspath(os.path.expanduser(str(runtime_dir))))
        if (
            requested.name != "runtime"
            or requested.parent.name != run_id
            or requested.parent.parent.name != "runs"
            or requested.parent.parent.parent.name != "demo"
        ):
            raise ArtifactValidationError("runtime directory is not canonical demo/runs/<run-id>/runtime")
        boundary = requested.parent.parent.parent
        for current in (boundary, boundary / "runs", boundary / "runs" / run_id, requested):
            if current.is_symlink():
                raise ArtifactValidationError("runtime path contains a symlinked boundary")
        if requested.is_symlink() or requested.parent.is_symlink():
            raise ArtifactValidationError("runtime or run directory symlink is not allowed")
        resolved = requested.resolve()
        if resolved != requested or resolved.name != "runtime" or resolved.parent.name != run_id:
            raise ArtifactValidationError("runtime directory is not canonical")
        repo_root = resolved.parent.parent.parent.parent
        try:
            canonical_run = run_path(repo_root, resolved.parent.name)
        except IsolationBoundaryError as exc:
            raise ArtifactValidationError("run directory is outside demo/runs") from exc
        if canonical_run != resolved.parent:
            raise ArtifactValidationError("run directory is not canonical")
        return resolved

    def _artifact_path(self, path: Path) -> Path:
        if path.is_symlink():
            raise ArtifactValidationError("artifact symlink is not allowed")
        try:
            resolved = path.resolve()
            resolved.relative_to(self.runtime_dir)
        except ValueError as exc:
            raise ArtifactValidationError("artifact path escapes runtime directory") from exc
        # Reject symlinked parent components even when their target is inside.
        current = path.parent
        while current != self.runtime_dir.parent:
            if current.is_symlink():
                raise ArtifactValidationError("artifact path contains a symlink")
            if current == self.runtime_dir:
                break
            current = current.parent
        return resolved

    def _read_complete_lines(self, path: Path, cursor: _Cursor) -> tuple[list[dict[str, Any]], bool, _Cursor]:
        path = self._artifact_path(path)
        if not path.exists():
            if not cursor.initialized:
                return [], False, _Cursor(initialized=True)
            return [], True, _Cursor(initialized=True)
        try:
            stat = path.stat()
            if stat.st_size > MAX_ARTIFACT_BYTES:
                raise ArtifactValidationError("artifact exceeds bounded size")
            data = path.read_bytes()
        except ArtifactValidationError:
            raise
        except OSError as exc:
            raise ArtifactValidationError("artifact could not be read") from exc
        identity = (stat.st_dev, stat.st_ino)
        append_only = (
            cursor.initialized
            and cursor.identity == identity
            and len(data) >= len(cursor.last_data)
            and data.startswith(cursor.last_data)
        )
        reset = not append_only
        chunk = data[len(cursor.last_data):] if append_only else data
        prefix = (cursor.partial if append_only else b"") + chunk
        lines: list[bytes] = []
        partial = b""
        for line in prefix.splitlines(keepends=True):
            if not line.endswith((b"\n", b"\r")):
                partial = line
                break
            lines.append(line.rstrip(b"\r\n"))
        if prefix and not prefix.endswith((b"\n", b"\r")) and not partial:
            partial = prefix
        rows: list[dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            if len(line) > MAX_LINE_BYTES:
                raise ArtifactValidationError("artifact record exceeds bounded size")
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ArtifactValidationError("malformed complete JSONL record") from exc
            if not isinstance(value, dict):
                raise ArtifactValidationError("complete JSONL record must be an object")
            rows.append(value)
        new_cursor = _Cursor(identity=identity, last_data=data, partial=partial, initialized=True)
        return rows, reset, new_cursor

    def _validate_identity(self, row: Mapping[str, Any], *, turn_label: str = "turn") -> tuple[int, str]:
        if row.get("run_id") != self.run_id or row.get("sample_id") != self.sample_id or row.get("setting_id") != self.setting_id:
            raise ArtifactValidationError("foreign runtime identity")
        turn = row.get("turn_id")
        if type(turn) is not int or turn not in self.allowed_turn_ids:
            raise ArtifactValidationError(f"unknown selected {turn_label}")
        return turn, _bounded_id(str(row.get("sample_id")), "sample")

    def _normalize_event_row(self, row: Mapping[str, Any]) -> tuple[tuple[str, str, int, str], dict[str, Any]]:
        turn, sample = self._validate_identity(row, turn_label="turn")
        module_id = row.get("module_id")
        if module_id not in self.module_ids:
            raise ArtifactValidationError("unknown module identity")
        try:
            normalized = dict(normalize_event(row))
        except Exception as exc:  # contract error details are bounded below
            raise _safe_error(exc) from exc
        if normalized.get("moduleId") != module_id:
            raise ArtifactValidationError("normalized module identity mismatch")
        return (self.run_id, sample, turn, module_id), normalized

    def _check_turn_order(
        self,
        key: tuple[str, str, int, str],
        state: dict[tuple[str, str, int, str], dict[str, Any]],
        order: list[tuple[str, str, int, str]],
        terminal: set[int],
        allowed: tuple[int, ...],
    ) -> None:
        _, _, turn, module = key
        module_index = self.module_ids.index(module)
        existing_turns = [item[2] for item in order]
        if turn not in existing_turns:
            if existing_turns:
                prior = existing_turns[-1]
                if allowed.index(turn) != allowed.index(prior) + 1:
                    raise ArtifactValidationError("impossible turn ordering")
                prior_complete = any(item[2] == prior and self.module_ids.index(item[3]) == len(self.module_ids) - 1 for item in order) or prior in terminal
                if not prior_complete:
                    raise ArtifactValidationError("next turn appeared before prior turn completed")
            if module_index != 0:
                raise ArtifactValidationError("impossible module ordering")
        else:
            turn_items = [item for item in order if item[2] == turn]
            if turn in terminal:
                raise ArtifactValidationError("event appeared after terminal decision")
            expected_index = len(turn_items)
            if module_index != expected_index:
                raise ArtifactValidationError("impossible module ordering")

    def _event_with_meta(self, normalized: Mapping[str, Any], *, kind: str, sequence: int, revision: int | None = None, identity: tuple[str, str, int, str] | None = None) -> dict[str, Any]:
        event = deepcopy(dict(normalized))
        event["eventType"] = kind
        event["streamSequence"] = sequence
        if revision is not None:
            event["revision"] = revision
        if kind == "retract" and identity is not None:
            event = {
                "eventType": "retract",
                "streamSequence": sequence,
                "runId": identity[0],
                "sampleId": identity[1],
                "turnId": identity[2],
                "moduleId": identity[3],
                "revision": revision or 0,
            }
        return event

    def _build_event_state(self, rows: list[Mapping[str, Any]]) -> tuple[dict[tuple[str, str, int, str], dict[str, Any]], list[tuple[str, str, int, str]], set[int]]:
        state: dict[tuple[str, str, int, str], dict[str, Any]] = {}
        order: list[tuple[str, str, int, str]] = []
        terminal: set[int] = set()
        for row in rows:
            key, normalized = self._normalize_event_row(row)
            if key in state:
                if _canonical(state[key]) == _canonical(normalized):
                    continue
                raise ArtifactConflictError("conflicting duplicate module evidence")
            self._check_turn_order(key, state, order, terminal, self.allowed_turn_ids)
            state[key] = normalized
            order.append(key)
            if str(normalized.get("decision", "")).lower() in _TERMINAL_DECISIONS:
                terminal.add(key[2])
        return state, order, terminal

    def poll_events(self) -> list[dict[str, Any]]:
        """Return newly available sanitized events, never raw JSONL rows."""
        with self._lock:
            rows, reset, new_cursor = self._read_complete_lines(self.module_events_path, self._event_cursor)
            if not rows and not reset:
                self._event_cursor = new_cursor
                return []
            if reset:
                # Rebuild from the complete current file so compaction cannot
                # silently combine old and new evidence.
                all_rows = rows
                new_state, new_order, new_terminal = self._build_event_state(all_rows)
                old_state = self._seen
                emissions: list[dict[str, Any]] = []
                for key in new_order:
                    if key not in old_state:
                        self._revisions.setdefault(key, 0)
                        emissions.append(self._event_with_meta(new_state[key], kind="module", sequence=0))
                    elif _canonical(old_state[key]) != _canonical(new_state[key]):
                        revision = self._revisions.get(key, 0) + 1
                        self._revisions[key] = revision
                        emissions.append(self._event_with_meta(new_state[key], kind="revision", sequence=0, revision=revision))
                for key in self._seen_order:
                    if key not in new_state:
                        revision = self._revisions.get(key, 0) + 1
                        self._revisions[key] = revision
                        emissions.append(self._event_with_meta({}, kind="retract", sequence=0, revision=revision, identity=key))
                self._seen, self._seen_order = new_state, new_order
                self._event_cursor = new_cursor
                for event in emissions:
                    event["streamSequence"] = self._next_sequence()
                # Retain terminal state for append validation by reconstructing
                # it from normalized rows.
                self._terminal_turns = new_terminal
                return emissions

            # Append path: validate all rows before committing any state.
            state = dict(self._seen)
            order = list(self._seen_order)
            terminal = set(getattr(self, "_terminal_turns", set()))
            emissions: list[dict[str, Any]] = []
            for row in rows:
                key, normalized = self._normalize_event_row(row)
                if key in state:
                    if _canonical(state[key]) == _canonical(normalized):
                        continue
                    raise ArtifactConflictError("conflicting duplicate module evidence")
                self._check_turn_order(key, state, order, terminal, self.allowed_turn_ids)
                state[key] = normalized
                order.append(key)
                self._revisions.setdefault(key, 0)
                kind = "module"
                emissions.append(self._event_with_meta(normalized, kind=kind, sequence=0))
                if str(normalized.get("decision", "")).lower() in _TERMINAL_DECISIONS:
                    terminal.add(key[2])
            self._seen, self._seen_order, self._terminal_turns = state, order, terminal
            self._event_cursor = new_cursor
            for event in emissions:
                event["streamSequence"] = self._next_sequence()
            return emissions

    def _next_sequence(self) -> int:
        current = getattr(self, "_stream_sequence", 0) + 1
        self._stream_sequence = current
        return current

    def _validate_final_trace(
        self,
        trace: list[Any],
        turn: int,
        final_row: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        state: dict[tuple[str, str, int, str], dict[str, Any]] = {}
        order: list[tuple[str, str, int, str]] = []
        terminal: set[int] = set()
        materialized: list[dict[str, Any]] = []
        for event in trace:
            if not isinstance(event, Mapping):
                raise ArtifactValidationError("final module trace contains invalid row")
            event_row = dict(event)
            if "output" not in event_row:
                event_row = {
                    "created_at": final_row.get("created_at"),
                    "run_id": self.run_id,
                    "setting_id": self.setting_id,
                    "sequence_id": final_row.get("sequence_id"),
                    "sample_id": self.sample_id,
                    "turn_id": turn,
                    "module_id": event.get("module_id"),
                    "output": event_row,
                }
            event_key, normalized_event = self._normalize_event_row(event_row)
            if event_key[2] != turn:
                raise ArtifactValidationError("final module trace turn identity mismatch")
            self._check_turn_order(event_key, state, order, terminal, (turn,))
            state[event_key] = normalized_event
            order.append(event_key)
            materialized.append(event_row)
            if str(normalized_event.get("decision", "")).lower() in _TERMINAL_DECISIONS:
                terminal.add(turn)
        if not order:
            raise ArtifactValidationError("final module trace must contain live module evidence")
        if turn not in terminal and len(order) != len(self.module_ids):
            raise ArtifactValidationError("final module trace skips required modules")
        return materialized

    def _normalize_final_row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        turn, _ = self._validate_identity(row, turn_label="final")
        trace = row.get("module_trace")
        if not isinstance(trace, list):
            raise ArtifactValidationError("final module trace must be a list")
        normalized_row = dict(row)
        normalized_row["module_trace"] = self._validate_final_trace(trace, turn, row)
        try:
            normalized = dict(normalize_final_result(normalized_row))
        except Exception as exc:
            raise _safe_error(exc) from exc
        if normalized.get("turnId") != turn:
            raise ArtifactValidationError("final turn identity mismatch")
        return normalized

    def read_final_result(self) -> BrowserFinalResult | None:
        """Return the selected normalized result, or ``None`` while pending."""
        with self._lock:
            rows, reset, new_cursor = self._read_complete_lines(self.final_rows_path, self._final_cursor)
            if reset:
                candidate: list[dict[str, Any]] = []
                for row in rows:
                    normalized = self._normalize_final_row(row)
                    if candidate:
                        if normalized["turnId"] <= candidate[-1]["turnId"]:
                            raise ArtifactValidationError("duplicate or out-of-order final rows")
                    candidate.append(normalized)
                self._final_rows = candidate
            else:
                candidate = list(self._final_rows)
                for row in rows:
                    normalized = self._normalize_final_row(row)
                    if candidate and normalized["turnId"] <= candidate[-1]["turnId"]:
                        raise ArtifactValidationError("duplicate or out-of-order final rows")
                    candidate.append(normalized)
                self._final_rows = candidate
            self._final_cursor = new_cursor
            turns = tuple(item["turnId"] for item in self._final_rows)
            expected_prefix = self.expected_final_turns
            if turns != expected_prefix[: len(turns)]:
                raise ArtifactValidationError("final rows do not match selected prefix")
            if turns != expected_prefix:
                self._final_result = None
                return None
            self._final_result = deepcopy(self._final_rows[-1])
            return deepcopy(self._final_result)

    def _read_aux_rows(self, path: Path, cursor: _Cursor) -> tuple[list[dict[str, Any]], bool, _Cursor]:
        rows, reset, new_cursor = self._read_complete_lines(path, cursor)
        return rows, reset, new_cursor

    def _retry_observed(self) -> bool:
        rows, reset, new_cursor = self._read_aux_rows(self.retry_events_path, self._retry_cursor)
        if reset:
            self._api429_retry_observed = False
        required = ("run_id", "setting_id", "sequence_id", "sample_id", "failed_turn_id", "error_type")
        for row in rows:
            if row.get("error_type") != "API_429":
                if any(field in row for field in required if field != "error_type"):
                    raise ArtifactValidationError("invalid API-429 retry error type")
                continue
            if any(field not in row for field in required):
                raise ArtifactValidationError("incomplete API-429 retry metadata")
            if type(row["error_type"]) is not str or row["error_type"] != "API_429":
                raise ArtifactValidationError("invalid API-429 retry error type")
            if (
                type(row["run_id"]) is not str
                or row["run_id"] != self.run_id
                or type(row["setting_id"]) is not str
                or row["setting_id"] != self.setting_id
                or type(row["sample_id"]) is not str
                or row["sample_id"] != self.sample_id
            ):
                raise ArtifactValidationError("foreign retry metadata")
            expected_sequence_id = self.sample_id if self.turn_type == "multi" else None
            if row["sequence_id"] != expected_sequence_id or (
                self.turn_type == "multi" and type(row["sequence_id"]) is not str
            ):
                raise ArtifactValidationError("foreign retry sequence metadata")
            failed_turn_id = row["failed_turn_id"]
            if type(failed_turn_id) is not int or failed_turn_id not in self.expected_final_turns:
                raise ArtifactValidationError("foreign retry failed turn metadata")
            self._api429_retry_observed = True
        self._retry_cursor = new_cursor
        return getattr(self, "_api429_retry_observed", False)

    def _safe_error_summary(self) -> dict[str, Any] | None:
        path = self._artifact_path(self.error_summary_path)
        if not path.exists():
            return None
        try:
            if path.stat().st_size > MAX_ARTIFACT_BYTES:
                raise ArtifactValidationError("runtime error summary exceeds bounded size")
            value = json.loads(path.read_bytes().decode("utf-8"))
        except ArtifactValidationError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactValidationError("runtime error summary is malformed") from exc
        if not isinstance(value, Mapping) or value.get("run_id") != self.run_id:
            raise ArtifactValidationError("runtime error summary identity mismatch")
        # Counts and reason keys are safe; omit all raw messages/provider data.
        result: dict[str, Any] = {}
        for source, target in (
            ("runtime_error_count", "runtimeErrorCount"),
            ("api_429_error_count", "api429ErrorCount"),
            ("api_429_retry_event_count", "api429RetryEventCount"),
            ("api_429_retry_sequence_count", "api429RetrySequenceCount"),
            ("runtime_rows", "runtimeRows"),
        ):
            number = value.get(source)
            if type(number) is int and 0 <= number <= 1_000_000:
                result[target] = number
        groups = value.get("error_groups")
        if isinstance(groups, Mapping):
            result["errorGroups"] = [str(key)[:MAX_STRING_LENGTH] for key in list(groups)[:100]]
        return sanitize_json_value(result)

    def status(self, *, process_running: bool = True, exit_code: int | None = None, cancelled: bool = False) -> dict[str, Any]:
        """Return honest, bounded state for a future job/process layer."""
        with self._lock:
            final = self.read_final_result()
            retry = self._retry_observed()
            summary = self._safe_error_summary()
            if cancelled:
                state = "cancelled"
            elif final is not None:
                state = "completed"
            elif retry and process_running:
                state = "api_429_retry"
            elif not process_running and exit_code not in (None, 0):
                state = "failed"
            else:
                state = "pending"
            return {
                "status": state,
                "state": state,
                "final_result": final,
                "api429RetryObserved": retry,
                "runtime_error_summary": summary,
            }

    def poll(self, *, process_running: bool = True, exit_code: int | None = None, cancelled: bool = False) -> dict[str, Any]:
        with self._lock:
            events = self.poll_events()
            final = self.read_final_result()
            status = self.status(process_running=process_running, exit_code=exit_code, cancelled=cancelled)
            return {"events": events, "final_result": final, "status": status}

    # Friendly aliases for the job layer.
    read_events = poll_events
    get_final_result = read_final_result
    get_status = status


TrustedSqlArtifactStream = ArtifactStream
SanitizedArtifactStream = ArtifactStream

__all__ = [
    "ArtifactConflictError",
    "ArtifactError",
    "ArtifactStream",
    "ArtifactValidationError",
    "SanitizedArtifactStream",
    "TrustedSqlArtifactStream",
]
