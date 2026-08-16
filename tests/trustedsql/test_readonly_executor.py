from __future__ import annotations

from trustedsql.db.executor import _assert_select_only


def test_readonly_executor_accepts_plain_select() -> None:
    ok, signature = _assert_select_only("SELECT student_id FROM students WHERE student_id = 1")

    assert ok is True
    assert signature["is_select_only"] is True


def test_readonly_executor_rejects_data_modifying_cte() -> None:
    sql = """
    WITH deleted AS (
        DELETE FROM students WHERE student_id = 1 RETURNING student_id
    )
    SELECT student_id FROM deleted
    """

    ok, signature = _assert_select_only(sql)

    assert ok is False
    assert "DANGEROUS_KEYWORD" in signature["risks"]


def test_readonly_executor_rejects_multi_statement_select() -> None:
    ok, signature = _assert_select_only("SELECT 1; SELECT 2")

    assert ok is False
    assert signature["multi_statement"] is True
