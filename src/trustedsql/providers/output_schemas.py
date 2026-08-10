from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PromptIntegrityOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["ALLOW", "DENY"]
    reason: str = Field(min_length=1, max_length=800)


class EntityPredicateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    column: str
    operator: Literal["=", "!=", "LIKE", "IN", "BETWEEN", ">", ">=", "<", "<=", "IS NULL", "IS NOT NULL"] = "="
    value: Any = None

    @model_validator(mode="after")
    def validate_value_shape(self) -> "EntityPredicateOutput":
        if self.operator in {"IS NULL", "IS NOT NULL"}:
            if self.value is not None:
                raise ValueError(f"{self.operator} requires value=null")
        elif self.operator == "IN":
            if not isinstance(self.value, list) or not self.value or not all(_is_scalar(item) for item in self.value):
                raise ValueError("IN requires a non-empty array of scalar values")
        elif self.operator == "BETWEEN":
            if not isinstance(self.value, list) or len(self.value) != 2 or not all(_is_scalar(item) for item in self.value):
                raise ValueError("BETWEEN requires exactly two scalar values")
        elif not _is_scalar(self.value):
            raise ValueError(f"{self.operator} requires one scalar value")
        values = self.value if isinstance(self.value, list) else [self.value]
        if any(isinstance(value, str) and _looks_like_expression(value) for value in values):
            raise ValueError("Predicate values must be concrete literals, not SQL or column references")
        if self.column.lower().endswith("_id") and any(
            value is not None and (not isinstance(value, (int, float)) and not str(value).strip().isdigit())
            for value in values
        ):
            raise ValueError("Identifier columns require numeric values")
        return self


class RequestedResourceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    columns: list[str] = Field(default_factory=list)


class ResourcePlannerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(min_length=1)
    policy_refs: list[str] = Field(default_factory=list)
    requested_resources: list[RequestedResourceOutput] = Field(default_factory=list)
    scope_type: Literal["SELF", "ENROLLED", "ASSIGNED", "ALL", "UNKNOWN"]
    target_resource_table: str | None = None
    target_identity_predicates: list[EntityPredicateOutput] = Field(default_factory=list)
    query_filter_predicates: list[EntityPredicateOutput] = Field(default_factory=list)


def _is_scalar(value: Any) -> bool:
    return value not in (None, "") and isinstance(value, (str, int, float, bool))


def _looks_like_expression(value: str) -> bool:
    normalized = value.strip()
    return bool(
        normalized.startswith("(")
        or "SELECT " in normalized.upper()
        or re.fullmatch(r"[A-Za-z_][\w]*\.[A-Za-z_][\w]*", normalized)
    )
