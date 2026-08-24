"""Thread-safe TrustedSQL demo job lifecycle orchestration.

The manager deliberately owns no provider or database clients.  Runtime and
artifact factories are injected so this layer can be exercised with local fakes;
the default factories point at the existing TrustedSQL adapter and sanitized
artifact stream.
"""

from __future__ import annotations

import inspect
import queue
import re
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .artifacts import ArtifactStream, ArtifactValidationError
from .catalog import (
    ScenarioCatalogError,
    get_dataset_prompt_scenario,
    load_dataset_prompt_catalog,
    load_scenario_catalog,
    search_dataset_prompt_catalog,
)
from .paths import run_path
from .runtime import (
    DIRECT_MODE,
    DIRECT_SETTING_ID,
    INTERACTIVE_SAMPLE_ID,
    INTERACTIVE_SCENARIO_KEY,
    MAX_CHAT_QUERY_CHARS,
    MAX_CHAT_TURNS,
    REQUIRED_MODULES,
    TRUSTEDSQL_MODE,
    TRUSTEDSQL_SETTING_ID,
    TrustedSqlRuntimeAdapter,
)
from .rag import DATABASE_ROUTE, DOCUMENT_ROUTE, KnowledgeQueryRouter, RagError, VertexRagService
from .sanitization import MAX_ID_LENGTH, MAX_LIST_ITEMS, MAX_STRING_LENGTH, redact_error, sanitize_json_value

_SAFE_RUN_ID = re.compile(r"^run-[0-9a-f]{32}$")
_SAFE_CONVERSATION_ID = re.compile(r"^conversation-[0-9a-f]{32}$")
_EXECUTION_MODES = {TRUSTEDSQL_MODE, DIRECT_MODE}
_TERMINAL = {"complete", "denied", "error", "cancelled"}
_FINAL_KEYS = {
    "timestamp", "runId", "scenarioId", "sampleId", "sequenceId", "turnId", "decision",
    "detectedAt", "enforcedAt", "executed", "dbTouched", "columns", "rows", "rawSql",
    "finalSql", "latencyMs", "error", "route", "mode", "resultType",
}
_EVENT_KEYS = {
    "timestamp", "runId", "scenarioId", "sampleId", "sequenceId", "turnId", "moduleId",
    "stage", "decision", "artifact", "audit", "latencyMs", "error", "eventType",
    "streamSequence", "revision", "detail", "traceLines", "traceStep", "traceTotal",
    "gnnGraph",
}

RuntimeFactory = Callable[[], Any]
ArtifactFactory = Callable[..., Any]
ReadinessFactory = Callable[[], Any]
RagFactory = Callable[[], VertexRagService]


def _redact_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _redact_payload(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str):
        return redact_error(value)
    return value


class JobError(RuntimeError):
    """Base public-safe job manager error."""


class JobNotFound(JobError):
    pass


class JobNotReady(JobError):
    pass


class JobConflict(JobError):
    pass


class JobCapacityError(JobConflict):
    """Raised when bounded active-job admission is full."""


class CancellationConflict(JobConflict):
    pass


class ConversationNotFound(JobError):
    pass


class ConversationConflict(JobConflict):
    pass


@dataclass
class _Job:
    run_id: str
    conversation_id: str
    scenario_key: str
    sample_id: str
    through_turn: int
    turn_type: str
    allowed_turn_ids: tuple[int, ...]
    nlq: str
    history: tuple[dict[str, Any], ...]
    mode: str = TRUSTEDSQL_MODE
    state: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    events: list[dict[str, Any]] = field(default_factory=list)
    final_result: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    artifact: Any | None = None
    runtime_started: bool = False
    stream_refs: int = 0
    next_stream_sequence: int = 1
    route_type: str | None = None


@dataclass
class _Conversation:
    conversation_id: str
    mode: str = TRUSTEDSQL_MODE
    history: list[dict[str, Any]] = field(default_factory=list)
    pending_run_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class DemoJobManager:
    """Run interactive chat histories with locked state and bounded concurrency."""

    def __init__(
        self,
        repo_root: str | Path,
        provider_config: str | Path | None = None,
        *,
        runtime_factory: RuntimeFactory | None = None,
        artifact_factory: ArtifactFactory = ArtifactStream,
        readiness: ReadinessFactory | None = None,
        rag_factory: RagFactory | None = None,
        catalog: Mapping[str, Mapping[str, Any]] | None = None,
        worker_count: int = 1,
        heartbeat_seconds: float = 0.25,
        max_queued_jobs: int = 64,
        max_terminal_jobs: int = 128,
    ) -> None:
        if type(worker_count) is not int or not 1 <= worker_count <= 32:
            raise ValueError("worker_count must be between 1 and 32")
        if type(max_queued_jobs) is not int or not 1 <= max_queued_jobs <= 10_000:
            raise ValueError("max_queued_jobs must be between 1 and 10000")
        if type(max_terminal_jobs) is not int or not 1 <= max_terminal_jobs <= 10_000:
            raise ValueError("max_terminal_jobs must be between 1 and 10000")
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.provider_config = provider_config
        self.catalog = deepcopy(dict(catalog)) if catalog is not None else load_scenario_catalog(self.repo_root)
        try:
            self.dataset_prompt_catalog = load_dataset_prompt_catalog(self.repo_root)
        except ScenarioCatalogError:
            self.dataset_prompt_catalog = {}
        self.artifact_factory = artifact_factory
        self._runtime_factory = runtime_factory
        self._readiness_factory = readiness
        self._rag_service = rag_factory() if rag_factory is not None else VertexRagService()
        self._query_router = KnowledgeQueryRouter()
        self.worker_count = worker_count
        self.heartbeat_seconds = max(0.02, min(float(heartbeat_seconds), 2.0))
        self.max_queued_jobs = max_queued_jobs
        self.max_terminal_jobs = max_terminal_jobs
        self._jobs: dict[str, _Job] = {}
        self._conversations: dict[str, _Conversation] = {}
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=max_queued_jobs)
        self._closed = False
        self._workers = [threading.Thread(target=self._worker_loop, name=f"trustedsql-demo-{i}", daemon=True) for i in range(worker_count)]
        self.artifact_calls: list[dict[str, Any]] = []
        for worker in self._workers:
            worker.start()

    def _new_runtime(self) -> Any:
        if self._runtime_factory is not None:
            try:
                parameters = inspect.signature(self._runtime_factory).parameters
            except (TypeError, ValueError):
                parameters = {}
            if parameters:
                return self._runtime_factory(self.repo_root, self.provider_config)  # type: ignore[misc]
            return self._runtime_factory()
        if self.provider_config is None:
            raise JobError("provider_config is required")
        return TrustedSqlRuntimeAdapter(self.repo_root, self.provider_config)

    def _readiness(self) -> dict[str, Any]:
        if self._readiness_factory is not None:
            value = self._readiness_factory()
        else:
            runtime = self._new_runtime()
            checker = getattr(runtime, "check_readiness", None)
            value = checker() if callable(checker) else {"ready": True, "checks": {}, "errors": []}
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        if not isinstance(value, Mapping):
            return {"ready": False, "checks": {}, "errors": ["readiness_unavailable"]}
        checks = value.get("checks", {})
        errors = value.get("errors", [])
        safe_errors = []
        if isinstance(errors, (list, tuple)):
            for item in list(errors)[:32]:
                reason = str(item)
                safe_errors.append(reason if re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", reason) else "readiness_unavailable")
        return {
            "ready": bool(value.get("ready")) and isinstance(checks, Mapping),
            "checks": {
                (str(k)[:64] if re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", str(k)) else "unknown_check"): bool(v)
                for k, v in list(checks.items())[:32]
            } if isinstance(checks, Mapping) else {},
            "errors": safe_errors or ([] if bool(value.get("ready")) else ["readiness_unavailable"]),
        }

    @staticmethod
    def _chat_message(value: Any) -> str:
        if not isinstance(value, str):
            raise ScenarioCatalogError("message must be a string")
        nlq = value.strip()
        if not nlq or len(nlq) > MAX_CHAT_QUERY_CHARS or "\x00" in nlq:
            raise ScenarioCatalogError("chat message is empty or exceeds the bounded limit")
        return nlq

    def submit(
        self,
        message: str,
        conversation_id: str | None = None,
        mode: str = TRUSTEDSQL_MODE,
        replace_turn: int | None = None,
    ) -> dict[str, Any]:
        readiness = self._readiness()
        if not readiness["ready"]:
            raise JobNotReady("TrustedSQL demo is not ready")
        nlq = self._chat_message(message)
        if mode not in _EXECUTION_MODES:
            raise ScenarioCatalogError("mode must be trustedsql or direct")
        if replace_turn is not None and (type(replace_turn) is not int or replace_turn < 1):
            raise ScenarioCatalogError("replace_turn must be a positive integer")
        if conversation_id is not None and (
            not isinstance(conversation_id, str)
            or not _SAFE_CONVERSATION_ID.fullmatch(conversation_id)
        ):
            raise ConversationNotFound("conversation not found")
        run_id = f"run-{uuid.uuid4().hex}"
        with self._changed:
            if self._closed:
                raise JobError("job manager is closed")
            self._compact_queue_locked()
            self._prune_terminal_locked()
            active_count = sum(item.state not in _TERMINAL for item in self._jobs.values())
            if active_count >= self.worker_count + self.max_queued_jobs:
                raise JobCapacityError("demo job capacity is temporarily full")
            created_conversation = False
            if conversation_id is None:
                if replace_turn is not None:
                    raise ConversationConflict("a replacement requires an existing conversation")
                created_conversation = True
                conversation_id = f"conversation-{uuid.uuid4().hex}"
                conversation = _Conversation(conversation_id, mode=mode)
                self._conversations[conversation_id] = conversation
            else:
                source_conversation = self._conversations.get(conversation_id)
                if source_conversation is None:
                    raise ConversationNotFound("conversation not found")
                if source_conversation.mode != mode:
                    raise ConversationConflict("conversation execution mode cannot change")
                if source_conversation.pending_run_id is not None:
                    raise ConversationConflict("conversation already has an active turn")
                if replace_turn is not None:
                    if replace_turn != len(source_conversation.history):
                        raise ConversationConflict("only the latest completed turn can be replaced")
                    created_conversation = True
                    conversation_id = f"conversation-{uuid.uuid4().hex}"
                    conversation = _Conversation(
                        conversation_id,
                        mode=mode,
                        history=deepcopy(source_conversation.history[: replace_turn - 1]),
                    )
                    self._conversations[conversation_id] = conversation
                else:
                    conversation = source_conversation
            if conversation.pending_run_id is not None:
                raise ConversationConflict("conversation already has an active turn")
            through_turn = len(conversation.history) + 1
            if replace_turn is not None and through_turn != replace_turn:
                raise ConversationConflict("replacement turn identity is invalid")
            if through_turn > MAX_CHAT_TURNS:
                raise ConversationConflict("conversation reached the maximum turn count")
            job = _Job(
                run_id=run_id,
                conversation_id=conversation_id,
                scenario_key=INTERACTIVE_SCENARIO_KEY,
                sample_id=INTERACTIVE_SAMPLE_ID,
                through_turn=through_turn,
                turn_type="multi",
                allowed_turn_ids=(through_turn,),
                nlq=nlq,
                history=tuple(deepcopy(conversation.history)),
                mode=mode,
            )
            self._jobs[run_id] = job
            conversation.pending_run_id = run_id
            conversation.updated_at = time.time()
            try:
                self._queue.put_nowait(run_id)
            except queue.Full as exc:
                self._jobs.pop(run_id, None)
                conversation.pending_run_id = None
                if created_conversation:
                    self._conversations.pop(conversation_id, None)
                raise JobCapacityError("demo job capacity is temporarily full") from exc
            self._changed.notify_all()
        return self._snapshot(job)

    def _compact_queue_locked(self) -> None:
        """Drop cancelled/pruned queue entries without disturbing active jobs."""
        retained: list[str | None] = []
        while True:
            try:
                queued_id = self._queue.get_nowait()
            except queue.Empty:
                break
            if queued_id is None or (queued_id in self._jobs and self._jobs[queued_id].state == "queued"):
                retained.append(queued_id)
            else:
                self._queue.task_done()
        for queued_id in retained:
            self._queue.put_nowait(queued_id)

    def _prune_terminal_locked(self) -> None:
        terminal = [job for job in self._jobs.values() if job.state in _TERMINAL and job.stream_refs == 0]
        terminal.sort(key=lambda job: (job.updated_at, job.created_at, job.run_id))
        for job in terminal[: max(0, len(terminal) - self.max_terminal_jobs)]:
            self._jobs.pop(job.run_id, None)

    def _worker_loop(self) -> None:
        while True:
            try:
                run_id = self._queue.get(timeout=self.heartbeat_seconds)
            except queue.Empty:
                with self._changed:
                    if self._closed:
                        return
                continue
            try:
                if run_id is None:
                    return
                with self._changed:
                    job = self._jobs.get(run_id)
                    if job is None or job.state != "queued":
                        continue
                    if self._closed:
                        job.state = "cancelled"
                        job.error = None
                        self._touch(job)
                        self._finish_conversation_locked(job)
                        self._changed.notify_all()
                        continue
                    job.state = "running"
                    job.runtime_started = True
                    self._touch(job)
                    self._changed.notify_all()
                self._execute(job)
            finally:
                self._queue.task_done()

    def _touch(self, job: _Job) -> None:
        job.updated_at = time.time()

    def _set_error(self, job: _Job, code: str, message: str = "The demo run failed safely.") -> None:
        job.state = "error"
        job.final_result = None
        job.error = {"code": code[:64], "message": redact_error(message)[:MAX_STRING_LENGTH]}
        self._touch(job)

    def _finish_conversation_locked(self, job: _Job, runtime_result: Any | None = None) -> None:
        """Commit exactly one authoritative history item and release the conversation."""

        conversation = self._conversations.get(job.conversation_id)
        if conversation is None or conversation.pending_run_id != job.run_id:
            return
        if len(conversation.history) != job.through_turn - 1:
            conversation.pending_run_id = None
            conversation.updated_at = time.time()
            return

        final_output = getattr(runtime_result, "final_output", None)
        if not isinstance(final_output, Mapping) and isinstance(runtime_result, Mapping):
            candidate = runtime_result.get("final_output")
            final_output = candidate if isinstance(candidate, Mapping) else None
        source = final_output if isinstance(final_output, Mapping) else {}
        if not source and isinstance(job.final_result, Mapping):
            source = job.final_result
        decision = source.get("decision")
        if decision not in {"ALLOW", "DENY", "ERROR"} and isinstance(job.final_result, Mapping):
            decision = job.final_result.get("decision")
        if decision not in {"ALLOW", "DENY", "ERROR"}:
            decision = "ERROR"
        blocked_at = source.get("blocked_at")
        if blocked_at not in REQUIRED_MODULES:
            blocked_at = None
        raw_sql = source.get("raw_sql")
        final_sql = source.get("final_sql")
        conversation.history.append(
            {
                "turn_id": job.through_turn,
                "nlq": job.nlq,
                "decision": decision,
                "raw_sql": raw_sql if isinstance(raw_sql, str) else None,
                "final_sql": final_sql if isinstance(final_sql, str) else None,
                "executed": bool(source.get("executed")) if decision == "ALLOW" else False,
                "execution_result_json": source.get("execution_result_json"),
                "blocked_at": blocked_at,
                "route_type": DOCUMENT_ROUTE if source.get("resultType") == "rag" else DATABASE_ROUTE,
            }
        )
        conversation.pending_run_id = None
        conversation.updated_at = time.time()

    def _safe_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        safe = {key: event[key] for key in _EVENT_KEYS if key in event}
        for nested_key, allowed in (("artifact", {"verdict", "reasonCode", "reasonCodes", "riskScore", "count", "rowCount", "executionMs", "timingMs", "table", "tables", "tableName", "column", "columns", "columnName", "violation", "violations"}), ("audit", {"verdict", "reasonCode", "reasonCodes", "riskScore", "count", "rowCount", "executionMs", "timingMs", "table", "tables", "tableName", "column", "columns", "columnName", "violation", "violations", "action", "eventType"})):
            if isinstance(safe.get(nested_key), Mapping):
                safe[nested_key] = {key: safe[nested_key][key] for key in allowed if key in safe[nested_key]}
        value = _redact_payload(sanitize_json_value(safe))
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _take_stream_sequence(job: _Job) -> int:
        sequence = job.next_stream_sequence
        job.next_stream_sequence += 1
        return sequence

    def _append_polled_events(self, job: _Job, events: list[Mapping[str, Any]]) -> None:
        """Expand operation traces into first-class SSE events before the verdict event."""

        identity_keys = ("timestamp", "runId", "scenarioId", "sampleId", "sequenceId", "turnId", "moduleId", "stage")
        for raw_event in events:
            safe = self._safe_event(raw_event)
            trace_lines = safe.pop("traceLines", None)
            event_type = safe.get("eventType", "module")
            if event_type in {"module", "revision"} and isinstance(trace_lines, list):
                bounded_lines = [line for line in trace_lines[:6] if isinstance(line, str) and line]
                total = len(bounded_lines)
                for index, line in enumerate(bounded_lines, start=1):
                    trace = {key: safe[key] for key in identity_keys if key in safe}
                    trace.update({
                        "eventType": "trace",
                        "streamSequence": self._take_stream_sequence(job),
                        "decision": "RUNNING",
                        "detail": line,
                        "traceStep": index,
                        "traceTotal": total,
                    })
                    job.events.append(trace)
            safe["streamSequence"] = self._take_stream_sequence(job)
            job.events.append(safe)

    def _append_system_event(
        self,
        job: _Job,
        *,
        module_id: str,
        stage: str,
        decision: str,
        detail: str,
        trace_lines: list[str] | None = None,
    ) -> None:
        """Publish router/RAG evidence through the same ordered SSE channel."""

        event = {
            "runId": job.run_id,
            "scenarioId": "vertex_rag_engine" if module_id == "RAG" else "query_router",
            "sampleId": job.sample_id,
            "turnId": job.through_turn,
            "moduleId": module_id,
            "stage": stage,
            "decision": decision,
            "detail": detail,
            "traceLines": trace_lines or [],
            "eventType": "module",
        }
        with self._changed:
            self._append_polled_events(job, [event])
            self._touch(job)
            self._changed.notify_all()

    def _safe_final(self, job: _Job, result: Mapping[str, Any]) -> dict[str, Any]:
        value = _redact_payload(sanitize_json_value({key: result[key] for key in _FINAL_KEYS if key in result}))
        if not isinstance(value, dict):
            raise ValueError("final artifact is not an object")
        setting_id = TRUSTEDSQL_SETTING_ID if job.mode == TRUSTEDSQL_MODE else DIRECT_SETTING_ID
        for key, expected in (("runId", job.run_id), ("scenarioId", setting_id), ("sampleId", job.sample_id), ("turnId", job.through_turn), ("mode", job.mode)):
            if key not in value:
                raise ValueError("final artifact identity is incomplete")
            if key in value and value[key] != expected:
                raise ValueError("final artifact identity mismatch")
        if value.get("decision") not in {"ALLOW", "DENY", "ERROR"}:
            raise ValueError("final artifact decision is invalid")
        return value

    def _artifact_if_available(self, job: _Job, runtime_dir: Path) -> Any | None:
        if job.artifact is not None:
            return job.artifact
        run_dir = runtime_dir.parent
        if not run_dir.is_dir():
            return None
        kwargs = {
            "run_id": job.run_id,
            "sample_id": job.sample_id,
            "allowed_turn_ids": job.allowed_turn_ids,
            "selected_final_turn": job.through_turn,
            "setting_id": TRUSTEDSQL_SETTING_ID if job.mode == TRUSTEDSQL_MODE else DIRECT_SETTING_ID,
            "turn_type": job.turn_type,
        }
        artifact = self.artifact_factory(runtime_dir, **kwargs)
        with self._changed:
            job.artifact = artifact
            self.artifact_calls.append(kwargs.copy())
            self._touch(job)
            self._changed.notify_all()
        return artifact

    def _poll_artifact(self, job: _Job, runtime_dir: Path) -> None:
        artifact = self._artifact_if_available(job, runtime_dir)
        if artifact is None:
            return
        poll_events = getattr(artifact, "poll_events", None) or getattr(artifact, "read_events", None)
        read_final = getattr(artifact, "read_final_result", None) or getattr(artifact, "get_final_result", None)
        if not callable(poll_events) or not callable(read_final):
            raise ValueError("artifact stream has no sanitized polling interface")
        events = poll_events()
        final = read_final()
        with self._changed:
            if isinstance(events, list):
                if any(not isinstance(item, Mapping) for item in events):
                    raise ValueError("artifact event is not a mapping")
                self._append_polled_events(job, events)
                del job.events[MAX_LIST_ITEMS:]
            if final is not None:
                if not isinstance(final, Mapping):
                    raise ValueError("final artifact is not a mapping")
                job.final_result = self._safe_final(job, final)
            self._touch(job)
            self._changed.notify_all()

    def _execute(self, job: _Job) -> None:
        route = self._query_router.classify(job.nlq, job.history)
        job.route_type = route.branch
        signal_text = ", ".join(route.signals) if route.signals else "none"
        self._append_system_event(
            job,
            module_id="ROUTER",
            stage="orchestrator",
            decision="ALLOW",
            detail=f"Route selected: {route.branch} · {route.reason}",
            trace_lines=[
                f"hydrate server-owned conversation memory -> {len(job.history)} prior turn(s)",
                "normalize current query with bounded conversation context",
                f"evaluate routing signals -> {signal_text}",
                f"select isolated execution branch -> {route.branch}",
            ],
        )
        if route.branch == DOCUMENT_ROUTE:
            self._execute_rag(job)
            return

        runtime_dir = run_path(self.repo_root, f"{job.run_id}/runtime")
        holder: dict[str, Any] = {}

        def execute_runtime() -> None:
            try:
                runtime = self._new_runtime()
                execute_turn = (
                    getattr(runtime, "execute_direct_turn", None)
                    if job.mode == DIRECT_MODE
                    else getattr(runtime, "execute_turn", None)
                )
                if callable(execute_turn):
                    holder["result"] = execute_turn(
                        job.nlq,
                        job.through_turn,
                        list(job.history),
                        job.run_id,
                    )
                else:
                    # Compatibility for injected legacy fakes; production always
                    # uses execute_turn and never receives client-owned history.
                    holder["result"] = runtime.execute([job.nlq], job.run_id)
            except BaseException as exc:  # worker must convert all runtime failures to safe state
                holder["exception"] = exc

        execution = threading.Thread(target=execute_runtime, name=f"{job.run_id}-runtime", daemon=True)
        execution.start()
        artifact_error: BaseException | None = None
        while execution.is_alive():
            try:
                self._poll_artifact(job, runtime_dir)
            except BaseException as exc:
                artifact_error = exc
                break
            with self._changed:
                self._changed.wait(timeout=self.heartbeat_seconds)
        execution.join()
        if artifact_error is not None:
            with self._changed:
                self._set_error(job, "artifact_invalid")
                self._finish_conversation_locked(job)
                self._changed.notify_all()
            return
        try:
            self._poll_artifact(job, runtime_dir)
        except BaseException as exc:
            with self._changed:
                self._set_error(job, "artifact_invalid")
                self._finish_conversation_locked(job)
                self._changed.notify_all()
            return
        with self._changed:
            if "exception" in holder:
                self._set_error(job, "runtime_failed")
            elif job.final_result is None:
                self._set_error(job, "final_artifact_missing")
            elif job.final_result.get("decision") == "ALLOW":
                job.state = "complete"
                job.error = None
                self._touch(job)
            elif job.final_result.get("decision") == "DENY":
                job.state = "denied"
                job.error = None
                self._touch(job)
            else:
                self._set_error(job, "final_decision_invalid")
            self._finish_conversation_locked(job, holder.get("result"))
            self._prune_terminal_locked()
            self._changed.notify_all()

    def _execute_rag(self, job: _Job) -> None:
        """Run the document branch without constructing a SQL runtime."""

        started = time.perf_counter()
        self._append_system_event(
            job,
            module_id="RAG",
            stage="vertex_rag_retrieval",
            decision="RUNNING",
            detail="Retrieving grounded document context from Vertex AI RAG Engine",
            trace_lines=[
                "bind configured Vertex AI RAG corpus",
                "retrieve semantically relevant Markdown chunks",
                "ground Gemini response on retrieved chunks only",
                "extract attributable sources from grounding metadata",
            ],
        )
        try:
            rag_answer = self._rag_service.answer(job.nlq, job.history)
        except RagError:
            with self._changed:
                self._set_error(job, "rag_failed", "Vertex AI RAG could not return a grounded answer")
                self._finish_conversation_locked(job)
                self._changed.notify_all()
            return
        except BaseException:
            with self._changed:
                self._set_error(job, "rag_failed", "Vertex AI RAG could not return a grounded answer")
                self._finish_conversation_locked(job)
                self._changed.notify_all()
            return

        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        self._append_system_event(
            job,
            module_id="RAG",
            stage="vertex_rag_grounding",
            decision="ALLOW",
            detail=f"Grounded answer completed · sources={len(rag_answer.sources)}",
            trace_lines=[
                f"validate grounding metadata -> {len(rag_answer.sources)} source(s)",
                "deduplicate retrieved document references",
                "publish answer and citation list; database remains untouched",
            ],
        )
        final = {
            "runId": job.run_id,
            "scenarioId": "vertex_rag_engine",
            "sampleId": job.sample_id,
            "turnId": job.through_turn,
            "decision": "ALLOW",
            "executed": False,
            "dbTouched": False,
            "answer": rag_answer.answer,
            "sources": [source.to_dict() for source in rag_answer.sources],
            "latencyMs": latency_ms,
            "error": None,
            "route": ["chat", "orchestrator", "context_memory", "rag"],
            "mode": job.mode,
            "resultType": "rag",
        }
        with self._changed:
            job.final_result = final
            job.state = "complete"
            job.error = None
            self._touch(job)
            self._finish_conversation_locked(job)
            self._prune_terminal_locked()
            self._changed.notify_all()

    def _lookup(self, run_id: str) -> _Job:
        if not isinstance(run_id, str) or not _SAFE_RUN_ID.fullmatch(run_id):
            raise JobNotFound("run not found")
        job = self._jobs.get(run_id)
        if job is None:
            raise JobNotFound("run not found")
        return job

    def _snapshot(self, job: _Job) -> dict[str, Any]:
        return {
            "runId": job.run_id,
            "conversationId": job.conversation_id,
            "state": job.state,
            "scenarioKey": job.scenario_key,
            "sampleId": job.sample_id,
            "throughTurn": job.through_turn,
            "turnType": job.turn_type,
            "mode": job.mode,
            "createdAt": job.created_at,
            "updatedAt": job.updated_at,
            "events": deepcopy(job.events),
            "finalResult": deepcopy(job.final_result),
            "error": deepcopy(job.error),
        }

    def get_job(self, run_id: str) -> dict[str, Any]:
        with self._changed:
            return self._snapshot(self._lookup(run_id))

    def acquire_stream(self, run_id: str) -> dict[str, Any]:
        """Hold a reference so terminal retention cannot prune an active SSE job."""
        with self._changed:
            job = self._lookup(run_id)
            job.stream_refs += 1
            return self._snapshot(job)

    def release_stream(self, run_id: str) -> dict[str, Any]:
        with self._changed:
            job = self._lookup(run_id)
            if job.stream_refs > 0:
                job.stream_refs -= 1
            self._prune_terminal_locked()
            self._changed.notify_all()
            return self._snapshot(job)

    def cancel(self, run_id: str) -> dict[str, Any]:
        with self._changed:
            job = self._lookup(run_id)
            if job.state == "queued":
                job.state = "cancelled"
                job.error = None
                self._touch(job)
                self._finish_conversation_locked(job)
                self._compact_queue_locked()
                self._prune_terminal_locked()
                self._changed.notify_all()
                return self._snapshot(job)
            if job.state == "running":
                raise CancellationConflict("running jobs cannot be cancelled safely")
            return self._snapshot(job)

    def events_since(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        if type(after) is not int or after < 0:
            raise ValueError("after must be a non-negative integer")
        with self._changed:
            job = self._lookup(run_id)
            return [deepcopy(event) for event in job.events if event.get("streamSequence", 0) > after]

    def wait_for_update(self, run_id: str, after: int = 0, timeout: float | None = None) -> dict[str, Any]:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with self._changed:
            job = self._lookup(run_id)
            while True:
                if job.state in _TERMINAL or any(event.get("streamSequence", 0) > after for event in job.events):
                    return self._snapshot(job)
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return self._snapshot(job)
                self._changed.wait(timeout=remaining)

    def bootstrap(self) -> dict[str, Any]:
        readiness = self._readiness()
        safe_catalog = {}
        allowed_scenario = {"key", "canonical_id", "title", "description", "source_file", "role", "user_id", "turn_type", "turn_count", "turns"}
        for key, scenario in self.catalog.items():
            if not isinstance(scenario, Mapping):
                continue
            safe = {field: scenario[field] for field in allowed_scenario if field in scenario}
            turns = safe.get("turns")
            if isinstance(turns, list):
                safe["turns"] = [
                    {field: item[field] for field in ("turn_id", "nlq", "turn_label", "option_id", "replace_turn") if field in item}
                    for item in turns[:MAX_LIST_ITEMS]
                    if isinstance(item, Mapping)
                ]
            safe_catalog[str(key)] = sanitize_json_value(safe)
        return {
            "ready": readiness["ready"],
            "readiness": readiness,
            "catalog": safe_catalog,
            "architecture": {
                "label": "Document RAG router with TrustedSQL vs Direct SQL data paths",
                "modules": list(REQUIRED_MODULES),
                "modes": [TRUSTEDSQL_MODE, DIRECT_MODE],
            },
            "rag": self._rag_service.readiness(),
        }

    def search_prompt_library(
        self, query: str, limit: int = 12, role: str | None = None
    ) -> dict[str, Any]:
        matches = search_dataset_prompt_catalog(
            self.dataset_prompt_catalog, query, limit, role
        )
        return sanitize_json_value(
            {"query": query.strip(), "role": role or "all", "matches": matches}
        )

    def get_prompt_library_scenario(self, scenario_id: str) -> dict[str, Any]:
        return sanitize_json_value(
            get_dataset_prompt_scenario(self.dataset_prompt_catalog, scenario_id)
        )

    def close(self) -> None:
        shutdown_started = time.monotonic()
        with self._changed:
            if self._closed:
                return
            self._closed = True
            # Never block admission of shutdown markers behind queued work.
            # Drained queue entries are terminalized under the same lock as
            # lookup, so a worker cannot start one after shutdown begins.
            while True:
                try:
                    run_id = self._queue.get_nowait()
                except queue.Empty:
                    break
                self._queue.task_done()
                if run_id is not None:
                    job = self._jobs.get(run_id)
                    if job is not None and job.state == "queued":
                        job.state = "cancelled"
                        job.error = None
                        self._touch(job)
                        self._finish_conversation_locked(job)
            for _ in range(min(len(self._workers), self._queue.maxsize)):
                try:
                    self._queue.put_nowait(None)
                except queue.Full:
                    break
            self._changed.notify_all()
        for worker in self._workers:
            remaining = 2.0 - (time.monotonic() - shutdown_started)
            if remaining <= 0:
                break
            worker.join(timeout=remaining)


__all__ = [
    "CancellationConflict", "ConversationConflict", "ConversationNotFound", "DemoJobManager", "JobCapacityError", "JobConflict", "JobError", "JobNotFound", "JobNotReady",
]
