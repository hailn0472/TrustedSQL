from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from trustedsql.config import load_config
from trustedsql.datasets.loader import load_sequences
from trustedsql.modules import prompt_integrity_guard, row_scope_verifier, sql_conformance_validator, sql_generator, table_column_access_validator
from trustedsql.modules import access_planner
from trustedsql.policy.index import load_policy_index
from trustedsql.policy.row_filter import current_user_bindings
from trustedsql.runtime import runner as runtime_runner
from trustedsql.schemas import (
    ResourcePlan,
    GenerationInput,
    MethodTurnOutput,
    ResourceContract,
    RuntimeTurnInput,
    TurnHistoryItem,
    TrustedContext,
)
from trustedsql.sql.schema import load_schema_graph, role_authorized_schema


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _context(role: str = "student", user_id: int = 40) -> TrustedContext:
    config = load_config(PROJECT_ROOT / "configs")
    schema = load_schema_graph(config.ddl_path)
    policy = load_policy_index(config.policy_index_path, config.role_access_matrix_path)
    return TrustedContext(
        run_id="test",
        setting_id="full_trustedsql",
        sequence_id=None,
        sample_id="sample",
        turn_id=1,
        role=role,
        user_id=user_id,
        nlq="test request",
        history=[],
        schema_ddl=schema.ddl,
        schema_graph=schema,
        policy_index=policy,
        compact_schema="schema",
    )


def test_runtime_input_excludes_ground_truth_and_labels() -> None:
    config = load_config(PROJECT_ROOT / "configs")
    sequence = load_sequences(config.datasets, config.project_root, max_samples=1)[0]
    turn = sequence.turns[0]
    runtime = RuntimeTurnInput(
        run_id="test",
        setting_id="full_trustedsql",
        sequence_id=None,
        sample_id=sequence.sample_id,
        turn_id=turn.turn_id,
        role=sequence.role,
        user_id=sequence.user_id,
        nlq=turn.nlq,
        history=[],
    ).to_dict()
    for field in ("sql_gt", "turn_label", "seq_label", "attack_tags", "source_dataset"):
        assert field not in runtime


def test_only_full_trustedsql_setting_is_enabled() -> None:
    config = load_config(PROJECT_ROOT / "configs")
    enabled = config.enabled_settings()
    assert list(enabled) == ["full_trustedsql"]
    assert all(value["modules"] == ["C0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "X1"] for value in enabled.values())


def test_m1_history_turn_limit_is_configurable() -> None:
    history = [
        TurnHistoryItem(turn_id=index, nlq=f"turn {index}", decision="ALLOW")
        for index in range(1, 5)
    ]
    assert [item.nlq for item in prompt_integrity_guard._recent_history(history, {})] == ["turn 2", "turn 3", "turn 4"]
    assert [item.nlq for item in prompt_integrity_guard._recent_history(history, {"history_turn_limit": "max"})] == ["turn 1", "turn 2", "turn 3", "turn 4"]
    assert [item.nlq for item in prompt_integrity_guard._recent_history(history, {"history_turn_limit": 2})] == ["turn 3", "turn 4"]
    assert prompt_integrity_guard._recent_history(history, {"history_turn_limit": 0}) == []


def test_row_filter_bindings_resolve_inside_subquery_scope() -> None:
    context = _context()
    assert current_user_bindings(
        "schedules.class_course_id IN (SELECT class_course_id FROM enrollments WHERE student_id = @user_id)",
        context.schema_graph,
        context.user_id,
    ) == [{"table": "enrollments", "column": "student_id", "operator": "=", "value": 40}]

    lecturer = _context(role="lecturer", user_id=1)
    assert current_user_bindings(
        "attendance.enrollment_id IN (SELECT e.enrollment_id FROM enrollments e "
        "JOIN classcourse cc ON e.class_course_id = cc.class_course_id WHERE cc.lecturer_id = @user_id)",
        lecturer.schema_graph,
        lecturer.user_id,
    ) == [{"table": "classcourse", "column": "lecturer_id", "operator": "=", "value": 1}]


def test_every_scoped_policy_has_schema_valid_current_user_binding() -> None:
    for role, user_id in (("student", 40), ("lecturer", 1)):
        context = _context(role=role, user_id=user_id)
        for policy_ref in context.policy_index.role_policy_refs(role):
            rule = context.policy_index.policy_rule(policy_ref)
            if rule is None or rule.scope_type == "ALL":
                continue
            bindings = current_user_bindings(rule.row_filter, context.schema_graph, user_id)
            assert bindings, policy_ref
            assert all(
                context.schema_graph.has_column(binding["table"], binding["column"])
                for binding in bindings
            ), policy_ref


def test_role_authorized_schema_uses_only_matrix_columns() -> None:
    context = _context()
    ddl = role_authorized_schema(context.schema_graph, context.policy_index.role_access_matrix, "student")
    assert "CREATE TABLE public.students" in ddl
    assert "student_id int4" in ddl
    assert "password" not in ddl
    assert "rolepermission" not in ddl


def test_runner_builds_role_schema_once_and_records_audit_stats() -> None:
    config = load_config(PROJECT_ROOT / "configs")
    runner = runtime_runner.MethodRunner(config, "role-schema-test")
    assert runner._role_authorized_schemas == {}
    first = runner._role_authorized_schema("student")
    second = runner._role_authorized_schema("student")
    assert first is second
    stats = runner._role_authorized_schema_stats["student"]
    assert stats["table_count"] == sum(
        1
        for table in runner.policy_index.role_access_matrix["student"]
        if runner.schema_graph.has_table(table)
    )
    assert stats["column_count"] == sum(
        1
        for table, columns in runner.policy_index.role_access_matrix["student"].items()
        for column in columns
        if runner.schema_graph.has_column(table, column)
    )
    assert stats["character_count"] == len(first)
    assert stats["example_column_count"] >= 0


def test_m3_receives_complete_structured_history() -> None:
    context = _context()
    context.history = [
        TurnHistoryItem(
            turn_id=index,
            nlq=f"question {index}",
            decision="ALLOW",
            final_sql=f"SELECT {index}",
            executed=True,
            execution_result_json=[{"value": index}],
        )
        for index in range(1, 7)
    ]

    class FakeLlm:
        prompt = ""

        def generate_json(self, prompt: str, **_: object):
            self.prompt = prompt
            return ({
                "intent": "continue",
                "policy_refs": ["S03"],
                "requested_resources": [{"table": "enrollments", "columns": []}],
                "scope_type": "SELF",
                "target_resource_table": "enrollments",
                "target_identity_predicates": [],
                "query_filter_predicates": [],
            }, {})

    llm = FakeLlm()
    plan, _ = access_planner._llm_plan(context, llm, {"vertex": {}}, None)
    assert plan.target_resource_table == "enrollments"
    assert "question 1" in llm.prompt
    assert "question 6" in llm.prompt
    assert "SELECT 1" in llm.prompt
    assert '"value": 6' in llm.prompt


def test_m4_validates_requested_resources_and_builds_resource_contract() -> None:
    context = _context()
    plan = ResourcePlan(
        intent="own status",
        policy_refs=["S03"],
        requested_resources=[{"table": "enrollments", "columns": ["status"]}],
        scope_type="SELF",
        target_resource_table="enrollments",
        target_identity_predicates=[],
        query_filter_predicates=[{"table": "enrollments", "column": "status", "operator": "=", "value": "passed"}],
    )
    validated, resource, result = table_column_access_validator.run(context, plan)
    assert result.decision == "ALLOW"
    assert validated == plan
    assert resource is not None
    assert resource.row_filter == "enrollments.student_id = @user_id"
    assert resource.requires_db_proof is False
    assert resource.query_filter_predicates[0]["column"] == "status"


def test_m4_removes_current_user_binding_from_external_targets() -> None:
    context = _context()
    plan = ResourcePlan(
        intent="classmates",
        policy_refs=["S04"],
        requested_resources=[{"table": "students", "columns": ["student_code"]}],
        scope_type="ENROLLED",
        target_resource_table="students",
        target_identity_predicates=[
            {"table": "enrollments", "column": "student_id", "operator": "=", "value": 40}
        ],
    )
    _, resource, result = table_column_access_validator.run(context, plan)
    assert result.decision == "ALLOW"
    assert resource is not None
    assert resource.target_identity_predicates == []
    assert resource.requires_db_proof is False


def test_m4_denies_explicit_disallowed_column() -> None:
    context = _context()
    plan = ResourcePlan(
        intent="password",
        policy_refs=["S01"],
        requested_resources=[{"table": "users", "columns": ["password"]}],
        scope_type="SELF",
        target_resource_table="users",
    )
    _, _, result = table_column_access_validator.run(context, plan)
    assert result.decision == "DENY"
    assert "column_not_allowed:users.password" in result.artifact["violations"]


def test_m4_denies_missing_policy_even_when_resources_are_empty() -> None:
    context = _context()
    plan = ResourcePlan(
        intent="unmapped request",
        policy_refs=[],
        requested_resources=[],
        scope_type="UNKNOWN",
    )
    _, _, result = table_column_access_validator.run(context, plan)
    assert result.decision == "DENY"
    assert result.audit["reason_code"] == "INSUFFICIENT_ACCESS_PLAN"


def test_m5_excludes_query_filters_from_exists_proof() -> None:
    context = _context(role="lecturer", user_id=1)
    resource = ResourceContract(
        policy_refs=["L08"],
        scope_type="ASSIGNED",
        target_resource_table="students",
        scope_anchor_table="students",
        row_filter=(
            "students.student_id IN (SELECT e.student_id FROM enrollments e "
            "JOIN classcourse cc ON e.class_course_id = cc.class_course_id WHERE cc.lecturer_id = @user_id)"
        ),
        target_identity_predicates=[{"table": "classes", "column": "class_name", "operator": "=", "value": "HN-SE1801"}],
        query_filter_predicates=[{"table": "enrollments", "column": "status", "operator": "=", "value": "passed"}],
        requires_db_proof=True,
    )
    sql, _ = row_scope_verifier._compile_exists_sql(
        context.schema_graph,
        "students",
        resource.row_filter or "",
        1,
        resource.target_identity_predicates,
    )
    assert sql is not None
    assert "class_name" in sql
    assert "status" not in sql
    assert "JOIN enrollments" in sql
    assert "JOIN classcourse" in sql
    assert "JOIN curriculum_frameworks" not in sql


def test_m5_self_content_filter_does_not_require_db_proof() -> None:
    context = _context()
    resource = ResourceContract(
        policy_refs=["S03"],
        scope_type="SELF",
        target_resource_table="enrollments",
        scope_anchor_table="enrollments",
        row_filter="enrollments.student_id = @user_id",
        target_identity_predicates=[],
        query_filter_predicates=[{"table": "enrollments", "column": "status", "operator": "=", "value": "passed"}],
        requires_db_proof=False,
    )
    _, authorization, result = row_scope_verifier.run(context, resource, SimpleNamespace())
    assert result.decision == "ALLOW"
    assert authorization is not None
    assert authorization.current_user_bindings == [
        {"table": "enrollments", "column": "student_id", "operator": "=", "value": 40}
    ]


def test_m5_authorization_uses_scoped_subquery_binding() -> None:
    context = _context()
    resource = ResourceContract(
        policy_refs=["S05"],
        scope_type="ENROLLED",
        target_resource_table="schedules",
        scope_anchor_table="schedules",
        row_filter="schedules.class_course_id IN (SELECT class_course_id FROM enrollments WHERE student_id = @user_id)",
        requires_db_proof=False,
    )
    _, authorization, result = row_scope_verifier.run(context, resource, SimpleNamespace())
    assert result.decision == "ALLOW"
    assert authorization is not None
    assert authorization.current_user_bindings == [
        {"table": "enrollments", "column": "student_id", "operator": "=", "value": 40}
    ]


def test_m5_generation_guide_omits_policy_refs() -> None:
    config = load_config(PROJECT_ROOT / "configs")
    runner = runtime_runner.MethodRunner(config, "m5-guide-test")
    guide = runner._m5_generation_guide(
        SimpleNamespace(
            policy_refs=["S03"],
            scope_type="SELF",
            current_user_bindings=[{"table": "enrollments", "column": "student_id", "operator": "=", "value": 40}],
            verified_targets=[],
        )
    )
    assert guide == {
        "scope_type": "SELF",
        "mandatory_scope_predicates": [
            {"table": "enrollments", "column": "student_id", "operator": "=", "value": 40}
        ],
        "verified_target_predicates": [],
    }


def test_m5_denies_invalid_external_target_instead_of_skipping_it() -> None:
    context = _context()
    resource = ResourceContract(
        policy_refs=["S03"],
        scope_type="SELF",
        target_resource_table="enrollments",
        scope_anchor_table="enrollments",
        row_filter="enrollments.student_id = @user_id",
        target_identity_predicates=[
            {"table": "students", "column": "student_id", "operator": "=", "value": "HE182001"}
        ],
        requires_db_proof=True,
    )
    _, _, result = row_scope_verifier.run(context, resource, SimpleNamespace())
    assert result.decision == "DENY"
    assert result.audit["reason_code"] == "INVALID_TARGET_PREDICATE"


def test_m5_denies_scoped_contract_without_canonical_row_filter() -> None:
    context = _context()
    resource = ResourceContract(
        policy_refs=["S03"],
        scope_type="SELF",
        target_resource_table="enrollments",
        scope_anchor_table=None,
        row_filter=None,
        requires_db_proof=False,
    )
    _, _, result = row_scope_verifier.run(context, resource, SimpleNamespace())
    assert result.decision == "DENY"
    assert result.audit["reason_code"] == "MISSING_ROW_FILTER"


def test_m6_receives_complete_history_role_schema_and_m5_guide() -> None:
    context = _context()
    context.history = [
        TurnHistoryItem(
            turn_id=index,
            nlq=f"question {index}",
            decision="ALLOW",
            final_sql=f"SELECT {index}",
            executed=True,
            execution_result_json=[{"value": index}],
        )
        for index in range(1, 7)
    ]

    class FakeLlm:
        prompt = ""

        def generate_text(self, prompt: str, **_: object):
            self.prompt = prompt
            return SimpleNamespace(text="SELECT student_id FROM students", usage={})

    llm = FakeLlm()
    generation = GenerationInput(
        role_authorized_schema="CREATE TABLE public.students (student_id int4);",
        m5_guide={
            "scope_type": "SELF",
            "mandatory_scope_predicates": [
                {"table": "students", "column": "student_id", "operator": "=", "value": 40}
            ],
            "verified_target_predicates": [],
        },
    )
    sql, result = sql_generator.run(context, generation, llm, {"llm": {}})
    assert sql == "SELECT student_id FROM students"
    assert result.decision == "ALLOW"
    assert "question 1" in llm.prompt and "question 6" in llm.prompt
    assert '"value": 6' in llm.prompt
    assert "CREATE TABLE public.students" in llm.prompt
    assert "Verified M5 authorization constraints" in llm.prompt


def test_m7_decision_does_not_depend_on_m5_generation_guide() -> None:
    context = _context()
    generation = GenerationInput(role_authorized_schema=context.schema_ddl, m5_guide={"scope_type": "SELF"})
    validation, result = sql_conformance_validator.run(context, "SELECT c.class_name FROM classes c", generation)
    assert result.decision == "ALLOW"
    assert validation and validation.final_sql
    assert result.artifact["m5_guide_included"] is True


def test_api_429_exhausted_keeps_all_sequence_turns() -> None:
    sequence = SimpleNamespace(
        sample_id="MT-1",
        source_dataset="malicious_multi",
        turn_type="multi",
        seq_label="MALICIOUS",
        role="student",
        user_id=40,
        attack_tags={},
        primary_type="MT-01",
        turns=[SimpleNamespace(turn_id=1, nlq="prefix", turn_label="BENIGN"), SimpleNamespace(turn_id=2, nlq="final", turn_label="MALICIOUS")],
    )
    failed = MethodTurnOutput(
        run_id="run",
        setting_id="full_trustedsql",
        sequence_id="MT-1",
        sample_id="MT-1",
        turn_id=1,
        role="student",
        user_id=40,
        nlq="prefix",
        decision="ERROR",
        blocked_at="M1",
        error="429 RESOURCE_EXHAUSTED",
    )
    outputs = runtime_runner._complete_sequence_after_api_429_exhausted(
        "run", "full_trustedsql", sequence, [failed], failed
    )
    assert [output.turn_id for output in outputs] == [1, 2]
    assert outputs[1].blocked_at == "API_429_SEQUENCE_ABORT"

