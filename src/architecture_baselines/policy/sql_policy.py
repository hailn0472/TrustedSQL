from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from architecture_baselines.policy.index import PermissionRule, PolicyIndex
from architecture_baselines.sql.parser import SqlSignature, analyze_sql


@dataclass
class PolicyCheck:
    policy_verdict: str
    table_verdict: str
    column_verdict: str
    row_scope_verdict: str
    violations: list[dict[str, Any]] = field(default_factory=list)
    unknown_reasons: list[str] = field(default_factory=list)
    sensitive_unknown: bool = False
    signature: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_verdict": self.policy_verdict,
            "table_verdict": self.table_verdict,
            "column_verdict": self.column_verdict,
            "row_scope_verdict": self.row_scope_verdict,
            "violations": self.violations,
            "unknown_reasons": self.unknown_reasons,
            "sensitive_unknown": self.sensitive_unknown,
            "signature": self.signature,
        }


def _resolve_unqualified_column(signature: SqlSignature, column: str, schema_columns: dict[str, list[str]] | None = None) -> str | None:
    if len(signature.tables) == 1:
        return signature.tables[0]
    if schema_columns:
        candidates = [table for table in signature.tables if column in schema_columns.get(table, [])]
        if len(candidates) == 1:
            return candidates[0]
    return None


def _rules_require_filter(rules: list[PermissionRule]) -> bool:
    if not rules:
        return False
    return not any((rule.row_scope.get("scope_type") or "ALL") == "ALL" for rule in rules)


def _predicate_usable_for_outer_sql(template: str, table: str, outer_tables: set[str]) -> bool:
    outer_prefix = re.split(r"\(\s*SELECT\b", template, maxsplit=1, flags=re.IGNORECASE)[0]
    refs = {ref.lower() for ref in re.findall(r"\b([A-Za-z_][\w]*)\.", outer_prefix)}
    return refs.issubset(outer_tables | {table.lower()})


def _scope_template(rules: list[PermissionRule], table: str, outer_tables: set[str]) -> str | None:
    for rule in rules:
        template = rule.row_scope.get("predicate_template")
        if template and template != "ALL_ROWS":
            template_text = str(template)
            if _predicate_usable_for_outer_sql(template_text, table, outer_tables):
                return template_text
    return None


def _compact_sql(value: str) -> str:
    return re.sub(r"\s+", "", value.lower().strip().strip(";"))


def _canonical_expr(value: exp.Expression) -> str:
    return _compact_sql(value.sql(dialect="postgres"))


def _parse_predicate_expr(predicate: str) -> exp.Expression | None:
    try:
        expression = sqlglot.parse_one(f"SELECT 1 WHERE {predicate}", read="postgres")
    except Exception:
        return None
    where = expression.find(exp.Where)
    return where.this if where is not None else None


def _safe_conjuncts(expression: exp.Expression | None) -> list[exp.Expression]:
    if expression is None:
        return []
    if isinstance(expression, exp.And):
        return _safe_conjuncts(expression.args.get("this")) + _safe_conjuncts(expression.args.get("expression"))
    if any(isinstance(node, (exp.Or, exp.Not)) for node in expression.walk()):
        return []
    return [expression]


def _scope_evidence_conjuncts(sql: str) -> set[str]:
    expression = sqlglot.parse_one(sql, read="postgres")
    conjuncts: list[exp.Expression] = []
    for where in expression.find_all(exp.Where):
        conjuncts.extend(_safe_conjuncts(where.this))
    for join in expression.find_all(exp.Join):
        conjuncts.extend(_safe_conjuncts(join.args.get("on")))
    for having in expression.find_all(exp.Having):
        conjuncts.extend(_safe_conjuncts(having.this))
    return {_canonical_expr(conjunct) for conjunct in conjuncts}


def _replace_table_with_alias(predicate: str, table: str, aliases: dict[str, str]) -> str:
    alias = next((a for a, t in aliases.items() if t == table), None)
    if alias:
        return re.sub(rf"\b{re.escape(table)}\.", f"{alias}.", predicate)
    return predicate


def _has_scope_evidence(sql: str, rules: list[PermissionRule], table: str, aliases: dict[str, str], user_id: int) -> bool:
    if not _rules_require_filter(rules):
        return True
    try:
        evidence_conjuncts = _scope_evidence_conjuncts(sql)
    except Exception:
        return False
    for rule in rules:
        template = rule.row_scope.get("predicate_template")
        if not template or template == "ALL_ROWS":
            return True
        predicate = str(template).replace("@user_id", str(user_id)).replace("{current_user_id}", str(user_id))
        candidates = {predicate, _replace_table_with_alias(predicate, table, aliases)}
        for candidate in candidates:
            candidate_expr = _parse_predicate_expr(candidate)
            if candidate_expr is not None and _canonical_expr(candidate_expr) in evidence_conjuncts:
                return True
            if _compact_sql(candidate) in evidence_conjuncts:
                return True
    return False


def _expand_wildcard_columns(table: str, schema_columns: dict[str, list[str]] | None) -> set[str] | None:
    if not schema_columns:
        return None
    columns = schema_columns.get(table.lower())
    return set(columns) if columns is not None else None


def _columns_by_table(signature: SqlSignature, schema_columns: dict[str, list[str]] | None = None) -> tuple[dict[str, set[str]], list[str]]:
    columns_by_table: dict[str, set[str]] = {table: set() for table in signature.tables}
    unknowns: list[str] = []
    for ref in signature.columns:
        table = ref.table or _resolve_unqualified_column(signature, ref.column, schema_columns)
        if ref.column == "*":
            if table:
                expanded = _expand_wildcard_columns(table, schema_columns)
                if expanded is None:
                    unknowns.append(f"wildcard_without_schema:{table}")
                else:
                    columns_by_table.setdefault(table, set()).update(expanded)
            elif schema_columns and signature.tables:
                for wildcard_table in signature.tables:
                    expanded = _expand_wildcard_columns(wildcard_table, schema_columns)
                    if expanded is None:
                        unknowns.append(f"wildcard_without_schema:{wildcard_table}")
                    else:
                        columns_by_table.setdefault(wildcard_table, set()).update(expanded)
            else:
                unknowns.append("ambiguous_wildcard:*")
            continue
        if not table:
            unknowns.append(f"ambiguous_column:{ref.column}")
            continue
        columns_by_table.setdefault(table, set()).add(ref.column)
    return columns_by_table, unknowns


def _unknown_columns_are_sensitive(signature: SqlSignature, role: str, policy: PolicyIndex) -> bool:
    for table in signature.tables:
        if policy.denied_columns(role, table):
            return True
        if _rules_require_filter(policy.matching_rules(role, table, set())):
            return True
    return False


def check_sql_policy(sql: str, role: str, user_id: int, policy: PolicyIndex, schema_columns: dict[str, list[str]] | None = None) -> PolicyCheck:
    signature = analyze_sql(sql)
    violations: list[dict[str, Any]] = []
    unknowns: list[str] = []
    if signature.parser_status != "PARSED":
        return PolicyCheck("UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", unknown_reasons=[f"parser_{signature.parser_status.lower()}: {signature.error}"], sensitive_unknown=True, signature=signature.to_dict())
    role_policy = policy.role_policy(role)
    table_verdict = "COMPLIANT"
    for table in signature.tables:
        if table not in role_policy.allowed_tables:
            table_verdict = "VIOLATION"
            violations.append({"code": "RB-01", "table": table, "message": f"Role {role} cannot access table {table}"})
    columns_by_table, column_unknowns = _columns_by_table(signature, schema_columns)
    unknowns.extend(column_unknowns)
    column_verdict = "COMPLIANT"
    for table, columns in columns_by_table.items():
        denied = policy.denied_columns(role, table)
        permitted = policy.permitted_columns(role, table)
        for column in columns:
            if column in denied:
                column_verdict = "VIOLATION"
                violations.append({"code": "RB-02", "table": table, "column": column, "message": "Explicitly denied column"})
            elif not permitted or column not in permitted:
                column_verdict = "VIOLATION"
                violations.append({"code": "RB-02", "table": table, "column": column, "message": "Column is not in permitted set"})
    row_scope_verdict = "COMPLIANT"
    sensitive_unknown = _unknown_columns_are_sensitive(signature, role, policy) if column_unknowns else False
    for table in signature.tables:
        rules = policy.matching_rules(role, table, columns_by_table.get(table, set()))
        if not rules and table in role_policy.allowed_tables:
            row_scope_verdict = "UNKNOWN"
            sensitive_unknown = True
            unknowns.append(f"no_matching_permission_rule:{table}")
        elif _rules_require_filter(rules) and not _has_scope_evidence(signature.normalized_sql or sql, rules, table, signature.aliases, user_id):
            row_scope_verdict = "MISSING"
            sensitive_unknown = True
            unknowns.append(f"missing_or_unproven_row_scope:{table}")
    if violations:
        policy_verdict = "VIOLATION"
    elif row_scope_verdict == "MISSING":
        policy_verdict = "NEEDS_REWRITE"
    elif unknowns:
        policy_verdict = "UNKNOWN"
    else:
        policy_verdict = "COMPLIANT"
    return PolicyCheck(policy_verdict, table_verdict, column_verdict, row_scope_verdict, violations, unknowns, sensitive_unknown, signature.to_dict())


def _nested_subquery_rewrite(sql: str, predicates_by_table: dict[str, str]) -> str:
    expression = sqlglot.parse_one(sql, read="postgres")

    def transform(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Table):
            return node
        table = (node.name or "").lower()
        predicate = predicates_by_table.get(table)
        if not predicate:
            return node
        alias = node.alias or table
        inner = sqlglot.parse_one(f"SELECT * FROM {table} WHERE {predicate}", read="postgres")
        subquery = exp.Subquery(this=inner)
        subquery.set("alias", exp.TableAlias(this=exp.to_identifier(alias)))
        return subquery

    return expression.transform(transform).sql(dialect="postgres")


def rewrite_sql_with_policy(sql: str, role: str, user_id: int, policy: PolicyIndex, schema_columns: dict[str, list[str]] | None = None) -> dict[str, Any]:
    signature = analyze_sql(sql)
    if signature.parser_status != "PARSED" or not signature.is_select_only:
        return {"rewrite_applied": False, "rewrite_status": "UNSUPPORTED", "rewrite_strategy": "NONE", "injected_conditions": [], "rewritten_sql": None}
    predicates_by_table: dict[str, str] = {}
    outer_tables = set(signature.tables)
    for table in signature.tables:
        # D4 is an RLS rewrite hook, not a table/column permission verifier.
        # It must not skip row-scope rewriting just because the candidate SQL
        # selected a column that will later be diagnosed as a column violation.
        rules = policy.matching_rules(role, table, set())
        if _rules_require_filter(rules) and not _has_scope_evidence(signature.normalized_sql or sql, rules, table, signature.aliases, user_id):
            template = _scope_template(rules, table, outer_tables)
            if not template:
                return {"rewrite_applied": False, "rewrite_status": "FAILED", "rewrite_strategy": "NONE", "injected_conditions": [], "rewritten_sql": None}
            predicates_by_table[table] = template.replace("@user_id", str(user_id)).replace("{current_user_id}", str(user_id))
    if not predicates_by_table:
        return {"rewrite_applied": False, "rewrite_status": "NOOP", "rewrite_strategy": "NONE", "injected_conditions": [], "rewritten_sql": signature.normalized_sql}
    if re.search(r"\b(UNION|EXCEPT|INTERSECT)\b", signature.normalized_sql or "", flags=re.IGNORECASE):
        return {"rewrite_applied": False, "rewrite_status": "UNSUPPORTED", "rewrite_strategy": "NONE", "injected_conditions": [], "rewritten_sql": None}
    try:
        rewritten = _nested_subquery_rewrite(signature.normalized_sql or sql, predicates_by_table)
    except Exception as exc:
        return {"rewrite_applied": False, "rewrite_status": "FAILED", "rewrite_strategy": "NONE", "injected_conditions": [], "rewritten_sql": None, "error": str(exc)}
    return {"rewrite_applied": True, "rewrite_status": "SUCCESS", "rewrite_strategy": "NESTED_SUBQUERY", "injected_conditions": list(predicates_by_table.values()), "rewritten_sql": rewritten}

