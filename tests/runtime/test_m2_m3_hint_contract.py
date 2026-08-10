from __future__ import annotations

from trustedsql.providers.output_schemas import ResourcePlannerOutput
from pydantic import ValidationError


def test_m3_output_contract_separates_target_and_query_predicates() -> None:
    output = ResourcePlannerOutput.model_validate({
        "intent": "filter previous report",
        "policy_refs": ["S03"],
        "requested_resources": [{"table": "enrollments", "columns": ["status"]}],
        "scope_type": "SELF",
        "target_resource_table": "enrollments",
        "target_identity_predicates": [{"table": "enrollments", "column": "student_id", "operator": "=", "value": 40}],
        "query_filter_predicates": [{"table": "enrollments", "column": "status", "operator": "=", "value": "passed"}],
    })
    assert output.target_identity_predicates[0].column == "student_id"
    assert output.query_filter_predicates[0].column == "status"


def test_m3_output_contract_forbids_legacy_generation_fields() -> None:
    try:
        ResourcePlannerOutput.model_validate({
            "intent": "x",
            "policy_refs": [],
            "requested_resources": [],
            "scope_type": "ALL",
            "select_fields": ["users.fullname"],
        })
    except Exception:
        return
    raise AssertionError("Legacy generation fields must not be accepted")


def test_m3_predicate_contract_validates_operator_value_shape() -> None:
    try:
        ResourcePlannerOutput.model_validate({
            "intent": "x",
            "policy_refs": ["S03"],
            "requested_resources": [{"table": "enrollments", "columns": ["status"]}],
            "scope_type": "SELF",
            "query_filter_predicates": [
                {"table": "enrollments", "column": "status", "operator": "BETWEEN", "value": ["passed"]}
            ],
        })
    except ValidationError:
        return
    raise AssertionError("BETWEEN must require exactly two scalar values")


def test_m3_predicate_contract_supports_null_operators() -> None:
    output = ResourcePlannerOutput.model_validate({
        "intent": "missing value check",
        "policy_refs": ["S03"],
        "requested_resources": [{"table": "enrollments", "columns": ["status"]}],
        "scope_type": "SELF",
        "query_filter_predicates": [
            {"table": "enrollments", "column": "status", "operator": "IS NOT NULL", "value": None}
        ],
    })
    assert output.query_filter_predicates[0].operator == "IS NOT NULL"


def test_m3_predicate_contract_rejects_sql_and_column_reference_values() -> None:
    invalid_values = ["(SELECT course_id FROM courses)", "students.framework_id"]
    for value in invalid_values:
        try:
            ResourcePlannerOutput.model_validate({
                "intent": "x",
                "policy_refs": ["S03"],
                "requested_resources": [{"table": "enrollments", "columns": []}],
                "scope_type": "SELF",
                "target_identity_predicates": [
                    {"table": "enrollments", "column": "class_course_id", "operator": "=", "value": value}
                ],
            })
        except ValidationError:
            continue
        raise AssertionError(f"Expression value must be rejected: {value}")

