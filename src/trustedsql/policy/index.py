from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trustedsql.sql.schema import normalize_name
from trustedsql.utils.io import read_json


@dataclass
class PolicyRule:
    ref: str
    allowed_roles: list[str]
    scope_type: str
    row_filter: str | None
    requires_current_user_binding: bool
    raw: dict[str, Any]


@dataclass
class PolicyIndex:
    policy_index: dict[str, Any]
    role_access_matrix: dict[str, dict[str, list[str]]]

    def allowed_tables(self, role: str) -> set[str]:
        return {normalize_name(t) for t in self.role_access_matrix.get(role, {})}

    def allowed_columns(self, role: str, table: str) -> set[str]:
        return {normalize_name(c) for c in self.role_access_matrix.get(role, {}).get(normalize_name(table), [])}

    def role_can_access_table(self, role: str, table: str) -> bool:
        return normalize_name(table) in self.allowed_tables(role)

    def role_can_access_column(self, role: str, table: str, column: str) -> bool:
        table = normalize_name(table)
        column = normalize_name(column)
        return column in self.allowed_columns(role, table)

    def permissions(self) -> dict[str, Any]:
        return self.policy_index.get("permissions", {})

    def policy_rule(self, ref: str) -> PolicyRule | None:
        raw = self.permissions().get(ref)
        if not raw:
            return None
        return PolicyRule(
            ref=ref,
            allowed_roles=list(raw.get("allowed_roles") or []),
            scope_type=str(raw.get("scope_type") or "UNKNOWN"),
            row_filter=raw.get("row_filter"),
            requires_current_user_binding=bool(raw.get("requires_current_user_binding")),
            raw=raw,
        )

    def role_policy_refs(self, role: str) -> list[str]:
        refs = []
        for ref, raw in self.permissions().items():
            if role in (raw.get("allowed_roles") or []):
                refs.append(ref)
        return refs

    def policy_summary_for_role(self, role: str) -> str:
        lines = []
        for ref in self.role_policy_refs(role):
            raw = self.permissions()[ref]
            title = raw.get("title_en") or raw.get("title_vi") or ref
            scope = raw.get("scope_type") or "UNKNOWN"
            row_filter = raw.get("row_filter") or ""
            lines.append(f"- {ref}: {title}; scope={scope}; row_filter={row_filter}")
        return "\n".join(lines)


def load_policy_index(policy_path: Path, role_matrix_path: Path) -> PolicyIndex:
    return PolicyIndex(policy_index=read_json(policy_path), role_access_matrix=read_json(role_matrix_path))

