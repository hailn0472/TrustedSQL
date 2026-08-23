from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from demo.backend.app.artifacts import (
    ArtifactConflictError,
    ArtifactValidationError,
    ArtifactStream,
    MAX_ARTIFACT_BYTES,
)
from demo.backend.app.contracts import ALLOWED_MODULE_IDS


RUN_ID = "run-1"
SAMPLE_ID = "sample-1"
SETTING_ID = "full_trustedsql"


def event(module: str, *, turn: int = 1, decision: str = "allow", sequence: int | str | None = 1) -> dict:
    return {
        "created_at": "2026-08-23T15:25:49Z",
        "run_id": RUN_ID,
        "setting_id": SETTING_ID,
        "sequence_id": sequence,
        "sample_id": SAMPLE_ID,
        "turn_id": turn,
        "module_id": module,
        "input": {"prompt": "raw prompt", "schema": "raw schema"},
        "output": {
            "module_id": module,
            "stage": "complete",
            "decision": decision,
            "artifact": {"verdict": decision, "reason_code": "ok", "raw_objects": "secret"},
            "audit": {"table": "enrollments", "prompt": "secret"},
            "latency_ms": 2,
            "error": None,
            "raw_objects": {"provider": "secret"},
        },
    }


def final(turn: int, *, trace: list[dict] | None = None) -> dict:
    trace = trace or [
        event(module, decision="deny" if module == "M5" else "allow", turn=turn, sequence=SAMPLE_ID)
        for module in ALLOWED_MODULE_IDS[:6]
    ]
    return {
        "run_id": RUN_ID,
        "setting_id": SETTING_ID,
        "sequence_id": SAMPLE_ID,
        "sample_id": SAMPLE_ID,
        "turn_id": turn,
        "decision": "DENY",
        "blocked_at": "M5",
        "executed": False,
        "execution_result_json": None,
        "execution_columns": [],
        "raw_sql": None,
        "final_sql": None,
        "module_trace": trace,
        "latency_ms": 4,
        "error": None,
    }


@pytest.fixture
def stream(tmp_path: Path) -> ArtifactStream:
    runtime = tmp_path / "demo" / "runs" / RUN_ID / "runtime"
    runtime.mkdir(parents=True)
    return ArtifactStream(
        runtime,
        run_id=RUN_ID,
        sample_id=SAMPLE_ID,
        allowed_turn_ids=(1,),
        selected_final_turn=1,
        setting_id=SETTING_ID,
        turn_type="single",
    )


def write_line(path: Path, value: object, *, newline: bool = True) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value) + ("\n" if newline else ""))


def test_no_file_is_pending_and_has_no_events(stream: ArtifactStream):
    result = stream.poll()
    assert result["events"] == []
    assert result["final_result"] is None
    assert result["status"]["state"] == "pending"


def test_incremental_events_are_normalized_once_and_ordered(stream: ArtifactStream):
    path = stream.module_events_path
    write_line(path, event("C0"))
    first = stream.poll_events()
    write_line(path, event("M1"))
    second = stream.poll_events()
    assert [item["moduleId"] for item in first + second] == ["C0", "M1"]
    assert [item["streamSequence"] for item in first + second] == [1, 2]
    assert stream.poll_events() == []


def test_partial_trailing_line_is_withheld_until_completed(stream: ArtifactStream):
    path = stream.module_events_path
    encoded = json.dumps(event("C0"))
    path.write_text(encoded, encoding="utf-8")
    assert stream.poll_events() == []
    path.write_text(encoded + "\n", encoding="utf-8")
    assert [e["moduleId"] for e in stream.poll_events()] == ["C0"]


def test_normalized_event_never_exposes_raw_payloads(stream: ArtifactStream):
    single_turn = event("C0", sequence=None)
    write_line(stream.module_events_path, single_turn)
    event_value = stream.poll_events()[0]
    assert event_value["sequenceId"] is None
    serialized = json.dumps(event_value)
    for forbidden in ("raw prompt", "raw schema", "raw_objects", "provider", "secret", "prompt"):
        assert forbidden not in serialized


def test_identity_unknown_module_and_malformed_line_are_rejected(stream: ArtifactStream):
    foreign = event("C0")
    foreign["run_id"] = "other"
    write_line(stream.module_events_path, foreign)
    with pytest.raises(ArtifactValidationError):
        stream.poll_events()

    stream.module_events_path.write_text(json.dumps(event("UNTRUSTED")) + "\n", encoding="utf-8")
    with pytest.raises(ArtifactValidationError):
        stream.poll_events()

    stream.module_events_path.write_text("{bad json}\n", encoding="utf-8")
    with pytest.raises(ArtifactValidationError):
        stream.poll_events()


@pytest.mark.parametrize(
    ("field", "value"),
    [("sample_id", "other-sample"), ("turn_id", 99)],
)
def test_foreign_sample_and_turn_are_rejected(stream: ArtifactStream, field: str, value: object):
    row = event("C0")
    row[field] = value
    write_line(stream.module_events_path, row)
    with pytest.raises(ArtifactValidationError):
        stream.poll_events()


def test_multiturn_string_sequence_id_is_accepted(stream: ArtifactStream):
    write_line(stream.module_events_path, event("C0", sequence=SAMPLE_ID))
    assert stream.poll_events()[0]["sequenceId"] == SAMPLE_ID


def test_rewrite_discards_stale_partial_cursor_data(stream: ArtifactStream):
    path = stream.module_events_path
    encoded = json.dumps(event("C0", sequence=SAMPLE_ID))
    path.write_text(encoded[: len(encoded) // 2], encoding="utf-8")
    assert stream.poll_events() == []
    path.write_text(encoded + "\n", encoding="utf-8")
    assert [item["moduleId"] for item in stream.poll_events()] == ["C0"]


def test_duplicate_identical_is_deduped_and_conflict_is_rejected(stream: ArtifactStream):
    write_line(stream.module_events_path, event("C0"))
    assert len(stream.poll_events()) == 1
    write_line(stream.module_events_path, event("C0"))
    assert stream.poll_events() == []
    conflicting = event("C0")
    conflicting["output"]["stage"] = "changed"
    write_line(stream.module_events_path, conflicting)
    with pytest.raises(ArtifactConflictError):
        stream.poll_events()


def test_truncation_rewrite_emits_revision_and_retraction_without_stale_mix(stream: ArtifactStream):
    path = stream.module_events_path
    write_line(path, event("C0"))
    write_line(path, event("M1"))
    assert [e["moduleId"] for e in stream.poll_events()] == ["C0", "M1"]
    path.write_text(json.dumps(event("C0", sequence=2)) + "\n", encoding="utf-8")
    changes = stream.poll_events()
    assert changes[0]["eventType"] == "revision"
    assert changes[0]["moduleId"] == "C0"
    assert any(item["eventType"] == "retract" and item["moduleId"] == "M1" for item in changes)


def test_impossible_order_and_post_terminal_are_rejected(stream: ArtifactStream):
    write_line(stream.module_events_path, event("M1"))
    with pytest.raises(ArtifactValidationError):
        stream.poll_events()

    stream.module_events_path.write_text(json.dumps(event("C0", decision="deny")) + "\n", encoding="utf-8")
    assert stream.poll_events()[0]["moduleId"] == "C0"
    write_line(stream.module_events_path, event("M1"))
    with pytest.raises(ArtifactValidationError):
        stream.poll_events()


def test_api429_replacement_is_explicit_and_not_silently_combined(stream: ArtifactStream):
    path = stream.module_events_path
    write_line(path, event("C0"))
    assert stream.poll_events()[0]["eventType"] == "module"
    replacement = event("C0", sequence=99)
    path.write_text(json.dumps(replacement) + "\n", encoding="utf-8")
    changes = stream.poll_events()
    assert changes[0]["eventType"] == "revision"
    assert changes[0]["revision"] == 1


def test_final_result_requires_ordered_prefix_and_selected_turn(stream: ArtifactStream):
    path = stream.final_rows_path
    write_line(path, final(1))
    result = stream.read_final_result()
    assert result is not None and result["turnId"] == 1
    assert result["decision"] == "DENY"


def test_final_result_accepts_runner_output_only_module_trace(stream: ArtifactStream):
    trace = [
        event(module, turn=1, decision="allow", sequence=None)["output"]
        for module in ALLOWED_MODULE_IDS
    ]
    row = final(1, trace=trace)
    row.update({
        "sequence_id": None,
        "decision": "ALLOW",
        "blocked_at": None,
        "executed": True,
        "execution_result_json": [{"fullname": "Ngo Duc Kien"}],
        "execution_columns": ["fullname"],
        "raw_sql": "SELECT fullname FROM users",
        "final_sql": "SELECT fullname FROM users LIMIT 1",
    })
    write_line(stream.final_rows_path, row)

    result = stream.read_final_result()

    assert result is not None
    assert result.get("decision") == "ALLOW"
    assert result.get("rows") == [{"fullname": "Ngo Duc Kien"}]
    assert [item.get("moduleId") for item in result.get("events", [])] == list(ALLOWED_MODULE_IDS)
    assert result.get("route", [])[-1] == "education_db"


def test_complete_multi_turn_prefix_selects_exact_final_result(tmp_path: Path):
    runtime = tmp_path / "demo" / "runs" / RUN_ID / "runtime"
    runtime.mkdir(parents=True)
    selected = ArtifactStream(
        runtime,
        run_id=RUN_ID,
        sample_id=SAMPLE_ID,
        allowed_turn_ids=(1, 2, 3, 4),
        selected_final_turn=3,
        setting_id=SETTING_ID,
        turn_type="multi",
    )
    for turn in (1, 2):
        write_line(selected.final_rows_path, final(turn))
    assert selected.read_final_result() is None
    write_line(selected.final_rows_path, final(3))
    assert selected.read_final_result()["turnId"] == 3


@pytest.mark.parametrize(
    ("field", "value"),
    [("sample_id", "foreign-sample"), ("turn_id", 99)],
)
def test_final_rows_reject_foreign_sample_and_turn(stream: ArtifactStream, field: str, value: object):
    row = final(1)
    row[field] = value
    write_line(stream.final_rows_path, row)
    with pytest.raises(ArtifactValidationError):
        stream.read_final_result()


def test_final_rows_reject_out_of_order_rows(tmp_path: Path):
    runtime = tmp_path / "demo" / "runs" / RUN_ID / "runtime"
    runtime.mkdir(parents=True)
    selected = ArtifactStream(
        runtime,
        run_id=RUN_ID,
        sample_id=SAMPLE_ID,
        allowed_turn_ids=(1, 2, 3),
        selected_final_turn=3,
        setting_id=SETTING_ID,
        turn_type="multi",
    )
    write_line(selected.final_rows_path, final(1))
    write_line(selected.final_rows_path, final(3))
    with pytest.raises(ArtifactValidationError):
        selected.read_final_result()


def test_partial_trailing_final_row_is_withheld_until_completed(stream: ArtifactStream):
    encoded = json.dumps(final(1))
    stream.final_rows_path.write_text(encoded, encoding="utf-8")
    assert stream.read_final_result() is None
    with stream.final_rows_path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    assert stream.read_final_result()["turnId"] == 1


def test_final_rows_reject_duplicate_out_of_order_and_foreign(stream: ArtifactStream):
    path = stream.final_rows_path
    write_line(path, final(1))
    stream.read_final_result()
    write_line(path, final(1))
    with pytest.raises(ArtifactValidationError):
        stream.read_final_result()


def test_final_trace_rejects_skipped_or_reordered_modules(stream: ArtifactStream):
    invalid = final(1, trace=[
        event("C0", turn=1, sequence=SAMPLE_ID),
        event("M2", turn=1, sequence=SAMPLE_ID),
    ])
    write_line(stream.final_rows_path, invalid)
    with pytest.raises(ArtifactValidationError):
        stream.read_final_result()

    invalid = final(1, trace=[
        event("C0", turn=1, sequence=SAMPLE_ID),
        event("M1", turn=1, sequence=SAMPLE_ID, decision="deny"),
        event("M2", turn=1, sequence=SAMPLE_ID),
    ])
    stream.final_rows_path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")
    with pytest.raises(ArtifactValidationError):
        stream.read_final_result()


def test_status_distinguishes_retry_exit_cancel_and_error_summary(stream: ArtifactStream):
    write_line(
        stream.retry_events_path,
        {
            "run_id": RUN_ID,
            "setting_id": SETTING_ID,
            "sequence_id": None,
            "sample_id": SAMPLE_ID,
            "failed_turn_id": 1,
            "attempt": 1,
            "error_type": "API_429",
            "action": "retry_sequence",
        },
    )
    assert stream.status(process_running=True)["status"] == "api_429_retry"
    assert stream.status(process_running=False, exit_code=1)["status"] == "failed"
    assert stream.status(cancelled=True)["status"] == "cancelled"
    stream.error_summary_path.write_text(json.dumps({"run_id": RUN_ID, "runtime_error_count": 1, "api_429_error_count": 0, "error_groups": {"provider": {"message": "secret"}}}), encoding="utf-8")
    summary = stream.status()["runtime_error_summary"]
    assert summary["runtimeErrorCount"] == 1
    assert "secret" not in json.dumps(summary)




def test_retry_status_requires_complete_matching_api429_metadata(stream: ArtifactStream):
    path = stream.retry_events_path
    metadata = {
        "run_id": RUN_ID,
        "setting_id": SETTING_ID,
        "sequence_id": None,
        "sample_id": SAMPLE_ID,
        "failed_turn_id": 1,
        "error_type": "API_429",
    }
    incomplete = dict(metadata)
    del incomplete["failed_turn_id"]
    path.write_text(json.dumps(incomplete) + "\n", encoding="utf-8")
    with pytest.raises(ArtifactValidationError):
        stream.status(process_running=True)
    path.write_text(json.dumps(metadata), encoding="utf-8")
    assert stream.status(process_running=True)["status"] == "pending"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    assert stream.status(process_running=True)["status"] == "api_429_retry"

    path.write_text(json.dumps({"error_type": "OTHER", "message": "unrelated"}) + "\n", encoding="utf-8")
    assert stream.status(process_running=True)["status"] == "pending"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "other-run"),
        ("setting_id", "other-setting"),
        ("sample_id", "other-sample"),
        ("sequence_id", SAMPLE_ID),
        ("failed_turn_id", 2),
        ("failed_turn_id", True),
        ("error_type", "OTHER"),
    ],
)
def test_api429_retry_rejects_foreign_or_invalid_identity(stream: ArtifactStream, field: str, value: object):
    metadata = {
        "run_id": RUN_ID,
        "setting_id": SETTING_ID,
        "sequence_id": None,
        "sample_id": SAMPLE_ID,
        "failed_turn_id": 1,
        "error_type": "API_429",
    }
    metadata[field] = value
    write_line(stream.retry_events_path, metadata)
    with pytest.raises(ArtifactValidationError):
        stream.status(process_running=True)


@pytest.mark.parametrize("missing", ["run_id", "setting_id", "sequence_id", "sample_id", "failed_turn_id", "error_type"])
def test_api429_retry_rejects_partial_identity(stream: ArtifactStream, missing: str):
    metadata = {
        "run_id": RUN_ID,
        "setting_id": SETTING_ID,
        "sequence_id": None,
        "sample_id": SAMPLE_ID,
        "failed_turn_id": 1,
        "error_type": "API_429",
    }
    del metadata[missing]
    write_line(stream.retry_events_path, metadata)
    with pytest.raises(ArtifactValidationError):
        stream.status(process_running=True)
    with pytest.raises(ArtifactValidationError):
        stream.status(process_running=True)


def test_multiturn_api429_retry_requires_sample_sequence_id(tmp_path: Path):
    runtime = tmp_path / "demo" / "runs" / RUN_ID / "runtime"
    runtime.mkdir(parents=True)
    stream = ArtifactStream(
        runtime,
        run_id=RUN_ID,
        sample_id=SAMPLE_ID,
        allowed_turn_ids=(1, 2, 3),
        selected_final_turn=3,
        setting_id=SETTING_ID,
        turn_type="multi",
    )
    metadata = {
        "run_id": RUN_ID,
        "setting_id": SETTING_ID,
        "sequence_id": SAMPLE_ID,
        "sample_id": SAMPLE_ID,
        "failed_turn_id": 2,
        "error_type": "API_429",
    }
    write_line(stream.retry_events_path, metadata)
    assert stream.status(process_running=True)["status"] == "api_429_retry"


def test_retry_for_unselected_future_turn_is_rejected(tmp_path: Path):
    runtime = tmp_path / "demo" / "runs" / RUN_ID / "runtime"
    runtime.mkdir(parents=True)
    stream = ArtifactStream(
        runtime,
        run_id=RUN_ID,
        sample_id=SAMPLE_ID,
        allowed_turn_ids=(1, 2, 3),
        selected_final_turn=2,
        setting_id=SETTING_ID,
        turn_type="multi",
    )
    write_line(
        stream.retry_events_path,
        {
            "run_id": RUN_ID,
            "setting_id": SETTING_ID,
            "sequence_id": SAMPLE_ID,
            "sample_id": SAMPLE_ID,
            "failed_turn_id": 3,
            "error_type": "API_429",
        },
    )
    with pytest.raises(ArtifactValidationError):
        stream.status(process_running=True)


def test_one_turn_multi_prefix_requires_explicit_turn_type(tmp_path: Path):
    runtime = tmp_path / "demo" / "runs" / RUN_ID / "runtime"
    runtime.mkdir(parents=True)
    with pytest.raises(ValueError):
        ArtifactStream(
            runtime,
            run_id=RUN_ID,
            sample_id=SAMPLE_ID,
            allowed_turn_ids=(1,),
            selected_final_turn=1,
            setting_id=SETTING_ID,
        )


def test_one_turn_multi_prefix_with_explicit_type_requires_sample_sequence_id(tmp_path: Path):
    runtime = tmp_path / "demo" / "runs" / RUN_ID / "runtime"
    runtime.mkdir(parents=True)
    stream = ArtifactStream(
        runtime,
        run_id=RUN_ID,
        sample_id=SAMPLE_ID,
        allowed_turn_ids=(1,),
        selected_final_turn=1,
        setting_id=SETTING_ID,
        turn_type="multi",
    )
    write_line(
        stream.retry_events_path,
        {
            "run_id": RUN_ID,
            "setting_id": SETTING_ID,
            "sequence_id": SAMPLE_ID,
            "sample_id": SAMPLE_ID,
            "failed_turn_id": 1,
            "error_type": "API_429",
        },
    )
    assert stream.status(process_running=True)["status"] == "api_429_retry"


def test_live_multiturn_module_events_preserve_order_with_string_sequence_id(tmp_path: Path):
    runtime = tmp_path / "demo" / "runs" / RUN_ID / "runtime"
    runtime.mkdir(parents=True)
    stream = ArtifactStream(
        runtime,
        run_id=RUN_ID,
        sample_id=SAMPLE_ID,
        allowed_turn_ids=(1, 2),
        selected_final_turn=2,
        setting_id=SETTING_ID,
        turn_type="multi",
    )
    events: list[dict] = []
    for turn in (1, 2):
        for module in ALLOWED_MODULE_IDS:
            write_line(stream.module_events_path, event(module, turn=turn, sequence=SAMPLE_ID))
            events.extend(stream.poll_events())
    assert [(item["turnId"], item["moduleId"], item["sequenceId"]) for item in events] == [
        (turn, module, SAMPLE_ID)
        for turn in (1, 2)
        for module in ALLOWED_MODULE_IDS
    ]


def test_oversized_runtime_error_summary_is_rejected_before_reading(stream: ArtifactStream):
    stream.error_summary_path.write_bytes(b"{" + b"x" * MAX_ARTIFACT_BYTES + b"}")
    with pytest.raises(ArtifactValidationError):
        stream.status()


def test_symlink_path_escape_is_rejected(tmp_path: Path):
    runtime = tmp_path / "demo" / "runs" / RUN_ID / "runtime"
    runtime.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("", encoding="utf-8")
    link = runtime / "module_events.jsonl"
    link.symlink_to(outside)
    stream = ArtifactStream(
        runtime,
        run_id=RUN_ID,
        sample_id=SAMPLE_ID,
        allowed_turn_ids=(1,),
        turn_type="single",
    )
    with pytest.raises(ArtifactValidationError):
        stream.poll_events()


def test_symlinked_run_parent_alias_is_rejected(tmp_path: Path):
    real_run = tmp_path / "demo" / "runs" / "real-run"
    (real_run / "runtime").mkdir(parents=True)
    alias = tmp_path / "demo" / "runs" / "alias-run"
    alias.symlink_to(real_run, target_is_directory=True)
    with pytest.raises(ArtifactValidationError):
        ArtifactStream(
            alias / "runtime",
            run_id="alias-run",
            sample_id=SAMPLE_ID,
            allowed_turn_ids=(1,),
            turn_type="single",
        )


def test_concurrent_poll_calls_are_safe(stream: ArtifactStream):
    write_line(stream.module_events_path, event("C0"))
    results: list[list[dict]] = []
    threads = [threading.Thread(target=lambda: results.append(stream.poll_events())) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(len(value) for value in results) == 1
    json.dumps(results)
