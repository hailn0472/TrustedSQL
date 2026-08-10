from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from data_synthesis.common.io import load_json, save_json

from .policy_compiler import CompiledPolicyBundle, SUPPORTED_GENERATION_ROLES


REQUIRED_GROUPS = {
    "identity",
    "security_boundary",
    "turn_blueprint",
    "graph_evidence",
    "hard_negatives",
    "generation_validation",
}

SQL_GT_POLICIES = {"required", "optional", "null_for_unsafe"}
RBAC_TYPES = {"RB-01", "RB-02", "RB-03"}


@dataclass(frozen=True)
class PatternSpec:
    raw: Dict[str, Any]

    @property
    def identity(self) -> Dict[str, Any]:
        return self.raw["identity"]

    @property
    def pattern_id(self) -> str:
        return str(self.identity["pattern_id"])

    @property
    def dataset_family(self) -> str:
        return str(self.identity["dataset_family"])

    @property
    def primary_type(self) -> str:
        return str(self.identity["primary_type"])

    @property
    def status(self) -> str:
        return str(self.identity["status"])

    @property
    def security_boundary(self) -> Dict[str, Any]:
        return self.raw["security_boundary"]

    @property
    def turn_blueprint(self) -> List[Dict[str, Any]]:
        return list(self.raw["turn_blueprint"])

    @property
    def graph_evidence(self) -> Dict[str, Any]:
        return self.raw["graph_evidence"]

    @property
    def hard_negatives(self) -> List[Dict[str, Any]]:
        return list(self.raw.get("hard_negatives") or [])

    @property
    def generation_validation(self) -> Dict[str, Any]:
        return self.raw["generation_validation"]

    @property
    def policy_grounding(self) -> Dict[str, Any]:
        value = self.raw.get("policy_grounding")
        return value if isinstance(value, dict) else {}

    @property
    def target_count(self) -> int:
        return int(self.generation_validation["target_count"])

    @property
    def seq_label(self) -> str:
        return "BENIGN" if self.primary_type == "BENIGN" else "MALICIOUS"

    @property
    def primary_violation(self) -> Optional[str]:
        grounded = self.policy_grounding.get("primary_violation")
        if grounded in RBAC_TYPES or grounded is None and "primary_violation" in self.policy_grounding:
            return grounded
        if self.primary_type == "BENIGN":
            return None
        if self.primary_type in RBAC_TYPES:
            return self.primary_type
        value = self.security_boundary.get("rbac_violation")
        if isinstance(value, list):
            return str(value[0]) if value else None
        return str(value) if value in RBAC_TYPES else None

    @property
    def compatible_scopes(self) -> List[str]:
        grounded = self.policy_grounding.get("compatible_scopes")
        if isinstance(grounded, list) and grounded:
            return [str(scope) for scope in grounded]
        scope_map = {
            "SELF_SCOPE": "SELF",
            "ENROLLED_SCOPE": "ENROLLED",
            "ASSIGNED_SCOPE": "ASSIGNED",
            "PUBLIC_REFERENCE": "ALL",
            "GLOBAL_SCOPE": "ALL",
        }
        scopes = {
            scope_map[str(turn.get("scope"))]
            for turn in self.turn_blueprint
            if str(turn.get("scope")) in scope_map
        }
        if any(str(turn.get("scope")) == "CROSS_SCOPE" for turn in self.turn_blueprint):
            scopes.update({"SELF", "ENROLLED", "ASSIGNED"})
        return sorted(scopes)

    @property
    def allowed_roles(self) -> List[str]:
        grounded = self.policy_grounding.get("allowed_roles")
        if isinstance(grounded, list) and grounded:
            return [str(role) for role in grounded]
        return list(SUPPORTED_GENERATION_ROLES)

    @property
    def preferred_policy_refs(self) -> List[str]:
        values = self.policy_grounding.get("preferred_policy_refs")
        if not isinstance(values, list):
            return []
        return [str(value) for value in values]

    def behavioral_context(self) -> Dict[str, Any]:
        return {
            "identity": self.identity,
            "turn_blueprint": self.turn_blueprint,
            "hard_negatives": self.hard_negatives,
            "generation_validation": self.generation_validation,
            "compiled_contract": {
                "primary_violation": self.primary_violation,
                "compatible_scopes": self.compatible_scopes,
                "allowed_roles": self.allowed_roles,
                "preferred_policy_refs": self.preferred_policy_refs,
                "graph_evidence_mode": "dynamic_from_candidate_and_compiled_slot",
                "static_policy_targets_ignored": True,
            },
        }


@dataclass(frozen=True)
class PatternBank:
    source_path: str
    schema_version: str
    controlled_vocab: Dict[str, Any]
    coverage_requirements: Dict[str, Any]
    patterns: List[PatternSpec]
    validation: Dict[str, Any]

    @property
    def active_patterns(self) -> List[PatternSpec]:
        return [pattern for pattern in self.patterns if pattern.status == "active"]


def load_pattern_bank(path: str, *, families: Optional[Sequence[str]] = None) -> PatternBank:
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError("Pattern bank root must be a JSON object.")
    source_schema_version = str(data.get("schema_version"))
    if source_schema_version == "gnn_pattern_bank_v2":
        base_path = str(data.get("base_pattern_bank") or "")
        if not os.path.isabs(base_path):
            base_path = os.path.join(os.path.dirname(path), base_path)
        base_data = load_json(base_path)
        overlays = {
            str(item.get("pattern_id")): item
            for item in data.get("patterns") or []
            if isinstance(item, dict) and item.get("pattern_id")
        }
        for pattern in base_data.get("patterns") or []:
            pattern_id = str((pattern.get("identity") or {}).get("pattern_id") or "")
            if pattern_id in overlays:
                overlay = dict(overlays[pattern_id])
                turn_blueprint_override = overlay.pop("turn_blueprint_override", None)
                pattern["policy_grounding"] = overlay
                if turn_blueprint_override is not None:
                    pattern["turn_blueprint"] = turn_blueprint_override
        data = base_data
    validation = validate_pattern_bank(data, families=families)
    if not validation["ok"]:
        raise ValueError("Pattern bank validation failed: " + "; ".join(validation["errors"][:10]))

    family_filter = {str(item).strip() for item in families or [] if str(item).strip()}
    patterns = [
        PatternSpec(pattern)
        for pattern in data.get("patterns", [])
        if isinstance(pattern, dict)
        if not family_filter or str((pattern.get("identity") or {}).get("dataset_family")) in family_filter
    ]
    return PatternBank(
        source_path=path,
        schema_version=source_schema_version,
        controlled_vocab=data.get("controlled_vocab") or {},
        coverage_requirements=data.get("coverage_requirements_for_full_bank") or {},
        patterns=patterns,
        validation=validation,
    )


def validate_pattern_bank(data: Dict[str, Any], *, families: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    patterns = data.get("patterns")
    controlled = data.get("controlled_vocab") or {}
    coverage = data.get("coverage_requirements_for_full_bank") or {}
    family_filter = {str(item).strip() for item in families or [] if str(item).strip()}

    if data.get("schema_version") != "gnn_pattern_bank_v1":
        errors.append("schema_version must be gnn_pattern_bank_v1.")
    if not isinstance(patterns, list) or not patterns:
        errors.append("patterns must be a non-empty array.")
        patterns = []

    actual_by_family: Dict[str, set[str]] = {}
    filtered_count = 0
    for index, pattern in enumerate(patterns, 1):
        if not isinstance(pattern, dict):
            errors.append(f"patterns[{index}] must be an object.")
            continue
        identity = pattern.get("identity") if isinstance(pattern.get("identity"), dict) else {}
        pattern_id = str(identity.get("pattern_id") or f"patterns[{index}]")
        dataset_family = str(identity.get("dataset_family") or "")
        primary_type = str(identity.get("primary_type") or "")
        if family_filter and dataset_family not in family_filter:
            continue
        filtered_count += 1
        actual_by_family.setdefault(dataset_family, set()).add(primary_type)
        _validate_pattern(pattern, pattern_id, controlled, errors, warnings)

    if family_filter and filtered_count == 0:
        errors.append(f"No patterns matched requested families: {sorted(family_filter)}.")

    coverage_missing: Dict[str, List[str]] = {}
    for family, required_types in coverage.items():
        if family == "note" or not isinstance(required_types, list):
            continue
        if family_filter and family not in family_filter:
            continue
        missing = sorted(set(map(str, required_types)).difference(actual_by_family.get(family, set())))
        if missing:
            coverage_missing[family] = missing
            errors.append(f"{family}: missing coverage for {missing}.")

    return {
        "ok": not errors,
        "schema_version": data.get("schema_version"),
        "pattern_count": len(patterns),
        "filtered_pattern_count": filtered_count,
        "families": sorted(actual_by_family),
        "coverage_missing": coverage_missing,
        "errors": errors,
        "warnings": warnings,
    }


def write_pattern_validation(path: str, bank: PatternBank) -> None:
    payload = {
        "source_path": bank.source_path,
        "schema_version": bank.schema_version,
        **bank.validation,
    }
    save_json(path, payload)


def validate_pattern_policy_compatibility(
    bank: PatternBank,
    policy_bundle: CompiledPolicyBundle,
    *,
    available_roles: Sequence[str],
) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    pattern_reports: List[Dict[str, Any]] = []
    roles = [
        role
        for role in SUPPORTED_GENERATION_ROLES
        if role in set(available_roles)
    ]
    for pattern in bank.active_patterns:
        matches: Dict[str, int] = {}
        refs: set[str] = set()
        for role in roles:
            if role not in pattern.allowed_roles:
                matches[role] = 0
                continue
            targets = policy_bundle.targets_for(
                role=role,
                primary_violation=pattern.primary_violation,
                compatible_scopes=pattern.compatible_scopes,
            )
            if not targets and pattern.compatible_scopes and pattern.primary_type != "BENIGN":
                targets = policy_bundle.targets_for(
                    role=role,
                    primary_violation=pattern.primary_violation,
                )
            if pattern.preferred_policy_refs:
                preferred = [
                    target
                    for target in targets
                    if target.policy_ref in pattern.preferred_policy_refs
                ]
                if preferred:
                    targets = preferred
            matches[role] = len(targets)
            refs.update(target.policy_ref for target in targets)
        if not any(matches.values()):
            errors.append(
                f"{pattern.pattern_id}: no compiled policy target for "
                f"violation={pattern.primary_violation!r}, scopes={pattern.compatible_scopes}."
            )
        static_refs = set(pattern.security_boundary.get("violated_policies") or [])
        invalid_static = sorted(static_refs.difference(policy_bundle.policies).difference({"DEFAULT_DENY"}))
        if invalid_static:
            warnings.append(
                f"{pattern.pattern_id}: ignored invalid legacy policy refs {invalid_static}."
            )
        pattern_reports.append(
            {
                "pattern_id": pattern.pattern_id,
                "primary_type": pattern.primary_type,
                "primary_violation": pattern.primary_violation,
                "compatible_scopes": pattern.compatible_scopes,
                "target_counts_by_role": matches,
                "compiled_policy_refs": sorted(refs),
                "ignored_legacy_policy_refs": invalid_static,
            }
        )
    covered_refs = {
        ref
        for report in pattern_reports
        for ref in report["compiled_policy_refs"]
        if ref != "DEFAULT_DENY"
    }
    eligible_refs = {
        ref
        for ref, policy in policy_bundle.policies.items()
        if set(policy.allowed_roles).intersection(roles)
    }
    # Allowed turn-support policies are also part of the generated policy surface,
    # even when the final malicious target is a different policy boundary.
    for role in roles:
        covered_refs.update(
            target.policy_ref
            for target in policy_bundle.targets
            if target.role == role and target.primary_violation is None
        )
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "available_generation_roles": roles,
        "pattern_reports": pattern_reports,
        "covered_policy_refs": sorted(covered_refs),
        "uncovered_policy_refs": sorted(eligible_refs.difference(covered_refs)),
        "policy_coverage_ratio": (
            len(covered_refs.intersection(eligible_refs)) / len(eligible_refs)
            if eligible_refs
            else 1.0
        ),
    }


def pattern_to_prompt_context(pattern: PatternSpec) -> Dict[str, Any]:
    return pattern.behavioral_context()


def _validate_pattern(
    pattern: Dict[str, Any],
    pattern_id: str,
    controlled: Dict[str, Any],
    errors: List[str],
    warnings: List[str],
) -> None:
    missing = REQUIRED_GROUPS.difference(pattern)
    if missing:
        errors.append(f"{pattern_id}: missing groups {sorted(missing)}.")
    extra = set(pattern).difference(REQUIRED_GROUPS).difference({"policy_grounding"})
    if extra:
        warnings.append(f"{pattern_id}: extra groups {sorted(extra)}.")

    identity = pattern.get("identity") if isinstance(pattern.get("identity"), dict) else {}
    _require_vocab(pattern_id, "dataset_family", identity.get("dataset_family"), controlled, errors)
    _require_vocab(pattern_id, "status", identity.get("status"), controlled, errors)
    if not identity.get("pattern_id"):
        errors.append(f"{pattern_id}: identity.pattern_id is required.")
    if not identity.get("primary_type"):
        errors.append(f"{pattern_id}: identity.primary_type is required.")

    security = pattern.get("security_boundary") if isinstance(pattern.get("security_boundary"), dict) else {}
    rbac = security.get("rbac_violation")
    rbac_values = rbac if isinstance(rbac, list) else ([] if rbac is None else [rbac])
    for value in rbac_values:
        if value not in RBAC_TYPES:
            errors.append(f"{pattern_id}: invalid rbac_violation {value!r}.")
    for value in security.get("sensitivity") or []:
        _require_vocab(pattern_id, "sensitivity", value, controlled, errors)

    turns = pattern.get("turn_blueprint")
    if not isinstance(turns, list) or not turns:
        errors.append(f"{pattern_id}: turn_blueprint must be a non-empty array.")
        turns = []
    for turn_index, turn in enumerate(turns, 1):
        if not isinstance(turn, dict):
            errors.append(f"{pattern_id}: turn_blueprint[{turn_index}] must be an object.")
            continue
        if turn.get("turn_index") != turn_index:
            errors.append(f"{pattern_id}: turn_index must be sequential at {turn_index}.")
        for field in ("turn_label", "turn_role_in_attack", "operation", "scope"):
            _require_vocab(pattern_id, field, turn.get(field), controlled, errors)
        if turn.get("sql_gt_policy") not in SQL_GT_POLICIES:
            errors.append(f"{pattern_id}: invalid sql_gt_policy {turn.get('sql_gt_policy')!r}.")

    evidence = pattern.get("graph_evidence") if isinstance(pattern.get("graph_evidence"), dict) else {}
    for field in ("evidence_nodes", "evidence_edges", "malicious_reason_nodes", "malicious_reason_edges"):
        if field not in evidence:
            errors.append(f"{pattern_id}: graph_evidence missing {field}.")
    for edge_index, edge in enumerate(evidence.get("evidence_edges") or [], 1):
        if not isinstance(edge, dict):
            errors.append(f"{pattern_id}: graph_evidence.evidence_edges[{edge_index}] must be an object.")
            continue
        _require_vocab(pattern_id, "edge_types", edge.get("edge"), controlled, errors)

    is_benign = identity.get("primary_type") == "BENIGN"
    malicious_reason_nodes = evidence.get("malicious_reason_nodes") or []
    malicious_reason_edges = evidence.get("malicious_reason_edges") or []
    labels = [turn.get("turn_label") for turn in turns if isinstance(turn, dict)]
    if is_benign:
        if "MALICIOUS" in labels:
            errors.append(f"{pattern_id}: BENIGN pattern cannot include MALICIOUS turns.")
        if malicious_reason_nodes or malicious_reason_edges:
            errors.append(f"{pattern_id}: BENIGN pattern cannot include malicious reason evidence.")
    else:
        if "MALICIOUS" not in labels:
            errors.append(f"{pattern_id}: non-BENIGN pattern must include a MALICIOUS turn.")
        if not malicious_reason_nodes or not malicious_reason_edges:
            errors.append(f"{pattern_id}: non-BENIGN pattern needs malicious reason evidence.")

    generation = pattern.get("generation_validation") if isinstance(pattern.get("generation_validation"), dict) else {}
    for field in ("target_count", "min_turns", "max_turns", "acceptance_checks"):
        if field not in generation:
            errors.append(f"{pattern_id}: generation_validation missing {field}.")
    try:
        if int(generation.get("target_count", 0)) <= 0:
            errors.append(f"{pattern_id}: target_count must be positive.")
    except (TypeError, ValueError):
        errors.append(f"{pattern_id}: target_count must be an integer.")


def _require_vocab(
    pattern_id: str,
    vocab_name: str,
    value: Any,
    controlled: Dict[str, Any],
    errors: List[str],
) -> None:
    allowed = controlled.get(vocab_name)
    if not isinstance(allowed, list):
        errors.append(f"controlled_vocab.{vocab_name} must exist.")
        return
    if value not in allowed:
        errors.append(f"{pattern_id}: invalid {vocab_name}={value!r}.")
