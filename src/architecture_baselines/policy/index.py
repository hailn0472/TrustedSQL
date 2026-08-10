from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PermissionRule:
    policy_id: str
    policy_rule_ref: str
    role: str
    tables: set[str]
    permitted_columns: dict[str, set[str]]
    denied_columns: dict[str, set[str]]
    row_scope: dict[str, Any]

    def permits_table(self, table: str) -> bool:
        return table.lower() in self.tables

    def permits_columns(self, table: str, columns: set[str]) -> bool:
        table = table.lower()
        permitted = self.permitted_columns.get(table, set())
        denied = self.denied_columns.get(table, set())
        if columns & denied:
            return False
        return not columns or columns.issubset(permitted)


@dataclass
class RolePolicy:
    allowed_tables: set[str] = field(default_factory=set)
    permitted_columns: dict[str, set[str]] = field(default_factory=dict)
    denied_columns: dict[str, set[str]] = field(default_factory=dict)
    row_scopes: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    policy_refs: dict[str, list[str]] = field(default_factory=dict)
    permission_rules: list[PermissionRule] = field(default_factory=list)


def _permission_row_scope(permission: dict[str, Any]) -> dict[str, Any]:
    row_scope = permission.get("row_scope")
    if isinstance(row_scope, dict):
        return {
            "scope_type": row_scope.get("scope_type") or permission.get("scope_type") or "ALL",
            "predicate_template": row_scope.get("predicate_template") or row_scope.get("row_filter") or "ALL_ROWS",
        }
    row_filter = permission.get("row_filter")
    return {
        "scope_type": permission.get("scope_type") or ("ALL" if not row_filter else "SCOPED"),
        "predicate_template": row_filter or "ALL_ROWS",
    }


def _infer_row_scope_tables(permission: dict[str, Any]) -> list[str]:
    row_filter = permission.get("row_filter")
    if not row_filter:
        return []
    match = re.search(r"\b([A-Za-z_][\w]*)\.", str(row_filter))
    return [match.group(1).lower()] if match else []


@dataclass
class PolicyIndex:
    raw: dict[str, Any]
    roles: dict[str, RolePolicy]
    role_access_matrix: dict[str, dict[str, set[str]]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path, role_access_matrix_path: Path | None = None) -> "PolicyIndex":
        raw = json.loads(path.read_text(encoding="utf-8"))
        roles: dict[str, RolePolicy] = {}
        role_access_matrix: dict[str, dict[str, set[str]]] = {}
        if role_access_matrix_path is not None and role_access_matrix_path.exists():
            matrix_raw = json.loads(role_access_matrix_path.read_text(encoding="utf-8"))
            for role, table_map in (matrix_raw or {}).items():
                role_name = str(role).lower()
                role_policy = roles.setdefault(role_name, RolePolicy())
                role_access_matrix.setdefault(role_name, {})
                for table, columns in (table_map or {}).items():
                    table_name = str(table).lower()
                    column_set = {str(column).lower() for column in (columns or [])}
                    role_policy.allowed_tables.add(table_name)
                    role_policy.permitted_columns.setdefault(table_name, set()).update(column_set)
                    role_access_matrix[role_name][table_name] = column_set
        matrix_loaded = bool(role_access_matrix)

        for policy_id, permission in (raw.get("permissions") or {}).items():
            if permission.get("effect") != "PERMIT":
                continue
            legacy_tables = [t.lower() for t in permission.get("resources", {}).get("tables", [])]
            permitted = {
                t.lower(): {c.lower() for c in cols}
                for t, cols in (permission.get("permitted_columns") or {}).items()
            }
            denied = {
                t.lower(): {c.lower() for c in cols}
                for t, cols in (permission.get("denied_columns") or {}).items()
            }
            row_scope = _permission_row_scope(permission)
            target_tables = legacy_tables or _infer_row_scope_tables(permission)
            for role in permission.get("allowed_roles") or []:
                role_name = str(role).lower()
                role_policy = roles.setdefault(role_name, RolePolicy())
                if legacy_tables and not matrix_loaded:
                    rule = PermissionRule(
                        policy_id=str(policy_id),
                        policy_rule_ref=str(permission.get("policy_rule_ref") or policy_id),
                        role=role_name,
                        tables=set(legacy_tables),
                        permitted_columns={table: set(cols) for table, cols in permitted.items()},
                        denied_columns={table: set(cols) for table, cols in denied.items()},
                        row_scope=row_scope,
                    )
                    role_policy.permission_rules.append(rule)
                    for table in legacy_tables:
                        role_policy.allowed_tables.add(table)
                        role_policy.permitted_columns.setdefault(table, set()).update(permitted.get(table, set()))
                        role_policy.denied_columns.setdefault(table, set()).update(denied.get(table, set()))
                for table in target_tables:
                    if table not in role_policy.allowed_tables and matrix_loaded:
                        continue
                    role_policy.row_scopes.setdefault(table, []).append(row_scope)
                    role_policy.policy_refs.setdefault(table, []).append(str(permission.get("policy_rule_ref") or policy_id))
        for role_policy in roles.values():
            for table in sorted(role_policy.allowed_tables):
                if table not in role_policy.row_scopes:
                    role_policy.row_scopes[table] = [{"scope_type": "ALL", "predicate_template": "ALL_ROWS"}]
        return cls(raw=raw, roles=roles, role_access_matrix=role_access_matrix)

    def role_policy(self, role: str) -> RolePolicy:
        return self.roles.get(role, RolePolicy())

    def allowed_tables(self, role: str) -> list[str]:
        return sorted(self.role_policy(role).allowed_tables)

    def role_schema_columns(self, role: str, schema_columns: dict[str, list[str]]) -> dict[str, list[str]]:
        scoped: dict[str, list[str]] = {}
        role_policy = self.role_policy(role)
        for table in sorted(role_policy.allowed_tables):
            if table not in schema_columns:
                continue
            permitted = role_policy.permitted_columns.get(table, set())
            if permitted:
                scoped[table] = [col for col in schema_columns[table] if col in permitted]
        return scoped

    def permitted_columns(self, role: str, table: str) -> set[str]:
        return set(self.role_policy(role).permitted_columns.get(table.lower(), set()))

    def denied_columns(self, role: str, table: str) -> set[str]:
        table = table.lower()
        return set(self.role_policy(role).denied_columns.get(table, set()))

    def matching_rules(self, role: str, table: str, columns: set[str] | None = None) -> list[PermissionRule]:
        table = table.lower()
        columns = {column.lower() for column in (columns or set())}
        role_policy = self.role_policy(role)
        if not role_policy.permission_rules and table in role_policy.allowed_tables:
            return [
                PermissionRule(
                    policy_id=f"synthetic.{role}.{table}",
                    policy_rule_ref=f"synthetic.{role}.{table}",
                    role=role,
                    tables={table},
                    permitted_columns={table: set(role_policy.permitted_columns.get(table, set()))},
                    denied_columns={table: set(role_policy.denied_columns.get(table, set()))},
                    row_scope=scope,
                )
                for scope in role_policy.row_scopes.get(table, [{"scope_type": "ALL", "predicate_template": "ALL_ROWS"}])
                if not columns or columns.issubset(role_policy.permitted_columns.get(table, set()))
            ]
        return [rule for rule in role_policy.permission_rules if rule.permits_table(table) and rule.permits_columns(table, columns)]

