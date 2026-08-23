import json

import pytest

from demo.backend.app.contracts import (
    ALLOWED_MODULE_IDS,
    ContractError,
    normalize_event,
    normalize_events,
    normalize_final_result,
)


def _event(module_id, *, decision="allow", stage="complete", sequence_id: int | str | None=1):
    return {
        "created_at": "2026-08-23T15:25:49Z",
        "run_id": "run-1",
        "setting_id": "scenario-1",
        "sequence_id": sequence_id,
        "sample_id": "sample-1",
        "turn_id": 1,
        "module_id": module_id,
        "input": {"prompt": "FULL PROMPT", "schema": "FULL SCHEMA"},
        "output": {
            "module_id": module_id,
            "stage": stage,
            "decision": decision,
            "artifact": {
                "verdict": decision,
                "reason_code": "scope_ok",
                "risk_score": 0.1,
                "schema_body": "DO NOT EXPOSE",
            },
            "audit": {
                "row_count": 2,
                "table": "enrollments",
                "columns": ["student_id"],
                "prompt_body": "DO NOT EXPOSE",
            },
            "latency_ms": 4,
            "llm_usage": {"prompt_tokens": 999},
            "raw_objects": {"provider_payload": "SECRET", "attack_label": "rbac"},
            "error": None,
        },
        "unknown": "must not forward",
    }


def _recursive_values(value):
    yield value
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from _recursive_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _recursive_values(nested)


def test_normalize_one_event_per_module_family_preserves_order_and_identity():
    rows = [_event(module_id, sequence_id=index) for index, module_id in enumerate(ALLOWED_MODULE_IDS)]

    events = normalize_events(rows)

    assert [event["moduleId"] for event in events] == list(ALLOWED_MODULE_IDS)
    assert [event["sequenceId"] for event in events] == list(range(len(ALLOWED_MODULE_IDS)))
    assert all(event["runId"] == "run-1" and event["turnId"] == 1 for event in events)
    assert all(set(event) <= {
        "timestamp", "runId", "scenarioId", "sampleId", "sequenceId", "turnId",
        "moduleId", "stage", "decision", "artifact", "audit", "latencyMs", "error",
    } for event in events)


def test_multiturn_sequence_id_accepts_bounded_string():
    normalized = normalize_event(_event("M5", sequence_id="sample-1"))
    assert normalized["sequenceId"] == "sample-1"


def test_event_allowlist_excludes_runtime_prompts_schema_provider_usage_attacks_and_unknowns():
    event = normalize_event(_event("M5"))
    serialized = json.dumps(event)

    for forbidden in ("input", "raw_objects", "FULL PROMPT", "FULL SCHEMA", "provider_payload", "prompt_tokens", "rbac", "unknown"):
        assert forbidden not in serialized
    assert event["artifact"] == {
        "verdict": "allow",
        "reasonCode": "scope_ok",
        "riskScore": 0.1,
    }
    assert event["audit"] == {
        "rowCount": 2,
        "table": "enrollments",
        "columns": ["student_id"],
    }


def test_summary_allowlist_does_not_preserve_nested_forbidden_payloads():
    row = _event("M5")
    row["output"]["artifact"]["verdict"] = {"raw_objects": {"secret": "DO NOT EXPOSE"}}
    row["output"]["audit"]["columns"] = [{"provider_payload": "DO NOT EXPOSE"}]

    event = normalize_event(row)
    serialized = json.dumps(event)

    assert "DO NOT EXPOSE" not in serialized
    assert "verdict" not in event["artifact"]
    assert event["artifact"]["reasonCode"] == "scope_ok"
    assert event["audit"]["columns"] == []


def test_event_requires_consistent_nested_module_identity():
    row = _event("M5")
    del row["output"]["module_id"]
    with pytest.raises(ContractError):
        normalize_event(row)

    row = _event("M5")
    row["output"]["module_id"] = "M6"
    with pytest.raises(ContractError):
        normalize_event(row)


def test_unknown_module_is_safely_labeled_without_forwarding_payload():
    event = normalize_event(_event("UNTRUSTED", decision="deny"))

    assert event["moduleId"] == "unknown"
    assert event["decision"] == "deny"
    assert "UNTRUSTED" not in json.dumps(event)


def _final(*, decision="DENY", executed=False, blocked_at: str | None="M5", error=None, events=None):
    return {
        "run_id": "run-1",
        "setting_id": "scenario-1",
        "sample_id": "sample-1",
        "turn_id": 1,
        "decision": decision,
        "blocked_at": blocked_at,
        "executed": executed,
        "execution_result_json": {"rows": [{"student_id": 1, "name": "A"}], "row_count": 1},
        "execution_columns": ["student_id", "name"],
        "raw_sql": "SELECT * FROM enrollments",
        "final_sql": "SELECT student_id, name FROM enrollments WHERE lecturer_id = 1",
        "module_trace": events if events is not None else [_event("M5", decision="deny")],
        "latency_ms": 12,
        "error": error,
    }


def test_deny_route_preserves_detector_and_separates_enforcement():
    result = normalize_final_result(_final())

    assert result["decision"] == "DENY"
    assert result["detectedAt"] == "M5"
    assert result["enforcedAt"] == "trustedsql"
    assert result["executed"] is False
    assert result["dbTouched"] is False
    assert result["route"] == ["chat", "orchestrator", "context_memory", "rag", "policy_engine", "trustedsql"]
    assert "education_db" not in result["route"]


def test_deny_rejects_all_allow_trace_even_when_blocked_at_is_allowlisted():
    with pytest.raises(ContractError):
        normalize_final_result(_final(
            decision="DENY",
            blocked_at="M5",
            events=[_event("M5", decision="allow"), _event("M6", decision="allow")],
        ))


@pytest.mark.parametrize("blocked_at", [123, {"module": "M5"}, "unknown-module"])
def test_deny_requires_strictly_typed_allowlisted_blocked_at(blocked_at):
    with pytest.raises(ContractError):
        normalize_final_result(_final(blocked_at=blocked_at))


def test_deny_accepts_terminal_matching_deny_trace():
    result = normalize_final_result(_final(
        decision="DENY",
        blocked_at="M5",
        events=[_event("M4", decision="allow"), _event("M5", decision="deny")],
    ))

    assert result["detectedAt"] == "M5"
    assert result["enforcedAt"] == "trustedsql"


def test_allow_executed_route_reaches_education_db():
    result = normalize_final_result(_final(
        decision="allow",
        executed=True,
        blocked_at=None,
        events=[_event("M5", decision="allow"), _event("M6", decision="allow"), _event("M7", decision="allow")],
    ))

    assert result["decision"] == "ALLOW"
    assert result["detectedAt"] is None
    assert result["enforcedAt"] is None
    assert result["executed"] is True
    assert result["dbTouched"] is True
    assert result["route"][-2:] == ["trustedsql", "education_db"]
    assert result["rows"] == [{"student_id": 1, "name": "A"}]


def test_allow_executed_rejects_contradictory_deny_trace():
    with pytest.raises(ContractError):
        normalize_final_result(_final(
            decision="ALLOW",
            executed=True,
            blocked_at=None,
            events=[_event("M5", decision="deny")],
        ))


def test_executed_allow_requires_valid_result_and_sql_types():
    row = _final(decision="ALLOW", executed=True, blocked_at=None, events=[])
    row["execution_result_json"] = None
    with pytest.raises(ContractError):
        normalize_final_result(row)

    row = _final(decision="ALLOW", executed=True, blocked_at=None, events=[])
    row["execution_result_json"] = {"rows": "not-a-list"}
    with pytest.raises(ContractError):
        normalize_final_result(row)

    row = _final(decision="ALLOW", executed=True, blocked_at=None, events=[])
    row["execution_columns"] = None
    with pytest.raises(ContractError):
        normalize_final_result(row)

    row = _final(decision="ALLOW", executed=True, blocked_at=None, events=[])
    row["raw_sql"] = 123
    with pytest.raises(ContractError):
        normalize_final_result(row)

    row = _final(decision="ALLOW", executed=True, blocked_at=None, events=[])
    row["final_sql"] = {"sql": "SELECT 1"}
    with pytest.raises(ContractError):
        normalize_final_result(row)


def test_non_executed_path_may_omit_execution_result():
    row = _final(executed=False)
    row["execution_result_json"] = None

    result = normalize_final_result(row)

    assert result["rows"] == []


def test_final_identity_fields_are_required_and_strictly_typed():
    for field in ("run_id", "setting_id", "sample_id", "turn_id"):
        row = _final()
        del row[field]
        with pytest.raises(ContractError):
            normalize_final_result(row)

    for field in ("run_id", "setting_id", "sample_id"):
        row = _final()
        row[field] = {"not": "a scalar"}
        with pytest.raises(ContractError):
            normalize_final_result(row)

    row = _final()
    row["turn_id"] = "1"
    with pytest.raises(ContractError):
        normalize_final_result(row)

    row = _final()
    row["turn_id"] = True
    with pytest.raises(ContractError):
        normalize_final_result(row)


def test_error_route_keeps_actual_error_module_and_does_not_fabricate_decision():
    result = normalize_final_result(_final(
        decision="error",
        executed=False,
        blocked_at="M3",
        error="provider failed",
        events=[_event("M3", decision="error")],
    ))

    assert result["decision"] == "ERROR"
    assert result["detectedAt"] == "M3"
    assert result["enforcedAt"] is None
    assert result["dbTouched"] is False
    assert result["route"][-1] == "M3"
    assert "education_db" not in result["route"]
    assert result["error"] == "provider failed"


def test_final_result_caps_rows_and_all_strings_and_is_json_serializable():
    row = _final(decision="ALLOW", executed=True, blocked_at=None, events=[])
    row["execution_result_json"] = {"rows": [{"value": "x" * 10_000} for _ in range(500)]}
    row["raw_sql"] = "S" * 10_000

    result = normalize_final_result(row)

    assert len(result["rows"]) <= 100
    assert len(result["rows"][0]["value"]) <= 512
    assert len(result["rawSql"]) <= 512
    json.dumps(result)


def test_malformed_rows_fail_explicitly_instead_of_synthesizing_output():
    with pytest.raises(ContractError):
        normalize_event(None)
    with pytest.raises(ContractError):
        normalize_final_result({"decision": "ALLOW"})
    with pytest.raises(ContractError):
        normalize_final_result(_final(executed="yes"))
