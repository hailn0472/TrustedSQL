from __future__ import annotations

import re

from trustedsql_gnn.contracts import HistoryTurn, IntentResolution
from trustedsql_gnn.security.contracts import AuthContext, PolicyRoute


CODE_RE = re.compile(r"\b(?:HE|HS)\d{4,}\b", re.IGNORECASE)

STUDENT_TARGET_PATTERNS = [
    r"\bthat student\b",
    r"\bthat classmate\b",
    r"\bselected student\b",
    r"\bmatching student\b",
    r"\bmissing student\b",
    r"\bleftover classmate\b",
    r"\bremaining classmate\b",
    r"\bone classmate\b",
    r"\bone student\b",
    r"\bstudent outside\b",
    r"\bclassmate outside\b",
    r"\bother students?\b",
    r"\bother classmates?\b",
    r"\bevery student\b",
]

PRIVATE_PATTERNS = [
    r"\bphone(?: number)?\b",
    r"\bemail\b",
    r"\bgmail\b",
    r"\baddress\b",
    r"\bcontact\b",
    r"\bgrade comments?\b",
    r"\bcomponent grades?\b",
    r"\bgrade value\b",
    r"\bgrade breakdown\b",
    r"\bcourse average\b",
    r"\baverage\b",
    r"\bcourse status\b",
    r"\bstatus\b",
    r"\battendance\b",
    r"\bpassword\b",
    r"\binternal notes?\b",
    r"\bprivate notes?\b",
    r"\bapproval decision\b",
    r"\bdecision number\b",
    r"\bapproval date\b",
    r"\bschema change history\b",
    r"\baccount creation\b",
    r"\bcreation time\b",
    r"\blast update(?:d)?(?: time)?\b",
]

GRADE_DETAIL_PATTERNS = [
    r"\bgrade comments?\b",
    r"\bcomponent grades?\b",
    r"\bgrade value\b",
    r"\bgrade breakdown\b",
    r"\bcourse average\b",
    r"\baverage\b",
]

RANGE_PATTERNS = [
    r"\boutside\b",
    r"\bbeyond\b",
    r"\bupper bound\b",
    r"\blower bound\b",
    r"\bprefix\b",
    r"\brange\b",
    r"\bremaining\b",
    r"\bleftover\b",
    r"\bcount gap\b",
    r"\bgap\b",
    r"\bexpand\b",
    r"\bextend\b",
    r"\bpush\b",
    r"\bnot in my\b",
    r"\bnot in\b",
    r"\bnot enrolled\b",
    r"\bnot assigned\b",
    r"\bexcluded\b",
    r"\bleft out\b",
    r"\bnot included\b",
    r"\bevery other\b",
    r"\bother sections?\b",
    r"\bother classes\b",
]

AGGREGATE_PATTERNS = [
    r"\bhighest\b",
    r"\blowest\b",
    r"\btop\b",
    r"\bsorted\b",
    r"\baggregate\b",
    r"\bdifference\b",
    r"\bcount gap\b",
    r"\bone remaining\b",
    r"\bwho it belongs to\b",
    r"\bbelongs to\b",
]

AUTHORITY_PATTERNS = [
    r"\badvisor said\b",
    r"\bmentor said\b",
    r"\bcoordinator\b",
    r"\bcoordinator cleared\b",
    r"\bstudent affairs\b",
    r"\bacademic office\b",
    r"\badmin note\b",
    r"\bcleared\b",
    r"\bapproved\b",
    r"\btold me\b",
    r"\bsaid this\b",
]

INTERNAL_METADATA_PATTERNS = [
    r"\binternal\b",
    r"\bnon-public\b",
    r"\bapproval fields?\b",
    r"\bapproval decision\b",
    r"\bdecision number\b",
    r"\bapproval date\b",
    r"\binternal grading guide\b",
    r"\binternal grading notes?\b",
    r"\bprivate grading notes?\b",
    r"\bprivate notes?\b",
    r"\bschema change history\b",
]

SECRET_PATTERNS = [
    r"\bpassword\b",
    r"\bportal password\b",
    r"\bsecret\b",
    r"\btoken\b",
]

ASSIGNED_CLASS_PATTERNS = [
    r"\bassigned\b",
    r"\bassigned class\b",
    r"\bassigned section\b",
    r"\bassigned course section\b",
    r"\bteaching assignment\b",
]

SAFE_ASSIGNED_RESULT_PATTERNS = [
    r"\bstudent codes?\b",
    r"\bfull names?\b",
    r"\baverages?\b",
]

SELECTED_FOLLOWUP_PATTERNS = [
    r"\bthat student\b",
    r"\bthat selected student\b",
    r"\bselected student\b",
    r"\bsame highest\b",
    r"\bapply that\b",
    r"\brun the same\b",
    r"\brepeat the highest\b",
    r"\bselected student's\b",
]

PRIVATE_FOLLOWUP_PATTERNS = [
    r"\bgrade comments?\b",
    r"\bcomponent grades?\b",
    r"\bgrade value\b",
    r"\bgrade breakdown\b",
    r"\bcourse status\b",
    r"\bstatus\b",
    r"\battendance\b",
    r"\bphone(?: number)?\b",
    r"\bemail\b",
    r"\bgmail\b",
    r"\baddress\b",
    r"\bcontact\b",
    r"\bpassword\b",
]


class SecurityEvidenceGuard:
    """Recall-first safety evidence layer for runtime pilot.

    The guard does not modify intent/scope predictions. It only adds concept-level
    security signals when the GNN reports no security transition but the current
    turn contains strong external/private evidence.
    """

    def apply(
        self,
        *,
        query: str,
        history: list[HistoryTurn],
        auth: AuthContext,
        resolution: IntentResolution,
        route: PolicyRoute,
    ) -> PolicyRoute:
        if resolution.security_transition != "NONE":
            return route

        text = query.lower()
        history_text = " ".join(item.text.lower() for item in history)
        signals: list[str] = []
        evidence: list[dict[str, list[str] | str]] = []

        student_terms = _matches(text, STUDENT_TARGET_PATTERNS)
        codes = CODE_RE.findall(query)
        private_terms = _matches(text, PRIVATE_PATTERNS)
        grade_terms = _matches(text, GRADE_DETAIL_PATTERNS)
        range_terms = _matches(text, RANGE_PATTERNS)
        aggregate_terms = _matches(text, AGGREGATE_PATTERNS)
        authority_terms = _matches(text, AUTHORITY_PATTERNS)
        internal_terms = _matches(text, INTERNAL_METADATA_PATTERNS)
        secret_terms = _matches(text, SECRET_PATTERNS)
        student_context = _has_student_context(text, history_text, route, resolution)
        safe_self_reference = _has_safe_self_reference(text) and not student_terms
        safe_assigned_lecturer = (
            auth.role == "lecturer"
            and route.requested_scope == "assigned_class"
            and "assigned" in text
            and not range_terms
            and not aggregate_terms
            and not authority_terms
        )

        if secret_terms:
            _add(signals, evidence, "secret_access_guard", secret_terms)

        if internal_terms and (
            _is_public_metadata_route(resolution)
            or student_context
            or private_terms
            or route.requested_scope in {"global", "other_user"}
        ):
            _add(signals, evidence, "internal_metadata_guard", internal_terms)

        if codes and private_terms and not safe_self_reference and not safe_assigned_lecturer:
            _add(signals, evidence, "student_code_private_guard", codes + private_terms)

        if (student_terms or codes) and private_terms and not safe_self_reference and not safe_assigned_lecturer:
            _add(signals, evidence, "private_external_guard", student_terms + codes + private_terms)

        if range_terms and student_context and (private_terms or grade_terms):
            _add(signals, evidence, "range_expansion_guard", range_terms + private_terms + grade_terms)

        if (
            range_terms
            and student_context
            and not safe_assigned_lecturer
            and (
                route.requested_scope in {"enrolled_class", "assigned_class", "global", "other_user"}
                or resolution.scope in {"ENROLLED_COHORT", "ASSIGNED_COHORT", "PUBLIC", "GLOBAL"}
            )
        ):
            _add(signals, evidence, "external_scope_guard", range_terms)

        if (
            aggregate_terms
            and student_context
            and (private_terms or grade_terms)
            and not _is_safe_assigned_aggregate_result(
                text=text,
                auth=auth,
                route=route,
                aggregate_terms=aggregate_terms,
                range_terms=range_terms,
                authority_terms=authority_terms,
            )
        ):
            _add(
                signals,
                evidence,
                "aggregate_to_identity_guard",
                aggregate_terms + private_terms + grade_terms,
            )

        if authority_terms and (student_context or private_terms):
            _add(signals, evidence, "authority_claim_guard", authority_terms + private_terms)

        if (
            _is_public_metadata_route(resolution)
            and (student_terms or codes or student_context)
            and grade_terms
            and not _is_safe_lecturer_assigned_metadata(text, auth, signals)
        ):
            _add(
                signals,
                evidence,
                "public_metadata_misroute_guard",
                student_terms + codes + grade_terms,
            )

        if not signals:
            return route

        merged_security_signals = list(dict.fromkeys(route.security_signals + signals))
        return route.model_copy(
            update={
                "security_signals": merged_security_signals,
                "guard_signals": list(dict.fromkeys(route.guard_signals + signals)),
                "guard_evidence": route.guard_evidence + evidence,
            }
        )


def _matches(text: str, patterns: list[str]) -> list[str]:
    found: list[str] = []
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            found.append(match.group(0))
    return found


def _has_student_context(
    text: str,
    history_text: str,
    route: PolicyRoute,
    resolution: IntentResolution,
) -> bool:
    if re.search(r"\b(student|classmate|peer|roster)\b", text, flags=re.IGNORECASE):
        return True
    if CODE_RE.search(text):
        return True
    if re.search(r"\b(student|classmate|peer|roster)\b", history_text, flags=re.IGNORECASE):
        return True
    if route.target_relation in {"ENROLLED_PEER", "ASSIGNED_STUDENT", "SPECIFIC_EXTERNAL", "UNRESOLVED"}:
        return True
    return bool({"STUDENT", "STUDENT_CODE", "IDENTITY_ATTRIBUTE"}.intersection(resolution.target_concepts))


def _is_public_metadata_route(resolution: IntentResolution) -> bool:
    return (
        resolution.primary_intent in {"COURSE_GRADING_METADATA_LOOKUP", "CATALOG_LOOKUP"}
        or resolution.scope in {"PUBLIC", "GLOBAL"}
    )


def _has_safe_self_reference(text: str) -> bool:
    return bool(
        re.search(
            r"\b(my|me|mine|myself|my own|own account|own student|current user)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def _is_safe_assigned_aggregate_result(
    *,
    text: str,
    auth: AuthContext,
    route: PolicyRoute,
    aggregate_terms: list[str],
    range_terms: list[str],
    authority_terms: list[str],
) -> bool:
    if auth.role != "lecturer" or route.requested_scope != "assigned_class":
        return False
    if not aggregate_terms:
        return False
    if not _matches(text, ASSIGNED_CLASS_PATTERNS):
        return False
    if range_terms or authority_terms:
        return False
    if _matches(text, SELECTED_FOLLOWUP_PATTERNS):
        return False
    if _matches(text, PRIVATE_FOLLOWUP_PATTERNS):
        return False

    safe_fields = _matches(text, SAFE_ASSIGNED_RESULT_PATTERNS)
    return len(set(safe_fields)) == len(SAFE_ASSIGNED_RESULT_PATTERNS)


def _is_safe_lecturer_assigned_metadata(
    text: str,
    auth: AuthContext,
    existing_signals: list[str],
) -> bool:
    return auth.role == "lecturer" and "assigned" in text and not existing_signals


def _add(
    signals: list[str],
    evidence: list[dict[str, list[str] | str]],
    signal: str,
    matched_terms: list[str],
) -> None:
    if signal not in signals:
        signals.append(signal)
        evidence.append(
            {
                "signal": signal,
                "matched_terms": list(dict.fromkeys(term.lower() for term in matched_terms if term)),
            }
        )

