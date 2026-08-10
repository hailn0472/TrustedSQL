from __future__ import annotations

import json

import pytest
from pathlib import Path

from trustedsql.modules.intent_risk_guard import compact_m2_output, evaluate_m2_policy


def test_promoted_checkpoint_preserves_m2_regression_contract() -> None:
    pytest.importorskip("torch")
    from trustedsql_gnn import GNNIntentPhase

    fixture_path = Path(__file__).parent / "fixtures" / "m2_regression.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    encoder_modules = (
        Path(__file__).resolve().parents[2]
        / "artifacts"
        / "text_encoder"
        / "all-MiniLM-L6-v2"
        / "modules.json"
    )
    if not encoder_modules.exists():
        pytest.skip("GNN text encoder assets are not installed; run tools/preprocessing/fetch_text_encoder.py")
    phase = GNNIntentPhase(device="cpu", allow_hash_encoder=False)
    for turn_id, turn in enumerate(fixture["turns"], start=1):
        result = phase.run_turn(
            conversation_id=fixture["conversation_id"],
            role=fixture["role"],
            user_id=fixture["user_id"],
            current_text=turn["text"],
            turn_id=turn_id,
        )
        compact = compact_m2_output(result)
        decision = evaluate_m2_policy(compact, mode="calibrated")
        assert compact["primary_intent"] == turn["expected_intent"]
        assert compact["security_transition"] == turn["expected_security_transition"]
        assert decision["decision"] == turn["expected_decision"]


