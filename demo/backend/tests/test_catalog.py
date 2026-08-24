from pathlib import Path

from demo.backend.app.catalog import load_scenario_catalog


ROOT = Path(__file__).resolve().parents[3]


def test_catalog_contains_the_curated_student_prompt_library():
    catalog = load_scenario_catalog(ROOT)
    assert list(catalog) == [
        "rag-documents",
        "direct-simple",
        "direct-rbac",
        "direct-multiturn",
        "prompt-injection",
    ]
    scenario = catalog["direct-multiturn"]
    assert scenario["canonical_id"] == "MT-MAL-150"
    assert scenario["role"] == "student"
    assert scenario["user_id"] == 40
    assert scenario["turn_type"] == "multi"
    assert [turn["turn_id"] for turn in scenario["turns"]] == [1, 2, 3, 4]
    assert [turn["turn_label"] for turn in scenario["turns"]] == [
        "BENIGN",
        "BENIGN",
        "BENIGN",
        "MALICIOUS",
    ]


def test_catalog_is_a_display_only_copyable_prompt_library():
    catalog = load_scenario_catalog(ROOT)
    encoded = str(catalog)
    assert all(
        turn["nlq"].strip()
        for scenario in catalog.values()
        for turn in scenario["turns"]
    )
    assert "sql_gt" not in encoded
    assert "attack_tags" not in encoded
    assert "primary_type" not in encoded


def test_catalog_loads_a_fresh_copy_each_time():
    first = load_scenario_catalog(ROOT)
    first["direct-multiturn"]["turns"][0]["nlq"] = "mutated"
    second = load_scenario_catalog(ROOT)
    assert second["direct-multiturn"]["turns"][0]["nlq"] != "mutated"
