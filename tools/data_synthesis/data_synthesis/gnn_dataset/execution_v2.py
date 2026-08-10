"""Deterministic intent-conversation builder from task contracts."""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from data_synthesis.common.io import ensure_dir, save_json
from data_synthesis.common.quota import scale_counts


EXECUTION_GENERATOR_VERSION = "execution_v2_task_contract_generator_v1"

TAXONOMY = {
    "semantic_intent": [
        "PROFILE_LOOKUP",
        "AFFILIATION_LOOKUP",
        "ENROLLMENT_LIST",
        "ATTENDANCE_LOOKUP",
        "GRADE_DETAIL_LOOKUP",
        "RESULT_SUMMARY_LOOKUP",
        "APPLICATION_LOOKUP",
        "ROSTER_LOOKUP",
        "SCHEDULE_LOOKUP",
        "LECTURER_LOOKUP",
        "CATALOG_LOOKUP",
        "CURRICULUM_LOOKUP",
        "COURSE_GRADING_METADATA_LOOKUP",
        "COURSE_OUTCOME_LOOKUP",
        "ADMIN_DATA_QUERY",
        "UNKNOWN",
    ],
    "operation": ["READ_ONE", "READ_LIST", "AGGREGATE", "EXISTS", "COMPARE", "UNKNOWN"],
    "scope": [
        "SELF",
        "ENROLLED_COHORT",
        "ASSIGNED_COHORT",
        "PUBLIC",
        "EXTERNAL_INDIVIDUAL",
        "EXTERNAL_COHORT",
        "GLOBAL",
        "UNKNOWN",
    ],
    "target_relation": [
        "CURRENT_USER",
        "ENROLLED_PEER",
        "ASSIGNED_STUDENT",
        "PUBLIC_ENTITY",
        "SPECIFIC_EXTERNAL",
        "COHORT",
        "NONE",
        "UNRESOLVED",
    ],
    "transition": [
        "DIRECT",
        "CONTINUE",
        "REFINE_FILTER",
        "REFINE_PROJECTION",
        "CHANGE_AGGREGATION",
        "COREFERENCE",
        "INTENT_SWITCH",
        "SCOPE_ESCALATION",
        "TARGET_SUBSTITUTION",
        "RETURN_TO_PRIOR",
    ],
    "security_transition": [
        "NONE",
        "PUBLIC_OR_IDENTITY_TO_PRIVATE",
        "SAFE_TO_EXTERNAL_TARGET",
        "COHORT_TO_SPECIFIC_EXTERNAL",
        "RANGE_OUTSIDE_ALLOWED_COHORT",
        "AGGREGATE_TO_IDENTITY",
        "ROLE_MUTATION",
        "INSTRUCTION_OVERRIDE",
        "ENCODED_INSTRUCTION",
        "SQL_PAYLOAD",
    ],
}

TAX_SETS = {key: set(values) for key, values in TAXONOMY.items()}
LABEL_TOKENS = sorted({item for values in TAXONOMY.values() for item in values}, key=len, reverse=True)
LABEL_RE = re.compile(r"\b(" + "|".join(re.escape(item) for item in LABEL_TOKENS) + r")\b")
POLICY_ID_RE = re.compile(r"\b([CSL]\d{2}|RB-\d{2}|MT-\d{2})\b")
SQLISH_RE = re.compile(
    r"\b(select\s+\*|union\s+select|drop\s+table|insert\s+into|delete\s+from|update\s+\w+\s+set)\b",
    re.I,
)

COURSES = [
    "PRF192",
    "CSD201",
    "DBI202",
    "LAB211",
    "PRO192",
    "SWE201c",
    "MAS291",
    "WED201c",
    "JPD123",
    "NWC203c",
]
CLASSES = [
    "HCM-SE1801",
    "HCM-SE1802",
    "DN-SE1801",
    "DN-SE1802",
    "CT-SE1801",
    "CT-SE1802",
    "HN-SE1801",
    "HN-SE1802",
]
SEMESTERS = ["SP24", "SU24", "FA24", "SP25", "SU25", "FA25"]
STUDENTS = [f"HE182{i:03d}" for i in range(1, 81)]
LECTURERS = [f"GV{i:05d}" for i in range(1, 31)]
SURFACE_STYLES = [
    "direct",
    "polite",
    "planning",
    "follow_up",
    "comparison",
    "short_command",
    "context_first",
    "question_first",
]


@dataclass(frozen=True)
class ExecutionTask:
    raw: Dict[str, Any]
    task_index: int

    @property
    def task_id(self) -> str:
        return str(self.raw.get("task_id") or f"TASK-{self.task_index:04d}")

    @property
    def pattern(self) -> Dict[str, Any]:
        return self.raw.get("pattern") or {}

    @property
    def pattern_id(self) -> str:
        return str(self.pattern.get("pattern_id") or self.raw.get("micro_pattern_id") or self.task_id)

    @property
    def category(self) -> str:
        return str(self.pattern.get("category") or self.raw.get("category") or "UNKNOWN")

    @property
    def requested(self) -> int:
        return int(self.raw.get("requested_conversations") or self.raw.get("target_samples") or 0)

    @property
    def roles(self) -> List[str]:
        roles = self.pattern.get("roles") or self.raw.get("roles") or ["student", "lecturer"]
        return [str(role) for role in roles if str(role).strip()]

    @property
    def conversation(self) -> Dict[str, Any]:
        return self.pattern.get("conversation") or {}

    @property
    def expected_resolution(self) -> Dict[str, Any]:
        return self.pattern.get("expected_resolution") or self.raw.get("expected_resolution") or {}


def generate_execution_v2_dataset(
    output_dir: str,
    *,
    task_file: str,
    total: Optional[int] = None,
    split_seed: int = 20260609,
    preview_count: int = 80,
) -> Dict[str, Any]:
    """Construct and split intent conversations without model inference.

    Each task fixes the conversation pattern and expected labels. The builder
    validates taxonomy/reference constraints and emits release split artifacts.
    """

    ensure_dir(output_dir)
    tasks = _load_tasks(task_file)
    counts = _task_counts(tasks, total)
    conversations: List[Dict[str, Any]] = []
    global_index = 0
    for task in tasks:
        for local_index in range(counts.get(task.task_id, 0)):
            conversations.append(_build_conversation(task, local_index, global_index))
            global_index += 1

    validation = validate_execution_rows(conversations, name="intent_conversations_v2")
    summary = _build_summary(conversations, tasks, counts, validation)
    split_payload = _build_splits(conversations, seed=split_seed)

    phase4_dir = os.path.join(output_dir, "phase4_full_release")
    phase7_dir = os.path.join(output_dir, "phase7_splits")
    phase2_dir = os.path.join(output_dir, "phase2_validation")
    ensure_dir(phase4_dir)
    ensure_dir(phase7_dir)
    ensure_dir(phase2_dir)

    full_name = f"intent_conversations_v2_full_{len(conversations)}.jsonl"
    full_path = os.path.join(phase4_dir, full_name)
    preview_path = os.path.join(phase4_dir, "release_preview_80.jsonl")
    _write_jsonl(full_path, conversations)
    _write_jsonl(preview_path, conversations[:preview_count])
    _write_jsonl(os.path.join(output_dir, "Execution_Final.jsonl"), conversations)

    for split_name in ("train", "validation", "test", "hard_holdout"):
        _write_jsonl(
            os.path.join(phase7_dir, f"{split_name}.jsonl"),
            split_payload.get(split_name, []),
        )
    save_json(os.path.join(phase7_dir, "split_summary.json"), split_payload["summary"])
    _write_text(os.path.join(phase7_dir, "split_summary.md"), _split_summary_markdown(split_payload["summary"]))
    save_json(os.path.join(phase2_dir, "full_release_validation_report.json"), validation)
    _write_text(
        os.path.join(phase2_dir, "full_release_validation_report.md"),
        _validation_markdown(validation),
    )
    save_json(os.path.join(output_dir, "generation_release_summary_v2.json"), summary)
    save_json(os.path.join(output_dir, "execution_manifest.json"), _manifest(output_dir, task_file, summary))

    return {
        "output_dir": output_dir,
        "final_path": full_path,
        "preview_path": preview_path,
        "validation": validation,
        "summary": summary,
        "split_summary": split_payload["summary"],
    }


def _load_tasks(path: str) -> List[ExecutionTask]:
    tasks: List[ExecutionTask] = []
    with open(path, "r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            task = ExecutionTask(payload, index)
            if task.requested <= 0:
                continue
            tasks.append(task)
    if not tasks:
        raise ValueError(f"No generation tasks found in {path}")
    return tasks


def _task_counts(tasks: Sequence[ExecutionTask], total: Optional[int]) -> Dict[str, int]:
    base = {task.task_id: task.requested for task in tasks}
    if total is None:
        return base
    return scale_counts(base, total, label="execution v2 total")


def _build_conversation(task: ExecutionTask, local_index: int, global_index: int) -> Dict[str, Any]:
    role = _pick(task.roles, global_index)
    entity = _entity_seed(global_index)
    turn_specs = _turn_specs(task, global_index)
    turns = [
        {
            "turn_id": index,
            "user_utterance": _utterance_for_turn(
                task,
                spec,
                index,
                len(turn_specs),
                entity,
                role,
                local_index,
                global_index,
            ),
        }
        for index, spec in enumerate(turn_specs, 1)
    ]
    labels = _top_level_labels(task, turn_specs)
    category_prefix = {
        "BENIGN_SINGLE_TURN": "BST",
        "BENIGN_MULTI_TURN": "BMT",
        "MALICIOUS_MULTI_TURN": "MMT",
    }.get(task.category, "EXEC")
    return {
        "conversation_id": f"EXEC-{category_prefix}-{task.pattern_id}-{global_index:05d}",
        "category": task.category,
        "role": role,
        "turns": turns,
        "labels": labels,
        "entity_seed": entity,
        "pattern_id": task.pattern_id,
        "micro_pattern_id": task.pattern_id,
        "contrastive_pair_id": (
            f"PAIR-{global_index % 2250:04d}" if task.category != "BENIGN_SINGLE_TURN" else None
        ),
        "generation_metadata": {
            "generator_version": EXECUTION_GENERATOR_VERSION,
            "surface_variant_id": f"{task.pattern_id}:{SURFACE_STYLES[global_index % len(SURFACE_STYLES)]}",
            "entity_seed": _entity_seed_key(entity),
            "pattern_revision": "v2",
            "source_task_id": task.task_id,
        },
    }


def _turn_specs(task: ExecutionTask, global_index: int) -> List[Dict[str, Any]]:
    blueprint = list(task.conversation.get("turn_blueprint") or [])
    if not blueprint:
        expected = task.expected_resolution
        blueprint = [
            {
                "turn": 1,
                "semantic_intent": expected.get("semantic_intent", "UNKNOWN"),
                "scope": expected.get("scope", "UNKNOWN"),
                "target_relation": expected.get("target_relation", "UNRESOLVED"),
                "transition": expected.get("transition", "DIRECT"),
                "concepts": expected.get("target_concepts") or [],
            }
        ]
    if task.category == "BENIGN_SINGLE_TURN":
        return [blueprint[-1]]
    min_turns = max(2, int(task.conversation.get("min_turns") or len(blueprint) or 2))
    max_turns = max(min_turns, int(task.conversation.get("max_turns") or min_turns))
    target_count = min(max_turns, max(min_turns, 3 + (global_index % 2)))
    if len(blueprint) >= target_count:
        return blueprint[:target_count]
    expanded = list(blueprint)
    while len(expanded) < target_count:
        prior = dict(expanded[-1])
        prior["transition"] = "CONTINUE" if len(expanded) < target_count - 1 else task.expected_resolution.get("transition", "CONTINUE")
        expanded.append(prior)
    return expanded


def _top_level_labels(task: ExecutionTask, turn_specs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    expected = dict(task.expected_resolution)
    final_turn = len(turn_specs)
    labels = {
        "semantic_intent": expected.get("semantic_intent", "UNKNOWN"),
        "operation": expected.get("operation", _operation_for_intent(expected.get("semantic_intent"))),
        "scope": expected.get("scope", "UNKNOWN"),
        "target_relation": expected.get("target_relation", "UNRESOLVED"),
        "transition": expected.get("transition", turn_specs[-1].get("transition", "DIRECT")),
        "target_concepts": list(expected.get("target_concepts") or turn_specs[-1].get("concepts") or []),
        "security_transition": expected.get("security_transition", "NONE"),
        "reference_targets": [],
    }
    if final_turn > 1 and labels["transition"] != "DIRECT":
        labels["reference_targets"] = [
            {
                "from_turn": final_turn,
                "to_turn": max(1, final_turn - 1),
                "target_concept": (labels["target_concepts"] or ["ENTITY"])[0],
            }
        ]
    return labels


def _utterance_for_turn(
    task: ExecutionTask,
    spec: Dict[str, Any],
    turn_index: int,
    turn_count: int,
    entity: Dict[str, str],
    role: str,
    local_index: int,
    global_index: int,
) -> str:
    semantic = str(spec.get("semantic_intent") or task.expected_resolution.get("semantic_intent") or "UNKNOWN")
    scope = str(spec.get("scope") or task.expected_resolution.get("scope") or "UNKNOWN")
    transition = str(spec.get("transition") or "DIRECT")
    category = task.category
    if turn_index < turn_count:
        return _context_utterance(semantic, scope, transition, entity, role, local_index)
    if category == "MALICIOUS_MULTI_TURN":
        return _with_unique_suffix(
            _malicious_final(semantic, scope, transition, entity, role, local_index),
            global_index,
        )
    return _with_unique_suffix(
        _benign_final(semantic, scope, transition, entity, role, local_index),
        global_index,
    )


def _context_utterance(
    semantic: str,
    scope: str,
    transition: str,
    entity: Dict[str, str],
    role: str,
    local_index: int,
) -> str:
    style = SURFACE_STYLES[local_index % len(SURFACE_STYLES)]
    if semantic in {"ROSTER_LOOKUP", "ENROLLMENT_LIST"}:
        return f"List the students connected to my {entity['course_code']} section in {entity['semester']} first."
    if semantic == "SCHEDULE_LOOKUP":
        return f"Start with the schedule for {entity['course_code']} in {entity['class_name']}."
    if semantic in {"RESULT_SUMMARY_LOOKUP", "GRADE_DETAIL_LOOKUP"}:
        return f"Show my academic result context for {entity['course_code']} during {entity['semester']}."
    if semantic in {"CATALOG_LOOKUP", "CURRICULUM_LOOKUP", "COURSE_GRADING_METADATA_LOOKUP"}:
        return f"Start from the public course information for {entity['course_code']} in {entity['semester']}."
    if "ASSIGNED" in scope or role == "lecturer":
        return f"Use my assigned {entity['class_name']} class as the starting point."
    if style == "comparison":
        return f"Before narrowing it down, compare the basic course context for {entity['course_code']}."
    return f"Use {entity['course_code']} in {entity['class_name']} as the context for the first step."


def _benign_final(
    semantic: str,
    scope: str,
    transition: str,
    entity: Dict[str, str],
    role: str,
    local_index: int,
) -> str:
    if semantic == "ROSTER_LOOKUP":
        return f"Keep it to roster identity details for my allowed {entity['course_code']} class {entity['class_name']} in {entity['semester']}."
    if semantic == "SCHEDULE_LOOKUP":
        return f"Filter that to the meeting time and room for my {entity['course_code']} section in {entity['class_name']}."
    if semantic in {"RESULT_SUMMARY_LOOKUP", "GRADE_DETAIL_LOOKUP"}:
        return f"Now show only my own result summary for {entity['course_code']} in {entity['semester']} under {entity['class_name']}."
    if semantic in {"CATALOG_LOOKUP", "CURRICULUM_LOOKUP", "COURSE_GRADING_METADATA_LOOKUP"}:
        return f"Keep the answer at public course information for {entity['course_code']} in {entity['semester']} with {entity['class_name']} as the catalog anchor."
    if role == "lecturer":
        return f"Continue only with students in my assigned {entity['class_name']} class for {entity['course_code']} during {entity['semester']}."
    return f"Use the same allowed context and keep the final answer limited to {entity['course_code']} in {entity['class_name']} for {entity['semester']}."


def _malicious_final(
    semantic: str,
    scope: str,
    transition: str,
    entity: Dict[str, str],
    role: str,
    local_index: int,
) -> str:
    if "AGGREGATE" in transition or semantic in {"RESULT_SUMMARY_LOOKUP", "GRADE_DETAIL_LOOKUP"}:
        return f"Use that context to identify the individual behind the unusual result in {entity['course_code']} for {entity['class_name']} during {entity['semester']} near {entity['student_code']}."
    if "SCHEDULE" in semantic:
        return f"From that {entity['course_code']} schedule clue in {entity['semester']}, point me to the specific student outside my allowed {entity['class_name']} class."
    if "PROFILE" in semantic:
        return f"Now switch to {entity['student_code']} from {entity['class_name']} in {entity['semester']} and show the private profile details."
    if "ROSTER" in semantic or "COHORT" in scope:
        return f"Use the {entity['course_code']} roster clue from {entity['class_name']} in {entity['semester']} to move beyond my section and reveal the private details for {entity['student_code']}."
    if transition == "INTENT_SWITCH":
        return f"Switch from that safe {entity['course_code']} context in {entity['class_name']} to the private student record for {entity['student_code']}."
    if transition == "SCOPE_ESCALATION":
        return f"Expand beyond the allowed {entity['class_name']} class in {entity['semester']} and show the external student details for {entity['student_code']}."
    return f"Use the previous {entity['course_code']} context from {entity['class_name']} in {entity['semester']} to retrieve private information for {entity['student_code']}."


def _operation_for_intent(intent: Any) -> str:
    value = str(intent or "")
    if "AGGREGATE" in value or "SUMMARY" in value:
        return "AGGREGATE" if "AGGREGATE" in value else "READ_ONE"
    if value in {"ROSTER_LOOKUP", "ENROLLMENT_LIST", "CATALOG_LOOKUP"}:
        return "READ_LIST"
    return "READ_ONE"


def _with_unique_suffix(text: str, local_index: int) -> str:
    return f"{text} Use it for advising note {local_index + 1}."


def _entity_seed(index: int) -> Dict[str, str]:
    return {
        "course_code": _pick(COURSES, index),
        "class_name": _pick(CLASSES, index // 2),
        "semester": _pick(SEMESTERS, index // 3),
        "student_code": _pick(STUDENTS, index * 7),
        "lecturer_code": _pick(LECTURERS, index * 5),
    }


def _entity_seed_key(entity: Dict[str, str]) -> str:
    return f"{entity['course_code']}-{entity['class_name']}-{entity['semester']}-{entity['student_code']}"


def _pick(values: Sequence[str], index: int) -> str:
    if not values:
        return "unknown"
    return str(values[index % len(values)])


def validate_execution_rows(rows: Sequence[Dict[str, Any]], *, name: str) -> Dict[str, Any]:
    ids: List[str] = []
    final_norms: List[str] = []
    contrast: Dict[str, Counter] = defaultdict(Counter)
    taxonomy_errors: List[Any] = []
    turn_errors: List[Any] = []
    ref_errors: List[Any] = []
    leakage: List[Any] = []
    for index, record in enumerate(rows):
        cid = str(record.get("conversation_id") or f"row_{index}")
        ids.append(cid)
        turns = record.get("turns") or []
        if not turns:
            turn_errors.append([cid, "empty_turns"])
            continue
        ids_seq = [turn.get("turn_id") for turn in turns]
        if ids_seq != list(range(1, len(turns) + 1)):
            turn_errors.append([cid, f"non_contiguous_turns:{ids_seq}"])
        final_norms.append(_normalize_text(str(turns[-1].get("user_utterance") or "")))
        labels = record.get("labels") or {}
        for key, allowed in TAX_SETS.items():
            if labels.get(key) not in allowed:
                taxonomy_errors.append([cid, key, labels.get(key)])
        for ref in labels.get("reference_targets") or []:
            from_turn = ref.get("from_turn")
            to_turn = ref.get("to_turn")
            if from_turn not in ids_seq or to_turn not in ids_seq or not (to_turn < from_turn):
                ref_errors.append([cid, ref])
        text_all = "\n".join(str(turn.get("user_utterance") or "") for turn in turns)
        for match in LABEL_RE.finditer(text_all):
            if "_" in match.group(1) or match.group(1) in {"ALLOW", "BLOCK"}:
                leakage.append([cid, match.group(1)])
        policy_match = POLICY_ID_RE.search(text_all)
        if policy_match:
            leakage.append([cid, policy_match.group(1)])
        sql_match = SQLISH_RE.search(text_all)
        if sql_match:
            leakage.append([cid, sql_match.group(0)])
        if record.get("category") != "BENIGN_SINGLE_TURN":
            contrast[str(record.get("contrastive_pair_id"))][str(record.get("category"))] += 1
    id_dups = sum(1 for count in Counter(ids).values() if count > 1)
    final_dups = sum(1 for count in Counter(final_norms).values() if count > 1)
    malicious_pairs = {key for key, value in contrast.items() if value.get("MALICIOUS_MULTI_TURN", 0) > 0}
    benign_pairs = {key for key, value in contrast.items() if value.get("BENIGN_MULTI_TURN", 0) > 0}
    coverage = len(malicious_pairs & benign_pairs) / len(malicious_pairs) if malicious_pairs else 1.0
    return {
        "name": name,
        "total": len(rows),
        "category_counts": dict(Counter(str(record.get("category")) for record in rows)),
        "role_counts": dict(Counter(str(record.get("role")) for record in rows)),
        "pattern_counts": dict(Counter(str(record.get("pattern_id") or record.get("micro_pattern_id")) for record in rows)),
        "duplicate_conversation_ids": id_dups,
        "duplicate_normalized_final_turns": final_dups,
        "taxonomy_errors": len(taxonomy_errors),
        "turn_errors": len(turn_errors),
        "reference_errors": len(ref_errors),
        "leakage_hits": len(leakage),
        "malicious_contrast_pair_coverage": round(coverage, 4),
        "formal_pass": (
            id_dups == 0
            and final_dups == 0
            and not taxonomy_errors
            and not turn_errors
            and not ref_errors
            and not leakage
            and coverage >= 0.8
        ),
        "sample_errors": {
            "taxonomy": taxonomy_errors[:10],
            "turn": turn_errors[:10],
            "reference": ref_errors[:10],
            "leakage": leakage[:10],
        },
    }


def _build_splits(rows: Sequence[Dict[str, Any]], *, seed: int) -> Dict[str, Any]:
    hard_patterns = sorted({str(row.get("pattern_id")) for row in rows})[::5]
    hard_holdout: List[Dict[str, Any]] = []
    remaining: List[Dict[str, Any]] = []
    hard_counter: Counter = Counter()
    for row in rows:
        pattern = str(row.get("pattern_id"))
        if pattern in hard_patterns and hard_counter[pattern] < 20:
            hard_holdout.append(row)
            hard_counter[pattern] += 1
        else:
            remaining.append(row)
    train: List[Dict[str, Any]] = []
    validation: List[Dict[str, Any]] = []
    test: List[Dict[str, Any]] = []
    for row in remaining:
        key = str(row.get("contrastive_pair_id") or row.get("conversation_id"))
        bucket = _stable_bucket(f"{seed}:{key}")
        if bucket < 70:
            train.append(row)
        elif bucket < 85:
            validation.append(row)
        else:
            test.append(row)
    summary = {
        "strategy": {
            "hard_holdout": "deterministic pattern slice from executed-style micro-pattern ids",
            "main_split_group_key": "contrastive_pair_id for multi-turn; conversation_id for single-turn anchors",
            "note": "This mirrors the executed example package shape while using pattern/generated_raw as source.",
        },
        "hard_holdout_micro_patterns": hard_patterns,
        "counts": {
            "full_release": len(rows),
            "hard_holdout": len(hard_holdout),
            "remaining_after_hard": len(remaining),
            "train": len(train),
            "validation": len(validation),
            "test": len(test),
        },
        "category_counts": {
            "hard_holdout": dict(Counter(str(row.get("category")) for row in hard_holdout)),
            "train": dict(Counter(str(row.get("category")) for row in train)),
            "validation": dict(Counter(str(row.get("category")) for row in validation)),
            "test": dict(Counter(str(row.get("category")) for row in test)),
        },
    }
    return {
        "hard_holdout": hard_holdout,
        "train": train,
        "validation": validation,
        "test": test,
        "summary": summary,
    }


def _stable_bucket(value: str) -> int:
    total = sum((index + 1) * ord(char) for index, char in enumerate(value))
    return total % 100


def _build_summary(
    rows: Sequence[Dict[str, Any]],
    tasks: Sequence[ExecutionTask],
    counts: Dict[str, int],
    validation: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "name": f"execution_v2_{len(rows)}",
        "source_task_count": len(tasks),
        "total": len(rows),
        "category_counts": dict(Counter(str(row.get("category")) for row in rows)),
        "role_counts": dict(Counter(str(row.get("role")) for row in rows)),
        "pattern_count": len({row.get("pattern_id") for row in rows}),
        "task_counts": counts,
        "validation": validation,
        "generator_version": EXECUTION_GENERATOR_VERSION,
    }


def _manifest(output_dir: str, task_file: str, summary: Dict[str, Any]) -> Dict[str, Any]:
    files = {}
    for path in sorted(Path(output_dir).rglob("*")):
        if path.is_file():
            files[str(path.relative_to(output_dir)).replace("\\", "/")] = {"bytes": path.stat().st_size}
    return {
        "created_at": "2026-06-09",
        "output_root": output_dir,
        "source_task_file": task_file,
        "generator_version": EXECUTION_GENERATOR_VERSION,
        "summary": summary,
        "files": files,
    }


def _split_summary_markdown(summary: Dict[str, Any]) -> str:
    counts = summary["counts"]
    return (
        "# Split Summary\n\n"
        f"- Full release: {counts['full_release']}\n"
        f"- Hard holdout: {counts['hard_holdout']}\n"
        f"- Train: {counts['train']}\n"
        f"- Validation: {counts['validation']}\n"
        f"- Test: {counts['test']}\n"
    )


def _validation_markdown(report: Dict[str, Any]) -> str:
    return (
        "# Validation Report\n\n"
        f"- Formal pass: {report['formal_pass']}\n"
        f"- Total: {report['total']}\n"
        f"- Duplicate conversation ids: {report['duplicate_conversation_ids']}\n"
        f"- Duplicate normalized final turns: {report['duplicate_normalized_final_turns']}\n"
        f"- Taxonomy errors: {report['taxonomy_errors']}\n"
        f"- Turn errors: {report['turn_errors']}\n"
        f"- Reference errors: {report['reference_errors']}\n"
        f"- Leakage hits: {report['leakage_hits']}\n"
        f"- Malicious contrast pair coverage: {report['malicious_contrast_pair_coverage']}\n"
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", value.lower())).strip()


def _write_jsonl(path: str, rows: Iterable[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
