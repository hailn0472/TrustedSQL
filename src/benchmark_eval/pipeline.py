from __future__ import annotations

from pathlib import Path
from typing import Any

from trustedsql.datasets.loader import load_sequences
from trustedsql.db.executor import DatabaseExecutor
from trustedsql.sql.schema import SchemaGraph, load_schema_graph
from trustedsql.utils.hash import sha256_file
from trustedsql.utils.io import read_json, read_jsonl, write_csv, write_json, write_jsonl
from benchmark_eval.ex_metrics import ex_match, result_columns, soft_f1
from benchmark_eval.performance import performance_metrics
from benchmark_eval.security import multi_turn_security_metrics, single_turn_security_metrics
from benchmark_eval.utility import utility_metrics
from benchmark_eval.semantic_result import prefix_result_sufficient


def evaluate_run(config: Any, run_id: str) -> dict[str, Any]:
    run_dir = config.output_dir / run_id
    runtime_kind, snapshot, raw_runtime_rows = _load_runtime_bundle(run_dir)
    if not raw_runtime_rows:
        raise RuntimeError(f"Runtime output is missing or empty under: {run_dir}")
    if runtime_kind == "trustedsql":
        _validate_method_resource_fingerprints(config, snapshot)
        _validate_method_runtime_rows(raw_runtime_rows, snapshot)
    else:
        _validate_architecture_resource_snapshot(config, snapshot)
        _validate_architecture_runtime_rows(raw_runtime_rows, snapshot)
    sequences = load_sequences(config.datasets, config.project_root)
    metadata_map = _dataset_metadata_map(sequences)
    runtime_rows = [
        _attach_dataset_metadata(row, metadata_map) for row in raw_runtime_rows
    ]
    gt_map = {
        (sequence.source_dataset, sequence.sample_id, turn.turn_id): turn.sql_gt
        for sequence in sequences
        for turn in sequence.turns
    }
    evaluation_dir = run_dir / "evaluation"
    evidence_dir = evaluation_dir / "evidence"
    execution = config.raw.get("execution", {})
    db = DatabaseExecutor(
        config.raw.get("database", {}).get("url") or None,
        statement_timeout_ms=int(execution.get("statement_timeout_ms", 3000)),
        max_rows=int(execution.get("max_result_rows", 200)),
    )
    schema = load_schema_graph(config.ddl_path)
    utility_keys = {
        (str(row["source_dataset"]), str(row["sample_id"]), int(row["turn_id"]))
        for row in runtime_rows
        if _is_utility_turn(row)
    }
    ground_truth_results = {key: _execute_ground_truth(db, gt_map.get(key)) for key in sorted(utility_keys)}
    turn_evidence: list[dict[str, Any]] = []
    for row in runtime_rows:
        if not _is_utility_turn(row):
            continue
        key = (str(row["source_dataset"]), str(row["sample_id"]), int(row["turn_id"]))
        sql_gt = gt_map.get(key)
        gt = ground_truth_results[key]
        comparable = bool(
            sql_gt
            and gt.get("executed")
            and row.get("decision") == "ALLOW"
            and row.get("executed")
        )
        predicted = row.get("execution_result_json")
        expected = gt.get("rows") if gt.get("executed") else None
        gold_columns = list(gt.get("columns") or result_columns(expected))
        predicted_columns = list(row.get("execution_columns") or result_columns(predicted))
        predicted_column_source = "runtime_metadata" if row.get("execution_columns") else "result_keys"
        if not predicted_columns and comparable:
            predicted_columns = _sql_projection_columns(row.get("final_sql"))
            predicted_column_source = "sql_projection" if predicted_columns else "unavailable"
        turn_evidence.append(
            {
                "run_id": run_id,
                "setting_id": row["setting_id"],
                "source_dataset": row["source_dataset"],
                "sample_id": row["sample_id"],
                "turn_id": int(row["turn_id"]),
                "turn_label": row.get("turn_label"),
                "decision": row.get("decision"),
                "executed": bool(row.get("executed")),
                "sql_gt_available": bool(sql_gt),
                "gt_executed": bool(gt.get("executed")),
                "gt_error": gt.get("error"),
                "ex_match": ex_match(predicted, expected) if comparable else False,
                "soft_f1": soft_f1(predicted, expected) if comparable else 0.0,
                "prefix_result_sufficient": prefix_result_sufficient(
                    predicted,
                    expected,
                    predicted_sql=row.get("final_sql"),
                    gold_sql=sql_gt,
                    schema=schema,
                    predicted_columns=predicted_columns,
                    gold_columns=gold_columns,
                ) if comparable else False,
                "gold_columns": gold_columns,
                "predicted_columns": predicted_columns,
                "predicted_column_source": predicted_column_source,
            }
        )
    write_jsonl(evidence_dir / "turn_utility_evidence.jsonl", turn_evidence)

    settings = sorted({str(row["setting_id"]) for row in runtime_rows})
    utility: dict[str, Any] = {}
    single_security: dict[str, Any] = {}
    multi_security: dict[str, Any] = {}
    performance: dict[str, Any] = {}
    sequence_evidence: list[dict[str, Any]] = []
    for setting_id in settings:
        utility[setting_id] = utility_metrics(setting_id, turn_evidence)
        single_security[setting_id] = single_turn_security_metrics(setting_id, runtime_rows)
        multi = multi_turn_security_metrics(setting_id, runtime_rows, turn_evidence)
        sequence_evidence.extend(multi.pop("sequence_evidence"))
        multi_security[setting_id] = multi
        secure_ids = {
            str(row["sample_id"])
            for row in sequence_evidence
            if row["setting_id"] == setting_id and row["valid_secure_sequence"]
        }
        performance[setting_id] = performance_metrics(setting_id, runtime_rows, secure_ids)
    write_jsonl(evidence_dir / "sequence_security_evidence.jsonl", sequence_evidence)

    metrics_dir = evaluation_dir / "metrics"
    benchmark_metrics = {
        "run_id": run_id,
        "protocol": "benchmark-refusal-v1",
        "runtime_kind": runtime_kind,
        "utility": utility,
        "single_turn_security": single_security,
        "multi_turn_security": multi_security,
        "performance": performance,
    }
    write_json(metrics_dir / "benchmark_metrics.json", benchmark_metrics)
    write_csv(metrics_dir / "utility_metrics.csv", _flatten_mapping(utility))
    write_csv(metrics_dir / "single_turn_security_metrics.csv", _flatten_nested_mapping(single_security, "group"))
    write_csv(metrics_dir / "multi_turn_security_metrics.csv", _flatten_mapping(multi_security))
    write_csv(metrics_dir / "performance_metrics.csv", _performance_rows(performance))
    return benchmark_metrics


def _execute_ground_truth(db: DatabaseExecutor, sql: str | None) -> dict[str, Any]:
    if not sql:
        return {"executed": False, "rows": None, "error": "NO_SQL_GT"}
    result = db.execute_read_only(sql)
    return {
        "executed": result.executed,
        "rows": result.rows if result.executed else None,
        "columns": list(getattr(result, "columns", None) or []),
        "error": result.db_error,
    }


def _sql_projection_columns(sql: str | None) -> list[str]:
    if not sql:
        return []
    try:
        import sqlglot
        from sqlglot import expressions as exp

        expression = sqlglot.parse_one(sql, read="postgres")
        columns: list[str] = []
        for selection in expression.selects:
            if isinstance(selection, exp.Star) or (isinstance(selection, exp.Column) and selection.is_star):
                return []
            name = str(selection.alias_or_name or "").strip().strip('"').lower()
            if not name:
                return []
            if name not in columns:
                columns.append(name)
        return columns
    except Exception:  # noqa: BLE001
        return []


def _load_runtime_bundle(run_dir: Path) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    manifest_path = run_dir / "run_manifest.json"
    legacy_method_snapshot = run_dir / "run_config_snapshot.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        runtime_kind = str(manifest.get("runtime_kind") or "")
        if runtime_kind == "trustedsql" or (manifest.get("protocol") == "trustedsql-refusal-v1" and not manifest.get("architectures")):
            path = run_dir / "runtime" / "raw_turn_outputs.jsonl"
            return "trustedsql", manifest, read_jsonl(path)
        architecture_ids = list(manifest.get("architectures") or [])
        if not architecture_ids:
            architecture_ids = sorted(path.name for path in (run_dir / "architectures").glob("*") if path.is_dir())
        rows: list[dict[str, Any]] = []
        for architecture_id in architecture_ids:
            path = run_dir / "architectures" / architecture_id / "raw_turn_outputs.jsonl"
            for row in read_jsonl(path):
                normalized = dict(row)
                normalized.setdefault("architecture_id", architecture_id)
                normalized["setting_id"] = str(normalized.get("architecture_id") or architecture_id)
                rows.append(normalized)
        return "architecture_baseline", manifest, rows
    if legacy_method_snapshot.exists():
        snapshot = read_json(legacy_method_snapshot)
        path = run_dir / "runtime" / "raw_turn_outputs.jsonl"
        return "trustedsql", snapshot, read_jsonl(path)
    raise RuntimeError(
        f"Runtime manifest is required under {run_dir}"
    )


def _validate_method_resource_fingerprints(config: Any, snapshot: dict[str, Any]) -> None:
    snapshot_datasets = snapshot.get("resolved_config", {}).get("datasets")
    if snapshot_datasets != config.datasets:
        raise RuntimeError("Evaluation dataset configuration does not match runtime snapshot")
    mismatches: list[str] = []
    for resource in snapshot.get("resource_fingerprints", []):
        # Post-runtime evaluation reads benchmark records for ground-truth
        # execution. Runtime-only resources are preserved in the snapshot for
        # provenance, but do not participate in metric computation.
        if not str(resource.get("name") or "").startswith("benchmark:"):
            continue
        raw_path = Path(str(resource.get("path") or ""))
        path = raw_path if raw_path.is_absolute() else config.project_root / raw_path
        expected = str(resource.get("sha256") or "")
        if not path.exists():
            mismatches.append(f"missing:{resource.get('name')}:{raw_path}")
            continue
        if sha256_file(path) != expected:
            mismatches.append(f"changed:{resource.get('name')}:{raw_path}")
    if mismatches:
        raise RuntimeError(
            "Evaluation resources do not match runtime snapshot: " + ", ".join(mismatches)
        )


def _validate_method_runtime_rows(rows: list[dict[str, Any]], snapshot: dict[str, Any]) -> None:
    expected_rows = snapshot.get("benchmark_selection", {}).get("expected_turns", [])
    expected = {
        (str(row["setting_id"]), str(row["sample_id"]), int(row["turn_id"]))
        for row in expected_rows
    }
    actual_list = [
        (str(row["setting_id"]), str(row["sample_id"]), int(row["turn_id"]))
        for row in rows
    ]
    counts: dict[tuple[str, str, int], int] = {}
    for key in actual_list:
        counts[key] = counts.get(key, 0) + 1
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    actual = set(actual_list)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if duplicates or missing or extra:
        raise RuntimeError(
            "Runtime evidence failed completeness validation: "
            f"duplicates={duplicates[:10]}, missing={missing[:10]}, extra={extra[:10]}"
        )


def _validate_architecture_resource_snapshot(config: Any, manifest: dict[str, Any]) -> None:
    snapshot = manifest.get("config") or {}
    datasets = _dataset_config_entries(snapshot.get("datasets") or {})
    mismatches: list[str] = []
    for name, current in _dataset_config_entries(config.datasets).items():
        current_path = Path(current["path"])
        if not current_path.is_absolute():
            current_path = (config.project_root / current_path).resolve()
        recorded = datasets.get(name) or {}
        expected = recorded.get("sha256")
        if expected and (not current_path.exists() or sha256_file(current_path) != expected):
            mismatches.append(f"dataset_changed:{name}:{current_path}")
    if mismatches:
        raise RuntimeError("Evaluation resources do not match runtime manifest: " + ", ".join(mismatches))


def _dataset_config_entries(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    configs = value.get("datasets", value) if isinstance(value, dict) else {}
    return {
        str(name): cfg
        for name, cfg in configs.items()
        if isinstance(cfg, dict) and cfg.get("path")
    }


def _validate_architecture_runtime_rows(rows: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    expected_rows = manifest.get("benchmark_selection", {}).get("expected_turns") or []
    if not expected_rows:
        return
    expected = {
        (
            str(row["setting_id"]),
            str(row.get("source_dataset") or ""),
            str(row["sample_id"]),
            int(row["turn_id"]),
        )
        for row in expected_rows
    }
    actual_list = [
        (
            str(row["setting_id"]),
            str(row.get("source_dataset") or ""),
            str(row["sample_id"]),
            int(row["turn_id"]),
        )
        for row in rows
    ]
    counts: dict[tuple[str, str, str, int], int] = {}
    for key in actual_list:
        counts[key] = counts.get(key, 0) + 1
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    actual = set(actual_list)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if duplicates or missing or extra:
        raise RuntimeError(
            "Runtime evidence failed completeness validation: "
            f"duplicates={duplicates[:10]}, missing={missing[:10]}, extra={extra[:10]}"
        )


def _is_utility_turn(row: dict[str, Any]) -> bool:
    return row.get("source_dataset") in {"benign_single", "benign_multi", "malicious_multi"} and row.get("turn_label") == "BENIGN"


def _dataset_metadata_map(sequences: list[Any]) -> dict[tuple[str, ...], dict[str, Any]]:
    output: dict[tuple[str, ...], dict[str, Any]] = {}
    for sequence in sequences:
        for turn in sequence.turns:
            key = (str(sequence.source_dataset), str(sequence.sample_id), str(int(turn.turn_id)))
            if key in output:
                raise ValueError(f"Duplicate benchmark turn identity: {key}")
            metadata = {
                "source_dataset": sequence.source_dataset,
                "seq_label": sequence.seq_label,
                "turn_label": turn.turn_label,
                "attack_tags": sequence.attack_tags,
                "primary_type": sequence.primary_type,
            }
            output[key] = metadata
            fallback_key = (str(sequence.sample_id), str(int(turn.turn_id)))
            output.setdefault(fallback_key, metadata)
    return output


def _attach_dataset_metadata(
    row: dict[str, Any],
    metadata_map: dict[tuple[str, ...], dict[str, Any]],
) -> dict[str, Any]:
    if row.get("source_dataset"):
        key = (str(row["source_dataset"]), str(row["sample_id"]), str(int(row["turn_id"])))
    else:
        key = (str(row["sample_id"]), str(int(row["turn_id"])))
    metadata = metadata_map.get(key)
    if metadata is None:
        raise KeyError(f"Runtime row has no canonical benchmark metadata: {key}")
    return {**row, **metadata}


def _flatten_mapping(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"setting_id": setting, **_flatten(values)} for setting, values in payload.items()]


def _flatten_nested_mapping(payload: dict[str, Any], key_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for setting, groups in payload.items():
        for group, values in groups.items():
            rows.append({"setting_id": setting, key_name: group, **_flatten(values)})
    return rows


def _performance_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for setting, values in payload.items():
        rows.append({"setting_id": setting, "path": "benign_served", **values["benign_served_path"]})
        for attack_type, metrics in values["single_turn_blocked_path"].items():
            rows.append({"setting_id": setting, "path": f"blocked_{attack_type}", **metrics})
        rows.append({"setting_id": setting, "path": "multi_turn_secure_sequence", **values["multi_turn_secure_sequence_path"]})
    return rows


def _flatten(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, item in value.items():
        name = f"{prefix}_{key}" if prefix else key
        if isinstance(item, dict):
            output.update(_flatten(item, name))
        else:
            output[name] = item
    return output

