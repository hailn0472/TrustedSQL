import pytest
import yaml

from trustedsql.modules.intent_risk_guard import M2IntentGuard


def test_packaged_intent_phase_entrypoints_available(project_root):
    pytest.importorskip("torch")
    from trustedsql_gnn import GNNIntentPhase

    checkpoint = (
        project_root
        / "artifacts"
        / "models"
        / "intent_gnn"
        / "v1"
        / "best.pt"
    )
    assert checkpoint.exists()
    assert callable(GNNIntentPhase)
    assert callable(M2IntentGuard)


def test_m2_config_uses_official_intent_gnn(project_root):
    config_path = project_root / "configs" / "providers" / "gemini_25_flash.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    m2 = payload["modules"]["M2"]
    assert m2["engine"] == "trustedsql_m2_intent_gnn"
    assert m2["mode"] == "calibrated"
    assert m2["intent_gnn"]["device"] == "cpu"
    assert m2["intent_gnn"]["allow_hash_encoder"] is False


