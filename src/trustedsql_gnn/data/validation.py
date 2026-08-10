from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from trustedsql_gnn.contracts import IntentConversation
from trustedsql_gnn.taxonomy import IntentTaxonomy


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def sha256_text(value: str) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


def validate_conversations(
    conversations: list[IntentConversation],
    taxonomy: IntentTaxonomy,
    patterns_by_id: dict[str, dict],
    known_concepts: set[str] | None = None,
) -> dict:
    errors: list[str] = []
    ids: set[str] = set()
    counts: Counter[str] = Counter()
    normalized_current: defaultdict[str, list[str]] = defaultdict(list)
    for conversation in conversations:
        try:
            taxonomy.validate_conversation(conversation)
        except ValueError as exc:
            errors.append(f"{conversation.conversation_id}:{exc}")
        if known_concepts is not None:
            for turn in conversation.turns:
                for concept in turn.labels.target_concepts:
                    if concept not in known_concepts:
                        errors.append(
                            f"{conversation.conversation_id}:turn_{turn.turn_id}:"
                            f"unknown_target_concept:{concept}"
                        )
                for mention in turn.mentions:
                    if mention.concept not in known_concepts:
                        errors.append(
                            f"{conversation.conversation_id}:turn_{turn.turn_id}:"
                            f"unknown_mention_concept:{mention.concept}"
                        )
        if conversation.conversation_id in ids:
            errors.append(f"duplicate_conversation_id:{conversation.conversation_id}")
        ids.add(conversation.conversation_id)
        pattern = patterns_by_id.get(conversation.pattern_id)
        if pattern is None:
            errors.append(f"{conversation.conversation_id}:unknown_pattern:{conversation.pattern_id}")
        else:
            errors.extend(_validate_pattern_contract(conversation, pattern))
        counts[conversation.category] += 1
        normalized_current[normalize_text(conversation.turns[-1].text)].append(conversation.conversation_id)

    duplicate_groups = {
        text: values
        for text, values in normalized_current.items()
        if text and len(values) > 1
    }
    return {
        "valid": not errors and not duplicate_groups,
        "errors": errors,
        "conversation_count": len(conversations),
        "category_counts": dict(counts),
        "duplicate_current_turn_groups": duplicate_groups,
    }


def _validate_pattern_contract(conversation: IntentConversation, pattern: dict) -> list[str]:
    errors: list[str] = []
    prefix = conversation.conversation_id
    if conversation.category != pattern["category"]:
        errors.append(f"{prefix}:category_mismatch")
    if conversation.role not in pattern["roles"]:
        errors.append(f"{prefix}:role_not_allowed_by_pattern")
    spec = pattern["conversation"]
    if not int(spec["min_turns"]) <= len(conversation.turns) <= int(spec["max_turns"]):
        errors.append(f"{prefix}:turn_count_outside_pattern_range")
    expected = pattern["expected_resolution"]
    actual = conversation.turns[-1].labels
    comparisons = {
        "semantic_intent": actual.semantic_intent,
        "operation": actual.operation,
        "scope": actual.scope,
        "target_relation": actual.target_relation,
        "transition": actual.transition,
        "security_transition": actual.security_transition,
    }
    for field, value in comparisons.items():
        if value != expected[field]:
            errors.append(f"{prefix}:final_{field}_mismatch:{value}!={expected[field]}")
    if set(actual.target_concepts) != set(expected["target_concepts"]):
        errors.append(f"{prefix}:final_target_concepts_mismatch")
    if bool(actual.reference_targets) != bool(expected["reference_required"]):
        errors.append(f"{prefix}:final_reference_requirement_mismatch")
    return errors


def load_holdout_text_hashes(manifest_path: str | Path) -> set[str]:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    root = Path(manifest["source_path"])
    hashes: set[str] = set()
    for filename in manifest["files"]:
        path = root / filename
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for record in payload:
            for turn in record.get("turns", []):
                text = turn.get("nlq") or turn.get("text")
                if text:
                    hashes.add(sha256_text(text))
    return hashes


def holdout_contamination_report(
    conversations: list[IntentConversation],
    holdout_hashes: set[str],
) -> dict:
    matches: list[dict] = []
    for conversation in conversations:
        for turn in conversation.turns:
            digest = sha256_text(turn.text)
            if digest in holdout_hashes:
                matches.append(
                    {
                        "conversation_id": conversation.conversation_id,
                        "turn_id": turn.turn_id,
                        "text_hash": digest,
                    }
                )
    return {"valid": not matches, "match_count": len(matches), "matches": matches}
