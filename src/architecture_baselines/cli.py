from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from architecture_baselines.config import PROJECT_ROOT, load_config
from architecture_baselines.datasets import load_sequences
from architecture_baselines.db import DatabaseExecutor
from architecture_baselines.llm import create_llm_client
from architecture_baselines.policy import PolicyIndex
from architecture_baselines.reporting import RunWriter
from architecture_baselines.runtime import ArchitectureRunner, Checkpoint, ModuleRegistry
from architecture_baselines.sql import load_schema_index
from benchmark_eval import evaluate_run


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run lean modular Text-to-SQL security architecture evaluation.")
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "configs")
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--datasets-file", type=Path, default=None)
    parser.add_argument("--modules-file", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--architecture", action="append", default=None, help="Architecture id to run. Can be passed multiple times or as comma-separated values.")
    parser.add_argument("--max-samples", type=int, default=None, help="Limit samples per source dataset.")
    parser.add_argument("--skip-runtime", action="store_true", help="Reuse existing raw_turn_outputs.jsonl and run only post-run evaluation.")
    parser.add_argument("--skip-evaluation", action="store_true", help="Run runtime only and do not build evaluator/review artifacts.")
    parser.add_argument(
        "--evaluation-phase",
        choices=["all", "runtime", "evaluate", "auto", "turn-review", "final"],
        default="all",
        help="Evaluation phase. New flow uses runtime/evaluate; auto/turn-review/final are accepted as aliases for evaluate.",
    )
    parser.add_argument("--no-resume", action="store_true", help="Do not resume from checkpoints.")
    parser.add_argument("--rerun-api-429", action="store_true", help="When resuming, remove turns with API 429 errors plus downstream turns in the same sequence and regenerate them in-place.")
    return parser.parse_args(argv)


def _architecture_ids(raw_architectures: dict[str, Any], requested: list[str] | None) -> list[str]:
    if requested:
        expanded: list[str] = []
        for item in requested:
            expanded.extend(part.strip() for part in item.split(",") if part.strip())
        unknown = [arch for arch in expanded if arch not in raw_architectures]
        if unknown:
            raise ValueError(f"Unknown architecture id(s): {', '.join(unknown)}")
        return expanded
    return [
        arch_id
        for arch_id, cfg in raw_architectures.items()
        if cfg.get("enabled", True)
    ]


def _limit_per_dataset(sequences: list[Any], max_samples: int | None) -> list[Any]:
    if max_samples is None:
        return sequences
    counts: dict[str, int] = {}
    limited = []
    for sequence in sequences:
        current = counts.get(sequence.source_dataset, 0)
        if current >= max_samples:
            continue
        counts[sequence.source_dataset] = current + 1
        limited.append(sequence)
    return limited


def _build_executor(config: Any) -> DatabaseExecutor:
    execution = config.raw.get("execution", {})
    return DatabaseExecutor(
        url=config.database_url,
        statement_timeout_ms=int(execution.get("statement_timeout_ms", 3000)),
        max_result_rows=int(execution.get("max_result_rows", 200)),
        connect_timeout_s=int(execution.get("connect_timeout_s", 10)),
        enforce_select_assertion=bool(execution.get("enforce_select_assertion", True)),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_snapshot(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size if path.exists() else None,
    }


def _config_snapshot(config: Any, selected_architectures: list[str]) -> dict[str, Any]:
    dataset_snapshot: dict[str, Any] = {}
    for name, ds_cfg in config.datasets.items():
        path = Path(ds_cfg["path"])
        if not path.is_absolute():
            path = (config.project_root / path).resolve()
        dataset_snapshot[name] = {**dict(ds_cfg), **_file_snapshot(path)}

    llm = dict(config.llm)
    llm_snapshot = {
        "provider": llm.get("provider", "vertex"),
        "project_id": llm.get("project_id"),
        "location": llm.get("location"),
        "model": llm.get("model"),
        "temperature": llm.get("temperature"),
        "top_p": llm.get("top_p"),
        "max_output_tokens": llm.get("max_output_tokens"),
        "max_retries": llm.get("max_retries"),
        "thinking_config": llm.get("thinking_config"),
        "api_url_configured": bool(llm.get("api_url") or llm.get("api_url_env")),
        "api_key_configured": bool(llm.get("api_key") or llm.get("api_key_env") or llm.get("api_key_env_by_model")),
        "google_application_credentials_configured": bool(llm.get("google_application_credentials")),
    }
    execution = dict(config.raw.get("execution", {}))
    runtime = dict(config.raw.get("runtime", {}))
    return {
        "paths": {
            "policy": _file_snapshot(config.policy_path),
            "role_access_matrix": _file_snapshot(config.role_access_matrix_path),
            "ddl": _file_snapshot(config.ddl_path),
            "output_dir": str(config.output_dir),
        },
        "datasets": dataset_snapshot,
        "architectures": {
            architecture_id: config.architectures[architecture_id]
            for architecture_id in selected_architectures
        },
        "modules": config.modules,
        "llm": llm_snapshot,
        "database": {
            "url_configured": bool(config.database_url),
            "url_hash": _sha256_text(config.database_url) if config.database_url else None,
        },
        "execution": execution,
        "runtime": runtime,
    }


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _has_runtime_artifacts(run_dir: Path) -> bool:
    arch_dir = run_dir / "architectures"
    if not arch_dir.exists():
        return False
    return any(arch_dir.glob("*/raw_turn_outputs.jsonl")) or any(arch_dir.glob("*/checkpoint.json"))


def _checkpoint_fingerprints(run_dir: Path) -> list[str]:
    fingerprints: list[str] = []
    for path in sorted((run_dir / "architectures").glob("*/checkpoint.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Existing checkpoint is not valid JSON: {path}") from exc
        fingerprint = data.get("run_fingerprint")
        if fingerprint:
            fingerprints.append(str(fingerprint))
    return fingerprints


def _run_reuse_status(run_dir: Path, run_fingerprint: str) -> dict[str, Any]:
    status = {
        "has_runtime_artifacts": _has_runtime_artifacts(run_dir),
        "fingerprint_match": True,
        "existing_manifest_fingerprint": None,
        "existing_manifest_summary": None,
        "checkpoint_fingerprints": [],
        "message": "",
    }
    if not _has_runtime_artifacts(run_dir):
        return status
    manifest_path = run_dir / "run_manifest.json"
    existing_fingerprint = None
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Existing manifest is not valid JSON: {manifest_path}") from exc
        existing_fingerprint = existing.get("run_fingerprint")
        status["existing_manifest_fingerprint"] = existing_fingerprint
        status["existing_manifest_summary"] = {
            "run_id": existing.get("run_id"),
            "created_at": existing.get("created_at"),
            "run_fingerprint": existing.get("run_fingerprint"),
            "architectures": existing.get("architectures"),
            "dataset_counts": existing.get("dataset_counts"),
            "paths": existing.get("paths"),
            "config_paths": existing.get("config", {}).get("paths", {}),
            "config_datasets": existing.get("config", {}).get("datasets", {}),
        }
        if existing_fingerprint == run_fingerprint:
            return status
    checkpoint_fingerprints = _checkpoint_fingerprints(run_dir)
    status["checkpoint_fingerprints"] = sorted(set(checkpoint_fingerprints))
    if checkpoint_fingerprints and set(checkpoint_fingerprints) == {run_fingerprint}:
        return status
    if not manifest_path.exists():
        raise ValueError(f"Existing runtime artifacts in {run_dir} have no manifest. Use a new run_id or clear stale outputs.")
    status["fingerprint_match"] = False
    status["message"] = (
        f"Run fingerprint mismatch for {run_dir}. Existing runtime artifacts were produced with a different "
        "config snapshot. Runtime resume is blocked; skip-runtime post-evaluation is allowed and logs this warning."
    )
    return status


def _read_runtime_manifest(run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "run_manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"Post-run evaluation requires the immutable runtime manifest: {manifest_path}")
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Runtime manifest is not valid JSON: {manifest_path}") from exc


def _append_evaluation_invocation(run_dir: Path, invocation: dict[str, Any]) -> None:
    """Record post-run provenance without mutating the runtime manifest."""
    path = run_dir / "evaluation_invocations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(invocation, ensure_ascii=False, default=str) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        eval_phase = "evaluate" if args.evaluation_phase in {"auto", "turn-review", "final"} else args.evaluation_phase
        cli_overrides: dict[str, Any] = {}
        if args.run_id:
            cli_overrides.setdefault("run", {})["run_id"] = args.run_id
        config = load_config(
            args.config_dir,
            args.env_file,
            args.datasets_file,
            args.modules_file,
            cli_overrides,
        )
        needs_runtime = not args.skip_runtime and eval_phase in {"all", "runtime"}
        needs_evaluation = not args.skip_evaluation and eval_phase in {"all", "evaluate"}
        needs_eval_db = needs_evaluation
        config.validate(
            require_runtime=needs_runtime and not args.skip_evaluation,
            require_database=needs_runtime or needs_eval_db,
            require_vertex=needs_runtime,
        )

        run_id = args.run_id or config.raw.get("run", {}).get("run_id") or f"arch_eval_{datetime.now().strftime('%Y%m%d_%H%M')}"
        run_dir = config.output_dir / run_id
        writer = RunWriter(run_dir)

        sequences = load_sequences(config.datasets, config.project_root)
        sequences = _limit_per_dataset(sequences, args.max_samples)
        policy = PolicyIndex.load(config.policy_path, config.role_access_matrix_path)
        schema = load_schema_index(config.ddl_path)
        executor = _build_executor(config) if needs_runtime or needs_eval_db else None
        architectures = config.architectures
        selected_architectures = _architecture_ids(architectures, args.architecture)
        config_snapshot = _config_snapshot(config, selected_architectures)
        run_fingerprint = _stable_hash(config_snapshot)

        manifest = {
            "run_id": run_id,
            "runtime_kind": "architecture_baseline",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "run_fingerprint": run_fingerprint,
            "architectures": selected_architectures,
            "dataset_counts": _dataset_counts(sequences),
            "paths": {
                "policy_path": str(config.policy_path),
                "role_access_matrix_path": str(config.role_access_matrix_path),
                "ddl_path": str(config.ddl_path),
            },
            "runtime_skipped": bool(args.skip_runtime),
            "evaluation_skipped": bool(args.skip_evaluation),
            "evaluation_phase": eval_phase,
            "benchmark_selection": {
                "expected_turns": _expected_turns(selected_architectures, sequences),
            },
            "config": config_snapshot,
            "runtime_retry": config.raw.get("runtime", {}).get("api_429_retry", {}),
        }
        run_reuse_status = _run_reuse_status(run_dir, run_fingerprint)
        manifest["run_reuse_status"] = run_reuse_status
        if run_reuse_status.get("has_runtime_artifacts") and not run_reuse_status.get("fingerprint_match") and needs_runtime:
            raise ValueError(
                f"Run fingerprint mismatch for {run_dir}. "
                "Use --skip-runtime for post-evaluation on copied runtime artifacts, or use a new run_id for runtime."
            )
        if run_reuse_status.get("has_runtime_artifacts") and not run_reuse_status.get("fingerprint_match") and not needs_runtime:
            print(f"WARNING: {run_reuse_status.get('message')}", file=sys.stderr)
        evaluation_invocation: dict[str, Any] | None = None
        if not needs_runtime:
            runtime_manifest = _read_runtime_manifest(run_dir)
            evaluation_invocation = {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "run_id": run_id,
                "evaluation_phase": eval_phase,
                "evaluation_skipped": bool(args.skip_evaluation),
                "runtime_manifest_fingerprint": runtime_manifest.get("run_fingerprint"),
                "evaluation_config_fingerprint": run_fingerprint,
                "fingerprint_match": runtime_manifest.get("run_fingerprint") == run_fingerprint,
                "run_reuse_status": run_reuse_status,
                "config": config_snapshot,
            }
            _append_evaluation_invocation(run_dir, evaluation_invocation)
            manifest = runtime_manifest
        else:
            writer.write_manifest(manifest)

        if needs_runtime:
            llm = create_llm_client(config.llm)
            assert executor is not None
            registry = ModuleRegistry(policy, schema, schema.ddl_text, config.raw, llm, executor)
            for architecture_id in selected_architectures:
                module_order = list(architectures[architecture_id]["modules"])
                checkpoint = Checkpoint(writer.architecture_dir(architecture_id) / "checkpoint.json", resume=not args.no_resume, run_fingerprint=run_fingerprint)
                runner = ArchitectureRunner(run_id, architecture_id, module_order, registry, writer, checkpoint, retry_config=config.raw.get("runtime", {}).get("api_429_retry", {}))
                if args.rerun_api_429 and not args.no_resume:
                    rerun_count = runner.rerun_existing_api_429(sequences)
                    if rerun_count:
                        print(f"[API 429] architecture={architecture_id} rerun_keys={rerun_count}")
                for sequence in sequences:
                    runner.run_sequence(sequence)
                summary = runner.write_runtime_error_summary()
                if summary.get("api_429_error_count") or summary.get("api_429_retry_event_count"):
                    print(
                        "[API 429] "
                        f"architecture={architecture_id} remaining_errors={summary.get('api_429_error_count')} "
                        f"retry_events={summary.get('api_429_retry_event_count')} "
                        f"retry_sequences={summary.get('api_429_retry_sequence_count')}"
                    )

        result: dict[str, Any] = {"run_dir": str(run_dir), "manifest": manifest}
        if evaluation_invocation is not None:
            result["evaluation_invocation"] = evaluation_invocation
        if needs_evaluation:
            result["evaluation"] = evaluate_run(config, run_id)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _dataset_counts(sequences: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for sequence in sequences:
        counts[sequence.source_dataset] = counts.get(sequence.source_dataset, 0) + 1
    return counts


def _expected_turns(architecture_ids: list[str], sequences: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "setting_id": architecture_id,
            "architecture_id": architecture_id,
            "source_dataset": sequence.source_dataset,
            "sample_id": sequence.sample_id,
            "turn_id": turn.turn_id,
        }
        for architecture_id in architecture_ids
        for sequence in sequences
        for turn in sequence.turns
    ]

