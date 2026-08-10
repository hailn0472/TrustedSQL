from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from .io import load_json


RBAC_TAG_BY_BOUNDARY = {
    "T1_table": "RB-01",
    "T2_column": "RB-02",
    "T3_row": "RB-03",
}


@dataclass(frozen=True)
class PolicyTarget:
    rbac_violation: Optional[str]
    violated_policies: Optional[List[str]]
    policy_ref: Optional[str]
    policy_name: Optional[str]
    role: str
    target_kind: str
    tables: List[str]
    columns: List[str]
    row_filter: Optional[str]
    description: str
    note: Optional[str] = None

    @property
    def attack_tags_fragment(self) -> Dict[str, Any]:
        return {
            "rbac_violation": None if self.rbac_violation is None else [self.rbac_violation],
            "violated_policies": self.violated_policies,
        }

    def to_prompt_context(self) -> Dict[str, Any]:
        return {
            "rbac_violation": self.rbac_violation,
            "violated_policies": self.violated_policies,
            "policy_ref": self.policy_ref,
            "policy_name": self.policy_name,
            "role": self.role,
            "target_kind": self.target_kind,
            "tables": self.tables,
            "columns": self.columns,
            "row_filter": self.row_filter,
            "description": self.description,
            "note": self.note,
        }


class PolicyIndex:
    def __init__(self, data: Dict[str, Any]):
        self.data = data
        self.permissions: Dict[str, Dict[str, Any]] = data.get("permissions", {})
        self.refs: Set[str] = {
            str(permission.get("ref"))
            for permission in self.permissions.values()
            if permission.get("ref")
        }

    @classmethod
    def from_file(cls, path: str) -> "PolicyIndex":
        return cls(load_json(path))

    def validate_refs(self, refs: Optional[Sequence[str]]) -> None:
        if not refs:
            return
        missing = [ref for ref in refs if ref not in self.refs]
        if missing:
            raise ValueError(f"Unknown policy refs in policy_index.json: {missing}")

    def targets_for(
        self,
        *,
        role: str,
        rbac_violation: Optional[str],
        schema: Optional[str] = None,
    ) -> List[PolicyTarget]:
        if rbac_violation is None:
            return self._permitted_targets(role)
        if rbac_violation == "RB-01":
            return self._table_denial_targets(role, schema=schema)
        if rbac_violation == "RB-02":
            return self._column_denial_targets(role)
        if rbac_violation == "RB-03":
            return self._row_denial_targets(role)
        raise ValueError(f"Unsupported RBAC tag: {rbac_violation}")

    def _permissions_for_role(self, role: str) -> Iterable[tuple[str, Dict[str, Any]]]:
        for name, permission in self.permissions.items():
            allowed_roles = permission.get("allowed_roles", [])
            if role in allowed_roles:
                yield name, permission

    def _permitted_targets(self, role: str) -> List[PolicyTarget]:
        targets: List[PolicyTarget] = []
        for name, permission in self._permissions_for_role(role):
            permitted_columns = permission.get("permitted_columns") or {}
            tables = [table for table in permitted_columns if table != "ALL_OTHER_TABLES"]
            if not tables:
                continue
            first_table = tables[0]
            columns = list(permitted_columns.get(first_table) or [])
            targets.append(
                PolicyTarget(
                    rbac_violation=None,
                    violated_policies=None,
                    policy_ref=str(permission.get("ref", "")) or None,
                    policy_name=name,
                    role=role,
                    target_kind="permitted_control",
                    tables=[first_table],
                    columns=columns[:4],
                    row_filter=permission.get("row_filter"),
                    description=str(permission.get("description", "")),
                    note=permission.get("note"),
                )
            )
        return targets or [self._fallback_target(role, None)]

    def _table_denial_targets(self, role: str, *, schema: Optional[str]) -> List[PolicyTarget]:
        schema_tables = parse_schema_tables(schema or "")
        permitted_tables = self._permitted_tables(role)
        forbidden_tables = sorted(schema_tables - permitted_tables)
        targets = [
            PolicyTarget(
                rbac_violation="RB-01",
                violated_policies=["A01"] if "A01" in self.refs else None,
                policy_ref="A01" if "A01" in self.refs else None,
                policy_name="policy.admin.all_tables.full_access" if "A01" in self.refs else None,
                role=role,
                target_kind="forbidden_table",
                tables=[table],
                columns=[],
                row_filter=None,
                description=f"{role} is not granted access to table `{table}` under default-deny RBAC.",
            )
            for table in forbidden_tables
        ]
        return targets or [self._fallback_target(role, "RB-01")]

    def _column_denial_targets(self, role: str) -> List[PolicyTarget]:
        targets: List[PolicyTarget] = []
        for name, permission in self._permissions_for_role(role):
            if permission.get("violation_boundary") != "T2_column":
                continue
            denied_columns = permission.get("denied_columns") or {}
            for table, columns in denied_columns.items():
                if table == "ALL_OTHER_TABLES":
                    continue
                targets.append(
                    PolicyTarget(
                        rbac_violation="RB-02",
                        violated_policies=[str(permission.get("ref"))] if permission.get("ref") else None,
                        policy_ref=str(permission.get("ref", "")) or None,
                        policy_name=name,
                        role=role,
                        target_kind="denied_column",
                        tables=[table],
                        columns=list(columns),
                        row_filter=permission.get("row_filter"),
                        description=str(permission.get("description", "")),
                        note=permission.get("note"),
                    )
                )
        return targets or [self._fallback_target(role, "RB-02")]

    def _row_denial_targets(self, role: str) -> List[PolicyTarget]:
        targets: List[PolicyTarget] = []
        for name, permission in self._permissions_for_role(role):
            if permission.get("violation_boundary") != "T3_row":
                continue
            row_filter = permission.get("row_filter")
            if not row_filter:
                continue
            permitted_columns = permission.get("permitted_columns") or {}
            tables = [table for table in permitted_columns if table != "ALL_OTHER_TABLES"]
            first_table = tables[0] if tables else ""
            columns = list(permitted_columns.get(first_table) or []) if first_table else []
            targets.append(
                PolicyTarget(
                    rbac_violation="RB-03",
                    violated_policies=[str(permission.get("ref"))] if permission.get("ref") else None,
                    policy_ref=str(permission.get("ref", "")) or None,
                    policy_name=name,
                    role=role,
                    target_kind="row_scope_violation",
                    tables=[first_table] if first_table else [],
                    columns=columns[:5],
                    row_filter=row_filter,
                    description=str(permission.get("description", "")),
                    note=permission.get("note"),
                )
            )
        return targets or [self._fallback_target(role, "RB-03")]

    def _permitted_tables(self, role: str) -> Set[str]:
        tables: Set[str] = set()
        for _, permission in self._permissions_for_role(role):
            permitted_columns = permission.get("permitted_columns") or {}
            for table in permitted_columns:
                if table != "ALL_OTHER_TABLES":
                    tables.add(table)
        return tables

    def _fallback_target(self, role: str, rbac_violation: Optional[str]) -> PolicyTarget:
        return PolicyTarget(
            rbac_violation=rbac_violation,
            violated_policies=None,
            policy_ref=None,
            policy_name=None,
            role=role,
            target_kind="fallback",
            tables=[],
            columns=[],
            row_filter=None,
            description="Fallback target because policy_index.json did not contain a matching deterministic candidate.",
        )


def parse_schema_tables(schema: str) -> Set[str]:
    return set(re.findall(r"\btable\s+([A-Za-z_][A-Za-z0-9_]*)\s*,", schema))
