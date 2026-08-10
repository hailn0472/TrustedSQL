from __future__ import annotations

from typing import Any

from trustedsql.policy.index import PolicyIndex
from trustedsql.schemas import ModuleResult, RuntimeTurnInput, TrustedContext
from trustedsql.sql.schema import SchemaGraph


def _context_to_serializable(context: TrustedContext) -> dict[str, Any]:
    return {
        "run_id": context.run_id,
        "setting_id": context.setting_id,
        "sequence_id": context.sequence_id,
        "sample_id": context.sample_id,
        "turn_id": context.turn_id,
        "role": context.role,
        "user_id": context.user_id,
        "nlq": context.nlq,
        "nlq_chars": len(context.nlq),
        "history": [h.to_dict() for h in context.history],
        "schema_ddl_chars": len(context.schema_ddl),
        "compact_schema_chars": len(context.compact_schema or ""),
    }


def run(
    turn: RuntimeTurnInput,
    schema_graph: SchemaGraph,
    policy_index: PolicyIndex,
    compact_schema: str | None = None,
) -> tuple[TrustedContext, ModuleResult]:
    context = TrustedContext(
        run_id=turn.run_id,
        setting_id=turn.setting_id,
        sequence_id=turn.sequence_id,
        sample_id=turn.sample_id,
        turn_id=turn.turn_id,
        role=turn.role,
        user_id=turn.user_id,
        nlq=turn.nlq,
        history=turn.history,
        schema_ddl=schema_graph.ddl,
        schema_graph=schema_graph,
        policy_index=policy_index,
        compact_schema=compact_schema,
    )
    result = ModuleResult(
        module_id="C0",
        stage="runtime_context_builder",
        decision="ALLOW",
        artifact={
            "role": turn.role,
            "user_id": turn.user_id,
            "nlq": turn.nlq,
            "nlq_chars": len(turn.nlq),
            "history_count": len(turn.history),
            "history_summary": [
                {"turn_id": h.turn_id, "decision": h.decision, "executed": h.executed}
                for h in turn.history
            ],
            "schema_ddl_chars": len(schema_graph.ddl),
            "compact_schema_chars": len(compact_schema or ""),
            "schema_tables": sorted(schema_graph.tables) if hasattr(schema_graph, "tables") else [],
            "schema_table_count": len(schema_graph.tables) if hasattr(schema_graph, "tables") else 0,
            "policy_index_rules_count": len(policy_index.permissions()) if hasattr(policy_index, "permissions") else 0,
            "policy_index_role_tables_count": len(policy_index.role_access_matrix.get(turn.role, {})),
        },
        audit={
            "runtime_fields": ["role", "user_id", "nlq", "history", "schema", "compact_schema", "policy"],
            "trusted_context_keys": ["run_id", "setting_id", "sequence_id", "sample_id", "turn_id", "role", "user_id", "nlq", "history", "schema_ddl", "schema_graph", "policy_index", "compact_schema"],
        },
        raw_objects={"trusted_context": _context_to_serializable(context)},
    )
    return context, result
