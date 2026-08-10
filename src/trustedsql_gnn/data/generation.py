from __future__ import annotations

import json
from pathlib import Path

from trustedsql_gnn.data.io import write_jsonl


GENERATION_SYSTEM_INSTRUCTIONS = """
Generate university Text-to-SQL intent conversations from the supplied pattern contract.
Return JSON only. Follow every turn label and reference target exactly.
Do not mention dataset generation, maliciousness, policy labels, ALLOW/BLOCK, or graph labels.
Vary wording, entities, discourse style, sentence order, and reference distance.
Do not copy positive or contrastive prompts verbatim between samples.
The final turn must match expected_resolution. Previous turns are context, not extra samples.
""".strip()


def export_generation_tasks(
    *,
    pattern_bank_path: str | Path,
    output_path: str | Path,
    batch_size: int = 25,
) -> dict:
    payload = json.loads(Path(pattern_bank_path).read_text(encoding="utf-8"))
    tasks: list[dict] = []
    for pattern in payload["patterns"]:
        remaining = int(pattern["generation"]["target_samples"])
        batch_index = 0
        while remaining > 0:
            count = min(batch_size, remaining)
            tasks.append(
                {
                    "task_id": f"{pattern['pattern_id']}::batch_{batch_index:03d}",
                    "requested_conversations": count,
                    "system_instructions": GENERATION_SYSTEM_INSTRUCTIONS,
                    "pattern": pattern,
                    "output_contract": {
                        "conversation_id": "unique string",
                        "pattern_id": pattern["pattern_id"],
                        "category": pattern["category"],
                        "role": "one value allowed by pattern.roles",
                        "turns": [
                            {
                                "turn_id": "contiguous integer from 1",
                                "text": "natural language user turn",
                                "mentions": [
                                    {
                                        "surface": "text span",
                                        "concept": "concept catalog value",
                                        "source": "generator",
                                        "confidence": 1.0,
                                    }
                                ],
                                "labels": {
                                    "semantic_intent": "taxonomy value",
                                    "operation": "taxonomy value",
                                    "scope": "taxonomy value",
                                    "target_relation": "taxonomy value",
                                    "transition": "taxonomy value",
                                    "target_concepts": ["concept values"],
                                    "reference_targets": [
                                        {
                                            "target_turn": "earlier turn number",
                                            "target_concept": "concept value",
                                            "surface": "reference phrase",
                                        }
                                    ],
                                    "security_transition": "taxonomy value",
                                },
                            }
                        ],
                        "generation_metadata": {
                            "generator_version": "generator identifier",
                            "surface_variant_id": "unique surface-family id",
                            "contrastive_pair_id": "shared id or null",
                            "entity_seed": "stable entity seed",
                            "pattern_revision": "v1",
                        },
                    },
                }
            )
            remaining -= count
            batch_index += 1
    write_jsonl(output_path, tasks)
    return {
        "task_count": len(tasks),
        "requested_conversations": sum(item["requested_conversations"] for item in tasks),
        "output_path": str(Path(output_path).resolve()),
    }
