from pathlib import Path

from demo.backend.app.catalog import load_scenario_catalog


ROOT = Path(__file__).resolve().parents[3]


def test_catalog_contains_only_the_multiturn_prompt_library():
    catalog = load_scenario_catalog(ROOT)
    assert list(catalog) == ["multiturn"]
    scenario = catalog["multiturn"]
    assert scenario["canonical_id"] == "MT-MAL-420"
    assert scenario["role"] == "lecturer"
    assert scenario["user_id"] == 1
    assert scenario["turn_type"] == "multi"
    assert [turn["turn_id"] for turn in scenario["turns"]] == [1, 2, 3, 4, 5, 6]


def test_catalog_is_a_display_only_copyable_prompt_library():
    catalog = load_scenario_catalog(ROOT)
    encoded = str(catalog)
    assert all(turn["nlq"].strip() for turn in catalog["multiturn"]["turns"])
    assert "sql_gt" not in encoded
    assert "attack_tags" not in encoded
    assert "primary_type" not in encoded


def test_catalog_loads_a_fresh_copy_each_time():
    first = load_scenario_catalog(ROOT)
    first["multiturn"]["turns"][0]["nlq"] = "mutated"
    second = load_scenario_catalog(ROOT)
    assert second["multiturn"]["turns"][0]["nlq"] != "mutated"
