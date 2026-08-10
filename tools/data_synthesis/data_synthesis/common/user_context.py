from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .io import load_json


@dataclass(frozen=True)
class UserContext:
    role: str
    user_context_id: str
    source_path: str
    profile: Dict[str, Any]
    sample_rows: List[Dict[str, Any]]
    optimized_context: Optional[Dict[str, Any]] = None

    def to_prompt_context(self) -> Dict[str, Any]:
        if self.optimized_context:
            return {
                "role": self.role,
                "user_context_id": self.user_context_id,
                "source_path": self.source_path,
                "optimized_context": self.optimized_context,
            }
        return {
            "role": self.role,
            "user_context_id": self.user_context_id,
            "source_path": self.source_path,
            "profile": self.profile,
            "sample_rows": self.sample_rows[:5],
        }


class UserContextIndex:
    def __init__(self, contexts: Sequence[UserContext]):
        if not contexts:
            raise ValueError("user_context_file did not contain any usable user context.")
        self.contexts = _merge_duplicate_contexts(contexts)
        self.by_role: Dict[str, List[UserContext]] = {}
        for context in self.contexts:
            self.by_role.setdefault(context.role, []).append(context)

    @classmethod
    def from_files(cls, paths: Sequence[str]) -> "UserContextIndex":
        contexts: List[UserContext] = []
        for path in paths:
            if not path:
                continue
            if not os.path.exists(path):
                raise FileNotFoundError(f"user_context_file not found: {path}")
            contexts.extend(_contexts_from_json(path, load_json(path)))
        return cls(contexts)

    def require_roles(self, roles: Iterable[str]) -> None:
        missing = sorted({role for role in roles if role not in self.by_role})
        if missing:
            available = sorted(self.by_role)
            raise ValueError(
                f"user_context_file is missing role context(s): {missing}. "
                f"Available roles: {available}."
            )

    def select(self, role: str, sample_index: int = 0) -> UserContext:
        contexts = self.by_role.get(role)
        if not contexts:
            available = sorted(self.by_role)
            raise ValueError(f"No user context available for role {role!r}. Available roles: {available}.")
        return contexts[sample_index % len(contexts)]

    def to_prompt_contexts(self) -> List[Dict[str, Any]]:
        return [context.to_prompt_context() for context in self.contexts]


def split_user_context_paths(raw_values: Optional[Sequence[str]], *, base_dir: str) -> List[str]:
    if not raw_values:
        return []
    paths: List[str] = []
    for raw_value in raw_values:
        for piece in str(raw_value).replace(";", ",").split(","):
            piece = piece.strip().strip('"')
            if not piece:
                continue
            path = piece if os.path.isabs(piece) else os.path.join(base_dir, piece)
            if os.path.isdir(path):
                paths.extend(
                    os.path.join(path, name)
                    for name in sorted(os.listdir(path))
                    if name.lower().endswith(".json")
                )
            else:
                paths.append(path)
    return paths


def _contexts_from_json(path: str, data: Any) -> List[UserContext]:
    if _is_policy_grounded_context(data):
        return [_context_from_policy_grounded_json(path, data)]
    if _is_optimized_context(data):
        return [_context_from_optimized_json(path, data)]

    rows = _extract_rows(data)
    if not rows:
        raise ValueError(f"user_context_file has no row objects: {path}")

    role = _infer_role(path, rows)
    grouped = _group_rows_by_user(role, rows)
    contexts: List[UserContext] = []
    for user_context_id, user_rows in grouped.items():
        contexts.append(
            UserContext(
                role=role,
                user_context_id=user_context_id,
                source_path=path,
                profile=_build_profile(role, user_rows),
                sample_rows=_compact_rows(user_rows),
            )
        )
    return contexts


def _merge_duplicate_contexts(contexts: Sequence[UserContext]) -> List[UserContext]:
    grouped: Dict[tuple[str, str], List[UserContext]] = {}
    order: List[tuple[str, str]] = []
    for context in contexts:
        key = (context.role, context.user_context_id)
        if key not in grouped:
            order.append(key)
        grouped.setdefault(key, []).append(context)
    return [_merge_context_group(grouped[key]) for key in order]


def _merge_context_group(group: Sequence[UserContext]) -> UserContext:
    if len(group) == 1:
        return group[0]
    first = group[0]
    profile: Dict[str, Any] = {}
    for context in group:
        profile.update(context.profile or {})
    source_paths = [context.source_path for context in group]
    profile["merged_source_paths"] = source_paths
    optimized_context = _merge_optimized_contexts(group)
    return UserContext(
        role=first.role,
        user_context_id=first.user_context_id,
        source_path=";".join(source_paths),
        profile=profile,
        sample_rows=_merge_row_lists([context.sample_rows for context in group], limit=16),
        optimized_context=optimized_context,
    )


def _merge_optimized_contexts(group: Sequence[UserContext]) -> Dict[str, Any]:
    optimized_items = [context.optimized_context for context in group if context.optimized_context]
    if not optimized_items:
        return {
            "format": "merged_user_context",
            "identity": group[0].profile,
            "source_paths": [context.source_path for context in group],
            "sample_rows": _merge_row_lists([context.sample_rows for context in group], limit=16),
        }

    identity: Dict[str, Any] = {}
    rbac_policy_signal: List[Any] = []
    entities: Dict[str, List[Any]] = {}
    relationships: Dict[str, List[Any]] = {}
    column_profile: List[Any] = []
    relation_catalog: Dict[str, List[Any]] = {}
    entity_counts: Dict[str, int] = {}
    for item in optimized_items:
        identity.update(item.get("identity") or {})
        rbac_policy_signal.extend(item.get("rbac_policy_signal") or [])
        for name, rows in (item.get("entities") or {}).items():
            entities.setdefault(name, [])
            entities[name].extend(rows if isinstance(rows, list) else [rows])
        raw_relationships = item.get("relationships") or {}
        if isinstance(raw_relationships, dict):
            for name, rows in raw_relationships.items():
                relationships.setdefault(name, [])
                relationships[name].extend(rows if isinstance(rows, list) else [rows])
        else:
            relationships.setdefault("relationships", []).extend(
                raw_relationships if isinstance(raw_relationships, list) else [raw_relationships]
            )
        column_profile.extend(item.get("column_profile") or [])
        for name, rows in (item.get("relation_catalog") or {}).items():
            relation_catalog.setdefault(name, [])
            relation_catalog[name].extend(rows if isinstance(rows, list) else [rows])
        for name, count in (item.get("entity_counts") or {}).items():
            try:
                entity_counts[name] = max(entity_counts.get(name, 0), int(count))
            except (TypeError, ValueError):
                entity_counts.setdefault(name, 0)

    merged_entities = {
        name: _dedupe_jsonable(rows, limit=_entity_limit(name) + 8)
        for name, rows in entities.items()
    }
    merged_relationships = {
        name: _dedupe_jsonable(rows, limit=32)
        for name, rows in relationships.items()
    }
    return {
        "format": "merged_optimized_user_context",
        "subject": optimized_items[0].get("subject"),
        "source_files": [item.get("source_file") for item in optimized_items if item.get("source_file")],
        "source_paths": [context.source_path for context in group],
        "identity": identity,
        "rbac_policy_signal": _dedupe_jsonable(rbac_policy_signal, limit=24),
        "entity_counts": entity_counts,
        "entities": merged_entities,
        "relationships": merged_relationships,
        "column_profile": _dedupe_jsonable(column_profile, limit=96),
        "relation_catalog": {
            name: _dedupe_jsonable(rows, limit=96)
            for name, rows in relation_catalog.items()
        },
    }


def _merge_row_lists(row_groups: Sequence[Sequence[Dict[str, Any]]], *, limit: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for group in row_groups:
        rows.extend(group)
    return _dedupe_jsonable(rows, limit=limit)


def _dedupe_jsonable(values: Sequence[Any], *, limit: int) -> List[Any]:
    deduped: List[Any] = []
    seen = set()
    for value in values:
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(value)
        if len(deduped) >= limit:
            break
    if len(values) > len(deduped):
        omitted = len(values) - len(deduped)
        if omitted > 0 and len(deduped) < limit + 1:
            deduped.append({"_omitted_count": omitted})
    return deduped


def _is_optimized_context(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("identity"), dict)
        and isinstance(data.get("entities"), dict)
    )


def _is_policy_grounded_context(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("context_id"), str)
        and (
            "generation_anchors" in data
            or "generation_targets" in data
            or "policy_signal" in data
            or "policy_summary_for_student_role" in data
        )
    )


def _context_from_policy_grounded_json(path: str, data: Dict[str, Any]) -> UserContext:
    role = _infer_policy_grounded_role(path, data)
    identity = _policy_grounded_identity(role, data)
    user_context_id = _extract_optimized_user_context_id(role, identity)
    optimized_context = _compact_policy_grounded_context(role, data, identity)
    return UserContext(
        role=role,
        user_context_id=user_context_id,
        source_path=path,
        profile={
            **identity,
            "role": role,
            "context_id": data.get("context_id"),
            "policy_signal": data.get("policy_signal") or data.get("policy_summary_for_student_role") or [],
        },
        sample_rows=_sample_rows_from_policy_grounded(data),
        optimized_context=optimized_context,
    )


def _infer_policy_grounded_role(path: str, data: Dict[str, Any]) -> str:
    subject = data.get("subject")
    if isinstance(subject, dict):
        value = str(subject.get("role") or subject.get("role_name") or "").lower()
    else:
        value = str(subject or "").lower()
    identity = data.get("identity") if isinstance(data.get("identity"), dict) else {}
    role_name = str(identity.get("role_name") or "").lower()
    path_lower = os.path.basename(path).lower()
    joined = " ".join([value, role_name, path_lower])
    if "lecturer" in joined or "lecture" in joined:
        return "lecturer"
    if "student" in joined:
        return "student"
    if "admin" in joined:
        return "admin"
    raise ValueError(f"Cannot infer role from policy-grounded user context: {path}")


def _policy_grounded_identity(role: str, data: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(data.get("identity"), dict):
        return dict(data["identity"])
    subject = data.get("subject")
    if isinstance(subject, dict):
        identity = {
            key: value
            for key, value in subject.items()
            if not isinstance(value, (dict, list))
        }
        if "role_name" not in identity:
            identity["role_name"] = role.title()
        return identity
    return {"role_name": role.title(), "username": str(data.get("context_id") or role)}


def _compact_policy_grounded_context(
    role: str,
    data: Dict[str, Any],
    identity: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "format": "policy_grounded_user_context",
        "context_id": data.get("context_id"),
        "version": data.get("version") or data.get("policy_version"),
        "subject": role,
        "source_files": data.get("source_files") or [],
        "identity": identity,
        "rbac_policy_signal": _policy_grounded_policy_signal(data),
        "entity_counts": _policy_grounded_entity_counts(data),
        "entities": _policy_grounded_entities(role, data, identity),
        "relationships": _policy_grounded_relationships(role, data),
        "column_profile": _policy_grounded_column_profile(data),
        "relation_catalog": _policy_grounded_relation_catalog(role, data),
        "policy_context": _compact_mapping_values(data.get("policy_context") or {}, limit=12),
        "generation_anchors": _compact_mapping_values(data.get("generation_anchors") or {}, limit=16),
        "generation_targets": _compact_mapping_values(data.get("generation_targets") or {}, limit=16),
        "row_scope_validation_rules": _compact_mapping_values(
            data.get("row_scope_validation_rules") or {},
            limit=12,
        ),
        "out_of_scope_context_summary": _compact_mapping_values(
            data.get("out_of_scope_context_summary") or {},
            limit=12,
        ),
    }


def _policy_grounded_policy_signal(data: Dict[str, Any]) -> List[Any]:
    signal: List[Any] = []
    for key in (
        "policy_signal",
        "policy_summary_for_student_role",
        "focused_policy_boundaries_for_generation",
        "available_data_limitations",
        "design_principles",
    ):
        value = data.get(key)
        if value:
            signal.append({key: _compact_collection(value, limit=12)})
    return _dedupe_jsonable(signal, limit=16)


def _policy_grounded_entity_counts(data: Dict[str, Any]) -> Dict[str, int]:
    stats = data.get("source_statistics") if isinstance(data.get("source_statistics"), dict) else {}
    counts: Dict[str, int] = {}
    for key, value in stats.items():
        if key.endswith("_count") or key.endswith("_rows") or key in {
            "assigned_class_courses",
            "roster_rows",
            "unique_taught_students",
        }:
            try:
                counts[key] = int(value)
            except (TypeError, ValueError):
                continue
    return counts


def _policy_grounded_entities(
    role: str,
    data: Dict[str, Any],
    identity: Dict[str, Any],
) -> Dict[str, List[Any]]:
    entities: Dict[str, List[Any]] = {
        "identity": [identity],
    }
    if role == "student":
        subject_values = data.get("subject_reference_values") or {}
        if isinstance(subject_values, dict):
            for name, value in subject_values.items():
                entities[name] = value if isinstance(value, list) else [value]
        own_courses = (data.get("own_course_context_for_generation") or {}).get("records") or []
        if own_courses:
            entities["class_course"] = own_courses
    elif role == "lecturer":
        self_context = data.get("self_context") or {}
        if isinstance(self_context, dict):
            for name, value in self_context.items():
                entities[name] = value if isinstance(value, list) else [value]
        assigned = (data.get("assignment_scope") or {}).get("assigned_class_courses") or []
        if assigned:
            entities["class_course"] = assigned
    return {
        name: _dedupe_jsonable(rows if isinstance(rows, list) else [rows], limit=_entity_limit(name) + 8)
        for name, rows in entities.items()
    }


def _policy_grounded_relationships(role: str, data: Dict[str, Any]) -> Dict[str, List[Any]]:
    relationships: Dict[str, List[Any]] = {}
    if role == "student":
        for key in ("row_scope_context", "own_course_context_for_generation"):
            value = data.get(key)
            if value:
                relationships[key] = [_compact_collection(value, limit=16)]
    elif role == "lecturer":
        for key in ("assignment_scope", "student_scope"):
            value = data.get(key)
            if value:
                relationships[key] = [_compact_collection(value, limit=16)]
    return {
        name: _dedupe_jsonable(rows, limit=24)
        for name, rows in relationships.items()
    }


def _policy_grounded_column_profile(data: Dict[str, Any]) -> List[Any]:
    values = []
    for key in (
        "normalization_notes",
        "focused_policy_boundaries_for_generation",
        "policy_context",
    ):
        if data.get(key):
            values.append({key: data[key]})
    return _dedupe_jsonable(values, limit=32)


def _compact_mapping_values(value: Any, *, limit: int) -> Any:
    if not isinstance(value, dict):
        return _compact_collection(value, limit=limit)
    return {
        key: _compact_collection(item, limit=limit)
        for key, item in value.items()
        if item not in (None, [], {})
    }


def _policy_grounded_relation_catalog(role: str, data: Dict[str, Any]) -> Dict[str, List[Any]]:
    if role == "student":
        anchors = data.get("generation_anchors") or {}
        own_courses = (data.get("own_course_context_for_generation") or {}).get("records") or []
        classmates = anchors.get("allowed_classmate_targets") or []
        rb03_targets = anchors.get("rb03_row_scope_violation_targets") or []
        return {
            "classmates": _dedupe_jsonable(classmates, limit=96),
            "students": _dedupe_jsonable(rb03_targets, limit=96),
            "class_courses": _dedupe_jsonable(own_courses, limit=96),
        }
    if role == "lecturer":
        scope = data.get("student_scope") or {}
        targets = data.get("generation_targets") or {}
        positive = targets.get("positive_targets") or {}
        assigned = positive.get("valid_assigned_class_course_examples") or (
            data.get("assignment_scope") or {}
        ).get("assigned_class_courses") or []
        taught = positive.get("valid_taught_student_examples") or []
        rosters = scope.get("rosters_by_class_course") or []
        students: List[Any] = []
        class_courses: List[Any] = list(assigned)
        for roster in rosters:
            if isinstance(roster, dict):
                class_courses.append(
                    {
                        key: roster.get(key)
                        for key in ("class_course_id", "class_name", "course_code", "semester")
                        if roster.get(key) is not None
                    }
                )
                students.extend(roster.get("roster") or [])
        return {
            "students": _dedupe_jsonable([*taught, *students], limit=128),
            "class_courses": _dedupe_jsonable(class_courses, limit=96),
        }
    return {}


def _sample_rows_from_policy_grounded(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    samples: List[Dict[str, Any]] = []
    for collection in (
        ((data.get("own_course_context_for_generation") or {}).get("records") or []),
        ((data.get("generation_anchors") or {}).get("allowed_classmate_targets") or []),
        (((data.get("generation_targets") or {}).get("positive_targets") or {}).get("valid_taught_student_examples") or []),
    ):
        for row in collection[:5]:
            if isinstance(row, dict):
                samples.append(_compact_dict(row))
    return samples[:12]


def _context_from_optimized_json(path: str, data: Dict[str, Any]) -> UserContext:
    identity = data.get("identity") or {}
    role = _infer_optimized_role(path, data)
    user_context_id = _extract_optimized_user_context_id(role, identity)
    optimized_context = _compact_optimized_context(data)
    return UserContext(
        role=role,
        user_context_id=user_context_id,
        source_path=path,
        profile={
            **identity,
            "role": role,
            "subject": data.get("subject"),
            "rbac_policy_signal": data.get("rbac_policy_signal") or [],
            "entity_counts": optimized_context.get("entity_counts", {}),
        },
        sample_rows=_sample_rows_from_optimized(data),
        optimized_context=optimized_context,
    )


def _infer_optimized_role(path: str, data: Dict[str, Any]) -> str:
    subject = str(data.get("subject") or "").lower()
    role_name = str((data.get("identity") or {}).get("role_name") or "").lower()
    path_lower = os.path.basename(path).lower()
    for value in (role_name, subject):
        if value in {"student", "lecturer", "admin"}:
            return value
        if "lecturer" in value or "lecture" in value:
            return "lecturer"
        if "student" in value:
            return "student"
        if "admin" in value:
            return "admin"
    joined = " ".join([subject, role_name, path_lower])
    if "lecture" in joined or "lecturer" in joined:
        return "lecturer"
    if "student" in joined:
        return "student"
    if "admin" in joined:
        return "admin"
    raise ValueError(f"Cannot infer role from optimized user context: {path}")


def _extract_optimized_user_context_id(role: str, identity: Dict[str, Any]) -> str:
    if role == "student":
        value = identity.get("student_code") or identity.get("student_id") or identity.get("user_id")
    elif role == "lecturer":
        value = identity.get("lecturer_id") or identity.get("user_id") or identity.get("username")
    else:
        value = identity.get("user_id") or identity.get("username") or identity.get("role_name")
    if value is None or str(value).strip() == "":
        raise ValueError(f"Cannot infer user_context_id from optimized identity for role {role}: {identity}")
    return str(value)


def _compact_optimized_context(data: Dict[str, Any]) -> Dict[str, Any]:
    entities = data.get("entities") or {}
    relationships = data.get("relationships") or {}
    column_profile = data.get("column_profile") or []
    return {
        "format": "optimized_user_context",
        "subject": data.get("subject"),
        "source_file": data.get("source_file"),
        "identity": data.get("identity") or {},
        "rbac_policy_signal": data.get("rbac_policy_signal") or [],
        "entity_counts": {
            name: len(value) if isinstance(value, list) else 1
            for name, value in entities.items()
        },
        "entities": {
            name: _compact_collection(value, limit=_entity_limit(name))
            for name, value in entities.items()
        },
        "relationships": {
            name: _compact_collection(value, limit=24)
            for name, value in relationships.items()
        }
        if isinstance(relationships, dict)
        else _compact_collection(relationships, limit=24),
        "column_profile": _compact_collection(column_profile, limit=64),
        "relation_catalog": _build_relation_catalog(data),
    }


def _build_relation_catalog(data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    people: List[Dict[str, Any]] = []
    class_courses: List[Dict[str, Any]] = []
    source_name = None
    rows: List[Any] = []
    if isinstance(data.get("classmates_by_class_course"), list):
        source_name = "classmates"
        rows = data["classmates_by_class_course"]
    elif isinstance(data.get("students_by_class_course"), list):
        source_name = "students"
        rows = data["students_by_class_course"]
    if not source_name:
        return {}

    for item in rows:
        if not isinstance(item, dict):
            continue
        class_courses.append(
            {
                key: item.get(key)
                for key in (
                    "class_course_id",
                    "semester",
                    "class_name",
                    "course_code",
                    "course_name_en",
                )
                if item.get(key) is not None
            }
        )
        for person in item.get("roster") or []:
            if not isinstance(person, dict):
                continue
            people.append(
                {
                    key: person.get(key)
                    for key in (
                        "student_id",
                        "student_code",
                        "fullname",
                        "enrollment_status",
                    )
                    if person.get(key) is not None
                }
            )
    return {
        source_name: _dedupe_jsonable(people, limit=96),
        "class_courses": _dedupe_jsonable(class_courses, limit=64),
    }


def _sample_rows_from_optimized(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    entities = data.get("entities") or {}
    samples: List[Dict[str, Any]] = []
    for entity_name, rows in entities.items():
        if not isinstance(rows, list):
            continue
        for row in rows[:3]:
            if isinstance(row, dict):
                samples.append({"entity": entity_name, **_compact_dict(row)})
        if len(samples) >= 8:
            break
    return samples


def _compact_collection(value: Any, *, limit: int) -> Any:
    if isinstance(value, list):
        compacted = [_compact_dict(item) if isinstance(item, dict) else item for item in value[:limit]]
        if len(value) > limit:
            compacted.append({"_omitted_count": len(value) - limit})
        return compacted
    if isinstance(value, dict):
        return _compact_dict(value)
    return value


def _compact_dict(row: Dict[str, Any], *, max_keys: int = 32) -> Dict[str, Any]:
    compact: Dict[str, Any] = {}
    for key, value in row.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            continue
        compact[key] = value
        if len(compact) >= max_keys:
            compact["_truncated"] = True
            break
    return compact


def _entity_limit(entity_name: str) -> int:
    if entity_name in {"schedule"}:
        return 10
    if entity_name in {"enrollment", "class_course", "class", "course", "lecturer"}:
        return 8
    return 8


def _extract_rows(data: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if isinstance(data, list):
        candidates = data
    elif isinstance(data, dict):
        candidates = []
        for value in data.values():
            if isinstance(value, list):
                candidates.extend(value)
            elif isinstance(value, dict):
                candidates.append(value)
    else:
        candidates = []

    for item in candidates:
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _infer_role(path: str, rows: Sequence[Dict[str, Any]]) -> str:
    path_lower = os.path.basename(path).lower()
    if "student" in path_lower:
        return "student"
    if "lecture" in path_lower or "lecturer" in path_lower:
        return "lecturer"
    if "admin" in path_lower:
        return "admin"
    for row in rows:
        role_name = str(row.get("role_name") or row.get("role") or "").lower()
        if "student" in role_name:
            return "student"
        if "lecturer" in role_name or "teacher" in role_name:
            return "lecturer"
        if "admin" in role_name:
            return "admin"
    raise ValueError(f"Cannot infer role from user_context_file: {path}")


def _group_rows_by_user(role: str, rows: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        user_context_id = _extract_user_context_id(role, row)
        grouped.setdefault(user_context_id, []).append(row)
    return grouped


def _extract_user_context_id(role: str, row: Dict[str, Any]) -> str:
    if role == "student":
        value = row.get("student_code") or row.get("student_id") or row.get("user_id")
    elif role == "lecturer":
        value = row.get("lecturer_id") or row.get("user_id") or row.get("username")
    else:
        value = row.get("user_id") or row.get("username") or row.get("role_name")
    if value is None or str(value).strip() == "":
        raise ValueError(f"Cannot infer user_context_id for {role} from row: {row}")
    return str(value)


def _build_profile(role: str, rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    first = rows[0]
    keys = [
        "user_id",
        "username",
        "fullname",
        "gmail",
        "role_name",
        "campus_name",
        "student_id",
        "student_code",
        "lecturer_id",
        "major_code",
        "major_name",
        "dep_code",
        "dep_name",
    ]
    profile = {key: first.get(key) for key in keys if key in first}
    profile["role"] = role
    profile["class_ids"] = _unique_values(rows, "class_id", limit=12)
    profile["class_names"] = _unique_values(rows, "class_name", limit=12)
    profile["course_codes"] = _unique_values(rows, "course_code", limit=12)
    profile["semesters"] = _unique_values(rows, "semester", limit=12)
    return profile


def _compact_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keep_keys = [
        "user_id",
        "student_id",
        "student_code",
        "lecturer_id",
        "fullname",
        "class_id",
        "class_name",
        "class_course_id",
        "course_code",
        "course_name_en",
        "semester",
        "enrollment_id",
        "average",
        "enrollment_status",
        "schedule_id",
        "room",
        "slot",
    ]
    compacted: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        compact = {key: row.get(key) for key in keep_keys if key in row}
        marker = tuple(sorted(compact.items()))
        if marker in seen:
            continue
        seen.add(marker)
        compacted.append(compact)
        if len(compacted) >= 10:
            break
    return compacted


def _unique_values(rows: Sequence[Dict[str, Any]], key: str, *, limit: int) -> List[Any]:
    values: List[Any] = []
    seen = set()
    for row in rows:
        value = row.get(key)
        marker = str(value)
        if value is None or marker in seen:
            continue
        seen.add(marker)
        values.append(value)
        if len(values) >= limit:
            break
    return values
