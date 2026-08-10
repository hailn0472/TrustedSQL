from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SETTING_LABELS = {
    "full_trustedsql": "Full TrustedSQL",
    "trustedsql_minus_m1": "Minus M1 Prompt Integrity Guard",
    "trustedsql_minus_m2": "Minus M2 Conversation-Risk Model",
    "trustedsql_minus_m3_m4_m5": "Minus M3-M4-M5 Authorization Block",
    "trustedsql_minus_m7": "Minus M7 SQL Conformance Validator",
}

SOURCE_LABELS = {
    "ST-BENIGN": "single-turn benign",
    "MT-BEN": "multi-turn benign",
    "ST-RBAC": "single-turn RBAC attack",
    "ST-PI": "single-turn prompt-injection attack",
    "MT-MAL": "multi-turn malicious sequence",
}

KEY_METRICS = [
    "Utility - ST-EX \u2191",
    "Utility - MT-IEX \u2191",
    "RBAC Single-Turn Security - ASR \u2193",
    "RBAC Single-Turn Security - Refusal Recall \u2191",
    "Prompt Injection Single-Turn Security - ASR \u2193",
    "Prompt Injection Single-Turn Security - Refusal Recall \u2191",
    "Multi-Turn Security - Prefix-RS \u2191",
    "Multi-Turn Security - Sequence ASR \u2193",
    "Multi-Turn Security - Sequence Refusal Recall \u2191",
    "Multi-Turn Security - Valid Secure Sequence Rate \u2191",
    "Performance: Benign Served Path - Count",
    "Performance: Blocked RBAC Single-Turn Path - Count",
    "Performance: Blocked PI Single-Turn Path - Count",
    "Performance: Multi-Turn Secure Sequence Path - Count",
]


@dataclass(frozen=True)
class RunBundle:
    experiment_run_id: str
    run_index: int
    setting_id: str
    run_id: str
    run_dir: Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze EX3 ablation raw runtime dataflow and aggregate deltas."
    )
    parser.add_argument("--ex3-dir", default="output/ex3")
    parser.add_argument("--aggregate-csv", default="output/result_all_3times.csv")
    parser.add_argument("--output-dir", default="output/ex3_analysis")
    parser.add_argument("--project-root", default=None)
    args = parser.parse_args()

    root = Path(args.project_root).resolve() if args.project_root else Path.cwd()
    ex3_dir = _resolve(root, args.ex3_dir)
    aggregate_csv = _resolve(root, args.aggregate_csv)
    output_dir = _resolve(root, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    bundles = discover_runs(ex3_dir)
    raw_rows = load_raw_turn_rows(bundles)
    module_rows = load_module_event_rows(bundles)
    utility_rows = load_utility_evidence_rows(bundles)
    sequence_rows = load_sequence_evidence_rows(bundles)
    aggregate_rows = load_aggregate_metric_rows(aggregate_csv)

    coverage = build_run_coverage(bundles, raw_rows, module_rows, utility_rows, sequence_rows)
    turn_counts = build_turn_decision_counts(raw_rows)
    blocked_counts = build_blocked_stage_counts(raw_rows)
    module_counts = build_module_event_counts(module_rows)
    module_reach_counts = build_module_reach_counts(module_rows)
    pipeline_path_counts = build_pipeline_path_counts(raw_rows)
    sample_matrix = build_sample_decision_matrix(raw_rows)
    migrations = build_pairwise_migrations(raw_rows)
    case_studies = build_case_studies(bundles)
    utility_summary = build_utility_summary(utility_rows)
    sequence_summary = build_sequence_summary(sequence_rows)
    aggregate_summary = build_aggregate_summary(aggregate_rows)

    write_csv(output_dir / "run_coverage.csv", coverage)
    write_csv(output_dir / "turn_decision_counts.csv", turn_counts)
    write_csv(output_dir / "blocked_stage_by_source.csv", blocked_counts)
    write_csv(output_dir / "module_event_decisions.csv", module_counts)
    write_csv(output_dir / "module_reach_counts.csv", module_reach_counts)
    write_csv(output_dir / "pipeline_path_counts.csv", pipeline_path_counts)
    write_csv(output_dir / "sample_decision_matrix.csv", sample_matrix)
    write_csv(output_dir / "pairwise_decision_migrations.csv", migrations)
    (output_dir / "case_studies.json").write_text(
        json.dumps(case_studies, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(output_dir / "utility_evidence_summary.csv", utility_summary)
    write_csv(output_dir / "sequence_security_summary.csv", sequence_summary)
    write_csv(output_dir / "aggregate_metric_summary.csv", aggregate_summary)

    write_svg_bar_chart(
        output_dir / "blocked_stage_by_ablation.svg",
        blocked_counts,
        group_key="setting_id",
        stack_key="blocked_at",
        value_key="turns",
        title="EX3 Runtime Blocking Stage Distribution",
    )
    write_svg_bar_chart(
        output_dir / "decision_by_ablation.svg",
        turn_counts,
        group_key="setting_id",
        stack_key="decision",
        value_key="turns",
        title="EX3 Turn Decision Distribution",
    )
    write_report(
        output_dir / "ex3_ablation_dataflow_report.md",
        coverage=coverage,
        turn_counts=turn_counts,
        blocked_counts=blocked_counts,
        aggregate_summary=aggregate_summary,
        migrations=migrations,
        utility_summary=utility_summary,
        sequence_summary=sequence_summary,
    )

    print(f"Wrote EX3 ablation analysis to {output_dir}")
    return 0


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def discover_runs(ex3_dir: Path) -> list[RunBundle]:
    bundles: list[RunBundle] = []
    for run_index, experiment_dir in enumerate(sorted(ex3_dir.glob("ex3_*")), start=1):
        if not experiment_dir.is_dir():
            continue
        run_index_path = experiment_dir / "run_index.csv"
        indexed: dict[str, str] = {}
        if run_index_path.exists():
            for row in read_csv(run_index_path):
                indexed[row["run_id"]] = row.get("system_id") or infer_setting_id(row["run_id"])
        for run_dir in sorted(experiment_dir.glob("ex3_ablation__*")):
            if not run_dir.is_dir() or run_dir.name.startswith("resolved_"):
                continue
            if not (run_dir / "runtime" / "raw_turn_outputs.jsonl").exists():
                continue
            run_id = run_dir.name
            setting_id = indexed.get(run_id) or infer_setting_id(run_id)
            bundles.append(
                RunBundle(
                    experiment_run_id=experiment_dir.name,
                    run_index=run_index,
                    setting_id=setting_id,
                    run_id=run_id,
                    run_dir=run_dir,
                )
            )
    return bundles


def infer_setting_id(run_id: str) -> str:
    match = re.match(r"ex3_ablation__(.+?)__gemini_25_flash__", run_id)
    return match.group(1) if match else run_id


def load_raw_turn_rows(bundles: list[RunBundle]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bundle in bundles:
        for row in iter_jsonl(bundle.run_dir / "runtime" / "raw_turn_outputs.jsonl"):
            source_group = source_group_for(row.get("sample_id"))
            rows.append(
                {
                    "experiment_run_id": bundle.experiment_run_id,
                    "run_index": bundle.run_index,
                    "setting_id": bundle.setting_id,
                    "setting_label": SETTING_LABELS.get(bundle.setting_id, bundle.setting_id),
                    "run_id": bundle.run_id,
                    "source_group": source_group,
                    "source_label": SOURCE_LABELS.get(source_group, source_group),
                    "sample_id": row.get("sample_id"),
                    "sequence_id": row.get("sequence_id"),
                    "turn_id": int(row.get("turn_id") or 0),
                    "decision": normalize_decision(row.get("decision")),
                    "blocked_at": row.get("blocked_at") or "NONE",
                    "executed": bool(row.get("executed")),
                    "latency_ms": float(row.get("latency_ms") or 0.0),
                    "input_tokens": int((row.get("llm_usage") or {}).get("input_tokens") or 0),
                    "output_tokens": int((row.get("llm_usage") or {}).get("output_tokens") or 0),
                    "has_final_sql": bool(row.get("final_sql")),
            "execution_column_count": len(row.get("execution_columns") or []),
            "module_trace_length": len(row.get("module_trace") or []),
            "trace_path": " -> ".join(
                f"{trace.get('module_id')}:{normalize_decision(trace.get('decision'))}"
                for trace in (row.get("module_trace") or [])
            ),
        }
            )
    return rows


def load_module_event_rows(bundles: list[RunBundle]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bundle in bundles:
        path = bundle.run_dir / "runtime" / "module_events.jsonl"
        if not path.exists():
            continue
        for row in iter_jsonl(path):
            out = row.get("output") or {}
            source_group = source_group_for(row.get("sample_id"))
            rows.append(
                {
                    "experiment_run_id": bundle.experiment_run_id,
                    "run_index": bundle.run_index,
                    "setting_id": bundle.setting_id,
                    "setting_label": SETTING_LABELS.get(bundle.setting_id, bundle.setting_id),
                    "run_id": bundle.run_id,
                    "source_group": source_group,
                    "sample_id": row.get("sample_id"),
                    "turn_id": int(row.get("turn_id") or 0),
                    "module_id": row.get("module_id") or "UNKNOWN",
                    "module_decision": normalize_decision(out.get("decision")),
                }
            )
    return rows


def load_utility_evidence_rows(bundles: list[RunBundle]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bundle in bundles:
        path = bundle.run_dir / "evaluation" / "evidence" / "turn_utility_evidence.jsonl"
        if not path.exists():
            continue
        for row in iter_jsonl(path):
            rows.append(
                {
                    "experiment_run_id": bundle.experiment_run_id,
                    "run_index": bundle.run_index,
                    "setting_id": bundle.setting_id,
                    "source_dataset": row.get("source_dataset"),
                    "source_group": source_group_for(row.get("sample_id")),
                    "sample_id": row.get("sample_id"),
                    "turn_id": int(row.get("turn_id") or 0),
                    "decision": normalize_decision(row.get("decision")),
                    "executed": bool(row.get("executed")),
                    "ex_match": bool(row.get("ex_match")),
                    "prefix_result_sufficient": bool(row.get("prefix_result_sufficient")),
                    "soft_f1": float(row.get("soft_f1") or 0.0),
                }
            )
    return rows


def load_sequence_evidence_rows(bundles: list[RunBundle]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bundle in bundles:
        path = bundle.run_dir / "evaluation" / "evidence" / "sequence_security_evidence.jsonl"
        if not path.exists():
            continue
        for row in iter_jsonl(path):
            rows.append(
                {
                    "experiment_run_id": bundle.experiment_run_id,
                    "run_index": bundle.run_index,
                    "setting_id": bundle.setting_id,
                    "sample_id": row.get("sample_id"),
                    "prefix_rs": bool(row.get("prefix_rs")),
                    "final_decision": normalize_decision(row.get("final_decision")),
                    "valid_secure_sequence": bool(row.get("valid_secure_sequence")),
                }
            )
    return rows


def load_aggregate_metric_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = read_csv(path)
    if not raw:
        return []
    columns = list(raw[0])
    experiment_col, config_col, metric_col = columns[:3]
    result_cols = columns[3:6]
    rows: list[dict[str, Any]] = []
    for row in raw:
        if row.get(experiment_col) != "EX3":
            continue
        setting_id = normalize_aggregate_configuration(row.get(config_col, ""))
        for run_number, result_col in enumerate(result_cols, start=1):
            value, numerator, denominator = parse_metric_value(row.get(result_col))
            rows.append(
                {
                    "setting_id": setting_id,
                    "configuration": row.get(config_col),
                    "metric": row.get(metric_col),
                    "run_number": run_number,
                    "value": value,
                    "numerator": numerator,
                    "denominator": denominator,
                    "raw": row.get(result_col),
                }
            )
    return rows


def build_run_coverage(
    bundles: list[RunBundle],
    raw_rows: list[dict[str, Any]],
    module_rows: list[dict[str, Any]],
    utility_rows: list[dict[str, Any]],
    sequence_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counts = {
        "raw_turns": Counter((r["experiment_run_id"], r["setting_id"]) for r in raw_rows),
        "module_events": Counter((r["experiment_run_id"], r["setting_id"]) for r in module_rows),
        "utility_evidence": Counter((r["experiment_run_id"], r["setting_id"]) for r in utility_rows),
        "sequence_evidence": Counter((r["experiment_run_id"], r["setting_id"]) for r in sequence_rows),
    }
    rows = []
    for bundle in bundles:
        key = (bundle.experiment_run_id, bundle.setting_id)
        rows.append(
            {
                "experiment_run_id": bundle.experiment_run_id,
                "run_index": bundle.run_index,
                "setting_id": bundle.setting_id,
                "run_id": bundle.run_id,
                "raw_turns": counts["raw_turns"][key],
                "module_events": counts["module_events"][key],
                "utility_evidence": counts["utility_evidence"][key],
                "sequence_evidence": counts["sequence_evidence"][key],
            }
        )
    return rows


def build_turn_decision_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = aggregate(rows, ["setting_id", "source_group", "decision"])
    totals = aggregate(rows, ["setting_id", "source_group"])
    total_map = {(r["setting_id"], r["source_group"]): r["turns"] for r in totals}
    for row in grouped:
        total = total_map[(row["setting_id"], row["source_group"])]
        row["share"] = pct(row["turns"], total)
    return sort_rows(grouped, ["setting_id", "source_group", "decision"])


def build_blocked_stage_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = aggregate(rows, ["setting_id", "source_group", "blocked_at"])
    totals = aggregate(rows, ["setting_id", "source_group"])
    total_map = {(r["setting_id"], r["source_group"]): r["turns"] for r in totals}
    for row in grouped:
        total = total_map[(row["setting_id"], row["source_group"])]
        row["share"] = pct(row["turns"], total)
    return sort_rows(grouped, ["setting_id", "source_group", "blocked_at"])


def build_module_event_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sort_rows(
        aggregate(rows, ["setting_id", "source_group", "module_id", "module_decision"], count_name="events"),
        ["setting_id", "source_group", "module_id", "module_decision"],
    )


def build_module_reach_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reached: set[tuple[str, str, str, str, str, int]] = set()
    for row in rows:
        if row["source_group"] not in {"ST-RBAC", "ST-PI", "MT-MAL"}:
            continue
        reached.add(
            (
                row["experiment_run_id"],
                row["setting_id"],
                row["source_group"],
                row["module_id"],
                str(row["sample_id"]),
                int(row["turn_id"]),
            )
        )
    counter: Counter[tuple[str, str, str]] = Counter(
        (experiment_run_id, setting_id, module_id)
        for experiment_run_id, setting_id, _source_group, module_id, _sample_id, _turn_id in reached
    )
    source_counter: Counter[tuple[str, str, str, str]] = Counter(
        (experiment_run_id, setting_id, source_group, module_id)
        for experiment_run_id, setting_id, source_group, module_id, _sample_id, _turn_id in reached
    )
    output = [
        {
            "experiment_run_id": experiment_run_id,
            "setting_id": setting_id,
            "source_group": "ATTACK_ALL",
            "module_id": module_id,
            "turns_reached": turns,
        }
        for (experiment_run_id, setting_id, module_id), turns in counter.items()
    ]
    output.extend(
        {
            "experiment_run_id": experiment_run_id,
            "setting_id": setting_id,
            "source_group": source_group,
            "module_id": module_id,
            "turns_reached": turns,
        }
        for (experiment_run_id, setting_id, source_group, module_id), turns in source_counter.items()
    )
    return sort_rows(output, ["experiment_run_id", "setting_id", "source_group", "module_id"])


def build_pipeline_path_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter: Counter[tuple[str, str, str, str, str]] = Counter()
    for row in rows:
        if row["source_group"] not in {"ST-RBAC", "ST-PI", "MT-MAL"}:
            continue
        counter[
            (
                row["setting_id"],
                row["source_group"],
                row["decision"],
                row["blocked_at"],
                row["trace_path"],
            )
        ] += 1
    output = [
        {
            "setting_id": setting_id,
            "source_group": source_group,
            "decision": decision,
            "blocked_at": blocked_at,
            "trace_path": trace_path,
            "turns": turns,
        }
        for (setting_id, source_group, decision, blocked_at, trace_path), turns in counter.items()
    ]
    return sorted(output, key=lambda row: (row["setting_id"], row["source_group"], -int(row["turns"])))


def build_sample_decision_matrix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        if row["source_group"] not in {"ST-RBAC", "ST-PI", "MT-MAL"}:
            continue
        key = (row["experiment_run_id"], str(row["sample_id"]), int(row["turn_id"]))
        by_key[key][row["setting_id"]] = row
    output: list[dict[str, Any]] = []
    settings = ["trustedsql_minus_m1", "trustedsql_minus_m2", "trustedsql_minus_m3_m4_m5", "trustedsql_minus_m7"]
    for (experiment_run_id, sample_id, turn_id), setting_rows in sorted(by_key.items()):
        source_group = source_group_for(sample_id)
        record: dict[str, Any] = {
            "experiment_run_id": experiment_run_id,
            "source_group": source_group,
            "sample_id": sample_id,
            "turn_id": turn_id,
        }
        decisions = []
        blocked = []
        for setting in settings:
            row = setting_rows.get(setting)
            record[f"{setting}_decision"] = row["decision"] if row else ""
            record[f"{setting}_blocked_at"] = row["blocked_at"] if row else ""
            record[f"{setting}_trace_path"] = row["trace_path"] if row else ""
            if row:
                decisions.append(row["decision"])
                blocked.append(row["blocked_at"])
        record["decision_changed"] = len(set(decisions)) > 1
        record["blocked_stage_changed"] = len(set(blocked)) > 1
        output.append(record)
    return output


def build_pairwise_migrations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_run: dict[str, dict[str, dict[tuple[str, int], dict[str, Any]]]] = defaultdict(lambda: defaultdict(dict))
    for row in rows:
        key = (str(row["sample_id"]), int(row["turn_id"]))
        by_run[row["experiment_run_id"]][row["setting_id"]][key] = row
    output: list[dict[str, Any]] = []
    for experiment_run_id, settings in by_run.items():
        setting_ids = sorted(settings)
        for left_index, left in enumerate(setting_ids):
            for right in setting_ids[left_index + 1 :]:
                common = set(settings[left]) & set(settings[right])
                counter: Counter[tuple[str, str, str, str]] = Counter()
                for key in common:
                    lrow = settings[left][key]
                    rrow = settings[right][key]
                    counter[
                        (
                            lrow["source_group"],
                            lrow["decision"],
                            rrow["decision"],
                            f"{lrow['blocked_at']} -> {rrow['blocked_at']}",
                        )
                    ] += 1
                for (source_group, left_decision, right_decision, blocked_transition), turns in counter.items():
                    output.append(
                        {
                            "experiment_run_id": experiment_run_id,
                            "left_setting_id": left,
                            "right_setting_id": right,
                            "source_group": source_group,
                            "left_decision": left_decision,
                            "right_decision": right_decision,
                            "blocked_transition": blocked_transition,
                            "turns": turns,
                        }
                    )
    return sort_rows(
        output,
        [
            "experiment_run_id",
            "left_setting_id",
            "right_setting_id",
            "source_group",
            "left_decision",
            "right_decision",
            "blocked_transition",
        ],
    )


def build_case_studies(bundles: list[RunBundle]) -> list[dict[str, Any]]:
    latest_by_setting = latest_raw_runs_by_setting(bundles)
    loaded = {setting: load_raw_map(bundle.run_dir / "runtime" / "raw_turn_outputs.jsonl") for setting, bundle in latest_by_setting.items()}
    specs = [
        {
            "case_id": "C1",
            "title": "Authorization block removal turns an RBAC denial into execution",
            "left_setting": "trustedsql_minus_m2",
            "right_setting": "trustedsql_minus_m3_m4_m5",
            "source_group": "ST-RBAC",
            "left_decision": "DENY",
            "right_decision": "ALLOW",
            "preferred_sample_id": "ST-RBAC-008",
            "preferred_turn_id": 1,
        },
        {
            "case_id": "C2",
            "title": "Row-scope proof removal lets a multi-turn external-cohort request execute",
            "left_setting": "trustedsql_minus_m2",
            "right_setting": "trustedsql_minus_m3_m4_m5",
            "source_group": "MT-MAL",
            "left_decision": "DENY",
            "right_decision": "ALLOW",
            "preferred_sample_id": "MT-MAL-005",
            "preferred_turn_id": 3,
        },
        {
            "case_id": "C3",
            "title": "Prompt-integrity guard catches explicit bypass language",
            "left_setting": "trustedsql_minus_m1",
            "right_setting": "trustedsql_minus_m2",
            "source_group": "ST-PI",
            "left_decision": "ALLOW",
            "right_decision": "DENY",
            "preferred_sample_id": "ST-PI-001",
            "preferred_turn_id": 1,
        },
    ]
    cases = []
    for spec in specs:
        left_rows = loaded.get(spec["left_setting"], {})
        right_rows = loaded.get(spec["right_setting"], {})
        key = (spec["preferred_sample_id"], spec["preferred_turn_id"])
        if key not in left_rows or key not in right_rows:
            key = find_matching_case(
                left_rows,
                right_rows,
                spec["source_group"],
                spec["left_decision"],
                spec["right_decision"],
            )
        if key is None:
            continue
        left = left_rows[key]
        right = right_rows[key]
        cases.append(
            {
                **spec,
                "sample_id": key[0],
                "turn_id": key[1],
                "nlq": left.get("nlq"),
                "left": summarize_runtime_row(left),
                "right": summarize_runtime_row(right),
                "interpretation": case_interpretation(spec["case_id"]),
            }
        )
    return cases


def latest_raw_runs_by_setting(bundles: list[RunBundle]) -> dict[str, RunBundle]:
    latest: dict[str, RunBundle] = {}
    for bundle in bundles:
        if bundle.setting_id not in latest or bundle.experiment_run_id > latest[bundle.setting_id].experiment_run_id:
            latest[bundle.setting_id] = bundle
    return latest


def load_raw_map(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    return {(str(row.get("sample_id")), int(row.get("turn_id") or 0)): row for row in iter_jsonl(path)}


def find_matching_case(
    left_rows: dict[tuple[str, int], dict[str, Any]],
    right_rows: dict[tuple[str, int], dict[str, Any]],
    source_group: str,
    left_decision: str,
    right_decision: str,
) -> tuple[str, int] | None:
    for key, left in sorted(left_rows.items()):
        right = right_rows.get(key)
        if (
            right
            and str(key[0]).startswith(source_group)
            and normalize_decision(left.get("decision")) == left_decision
            and normalize_decision(right.get("decision")) == right_decision
        ):
            return key
    return None


def summarize_runtime_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "setting_id": row.get("setting_id"),
        "decision": normalize_decision(row.get("decision")),
        "blocked_at": row.get("blocked_at") or "NONE",
        "executed": bool(row.get("executed")),
        "trace_path": " -> ".join(
            f"{trace.get('module_id')}:{normalize_decision(trace.get('decision'))}"
            for trace in (row.get("module_trace") or [])
        ),
        "final_sql": compact(row.get("final_sql"), 600),
        "execution_columns": row.get("execution_columns") or [],
        "row_count": extract_row_count(row),
        "module_summaries": [summarize_trace(trace) for trace in (row.get("module_trace") or [])],
    }


def summarize_trace(trace: dict[str, Any]) -> dict[str, Any]:
    module_id = trace.get("module_id")
    artifact = trace.get("artifact") or {}
    audit = trace.get("audit") or {}
    summary: dict[str, Any] = {
        "module_id": module_id,
        "stage": trace.get("stage"),
        "decision": normalize_decision(trace.get("decision")),
    }
    if module_id == "M1":
        summary.update(
            {
                "heuristic_hits": artifact.get("heuristic_hits") or [],
                "llm_verdict": artifact.get("llm_verdict"),
                "llm_reason": compact(artifact.get("llm_reason"), 260),
            }
        )
    elif module_id == "M2":
        intent = artifact.get("intent_resolution") or {}
        hint = artifact.get("m2_downstream_hint") or {}
        summary.update(
            {
                "primary_intent": intent.get("primary_intent") or audit.get("m2_primary_intent"),
                "scope": intent.get("scope") or audit.get("m2_scope"),
                "target_relation": intent.get("target_relation") or audit.get("m2_target_relation"),
                "security_transition": intent.get("security_transition") or hint.get("security_transition"),
                "security_signals": hint.get("security_signals") or audit.get("m2_security_signals") or [],
            }
        )
    elif module_id == "M3":
        plan = artifact.get("access_plan") or {}
        summary.update(
            {
                "intent": plan.get("intent"),
                "policy_refs": plan.get("policy_refs") or [],
                "requested_resources": summarize_resources(plan.get("requested_resources") or []),
                "scope_type": plan.get("scope_type"),
                "target_resource_table": plan.get("target_resource_table"),
            }
        )
    elif module_id == "M4":
        summary.update(
            {
                "reason_code": audit.get("reason_code"),
                "violations": artifact.get("violations") or [],
                "violations_count": audit.get("violations_count"),
            }
        )
    elif module_id == "M5":
        summary.update({"reason_code": audit.get("reason_code"), "proof_status": audit.get("proof_status")})
    elif module_id == "M6":
        summary.update({"raw_sql": compact(artifact.get("raw_sql"), 420)})
    elif module_id == "M7":
        summary.update({"final_sql": compact(artifact.get("final_sql"), 420), "reason_code": audit.get("reason_code")})
    elif module_id == "X1":
        summary.update({"row_count": artifact.get("row_count"), "error": artifact.get("error")})
    return summary


def summarize_resources(resources: list[dict[str, Any]]) -> list[str]:
    items = []
    for resource in resources:
        columns = resource.get("columns") or []
        suffix = f"({', '.join(columns)})" if columns else ""
        items.append(f"{resource.get('table')}{suffix}")
    return items


def extract_row_count(row: dict[str, Any]) -> int | None:
    for trace in row.get("module_trace") or []:
        if trace.get("module_id") == "X1":
            return (trace.get("artifact") or {}).get("row_count")
    result = row.get("execution_result_json")
    return len(result) if isinstance(result, list) else None


def compact(value: Any, limit: int = 240) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def case_interpretation(case_id: str) -> str:
    if case_id == "C1":
        return "With M3/M4/M5 present, the planner exposes unauthorized role-management resources and M4 blocks them. When the authorization block is removed, the same request reaches SQL generation and execution."
    if case_id == "C2":
        return "The request appears structurally valid, but row-scope proof fails when M5 is present. Removing M3/M4/M5 skips the proof boundary, so an external-cohort roster query executes."
    if case_id == "C3":
        return "The prompt explicitly asks to bypass access rules. Without M1 the request is treated as a catalog lookup and executes; with M1 active, the prompt-integrity classifier stops it immediately."
    return ""


def build_utility_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["setting_id"], row["source_dataset"])].append(row)
    output = []
    for (setting_id, source_dataset), group in grouped.items():
        total = len(group)
        output.append(
            {
                "setting_id": setting_id,
                "source_dataset": source_dataset,
                "turns": total,
                "allow": sum(1 for r in group if r["decision"] == "ALLOW"),
                "executed": sum(1 for r in group if r["executed"]),
                "ex_match": sum(1 for r in group if r["ex_match"]),
                "prefix_result_sufficient": sum(1 for r in group if r["prefix_result_sufficient"]),
                "mean_soft_f1": round(sum(r["soft_f1"] for r in group) / total, 4) if total else 0.0,
                "ex_match_rate": pct(sum(1 for r in group if r["ex_match"]), total),
                "prefix_result_sufficient_rate": pct(
                    sum(1 for r in group if r["prefix_result_sufficient"]), total
                ),
            }
        )
    return sort_rows(output, ["setting_id", "source_dataset"])


def build_sequence_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["setting_id"]].append(row)
    output = []
    for setting_id, group in grouped.items():
        total = len(group)
        output.append(
            {
                "setting_id": setting_id,
                "sequences": total,
                "prefix_rs": sum(1 for r in group if r["prefix_rs"]),
                "final_allow": sum(1 for r in group if r["final_decision"] == "ALLOW"),
                "final_deny": sum(1 for r in group if r["final_decision"] == "DENY"),
                "valid_secure_sequence": sum(1 for r in group if r["valid_secure_sequence"]),
                "prefix_rs_rate": pct(sum(1 for r in group if r["prefix_rs"]), total),
                "sequence_asr": pct(sum(1 for r in group if r["final_decision"] == "ALLOW"), total),
                "valid_secure_sequence_rate": pct(
                    sum(1 for r in group if r["valid_secure_sequence"]), total
                ),
            }
        )
    return sort_rows(output, ["setting_id"])


def build_aggregate_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["metric"] in KEY_METRICS and row["value"] is not None:
            grouped[(row["setting_id"], row["metric"])].append(row)
    means: dict[tuple[str, str], dict[str, Any]] = {}
    for key, group in grouped.items():
        values = [float(r["value"]) for r in group]
        numerators = [r["numerator"] for r in group if r["numerator"] is not None]
        denominators = [r["denominator"] for r in group if r["denominator"] is not None]
        means[key] = {
            "mean_value": round(sum(values) / len(values), 4),
            "mean_numerator": round(sum(numerators) / len(numerators), 4) if numerators else "",
            "mean_denominator": round(sum(denominators) / len(denominators), 4) if denominators else "",
        }
    output = []
    for (setting_id, metric), data in sorted(means.items()):
        base = means.get(("full_trustedsql", metric), {}).get("mean_value")
        delta = data["mean_value"] - base if base is not None and setting_id != "full_trustedsql" else 0.0
        output.append(
            {
                "setting_id": setting_id,
                "metric": metric,
                **data,
                "delta_vs_full": round(delta, 4),
            }
        )
    return output


def aggregate(
    rows: list[dict[str, Any]],
    keys: list[str],
    *,
    count_name: str = "turns",
) -> list[dict[str, Any]]:
    counter: Counter[tuple[Any, ...]] = Counter(tuple(row.get(key) for key in keys) for row in rows)
    return [{**dict(zip(keys, key_tuple)), count_name: count} for key_tuple, count in counter.items()]


def normalize_decision(value: Any) -> str:
    text = str(value or "UNKNOWN").upper()
    return text if text in {"ALLOW", "DENY", "ERROR"} else text


def source_group_for(sample_id: Any) -> str:
    text = str(sample_id or "")
    parts = text.split("-")
    if len(parts) >= 2:
        return "-".join(parts[:2])
    return text or "UNKNOWN"


def pct(part: int | float, total: int | float) -> float:
    return round((float(part) / float(total) * 100.0), 4) if total else 0.0


def normalize_aggregate_configuration(value: str) -> str:
    text = value.strip()
    if text.startswith("full_trustedsql"):
        return "full_trustedsql"
    if text.startswith("trustedsql_minus_m1"):
        return "trustedsql_minus_m1"
    if text.startswith("trustedsql_minus_m2"):
        return "trustedsql_minus_m2"
    if text.startswith("trustedsql_minus_m3_m4_m5"):
        return "trustedsql_minus_m3_m4_m5"
    if text.startswith("trustedsql_minus_m7"):
        return "trustedsql_minus_m7"
    return text


def parse_metric_value(value: Any) -> tuple[float | None, int | None, int | None]:
    text = str(value or "")
    count_match = re.search(r"(\d+)\s*/\s*(\d+)\s*=\s*(-?\d+(?:\.\d+)?)%", text)
    if count_match:
        return float(count_match.group(3)), int(count_match.group(1)), int(count_match.group(2))
    pct_match = re.search(r"(-?\d+(?:\.\d+)?)%", text)
    if pct_match:
        return float(pct_match.group(1)), None, None
    number_match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?", text)
    if number_match:
        return float(number_match.group(0).replace(",", "")), None, None
    return None, None, None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def iter_jsonl(path: Path):
    with path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row}) if rows else ["empty"]
    preferred = [
        "experiment_run_id",
        "run_index",
        "setting_id",
        "metric",
        "source_group",
        "source_dataset",
        "decision",
        "blocked_at",
        "module_id",
    ]
    fieldnames = [key for key in preferred if key in fieldnames] + [
        key for key in fieldnames if key not in preferred
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def sort_rows(rows: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: tuple(str(row.get(key, "")) for key in keys))


def write_svg_bar_chart(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    group_key: str,
    stack_key: str,
    value_key: str,
    title: str,
) -> None:
    totals: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        if row.get("source_group") not in {None, "ST-RBAC", "ST-PI", "MT-MAL"}:
            continue
        totals[str(row[group_key])][str(row[stack_key])] += int(row[value_key])
    groups = sorted(totals)
    stacks = sorted({stack for counter in totals.values() for stack in counter})
    colors = {
        "ALLOW": "#2f6f4e",
        "DENY": "#b33a3a",
        "ERROR": "#7a6f2b",
        "NONE": "#2f6f4e",
        "M1": "#c6542d",
        "M2": "#9270b8",
        "M3": "#8a7a2b",
        "M4": "#c48a2c",
        "M5": "#b33a3a",
        "M6": "#4d8a9e",
        "M7": "#6b7c93",
        "X1": "#7a6f2b",
    }
    width = 980
    height = 120 + 54 * max(len(groups), 1)
    label_width = 260
    bar_width = 560
    max_total = max((sum(counter.values()) for counter in totals.values()), default=1)
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="24" y="34" font-family="Arial" font-size="20" font-weight="700">{escape_xml(title)}</text>',
    ]
    y = 70
    for group in groups:
        label = SETTING_LABELS.get(group, group)
        lines.append(
            f'<text x="24" y="{y + 18}" font-family="Arial" font-size="13">{escape_xml(label)}</text>'
        )
        x = label_width
        total = sum(totals[group].values()) or 1
        for stack in stacks:
            value = totals[group][stack]
            if not value:
                continue
            segment = bar_width * value / max_total
            color = colors.get(stack, "#999999")
            lines.append(
                f'<rect x="{x:.2f}" y="{y}" width="{segment:.2f}" height="24" fill="{color}"/>'
            )
            if segment > 34:
                lines.append(
                    f'<text x="{x + 4:.2f}" y="{y + 17}" font-family="Arial" font-size="11" fill="#ffffff">{escape_xml(stack)} {value}</text>'
                )
            x += segment
        lines.append(
            f'<text x="{label_width + bar_width + 12}" y="{y + 17}" font-family="Arial" font-size="12">{total}</text>'
        )
        y += 54
    legend_x = 24
    legend_y = height - 28
    for stack in stacks:
        color = colors.get(stack, "#999999")
        lines.append(
            f'<rect x="{legend_x}" y="{legend_y - 10}" width="10" height="10" fill="{color}"/>'
        )
        lines.append(
            f'<text x="{legend_x + 14}" y="{legend_y}" font-family="Arial" font-size="11">{escape_xml(stack)}</text>'
        )
        legend_x += 78
    lines.append("</svg>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def escape_xml(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def write_report(
    path: Path,
    *,
    coverage: list[dict[str, Any]],
    turn_counts: list[dict[str, Any]],
    blocked_counts: list[dict[str, Any]],
    aggregate_summary: list[dict[str, Any]],
    migrations: list[dict[str, Any]],
    utility_summary: list[dict[str, Any]],
    sequence_summary: list[dict[str, Any]],
) -> None:
    settings_with_raw = sorted({row["setting_id"] for row in coverage})
    missing_full_raw = "full_trustedsql" not in settings_with_raw
    lines = [
        "# EX3 Ablation Dataflow Analysis",
        "",
        "## What This Adds",
        "",
        "This analysis complements aggregate EX3 metrics with runtime dataflow evidence. It separates two evidence layers: aggregate full-vs-ablation deltas from `result_all_3times.csv`, and raw per-turn/module path redistribution from `output/ex3` runtime traces.",
        "",
        "## Coverage",
        "",
        f"- Raw settings found: {', '.join(settings_with_raw) if settings_with_raw else 'none'}.",
        f"- Full TrustedSQL raw traces present: {'no' if missing_full_raw else 'yes'}.",
        "- If Full TrustedSQL raw traces are added later, `pairwise_decision_migrations.csv` will include direct full-vs-ablation turn migrations.",
        "",
        "## Main Aggregate Deltas vs Full TrustedSQL",
        "",
    ]
    for setting_id in [
        "trustedsql_minus_m1",
        "trustedsql_minus_m2",
        "trustedsql_minus_m3_m4_m5",
        "trustedsql_minus_m7",
    ]:
        lines.append(f"### {SETTING_LABELS.get(setting_id, setting_id)}")
        for metric in KEY_METRICS[:10]:
            row = find_row(aggregate_summary, setting_id=setting_id, metric=metric)
            if row:
                lines.append(
                    f"- {metric}: {row['mean_value']:.2f} ({row['delta_vs_full']:+.2f} vs full)"
                )
        lines.append("")
    lines.extend(
        [
            "## Raw Runtime Path Findings",
            "",
            "### Turn Decisions",
            "",
        ]
    )
    for setting_id in settings_with_raw:
        subset = [r for r in turn_counts if r["setting_id"] == setting_id and r["source_group"] in {"ST-RBAC", "ST-PI", "MT-MAL"}]
        totals = Counter()
        for row in subset:
            totals[row["decision"]] += int(row["turns"])
        if totals:
            lines.append(
                f"- {SETTING_LABELS.get(setting_id, setting_id)}: "
                + ", ".join(f"{decision}={count}" for decision, count in sorted(totals.items()))
            )
    lines.extend(["", "### Blocking Stages", ""])
    for setting_id in settings_with_raw:
        subset = [
            r
            for r in blocked_counts
            if r["setting_id"] == setting_id
            and r["source_group"] in {"ST-RBAC", "ST-PI", "MT-MAL"}
            and r["blocked_at"] != "NONE"
        ]
        top = sorted(subset, key=lambda row: int(row["turns"]), reverse=True)[:5]
        if top:
            lines.append(f"- {SETTING_LABELS.get(setting_id, setting_id)}:")
            for row in top:
                lines.append(
                    f"  - {row['source_group']} blocked at {row['blocked_at']}: {row['turns']} turns ({row['share']:.2f}% within source group)"
                )
    lines.extend(
        [
            "",
            "## Interpretation For Results Section",
            "",
            "- Removing M1 mainly opens the prompt-injection path: aggregate PI ASR rises sharply, while raw traces show later modules absorbing part of the blocked traffic.",
            "- Removing M2 increases benign utility and reduces some over-refusal, but the sequence-level evidence shows weaker final-turn refusal and lower valid secure sequence rate.",
            "- Removing M3-M4-M5 changes the dataflow most visibly: many RBAC and multi-turn requests no longer stop at deterministic authorization gates and continue toward SQL generation/execution. This explains the large RBAC ASR jump even when utility rises.",
            "- Removing M7 has the smallest security effect in EX3, supporting the interpretation that SQL conformance validation is a final backstop rather than the primary RBAC or prompt-integrity barrier.",
            "",
            "## Generated Artifacts",
            "",
            "- `aggregate_metric_summary.csv`: key EX3 aggregate metrics and deltas against Full TrustedSQL.",
            "- `turn_decision_counts.csv`: ALLOW/DENY/ERROR counts by ablation and scenario family.",
            "- `blocked_stage_by_source.csv`: where requests stop in the pipeline.",
            "- `module_event_decisions.csv`: module-level event counts.",
            "- `pairwise_decision_migrations.csv`: direct per-turn migrations among raw settings available in the same experiment timestamp.",
            "- `sample_decision_matrix.csv`: side-by-side decision, block stage, and trace path for each raw ablation turn.",
            "- `pipeline_path_counts.csv` and `module_reach_counts.csv`: framework-level path and module reach distributions.",
            "- `case_studies.json`: representative per-request module-artifact traces for paper-ready examples.",
            "- `utility_evidence_summary.csv` and `sequence_security_summary.csv`: evaluation evidence summaries.",
            "- `decision_by_ablation.svg` and `blocked_stage_by_ablation.svg`: lightweight charts for the report or paper drafting.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def find_row(rows: list[dict[str, Any]], **criteria: Any) -> dict[str, Any] | None:
    for row in rows:
        if all(row.get(key) == value for key, value in criteria.items()):
            return row
    return None


if __name__ == "__main__":
    raise SystemExit(main())
