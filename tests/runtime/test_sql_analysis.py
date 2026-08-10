from trustedsql.sql.analysis import analyze_sql
from trustedsql.sql.schema import load_schema_graph


def test_column_resolution_keeps_subquery_alias_scope(project_root):
    schema = load_schema_graph(project_root / "resources" / "schema" / "ddl.md")
    sql = """
    SELECT T3.student_code, T4.fullname
    FROM enrollments AS T1
    JOIN classcourse AS T2 ON T1.class_course_id = T2.class_course_id
    JOIN students AS T3 ON T1.student_id = T3.student_id
    JOIN users AS T4 ON T3.student_id = T4.user_id
    WHERE T1.student_id IN (
      SELECT T1.student_id
      FROM enrollments AS T1
      JOIN classcourse AS T2 ON T1.class_course_id = T2.class_course_id
      JOIN courses AS T3 ON T2.course_id = T3.course_id
      JOIN classes AS T4 ON T2.class_id = T4.class_id
      WHERE T1.student_id = 40
        AND T3.course_code = 'MAE101'
        AND T4.class_name = 'HCM-SE1801'
    )
    """
    analysis = analyze_sql(sql, schema)
    pairs = {(col["table"], col["column"]) for col in analysis.columns}
    assert ("students", "student_code") in pairs
    assert ("users", "fullname") in pairs
    assert ("courses", "student_code") not in pairs
    assert ("classes", "fullname") not in pairs

