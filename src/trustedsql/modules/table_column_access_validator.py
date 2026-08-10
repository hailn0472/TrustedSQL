from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from trustedsql.modules.common import timed_module
from trustedsql.policy.index import PolicyIndex, PolicyRule
from trustedsql.policy.row_filter import current_user_bindings, predicate_matches_binding
from trustedsql.schemas import ModuleResult, ResourceContract, ResourcePlan, TrustedContext
from trustedsql.sql.schema import SchemaGraph, normalize_name


def run(
    context: TrustedContext,
    plan: ResourcePlan | None,
    m2_hint: dict[str, Any] | None = None,
) -> tuple[ResourcePlan | None, ResourceContract | None, ModuleResult]:
    def _inner() -> ModuleResult:
        if plan is None:
            return ModuleResult("M4", "table_column_access_validator", "DENY", audit={"reason_code": "MISSING_ACCESS_PLAN"})
        violations = validate_plan(plan, context.policy_index, context.schema_graph, context.role)
        if violations:
            return ModuleResult(
                "M4",
                "table_column_access_validator",
                "DENY",
                artifact={"nlq": context.nlq, "access_plan": asdict(plan), "violations": violations},
                audit={"reason_code": "ACCESS_PLAN_VIOLATION", "violations_count": len(violations)},
            )
        if not plan.policy_refs:
            return ModuleResult(
                "M4",
                "table_column_access_validator",
                "DENY",
                artifact={"nlq": context.nlq, "access_plan": asdict(plan), "violations": ["missing_policy_ref"]},
                audit={"reason_code": "INSUFFICIENT_ACCESS_PLAN"},
            )
        resource = build_resource_contract(plan, context.policy_index, context.schema_graph, context.user_id)
        return ModuleResult(
            "M4",
            "table_column_access_validator",
            "ALLOW",
            artifact={
                "nlq": context.nlq,
                "access_plan": asdict(plan),
                "resource_contract": asdict(resource),
                "m2_downstream_hint_observed": m2_hint,
            },
            audit={
                "access_source": "role_access_matrix",
                "validated_resource_count": len(plan.requested_resources),
                "validated_column_count": sum(len(item.get("columns") or []) for item in plan.requested_resources),
                "target_identity_predicates_count": len(plan.target_identity_predicates),
                "external_target_predicates_count": len(resource.target_identity_predicates),
                "current_user_predicates_removed_count": len(plan.target_identity_predicates) - len(resource.target_identity_predicates),
                "query_filter_predicates_count": len(plan.query_filter_predicates),
            },
            raw_objects={"validated_access_plan": asdict(plan), "resource_contract": asdict(resource)},
        )

    result = timed_module("M4", "table_column_access_validator", _inner)
    if result.decision != "ALLOW":
        return None, None, result
    return ResourcePlan(**result.artifact["access_plan"]), ResourceContract(**result.artifact["resource_contract"]), result


def validate_plan(plan: ResourcePlan, policy: PolicyIndex, schema: SchemaGraph, role: str) -> list[str]:
    violations: list[str] = []
    allowed_refs = set(policy.role_policy_refs(role))
    for ref in plan.policy_refs:
        if ref not in allowed_refs:
            violations.append(f"policy_ref_not_allowed:{ref}")
    target_table = normalize_name(plan.target_resource_table or "")
    if target_table:
        if not schema.has_table(target_table):
            violations.append(f"unknown_target_resource_table:{target_table}")
        elif not policy.role_can_access_table(role, target_table):
            violations.append(f"target_resource_table_not_allowed:{target_table}")
    for resource in plan.requested_resources:
        table = normalize_name(str(resource.get("table") or ""))
        if not schema.has_table(table):
            violations.append(f"unknown_table:{table}")
            continue
        if not policy.role_can_access_table(role, table):
            violations.append(f"table_not_allowed:{table}")
            continue
        for raw_column in resource.get("columns") or []:
            column = normalize_name(str(raw_column))
            if not schema.has_column(table, column):
                violations.append(f"unknown_column:{table}.{column}")
            elif not policy.role_can_access_column(role, table, column):
                violations.append(f"column_not_allowed:{table}.{column}")
    for kind, predicates in (
        ("target", plan.target_identity_predicates),
        ("query", plan.query_filter_predicates),
    ):
        for predicate in predicates:
            table = normalize_name(str(predicate.get("table") or ""))
            column = normalize_name(str(predicate.get("column") or ""))
            if not schema.has_column(table, column):
                violations.append(f"unknown_{kind}_predicate_column:{table}.{column}")
            elif not policy.role_can_access_column(role, table, column):
                violations.append(f"{kind}_predicate_column_not_allowed:{table}.{column}")
    return sorted(set(violations))


def build_resource_contract(
    plan: ResourcePlan,
    policy: PolicyIndex,
    schema: SchemaGraph,
    user_id: int,
) -> ResourceContract:
    rules = [rule for ref in plan.policy_refs if (rule := policy.policy_rule(ref))]
    selected = _select_policy_rule(rules, plan)
    row_filter = selected.row_filter if selected else None
    scope_type = selected.scope_type if selected else plan.scope_type
    bindings = current_user_bindings(row_filter, schema, user_id)
    external_targets = [
        dict(predicate)
        for predicate in plan.target_identity_predicates
        if not predicate_matches_binding(predicate, bindings)
    ]
    return ResourceContract(
        policy_refs=plan.policy_refs,
        scope_type=scope_type,
        target_resource_table=plan.target_resource_table,
        scope_anchor_table=_anchor_from_row_filter(row_filter),
        row_filter=row_filter,
        target_identity_predicates=external_targets,
        query_filter_predicates=[dict(item) for item in plan.query_filter_predicates],
        requires_db_proof=bool(external_targets and scope_type != "ALL"),
    )


def _select_policy_rule(rules: list[PolicyRule], plan: ResourcePlan) -> PolicyRule | None:
    if not rules:
        return None
    requested_tables = {normalize_name(str(item.get("table") or "")) for item in plan.requested_resources}
    target = normalize_name(plan.target_resource_table or "")

    def score(rule: PolicyRule) -> tuple[int, int]:
        anchor = _anchor_from_row_filter(rule.row_filter) or ""
        value = 0
        if target and anchor == target:
            value += 4
        if anchor in requested_tables:
            value += 2
        if rule.scope_type == plan.scope_type:
            value += 1
        return value, -rules.index(rule)

    return max(rules, key=score)


def _anchor_from_row_filter(row_filter: str | None) -> str | None:
    if not row_filter:
        return None
    match = re.search(r"\b([A-Za-z_][\w]*)\.", row_filter)
    return normalize_name(match.group(1)) if match else None
