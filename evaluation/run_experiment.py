from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from trustedsql.utils.io import to_jsonable


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run TrustedSQL benchmark experiments.")
    parser.add_argument("--experiment", required=True, help="Path to experiment YAML.")
    parser.add_argument("--phase", choices=["all", "runtime", "evaluate"], default="all")
    parser.add_argument("--systems", nargs="*", default=None, help="Optional system id filter.")
    parser.add_argument("--providers", nargs="*", default=None, help="Optional provider id filter.")
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--max-workers", type=int, default=None)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--rerun-api-429", action="store_true")
    parser.add_argument("--experiment-run-id", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    experiment_path = _resolve(ROOT, args.experiment)
    experiment_doc = _read_yaml(experiment_path)
    experiment = dict(experiment_doc.get("experiment") or {})
    if not experiment.get("id"):
        raise ValueError(f"Experiment file must contain experiment.id: {experiment_path}")

    experiment_run_id = args.experiment_run_id or f"{experiment['id']}__{_timestamp()}"
    experiment_dir = ROOT / "outputs" / "experiments" / experiment_run_id
    experiment_dir.mkdir(parents=True, exist_ok=True)

    dataset_profile = _resolve(ROOT, experiment["dataset_profile"])
    all_systems = _load_systems(ROOT / "configs" / "systems")
    requested_systems = _filter_items(list(experiment.get("systems") or []), args.systems)
    requested_providers = _filter_items(list(experiment.get("providers") or []), args.providers)
    repetitions = max(1, int(experiment.get("repetitions", 1)))

    if args.phase == "evaluate" and (experiment_dir / "run_index.csv").exists():
        run_rows = _read_csv(experiment_dir / "run_index.csv")
        aggregate: dict[str, Any] = {
            "experiment_run_id": experiment_run_id,
            "experiment": experiment,
            "runs": {},
        }
        selected_rows: list[dict[str, Any]] = []
        for row in run_rows:
            if row.get("system_id") not in requested_systems or row.get("provider_id") not in requested_providers:
                selected_rows.append(row)
                continue
            config_dir = _resolve(ROOT, str(row["config_dir"]))
            _run_evaluation(run_id=str(row["run_id"]), config_dir=config_dir)
            row["status"] = "evaluated"
            selected_rows.append(row)
            metrics_path = _metrics_path(str(row["run_id"]), config_dir)
            aggregate["runs"][str(row["run_id"])] = {
                "system_id": row.get("system_id"),
                "runtime_kind": row.get("runtime_kind"),
                "provider_id": row.get("provider_id"),
                "metrics": _read_json(metrics_path) if metrics_path.exists() else None,
            }
        _write_json(experiment_dir / "experiment_manifest.json", {
            "experiment_run_id": experiment_run_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "experiment_file": _portable(experiment_path),
            "experiment": experiment,
            "dataset_profile": _portable(dataset_profile),
            "systems": requested_systems,
            "providers": requested_providers,
            "phase": args.phase,
            "max_samples": args.max_samples,
        })
        _write_csv(experiment_dir / "run_index.csv", selected_rows)
        _write_json(experiment_dir / "aggregate_metrics.json", aggregate)
        _write_csv(experiment_dir / "aggregate_metrics.csv", _aggregate_rows(aggregate))
        print(json.dumps(to_jsonable({"experiment_run_id": experiment_run_id, "runs": selected_rows}), ensure_ascii=False, indent=2))
        return 0

    run_rows: list[dict[str, Any]] = []
    aggregate: dict[str, Any] = {
        "experiment_run_id": experiment_run_id,
        "experiment": experiment,
        "runs": {},
    }
    for provider_id in requested_providers:
        provider_profile = _resolve(ROOT, f"configs/providers/{provider_id}.yaml")
        if not provider_profile.exists():
            raise FileNotFoundError(provider_profile)
        for system_id in requested_systems:
            if system_id not in all_systems:
                raise KeyError(f"Unknown system id {system_id}; available={sorted(all_systems)}")
            system = dict(all_systems[system_id])
            runtime_kind = str(system.get("runtime_kind") or "")
            if runtime_kind not in {"trustedsql", "architecture_baseline"}:
                raise ValueError(f"System {system_id} has unsupported runtime_kind={runtime_kind!r}")
            for index in range(1, repetitions + 1):
                suffix = f"__r{index:02d}" if repetitions > 1 else ""
                run_id = f"{experiment['id']}__{system_id}__{provider_id}{suffix}__{_timestamp()}"
                resolved_config = _materialize_run_config(
                    experiment_dir=experiment_dir,
                    run_id=run_id,
                    experiment_id=str(experiment["id"]),
                    system_id=system_id,
                    system=system,
                    provider_id=provider_id,
                    provider_profile=provider_profile,
                    dataset_profile=dataset_profile,
                    runtime_kind=runtime_kind,
                    max_samples=args.max_samples,
                )
                row = {
                    "experiment_run_id": experiment_run_id,
                    "experiment_id": experiment["id"],
                    "run_id": run_id,
                    "run_dir": _portable(_run_dir(run_id, resolved_config)),
                    "system_id": system_id,
                    "runtime_kind": runtime_kind,
                    "provider_id": provider_id,
                    "config_dir": _portable(resolved_config),
                    "status": "planned",
                }
                run_rows.append(row)
                if args.phase in {"all", "runtime"}:
                    _run_runtime(
                        runtime_kind=runtime_kind,
                        run_id=run_id,
                        system_id=system_id,
                        config_dir=resolved_config,
                        max_samples=args.max_samples,
                        max_workers=args.max_workers,
                        no_resume=args.no_resume,
                        rerun_api_429=args.rerun_api_429,
                    )
                    row["status"] = "runtime_complete"
                if args.phase in {"all", "evaluate"}:
                    _run_evaluation(run_id=run_id, config_dir=resolved_config)
                    row["status"] = "evaluated"
                    metrics_path = _metrics_path(run_id, resolved_config)
                    aggregate["runs"][run_id] = {
                        "system_id": system_id,
                        "runtime_kind": runtime_kind,
                        "provider_id": provider_id,
                        "metrics": _read_json(metrics_path) if metrics_path.exists() else None,
                    }

    _write_json(experiment_dir / "experiment_manifest.json", {
        "experiment_run_id": experiment_run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "experiment_file": _portable(experiment_path),
        "experiment": experiment,
        "dataset_profile": _portable(dataset_profile),
        "systems": requested_systems,
        "providers": requested_providers,
        "phase": args.phase,
        "max_samples": args.max_samples,
    })
    _write_csv(experiment_dir / "run_index.csv", run_rows)
    _write_json(experiment_dir / "aggregate_metrics.json", aggregate)
    _write_csv(experiment_dir / "aggregate_metrics.csv", _aggregate_rows(aggregate))
    print(json.dumps(to_jsonable({"experiment_run_id": experiment_run_id, "runs": run_rows}), ensure_ascii=False, indent=2))
    return 0


def _run_runtime(
    *,
    runtime_kind: str,
    run_id: str,
    system_id: str,
    config_dir: Path,
    max_samples: int | None,
    max_workers: int | None,
    no_resume: bool,
    rerun_api_429: bool,
) -> None:
    if runtime_kind == "trustedsql":
        from trustedsql.cli import main as trustedsql_main

        argv = [
            "--project-root", str(ROOT),
            "run",
            "--run-id", run_id,
            "--config-dir", str(config_dir),
            "--settings", system_id,
        ]
        if max_samples is not None:
            argv += ["--max-samples", str(max_samples)]
        if max_workers is not None:
            argv += ["--max-workers", str(max_workers)]
        if no_resume:
            argv.append("--no-resume")
        if rerun_api_429:
            argv.append("--rerun-api-429")
        trustedsql_main(argv)
        return
    from architecture_baselines.cli import main as architecture_main

    argv = [
        "--config-dir", str(config_dir),
        "--env-file", str(ROOT / ".env"),
        "--run-id", run_id,
        "--architecture", system_id,
        "--evaluation-phase", "runtime",
        "--skip-evaluation",
    ]
    if max_samples is not None:
        argv += ["--max-samples", str(max_samples)]
    if no_resume:
        argv.append("--no-resume")
    if rerun_api_429:
        argv.append("--rerun-api-429")
    exit_code = architecture_main(argv)
    if exit_code:
        raise RuntimeError(f"Architecture runtime failed with exit_code={exit_code}: {run_id}")


def _run_evaluation(*, run_id: str, config_dir: Path) -> None:
    from benchmark_eval.cli import main as benchmark_eval_main

    benchmark_eval_main([
        "--project-root", str(ROOT),
        "evaluate",
        "--run-id", run_id,
        "--config-dir", str(config_dir),
    ])


def _materialize_run_config(
    *,
    experiment_dir: Path,
    run_id: str,
    experiment_id: str,
    system_id: str,
    system: dict[str, Any],
    provider_id: str,
    provider_profile: Path,
    dataset_profile: Path,
    runtime_kind: str,
    max_samples: int | None,
) -> Path:
    config_dir = experiment_dir / "resolved_configs" / run_id / "configs"
    runtime = _read_yaml(ROOT / "configs" / "runtime" / "default.yaml")
    runtime.setdefault("run", {})["run_id"] = run_id
    runtime["experiment"] = {
        "experiment_id": experiment_id,
        "system_id": system_id,
        "runtime_kind": runtime_kind,
        "provider_id": provider_id,
        "dataset_profile": _portable(dataset_profile),
        "max_samples": max_samples,
    }
    _write_yaml(config_dir / "runtime" / "default.yaml", runtime)
    _write_yaml(config_dir / "datasets.yaml", _read_yaml(dataset_profile))
    _write_yaml(config_dir / "providers" / "modules.yaml", _read_yaml(provider_profile))
    if runtime_kind == "trustedsql":
        _write_yaml(config_dir / "method" / "method.yaml", {"settings": {system_id: _runtime_system(system)}})
    else:
        _write_yaml(config_dir / "architectures" / "architectures.yaml", {"architectures": {system_id: _runtime_system(system)}})
        # Kept for benchmark_eval.load_config, which uses the method config loader.
        _write_yaml(config_dir / "method" / "method.yaml", {"settings": {}})
    return config_dir


def _metrics_path(run_id: str, config_dir: Path) -> Path:
    return _run_dir(run_id, config_dir) / "evaluation" / "metrics" / "benchmark_metrics.json"


def _run_dir(run_id: str, config_dir: Path) -> Path:
    runtime = _read_yaml(config_dir / "runtime" / "default.yaml")
    output_dir_value = runtime.get("output_dir") or runtime.get("paths", {}).get("output_dir", "outputs/runs")
    output_dir = _resolve(ROOT, output_dir_value)
    return output_dir / run_id


def _runtime_system(system: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in system.items()
        if key in {"enabled", "label", "description", "modules"}
    }


def _load_systems(path: Path) -> dict[str, dict[str, Any]]:
    systems: dict[str, dict[str, Any]] = {}
    for file in sorted(path.glob("*.yaml")):
        doc = _read_yaml(file)
        for system_id, system in (doc.get("systems") or {}).items():
            if system_id in systems:
                raise ValueError(f"Duplicate system id {system_id} in {file}")
            systems[system_id] = dict(system)
    return systems


def _filter_items(defaults: list[str], override: list[str] | None) -> list[str]:
    if not override:
        return defaults
    output: list[str] = []
    for item in override:
        output.extend(part.strip() for part in item.split(",") if part.strip())
    return output


def _aggregate_rows(aggregate: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run_id, payload in aggregate.get("runs", {}).items():
        metrics = payload.get("metrics") or {}
        rows.append({
            "run_id": run_id,
            "system_id": payload.get("system_id"),
            "runtime_kind": payload.get("runtime_kind"),
            "provider_id": payload.get("provider_id"),
            "has_metrics": bool(metrics),
        })
    return rows


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return yaml.safe_load(handle) or {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=False)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_jsonable(data), ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _portable(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


if __name__ == "__main__":
    raise SystemExit(main())
