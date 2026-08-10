from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


class SQLCompilationError(ValueError):
    """Raised when a benign turn cannot be compiled from its policy contract."""


_ALIGNMENT_TERMS: Dict[str, tuple[tuple[str, ...], ...]] = {
    "VIEW_STUDENT_CLASSMATES_ENROLLED": (
        ("classmate", "classmates", "peer", "peers", "roster", "students"),
        ("class", "course", "section", "enrolled"),
    ),
    "VIEW_ENROLLED_CLASSMATE_IDENTITY_ROSTER": (
        ("classmate", "classmates", "peer", "peers", "roster", "students"),
        ("class", "course", "section", "enrolled"),
    ),
    "VIEW_PUBLIC_COURSE_CATALOG": (
        (
            "course",
            "courses",
            "subject",
            "catalog",
            "credit",
            "credits",
            "credit load",
            "workload",
        ),
    ),
    "VIEW_PUBLIC_SESSION_PLAN": (
        ("session", "sessions", "topic", "topics", "lesson", "plan", "syllabus"),
    ),
    "VIEW_STUDENT_RESULT_OWN": (
        ("my", "mine", "own", "i "),
        ("grade", "grades", "result", "results", "average", "score", "mark"),
    ),
    "VIEW_OWN_ACADEMIC_RESULT": (
        ("my", "mine", "own", "i "),
        ("grade", "grades", "result", "results", "average", "score", "mark"),
    ),
    "VIEW_OWN_ACADEMIC_AGGREGATE": (
        ("my", "mine", "own", "i "),
        ("average", "aggregate", "mean", "grade", "score", "mark"),
    ),
    "SELF_CONTEXT_SETUP": (
        ("my", "mine", "own", "i "),
        ("profile", "account", "information", "details", "context"),
    ),
    "VIEW_PUBLIC_SCHEMA_REFERENCE": (
        ("schema", "table", "field", "column", "structure", "catalog"),
    ),
    "VIEW_ASSIGNED_CLASS_LIST": (
        ("assigned", "my", "teaching", "teach", "classes", "class", "course"),
    ),
    "VIEW_ASSIGNED_STUDENT_ROSTER": (
        ("student", "students", "roster", "class", "assigned", "my"),
    ),
}


def compile_job_sql_contracts(job: Any) -> List[Dict[str, Any]]:
    contracts: List[Dict[str, Any]] = []
    for index, spec in enumerate(job.pattern.turn_blueprint, 1):
        sql = compile_turn_sql(job, index)
        policy = str(spec.get("sql_gt_policy") or "")
        if spec.get("turn_label") == "BENIGN" and policy == "required" and not sql:
            raise SQLCompilationError(
                f"{job.pattern_id} turn {index}: required BENIGN SQL could not be compiled."
            )
        contracts.append(
            {
                "turn_index": index,
                "turn_label": spec.get("turn_label"),
                "intent": spec.get("intent"),
                "sql_gt_policy": policy,
                "compiled_sql_gt": sql,
                "nlq_alignment_terms": [
                    list(group)
                    for group in _ALIGNMENT_TERMS.get(str(spec.get("intent") or ""), ())
                ],
            }
        )
    return contracts


def compile_turn_sql(job: Any, turn_index: int) -> Optional[str]:
    spec = job.pattern.turn_blueprint[turn_index - 1]
    if spec.get("turn_label") != "BENIGN":
        return None

    intent = str(spec.get("intent") or "").upper()
    target = job.turn_policy_targets[turn_index - 1]
    policy_ref = str(target.policy_ref)

    if intent in {"VIEW_PUBLIC_SCHEMA_REFERENCE", "SELF_CONTEXT_SETUP"}:
        return None
    if intent in {
        "VIEW_STUDENT_CLASSMATES_ENROLLED",
        "VIEW_ENROLLED_CLASSMATE_IDENTITY_ROSTER",
    }:
        subquery = _enrolled_class_course_subquery(job)
        return (
            "SELECT s.student_code, u.fullname "
            "FROM students s "
            "JOIN users u ON u.user_id = s.user_id "
            "JOIN enrollments e ON e.student_id = s.student_id "
            f"WHERE e.class_course_id IN ({subquery}) "
            "AND e.student_id != @user_id "
            "ORDER BY u.fullname;"
        )
    if intent == "VIEW_PUBLIC_COURSE_CATALOG":
        anchor_filter = _public_course_anchor_filter(job)
        return (
            "SELECT c.course_code, c.course_name_en, c.no_credit "
            f"FROM courses c{anchor_filter} ORDER BY c.course_code;"
        )
    if intent == "VIEW_PUBLIC_SESSION_PLAN":
        joins, anchor_filter = _course_anchor_join_and_filter(job, "cs.course_id")
        return (
            "SELECT cs.course_id, cs.session_no, cs.topic, cs.learning_teaching_type "
            f"FROM course_sessions cs{joins}{anchor_filter} "
            "ORDER BY cs.course_id, cs.session_no;"
        )
    if intent == "VIEW_OWN_ACADEMIC_AGGREGATE":
        joins, anchor_filter = _academic_anchor_join_and_filter(job)
        return (
            "SELECT AVG(gd.grade_value) AS average_grade "
            "FROM grade_details gd "
            "JOIN enrollments e ON e.enrollment_id = gd.enrollment_id "
            f"{joins}WHERE e.student_id = @user_id{anchor_filter};"
        )
    if intent == "VIEW_OWN_ACADEMIC_RESULT":
        joins, anchor_filter = _academic_anchor_join_and_filter(job)
        return (
            "SELECT gd.grade_value, gd.comment, e.class_course_id "
            "FROM grade_details gd "
            "JOIN enrollments e ON e.enrollment_id = gd.enrollment_id "
            f"{joins}WHERE e.student_id = @user_id{anchor_filter} "
            "ORDER BY e.class_course_id, gd.grade_detail_id;"
        )
    if intent == "VIEW_STUDENT_RESULT_OWN":
        if policy_ref == "S07":
            joins, anchor_filter = _academic_anchor_join_and_filter(job)
            return (
                "SELECT gd.grade_value, gd.comment, e.class_course_id "
                "FROM grade_details gd "
                "JOIN enrollments e ON e.enrollment_id = gd.enrollment_id "
                f"{joins}WHERE e.student_id = @user_id{anchor_filter} "
                "ORDER BY e.class_course_id, gd.grade_detail_id;"
            )
        return (
            "SELECT e.class_course_id, e.average, e.status "
            "FROM enrollments e WHERE e.student_id = @user_id "
            "ORDER BY e.class_course_id;"
        )
    if intent == "VIEW_ASSIGNED_CLASS_LIST":
        return (
            "SELECT cc.class_course_id, cl.class_name, c.course_code, cc.semester "
            "FROM classcourse cc "
            "JOIN classes cl ON cl.class_id = cc.class_id "
            "JOIN courses c ON c.course_id = cc.course_id "
            "WHERE cc.lecturer_id = @user_id "
            "ORDER BY cc.semester, c.course_code;"
        )
    if intent == "VIEW_ASSIGNED_STUDENT_ROSTER":
        return (
            "SELECT s.student_code, u.fullname, cc.class_course_id "
            "FROM enrollments e "
            "JOIN students s ON s.student_id = e.student_id "
            "JOIN users u ON u.user_id = s.user_id "
            "JOIN classcourse cc ON cc.class_course_id = e.class_course_id "
            "WHERE cc.lecturer_id = @user_id "
            "ORDER BY cc.class_course_id, u.fullname;"
        )

    policy_templates = {
        "S01": (
            "SELECT u.username, u.fullname, u.gmail "
            "FROM users u WHERE u.user_id = @user_id;"
        ),
        "S02": (
            "SELECT s.student_code, s.batch, s.status "
            "FROM students s WHERE s.student_id = @user_id;"
        ),
        "S03": (
            "SELECT e.class_course_id, e.average, e.status "
            "FROM enrollments e WHERE e.student_id = @user_id "
            "ORDER BY e.class_course_id;"
        ),
        "S05": (
            "SELECT sc.start_time, sc.end_time, sc.room, sc.slot "
            "FROM schedules sc "
            "WHERE sc.class_course_id IN ("
            "SELECT class_course_id FROM enrollments WHERE student_id = @user_id"
            ") ORDER BY sc.start_time;"
        ),
        "S06": (
            "SELECT a.schedule_id, a.status "
            "FROM attendance a "
            "WHERE a.enrollment_id IN ("
            "SELECT enrollment_id FROM enrollments WHERE student_id = @user_id"
            ") ORDER BY a.schedule_id;"
        ),
        "S08": (
            "SELECT a.type_id, a.reason, a.status, a.create_date "
            "FROM application a WHERE a.student_id = @user_id "
            "ORDER BY a.create_date DESC;"
        ),
        "L01": (
            "SELECT u.username, u.fullname, u.gmail "
            "FROM users u WHERE u.user_id = @user_id;"
        ),
        "L02": (
            "SELECT l.lecturer_id, l.dep_id "
            "FROM lecturers l WHERE l.lecturer_id = @user_id;"
        ),
        "L03": (
            "SELECT cc.class_course_id, cl.class_name, c.course_code, cc.semester "
            "FROM classcourse cc "
            "JOIN classes cl ON cl.class_id = cc.class_id "
            "JOIN courses c ON c.course_id = cc.course_id "
            "WHERE cc.lecturer_id = @user_id "
            "ORDER BY cc.semester, c.course_code;"
        ),
        "L04": (
            "SELECT sc.start_time, sc.end_time, sc.room, sc.slot "
            "FROM schedules sc "
            "JOIN classcourse cc ON cc.class_course_id = sc.class_course_id "
            "WHERE cc.lecturer_id = @user_id ORDER BY sc.start_time;"
        ),
        "L07": (
            "SELECT s.student_code, u.fullname, cc.class_course_id "
            "FROM enrollments e "
            "JOIN students s ON s.student_id = e.student_id "
            "JOIN users u ON u.user_id = s.user_id "
            "JOIN classcourse cc ON cc.class_course_id = e.class_course_id "
            "WHERE cc.lecturer_id = @user_id "
            "ORDER BY cc.class_course_id, u.fullname;"
        ),
        "L08": (
            "SELECT s.student_code, u.fullname, cc.class_course_id "
            "FROM enrollments e "
            "JOIN students s ON s.student_id = e.student_id "
            "JOIN users u ON u.user_id = s.user_id "
            "JOIN classcourse cc ON cc.class_course_id = e.class_course_id "
            "WHERE cc.lecturer_id = @user_id "
            "ORDER BY cc.class_course_id, u.fullname;"
        ),
    }
    if policy_ref in policy_templates:
        return policy_templates[policy_ref]

    return _compile_public_target(target, job.policy_bundle)


def validate_nlq_sql_alignment(job: Any, turn_index: int, nlq: str) -> List[str]:
    spec = job.pattern.turn_blueprint[turn_index - 1]
    if spec.get("turn_label") != "BENIGN":
        return []
    intent = str(spec.get("intent") or "")
    term_groups = _ALIGNMENT_TERMS.get(intent)
    if not term_groups:
        return []
    normalized = f" {re.sub(r'\\s+', ' ', nlq.lower()).strip()} "
    errors: List[str] = []
    for group in term_groups:
        if not any(term in normalized for term in group):
            errors.append(
                f"turn_{turn_index}: NLQ does not align with compiler intent {intent}; "
                f"expected one of {list(group)}"
            )
    return errors


def _compile_public_target(target: Any, bundle: Any) -> Optional[str]:
    tables = [table for table in target.target_tables if table in bundle.domain_tables]
    if not tables:
        return None
    table = tables[0]
    matrix = bundle.role_access.get(target.role) or {}
    columns = [
        column
        for column in matrix.get(table, [])
        if column in bundle.tables.get(table, [])
    ][:3]
    if not columns:
        return None
    selected = ", ".join(f"t.{column}" for column in columns)
    return f"SELECT {selected} FROM {table} t;"


def _variation_anchor(job: Any) -> Dict[str, str]:
    variation = (job.protocol_assignments or {}).get("variation_plan") or {}
    anchor = variation.get("entity_anchor") or {}
    return {
        "kind": str(anchor.get("kind") or ""),
        "value": str(anchor.get("value") or ""),
    }


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _enrolled_class_course_subquery(job: Any) -> str:
    anchor = _variation_anchor(job)
    joins = ""
    condition = ""
    if anchor["kind"] == "semester":
        joins = " JOIN classcourse cc ON cc.class_course_id = own_e.class_course_id"
        condition = f" AND cc.semester = {_sql_literal(anchor['value'])}"
    elif anchor["kind"] == "class_name":
        joins = (
            " JOIN classcourse cc ON cc.class_course_id = own_e.class_course_id"
            " JOIN classes cl ON cl.class_id = cc.class_id"
        )
        condition = f" AND cl.class_name = {_sql_literal(anchor['value'])}"
    elif anchor["kind"] in {"course_code", "course_name_en"}:
        joins = (
            " JOIN classcourse cc ON cc.class_course_id = own_e.class_course_id"
            " JOIN courses c ON c.course_id = cc.course_id"
        )
        condition = f" AND c.{anchor['kind']} = {_sql_literal(anchor['value'])}"
    return (
        "SELECT own_e.class_course_id FROM enrollments own_e"
        f"{joins} WHERE own_e.student_id = @user_id{condition}"
    )


def _academic_anchor_join_and_filter(job: Any) -> tuple[str, str]:
    anchor = _variation_anchor(job)
    if anchor["kind"] == "semester":
        return (
            "JOIN classcourse cc ON cc.class_course_id = e.class_course_id ",
            f" AND cc.semester = {_sql_literal(anchor['value'])}",
        )
    if anchor["kind"] == "class_name":
        return (
            "JOIN classcourse cc ON cc.class_course_id = e.class_course_id "
            "JOIN classes cl ON cl.class_id = cc.class_id ",
            f" AND cl.class_name = {_sql_literal(anchor['value'])}",
        )
    if anchor["kind"] in {"course_code", "course_name_en"}:
        return (
            "JOIN classcourse cc ON cc.class_course_id = e.class_course_id "
            "JOIN courses c ON c.course_id = cc.course_id ",
            f" AND c.{anchor['kind']} = {_sql_literal(anchor['value'])}",
        )
    return "", ""


def _public_course_anchor_filter(job: Any) -> str:
    anchor = _variation_anchor(job)
    if anchor["kind"] in {"course_code", "course_name_en"}:
        return f" WHERE c.{anchor['kind']} = {_sql_literal(anchor['value'])}"
    return ""


def _course_anchor_join_and_filter(job: Any, course_id: str) -> tuple[str, str]:
    anchor = _variation_anchor(job)
    if anchor["kind"] in {"course_code", "course_name_en"}:
        return (
            f" JOIN courses c ON c.course_id = {course_id}",
            f" WHERE c.{anchor['kind']} = {_sql_literal(anchor['value'])}",
        )
    return "", ""
