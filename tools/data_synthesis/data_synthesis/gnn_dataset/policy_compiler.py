from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from data_synthesis.common.io import load_json


INTERNAL_TABLES = {
    "conversation_sessions",
    "episodic_memories",
    "memory_conflicts",
    "schema_migrations",
    "user_facts",
}

SUPPORTED_GENERATION_ROLES = ("student", "lecturer")
SUPPORTED_BOUNDARIES = {"RB-01", "RB-02", "RB-03"}

POLICY_TABLE_HINTS: Dict[str, List[str]] = {
    "C01": ["majors"],
    "C02": ["departments"],
    "C03": ["narrow_majors"],
    "C04": ["campuses"],
    "C05": ["classes"],
    "C06": ["curriculum_frameworks"],
    "C07": ["curriculum_subjects"],
    "C08": ["courses"],
    "C09": ["course_grading_categories"],
    "C10": ["course_clos"],
    "C11": ["course_materials"],
    "C12": ["course_sessions"],
    "C13": ["plos"],
    "C14": ["application_types"],
    "S01": ["users"],
    "S02": ["students"],
    "S03": ["enrollments", "classcourse", "courses", "classes"],
    "S04": ["students", "users", "enrollments", "classcourse"],
    "S05": ["schedules", "classcourse", "enrollments"],
    "S06": ["attendance", "enrollments"],
    "S07": ["grade_details", "enrollments"],
    "S08": ["application"],
    "S09": ["lecturers", "users", "classcourse", "enrollments"],
    "L01": ["users"],
    "L02": ["lecturers"],
    "L03": ["classcourse", "classes", "courses"],
    "L04": ["schedules", "classcourse"],
    "L05": ["attendance", "enrollments", "classcourse"],
    "L06": ["grade_details", "enrollments", "classcourse"],
    "L07": ["enrollments", "classcourse"],
    "L08": ["students", "users", "enrollments", "classcourse"],
    "L09": ["lecturers", "users"],
    "L10": ["course_grading_categories"],
    "A01": ["users"],
}


@dataclass(frozen=True)
class CompiledPolicy:
    ref: str
    title: str
    permission_group: str
    effect: str
    allowed_roles: List[str]
    action: str
    scope_type: str
    row_filter: Optional[str]
    requires_current_user_binding: bool
    context_variables: List[str]
    violation_boundaries: List[str]
    target_tables: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ref": self.ref,
            "title": self.title,
            "permission_group": self.permission_group,
            "effect": self.effect,
            "allowed_roles": self.allowed_roles,
            "action": self.action,
            "scope_type": self.scope_type,
            "row_filter": self.row_filter,
            "requires_current_user_binding": self.requires_current_user_binding,
            "context_variables": self.context_variables,
            "violation_boundaries": self.violation_boundaries,
            "target_tables": self.target_tables,
        }


@dataclass(frozen=True)
class CompiledTarget:
    target_id: str
    role: str
    policy_ref: str
    policy_title: str
    primary_violation: Optional[str]
    possible_secondary_violations: List[str]
    scope_type: str
    target_tables: List[str]
    target_columns: List[str]
    denied_columns: List[str]
    row_filter: Optional[str]
    required_context_bindings: List[str]
    allowed_subject: str
    forbidden_subject: Optional[str]
    target_kind: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_id": self.target_id,
            "role": self.role,
            "policy_ref": self.policy_ref,
            "policy_title": self.policy_title,
            "primary_violation": self.primary_violation,
            "possible_secondary_violations": self.possible_secondary_violations,
            "scope_type": self.scope_type,
            "target_tables": self.target_tables,
            "target_columns": self.target_columns,
            "denied_columns": self.denied_columns,
            "row_filter": self.row_filter,
            "required_context_bindings": self.required_context_bindings,
            "allowed_subject": self.allowed_subject,
            "forbidden_subject": self.forbidden_subject,
            "target_kind": self.target_kind,
        }


@dataclass(frozen=True)
class CompiledPolicyBundle:
    source_dir: str
    tables: Dict[str, List[str]]
    foreign_keys: List[Dict[str, str]]
    policies: Dict[str, CompiledPolicy]
    role_access: Dict[str, Dict[str, List[str]]]
    targets: List[CompiledTarget]
    validation: Dict[str, Any]
    source_hashes: Dict[str, str]

    @property
    def domain_tables(self) -> Dict[str, List[str]]:
        return {name: columns for name, columns in self.tables.items() if name not in INTERNAL_TABLES}

    def targets_for(
        self,
        *,
        role: str,
        primary_violation: Optional[str],
        compatible_scopes: Optional[Sequence[str]] = None,
    ) -> List[CompiledTarget]:
        scopes = {str(scope) for scope in compatible_scopes or [] if str(scope)}
        return [
            target
            for target in self.targets
            if target.role == role
            and target.primary_violation == primary_violation
            and (not scopes or target.scope_type in scopes)
        ]

    def compact_schema(self, target_tables: Sequence[str]) -> str:
        selected: Set[str] = {table for table in target_tables if table in self.domain_tables}
        anchors = set(selected)
        for fk in self.foreign_keys:
            if fk["source_table"] in anchors:
                selected.add(fk["target_table"])
            if fk["target_table"] in anchors:
                selected.add(fk["source_table"])
        lines = [
            f"table {table}, columns = [{', '.join(self.domain_tables[table])}]"
            for table in sorted(selected)
            if table in self.domain_tables
        ]
        relevant_fks = [
            f'{fk["source_table"]}.{fk["source_column"]} = {fk["target_table"]}.{fk["target_column"]}'
            for fk in self.foreign_keys
            if fk["source_table"] in selected and fk["target_table"] in selected
        ]
        if relevant_fks:
            lines.append("foreign keys:")
            lines.extend(relevant_fks)
        return "\n".join(lines)

    def manifest_fragment(self) -> Dict[str, Any]:
        return {
            "policy_source_dir": self.source_dir,
            "source_hashes_sha256": self.source_hashes,
            "schema_table_count": len(self.tables),
            "domain_table_count": len(self.domain_tables),
            "internal_tables_excluded": sorted(INTERNAL_TABLES.intersection(self.tables)),
            "policy_count": len(self.policies),
            "compiled_target_count": len(self.targets),
            "generation_roles": list(SUPPORTED_GENERATION_ROLES),
        }


def compile_policy_bundle(policy_dir: str) -> CompiledPolicyBundle:
    paths = {
        "ddl": os.path.join(policy_dir, "ddl_upgrade.txt"),
        "policy_index": os.path.join(policy_dir, "policy_index.json"),
        "role_access_matrix": os.path.join(policy_dir, "role_access_matrix.json"),
    }
    missing = [path for path in paths.values() if not os.path.exists(path)]
    if missing:
        raise FileNotFoundError(f"Policy 6-4 bundle is incomplete: {missing}")

    with open(paths["ddl"], "r", encoding="utf-8") as handle:
        ddl = handle.read()
    policy_data = load_json(paths["policy_index"])
    role_access = load_json(paths["role_access_matrix"])
    tables, foreign_keys = _parse_ddl(ddl)
    policies = _compile_policies(policy_data, tables)
    validation = _validate_sources(tables, policies, role_access)
    if not validation["ok"]:
        raise ValueError("Policy compiler validation failed: " + "; ".join(validation["errors"][:20]))

    targets = _compile_targets(tables, policies, role_access)
    return CompiledPolicyBundle(
        source_dir=os.path.abspath(policy_dir),
        tables=tables,
        foreign_keys=foreign_keys,
        policies=policies,
        role_access=role_access,
        targets=targets,
        validation={**validation, "compiled_target_count": len(targets)},
        source_hashes={name: _sha256(path) for name, path in paths.items()},
    )


def _parse_ddl(ddl: str) -> tuple[Dict[str, List[str]], List[Dict[str, str]]]:
    tables: Dict[str, List[str]] = {}
    foreign_keys: List[Dict[str, str]] = []
    table_pattern = re.compile(r"CREATE\s+TABLE\s+(?:public\.)?(\w+)\s*\((.*?)\n\);", re.I | re.S)
    for match in table_pattern.finditer(ddl):
        table = match.group(1)
        columns: List[str] = []
        for line in match.group(2).splitlines():
            column_match = re.match(r'\s*"?([A-Za-z_]\w*)"?\s+[^,]+', line)
            if not column_match:
                continue
            column = column_match.group(1)
            if column.upper() in {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK"}:
                continue
            columns.append(column)
        tables[table] = columns

    fk_pattern = re.compile(
        r"FOREIGN\s+KEY\s*\((\w+)\)\s+REFERENCES\s+(?:public\.)?(\w+)\s*\((\w+)\)",
        re.I,
    )
    for table_match in table_pattern.finditer(ddl):
        source_table = table_match.group(1)
        for match in fk_pattern.finditer(table_match.group(2)):
            foreign_keys.append(
                {
                    "source_table": source_table,
                    "source_column": match.group(1),
                    "target_table": match.group(2),
                    "target_column": match.group(3),
                }
            )
    return tables, foreign_keys


def _compile_policies(
    data: Dict[str, Any],
    tables: Dict[str, List[str]],
) -> Dict[str, CompiledPolicy]:
    raw_permissions = data.get("permissions")
    if not isinstance(raw_permissions, dict):
        raise ValueError("Policy 6-4 policy_index.permissions must be an object keyed by policy ref.")
    policies: Dict[str, CompiledPolicy] = {}
    for ref, raw in raw_permissions.items():
        if not isinstance(raw, dict):
            continue
        hinted = POLICY_TABLE_HINTS.get(str(ref), [])
        row_tables = _tables_from_expression(str(raw.get("row_filter") or ""), tables)
        target_tables = _dedupe([*hinted, *row_tables])
        policies[str(ref)] = CompiledPolicy(
            ref=str(ref),
            title=str(raw.get("title_en") or raw.get("title_vi") or ref),
            permission_group=str(raw.get("permission_group") or ""),
            effect=str(raw.get("effect") or ""),
            allowed_roles=[str(role) for role in raw.get("allowed_roles") or []],
            action=str(raw.get("action") or ""),
            scope_type=str(raw.get("scope_type") or "ALL"),
            row_filter=str(raw["row_filter"]) if raw.get("row_filter") else None,
            requires_current_user_binding=bool(raw.get("requires_current_user_binding")),
            context_variables=[str(item) for item in raw.get("context_variables") or []],
            violation_boundaries=[str(item) for item in raw.get("violation_boundaries") or []],
            target_tables=[table for table in target_tables if table in tables and table not in INTERNAL_TABLES],
        )
    return policies


def _validate_sources(
    tables: Dict[str, List[str]],
    policies: Dict[str, CompiledPolicy],
    role_access: Any,
) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    if not tables:
        errors.append("DDL did not produce any tables.")
    if not isinstance(role_access, dict):
        errors.append("role_access_matrix root must be an object.")
        role_access = {}
    for role, matrix in role_access.items():
        if not isinstance(matrix, dict):
            errors.append(f"role_access_matrix.{role} must be an object.")
            continue
        for table, columns in matrix.items():
            if table not in tables:
                errors.append(f"{role}: matrix table {table!r} is absent from DDL.")
                continue
            missing_columns = sorted(set(columns or []).difference(tables[table]))
            if missing_columns:
                errors.append(f"{role}.{table}: matrix columns absent from DDL: {missing_columns}.")
    for ref, policy in policies.items():
        invalid_boundaries = sorted(set(policy.violation_boundaries).difference(SUPPORTED_BOUNDARIES))
        if invalid_boundaries:
            errors.append(f"{ref}: invalid violation boundaries {invalid_boundaries}.")
        if not policy.target_tables:
            warnings.append(f"{ref}: no target table could be derived.")
        if "admin" in policy.allowed_roles and ref == "A01":
            continue
        unsupported = sorted(set(policy.allowed_roles).difference({"student", "lecturer", "admin"}))
        if unsupported:
            errors.append(f"{ref}: unsupported roles {unsupported}.")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "table_count": len(tables),
        "domain_table_count": len(set(tables).difference(INTERNAL_TABLES)),
        "internal_tables": sorted(INTERNAL_TABLES.intersection(tables)),
        "policy_count": len(policies),
        "matrix_roles": sorted(role_access),
    }


def _compile_targets(
    tables: Dict[str, List[str]],
    policies: Dict[str, CompiledPolicy],
    role_access: Dict[str, Dict[str, List[str]]],
) -> List[CompiledTarget]:
    targets: List[CompiledTarget] = []
    for role in SUPPORTED_GENERATION_ROLES:
        matrix = role_access.get(role) or {}
        for policy in policies.values():
            if role not in policy.allowed_roles:
                continue
            allowed_tables = [table for table in policy.target_tables if table in matrix]
            if not allowed_tables:
                continue
            for boundary in policy.violation_boundaries:
                # Table-level violations come from the role's default-deny
                # complement, never from a table explicitly permitted by this policy.
                if boundary == "RB-01":
                    continue
                target_columns = _dedupe(
                    column
                    for table in allowed_tables
                    for column in matrix.get(table, [])
                )[:12]
                denied_columns = _dedupe(
                    column
                    for table in allowed_tables
                    for column in tables.get(table, [])
                    if column not in set(matrix.get(table, []))
                )[:12]
                if boundary == "RB-02" and not denied_columns:
                    continue
                if boundary == "RB-03" and not policy.row_filter:
                    continue
                secondary = [
                    item
                    for item in policy.violation_boundaries
                    if item != boundary
                    and (item != "RB-02" or denied_columns)
                    and (item != "RB-03" or policy.row_filter)
                ]
                targets.append(
                    CompiledTarget(
                        target_id=f"{role}:{policy.ref}:{boundary}",
                        role=role,
                        policy_ref=policy.ref,
                        policy_title=policy.title,
                        primary_violation=boundary,
                        possible_secondary_violations=secondary,
                        scope_type=policy.scope_type,
                        target_tables=allowed_tables,
                        target_columns=target_columns,
                        denied_columns=denied_columns,
                        row_filter=policy.row_filter,
                        required_context_bindings=policy.context_variables,
                        allowed_subject=_allowed_subject(policy.scope_type),
                        forbidden_subject=_forbidden_subject(policy.scope_type),
                        target_kind=_target_kind(boundary),
                    )
                )

        forbidden_tables = sorted(
            set(tables).difference(INTERNAL_TABLES).difference(matrix)
        )
        for table in forbidden_tables:
            targets.append(
                CompiledTarget(
                    target_id=f"{role}:DEFAULT_DENY:RB-01:{table}",
                    role=role,
                    policy_ref="DEFAULT_DENY",
                    policy_title="Default-deny table boundary",
                    primary_violation="RB-01",
                    possible_secondary_violations=[],
                    scope_type="ALL",
                    target_tables=[table],
                    target_columns=tables[table][:8],
                    denied_columns=[],
                    row_filter=None,
                    required_context_bindings=[],
                    allowed_subject="no_access",
                    forbidden_subject="any_record_in_forbidden_table",
                    target_kind="forbidden_table",
                )
            )

        for policy in policies.values():
            if role not in policy.allowed_roles:
                continue
            allowed_tables = [table for table in policy.target_tables if table in matrix]
            if not allowed_tables:
                continue
            targets.append(
                CompiledTarget(
                    target_id=f"{role}:{policy.ref}:BENIGN",
                    role=role,
                    policy_ref=policy.ref,
                    policy_title=policy.title,
                    primary_violation=None,
                    possible_secondary_violations=[],
                    scope_type=policy.scope_type,
                    target_tables=allowed_tables,
                    target_columns=_dedupe(
                        column for table in allowed_tables for column in matrix.get(table, [])
                    )[:12],
                    denied_columns=[],
                    row_filter=policy.row_filter,
                    required_context_bindings=policy.context_variables,
                    allowed_subject=_allowed_subject(policy.scope_type),
                    forbidden_subject=None,
                    target_kind="allowed_policy_target",
                )
            )
    return targets


def _tables_from_expression(expression: str, tables: Dict[str, List[str]]) -> List[str]:
    found: List[str] = []
    lowered = expression.lower()
    for table in tables:
        if re.search(rf"\b{re.escape(table.lower())}\b", lowered):
            found.append(table)
    return found


def _allowed_subject(scope: str) -> str:
    return {
        "SELF": "authenticated_user_only",
        "ENROLLED": "entities_in_authenticated_student_enrollments",
        "ASSIGNED": "entities_in_authenticated_lecturer_assignments",
        "ALL": "all_rows_permitted_by_column_policy",
    }.get(scope, "policy_defined_scope")


def _forbidden_subject(scope: str) -> Optional[str]:
    return {
        "SELF": "another_user_or_global_rows",
        "ENROLLED": "entity_outside_authenticated_student_enrollments",
        "ASSIGNED": "entity_outside_authenticated_lecturer_assignments",
        "ALL": None,
    }.get(scope, "entity_outside_policy_scope")


def _target_kind(boundary: str) -> str:
    return {
        "RB-01": "forbidden_table",
        "RB-02": "denied_column",
        "RB-03": "row_scope_violation",
    }[boundary]


def _dedupe(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen: Set[str] = set()
    for value in values:
        text = str(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
