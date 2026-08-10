from __future__ import annotations

import yaml

from evaluation.run_experiment import ROOT, _load_systems


def _read_yaml(path):
    with path.open("r", encoding="utf-8-sig") as handle:
        return yaml.safe_load(handle) or {}


def test_experiment_configs_reference_known_systems_and_providers() -> None:
    systems = _load_systems(ROOT / "configs" / "systems")
    assert {"full_trustedsql", "generator_only_control", "full_architecture"}.issubset(systems)
    for path in sorted((ROOT / "configs" / "experiments").glob("*.yaml")):
        experiment = _read_yaml(path)["experiment"]
        assert (ROOT / experiment["dataset_profile"]).exists()
        for system_id in experiment["systems"]:
            assert system_id in systems
        for provider_id in experiment["providers"]:
            assert (ROOT / "configs" / "providers" / f"{provider_id}.yaml").exists()


def test_gemini_profile_supports_method_and_architecture_modules() -> None:
    profile = _read_yaml(ROOT / "configs" / "providers" / "gemini_25_flash.yaml")
    modules = profile["modules"]
    assert profile["llm"]["model"] == "gemini-2.5-flash"
    assert {"M1", "M2", "M3", "M6", "M7"}.issubset(modules)
    assert {"D1", "D2", "G1", "D3", "D4"}.issubset(modules)


def test_fci_profiles_expose_top_level_llm_for_architecture_baselines() -> None:
    for provider_id, model in {
        "fci_oss20b": "gpt-oss-20b",
        "fci_oss120b": "gpt-oss-120b",
    }.items():
        profile = _read_yaml(ROOT / "configs" / "providers" / f"{provider_id}.yaml")
        assert profile["llm"]["provider"] == "fci"
        assert profile["llm"]["model"] == model
        assert profile["modules"]["M3"]["llm"]["model"] == model
        assert profile["modules"]["M6"]["llm"]["model"] == model

