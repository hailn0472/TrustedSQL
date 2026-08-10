from __future__ import annotations

import json
from types import SimpleNamespace

from trustedsql.utils.io import write_json, write_jsonl
from trustedsql.utils.hash import sha256_file
from benchmark_eval.pipeline import (
    _validate_architecture_resource_snapshot,
    _validate_method_resource_fingerprints,
    _validate_method_runtime_rows,
    evaluate_run,
)


def test_automatic_pipeline_writes_final_metrics_without_review(tmp_path, monkeypatch) -> None:
    ddl_path = tmp_path / "ddl.md"
    ddl_path.write_text("CREATE TABLE values_table (x int);", encoding="utf-8")
    benign_path = tmp_path / "benign.json"
    benign_path.write_text(
        json.dumps(
            [
                {
                    "id": "B1",
                    "role": "student",
                    "user_context_id": 40,
                    "nlq": "benign",
                    "sql_gt": "SELECT 1 AS x",
                }
            ]
        ),
        encoding="utf-8",
    )
    malicious_path = tmp_path / "malicious.json"
    malicious_path.write_text(
        json.dumps(
            [
                {
                    "id": "M1",
                    "role": "student",
                    "user_context_id": 40,
                    "seq_label": "MALICIOUS",
                    "turns": [
                        {"turn_id": 1, "nlq": "prefix", "sql_gt": "SELECT 1 AS x", "turn_label": "BENIGN"},
                        {"turn_id": 2, "nlq": "attack", "sql_gt": None, "turn_label": "MALICIOUS"},
                    ],
                },
            ]
        ),
        encoding="utf-8",
    )
    run_id = "run"
    output_dir = tmp_path / "outputs"
    runtime_rows = [
        _row(run_id, "B1", 1, "ALLOW", True),
        _row(run_id, "M1", 1, "ALLOW", True),
        _row(run_id, "M1", 2, "DENY", False),
    ]
    write_jsonl(output_dir / run_id / "runtime" / "raw_turn_outputs.jsonl", runtime_rows)
    dataset_config = {
        "datasets": {
            "benign_single": {"path": "benign.json", "turn_type": "single"},
            "malicious_multi": {"path": "malicious.json", "turn_type": "multi"},
        }
    }
    write_json(
        output_dir / run_id / "run_manifest.json",
        {
            "runtime_kind": "trustedsql",
            "protocol": "trustedsql-refusal-v1",
            "resolved_config": {"datasets": dataset_config},
            "resource_fingerprints": [],
            "benchmark_selection": {
                "expected_turns": [
                    {"setting_id": "full", "sample_id": "B1", "turn_id": 1},
                    {"setting_id": "full", "sample_id": "M1", "turn_id": 1},
                    {"setting_id": "full", "sample_id": "M1", "turn_id": 2},
                ]
            },
        },
    )

    class FakeDb:
        def __init__(self, _url, **_kwargs):
            pass

        def execute_read_only(self, _sql):
            return SimpleNamespace(executed=True, rows=[{"x": 1}], db_error=None)

    monkeypatch.setattr("benchmark_eval.pipeline.DatabaseExecutor", FakeDb)
    config = SimpleNamespace(
        output_dir=output_dir,
        project_root=tmp_path,
        raw={"database": {"url": "fake"}},
        datasets=dataset_config,
        ddl_path=ddl_path,
    )
    metrics = evaluate_run(config, run_id)
    assert metrics["utility"]["full"]["st_ex"]["value"] == 1.0
    assert metrics["multi_turn_security"]["full"]["prefix_rs"]["value"] == 1.0
    assert metrics["multi_turn_security"]["full"]["valid_secure_sequence_rate"]["value"] == 1.0
    evaluation_dir = output_dir / run_id / "evaluation"
    assert not (evaluation_dir / "gt_execution_cache.jsonl").exists()
    assert (evaluation_dir / "evidence" / "turn_utility_evidence.jsonl").exists()
    assert (evaluation_dir / "evidence" / "sequence_security_evidence.jsonl").exists()
    assert (evaluation_dir / "metrics" / "benchmark_metrics.json").exists()
    assert not (evaluation_dir / "metrics" / "method_metrics.json").exists()
    assert not (output_dir / run_id / "review").exists()


def test_runtime_preflight_rejects_missing_and_duplicate_turns() -> None:
    snapshot = {
        "benchmark_selection": {
            "expected_turns": [
                {"setting_id": "full", "sample_id": "B1", "turn_id": 1},
                {"setting_id": "full", "sample_id": "B2", "turn_id": 1},
            ]
        }
    }
    rows = [
        _row("run", "B1", 1, "ALLOW", True),
        _row("run", "B1", 1, "ALLOW", True),
    ]
    try:
        _validate_method_runtime_rows(rows, snapshot)
    except RuntimeError as exc:
        assert "duplicates=" in str(exc)
        assert "missing=" in str(exc)
    else:
        raise AssertionError("Incomplete runtime evidence must be rejected")


def test_resource_validation_ignores_runtime_only_resources(tmp_path) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text("[]", encoding="utf-8")
    dataset_config = {"datasets": {"benign_single": {"path": "dataset.json"}}}
    config = SimpleNamespace(project_root=tmp_path, datasets=dataset_config)
    snapshot = {
        "resolved_config": {"datasets": dataset_config},
        "resource_fingerprints": [
            {
                "name": "benchmark:benign_single",
                "path": "dataset.json",
                "sha256": sha256_file(dataset_path),
            },
            {
                "name": "prompt:M3",
                "path": "missing-prompt.txt",
                "sha256": "old-runtime-hash",
            },
            {
                "name": "gnn_text_encoder_weights",
                "path": "missing-model.safetensors",
                "sha256": "old-runtime-hash",
            },
        ],
    }

    _validate_method_resource_fingerprints(config, snapshot)


def test_resource_validation_rejects_changed_benchmark(tmp_path) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text("[]", encoding="utf-8")
    dataset_config = {"datasets": {"benign_single": {"path": "dataset.json"}}}
    config = SimpleNamespace(project_root=tmp_path, datasets=dataset_config)
    snapshot = {
        "resolved_config": {"datasets": dataset_config},
        "resource_fingerprints": [
            {
                "name": "benchmark:benign_single",
                "path": "dataset.json",
                "sha256": "old-dataset-hash",
            }
        ],
    }

    try:
        _validate_method_resource_fingerprints(config, snapshot)
    except RuntimeError as exc:
        assert "changed:benchmark:benign_single" in str(exc)
    else:
        raise AssertionError("Changed benchmark data must be rejected")


def test_architecture_resource_validation_ignores_non_dataset_entries(tmp_path) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text("[]", encoding="utf-8")
    config = SimpleNamespace(
        project_root=tmp_path,
        datasets={
            "datasets": {
                "benign_single": {"path": "dataset.json", "turn_type": "single"},
            },
            "profile": "small_sample",
        },
    )
    manifest = {
        "config": {
            "datasets": {
                "benign_single": {"path": "dataset.json", "sha256": sha256_file(dataset_path)},
                "profile": "small_sample",
            }
        }
    }

    _validate_architecture_resource_snapshot(config, manifest)


def _row(run_id, sample, turn, decision, executed):
    return {
        "run_id": run_id,
        "setting_id": "full",
        "sample_id": sample,
        "turn_id": turn,
        "decision": decision,
        "executed": executed,
        "final_sql": "SELECT 1 AS x" if executed else None,
        "execution_result_json": [{"x": 1}] if executed else None,
        "latency_ms": 1,
        "llm_usage": {},
    }

