from __future__ import annotations

from trustedsql.sql.schema import ForeignKey, SchemaGraph
from benchmark_eval.semantic_result import prefix_result_sufficient


SCHEMA = SchemaGraph(
    ddl="",
    table_columns={
        "courses": ["course_id", "course_code", "course_name_en"],
        "classes": ["class_id", "class_name"],
        "users": ["user_id", "fullname"],
        "students": ["student_id", "major_id"],
        "majors": ["major_id", "major_code"],
        "enrollments": ["enrollment_id", "student_id", "average"],
        "attendance": ["enrollment_id", "schedule_id", "status"],
        "schedules": ["schedule_id", "class_course_id"],
    },
    table_chunks={},
    foreign_keys=[ForeignKey("attendance", "schedule_id", "schedules", "schedule_id")],
)


def test_prefix_rs_ignores_alias_column_order_and_row_order() -> None:
    predicted = [
        {"name": "Algorithms", "code": "CSD201"},
        {"name": "Databases", "code": "DBI202"},
    ]
    expected = [
        {"course_code": "DBI202", "course_name_en": "Databases"},
        {"course_code": "CSD201", "course_name_en": "Algorithms"},
    ]
    assert prefix_result_sufficient(
        predicted,
        expected,
        predicted_sql="SELECT c.course_name_en AS name, c.course_code AS code FROM courses c",
        gold_sql="SELECT c.course_code, c.course_name_en FROM courses c",
        schema=SCHEMA,
    )


def test_prefix_rs_allows_extra_columns_and_extra_rows() -> None:
    assert prefix_result_sufficient(
        [
            {"course_code": "CSD201", "course_name_en": "Algorithms", "class_name": "SE1801"},
            {"course_code": "DBI202", "course_name_en": "Databases", "class_name": "SE1802"},
        ],
        [{"course_code": "CSD201"}],
        predicted_sql=(
            "SELECT co.course_code, co.course_name_en, c.class_name "
            "FROM courses co CROSS JOIN classes c"
        ),
        gold_sql="SELECT co.course_code FROM courses co",
        schema=SCHEMA,
    )


def test_prefix_rs_preserves_duplicate_gold_rows() -> None:
    assert not prefix_result_sufficient(
        [{"course_code": "CSD201"}],
        [{"course_code": "CSD201"}, {"course_code": "CSD201"}],
        predicted_sql="SELECT c.course_code FROM courses c",
        gold_sql="SELECT c.course_code FROM courses c",
        schema=SCHEMA,
    )


def test_prefix_rs_rejects_missing_gold_column() -> None:
    assert not prefix_result_sufficient(
        [{"course_code": "CSD201"}],
        [{"course_code": "CSD201", "course_name_en": "Algorithms"}],
        predicted_sql="SELECT c.course_code FROM courses c",
        gold_sql="SELECT c.course_code, c.course_name_en FROM courses c",
        schema=SCHEMA,
    )


def test_prefix_rs_rejects_different_semantic_columns_even_when_values_match() -> None:
    assert not prefix_result_sufficient(
        [{"major_code": 12}],
        [{"major_id": 12}],
        predicted_sql="SELECT m.major_code FROM majors m",
        gold_sql="SELECT s.major_id FROM students s",
        schema=SCHEMA,
    )


def test_prefix_rs_matches_foreign_key_equivalent_columns() -> None:
    assert prefix_result_sufficient(
        [{"schedule_id": 8}],
        [{"schedule_id": 8}],
        predicted_sql="SELECT s.schedule_id FROM schedules s",
        gold_sql="SELECT a.schedule_id FROM attendance a",
        schema=SCHEMA,
    )

