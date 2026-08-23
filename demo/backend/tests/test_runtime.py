from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from demo.backend.app.runtime import (
    INTERACTIVE_SAMPLE_ID,
    REQUIRED_MODULES,
    RuntimeAdapterError,
    TrustedSqlRuntimeAdapter,
)
from trustedsql.schemas import MethodTurnOutput


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FORBIDDEN_VALUES = {"BENIGN", "MALICIOUS", "attack", "ground-truth", "evidence"}
CHAT_TURNS = [f"User-authored query {turn}" for turn in range(1, 7)]


@dataclass
class FakeConfig:
    project_root: Path
    raw: dict
    datasets: dict
    modules: dict
    method: dict
    ddl_path: Path
    compact_schema_path: Path
    policy_index_path: Path
    role_access_matrix_path: Path

    def enabled_settings(self, selected=None):
        settings = self.method["settings"]
        return {name: settings[name] for name in (selected or settings)}

    def module_llm_config(self, module_id):
        module = self.modules.get(module_id, {})
        return dict(module.get("llm") or module.get("vertex", {}))

    def resolve_path(self, value):
        path = Path(value)
        return (path if path.is_absolute() else self.project_root / path).resolve()

    def validate(self, require_database=False, require_vertex=False):
        if require_database and not self.raw.get("database", {}).get("url"):
            raise ValueError("database.url is required")
        if require_vertex and not self.raw.get("vertex", {}).get("model"):
            raise ValueError("vertex.model is required")


def _config(tmp_path: Path, *, modules=None, database_url="postgresql://safe/db") -> FakeConfig:
    assets = tmp_path / "assets"
    assets.mkdir(parents=True)
    paths = []
    for name in ("ddl.md", "compact.txt", "policy.json", "roles.json"):
        path = assets / name
        path.write_text(name, encoding="utf-8")
        paths.append(path)
    configured_modules = modules or {
        module_id: {"vertex": {"model": "fake-model", "temperature": 0.0}}
        for module_id in REQUIRED_MODULES
    }
    return FakeConfig(
        project_root=tmp_path,
        raw={"database": {"url": database_url}, "llm": {"temperature": 0.0}},
        datasets={
            "datasets": {
                "unrelated": {"path": "data/unrelated.json", "turn_type": "single"},
                "another": {"path": "data/another.json", "turn_type": "multi"},
            }
        },
        modules=configured_modules,
        method={"settings": {"full_trustedsql": {"modules": list(REQUIRED_MODULES)}}},
        ddl_path=paths[0],
        compact_schema_path=paths[1],
        policy_index_path=paths[2],
        role_access_matrix_path=paths[3],
    )


class FakeRunner:
    instances = []
    fail = None
    partial = False

    def __init__(self, config, run_id, *, output_dir):
        self.config = config
        self.run_id = run_id
        self.run_dir = Path(output_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.calls = []
        self.persisted = []
        self.checkpoints = []
        self.summary_calls = 0
        type(self).instances.append(self)

    def write_run_manifest(self, *, settings, sequences, max_samples):
        self.calls.append(("manifest", settings, sequences, max_samples))
        path = self.run_dir / "run_manifest.json"
        path.write_text(json.dumps({"sequence_count": len(sequences)}), encoding="utf-8")
        return {"sequence_count": len(sequences)}

    def _run_sequence_full(self, setting_id, sequence, *, seq_key, history, modules):
        self.calls.append(("run", setting_id, sequence, seq_key, history, modules))
        if self.fail is not None:
            raise self.fail
        turns = sequence.turns[:-1] if self.partial else sequence.turns
        return [
            MethodTurnOutput(
                run_id=self.run_id,
                setting_id=setting_id,
                sequence_id=sequence.sample_id if sequence.turn_type == "multi" else None,
                sample_id=sequence.sample_id,
                turn_id=turn.turn_id,
                role=sequence.role,
                user_id=sequence.user_id,
                nlq=turn.nlq,
                decision="ALLOW",
            )
            for turn in turns
        ]

    def _write_output(self, output):
        self.persisted.append(output)
        path = self.run_dir / "runtime" / "raw_turn_outputs.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(output.to_dict()) + "\n")

    def _save_checkpoint(self, completed):
        self.checkpoints.append(set(completed))
        path = self.run_dir / "runtime" / "checkpoint.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"completed": sorted(completed)}), encoding="utf-8")

    def _write_runtime_error_summary(self):
        self.summary_calls += 1
        path = self.run_dir / "runtime" / "runtime_error_summary.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        summary = {"run_id": self.run_id, "runtime_error_count": 0}
        path.write_text(json.dumps(summary), encoding="utf-8")
        return summary


def _run_id(tmp_path: Path, label: str) -> str:
    return f"{label}-{tmp_path.name}-{uuid4().hex[:8]}"


def _adapter(tmp_path, config, *, provider=True, runner=FakeRunner):
    provider_path = tmp_path / "provider.yaml"
    if provider:
        provider_path.write_text("llm:\n  model: fake-model\n", encoding="utf-8")
    return TrustedSqlRuntimeAdapter(
        REPOSITORY_ROOT,
        provider_path if provider else tmp_path / "missing-provider.yaml",
        config_loader=lambda _root, _provider: config,
        runner_factory=runner,
    )


def setup_function():
    FakeRunner.instances.clear()
    FakeRunner.fail = None
    FakeRunner.partial = False


def test_required_module_constant_is_exact_and_setting_must_match(tmp_path):
    assert REQUIRED_MODULES == ("C0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "X1")

    for bad in (
        [*REQUIRED_MODULES[1:], REQUIRED_MODULES[0]],
        list(REQUIRED_MODULES[:-1]),
        [*REQUIRED_MODULES, "EXTRA"],
    ):
        config = _config(tmp_path / str(len(bad)), modules=None)
        config.method["settings"]["full_trustedsql"]["modules"] = bad
        with pytest.raises(RuntimeAdapterError, match="module list"):
            _adapter(tmp_path / str(len(bad)), config).execute(["User query"], _run_id(tmp_path, "bad-modules"))


def test_provider_config_is_explicit_and_must_be_a_file(tmp_path):
    with pytest.raises(ValueError, match="provider_config"):
        TrustedSqlRuntimeAdapter(REPOSITORY_ROOT, None)

    result = _adapter(tmp_path, _config(tmp_path), provider=False).check_readiness()
    assert not result.ready
    assert "provider_config_missing" in result.errors
    assert "missing-provider" not in json.dumps(result.to_dict())


def test_safe_run_id_and_nonempty_reuse_are_rejected(tmp_path):
    adapter = _adapter(tmp_path, _config(tmp_path))
    for run_id in ("../escape", "nested/run", "/absolute", ".", "..", ""):
        with pytest.raises((ValueError, RuntimeAdapterError)):
            adapter.execute(["User query"], run_id)

    run_dir = REPOSITORY_ROOT / "demo" / "runs" / "occupied-test"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "existing.txt").write_text("do not replace", encoding="utf-8")
    try:
        with pytest.raises(RuntimeAdapterError, match="non-empty"):
            adapter.execute(["User query"], "occupied-test")
    finally:
        (run_dir / "existing.txt").unlink()
        run_dir.rmdir()


def test_run_id_reservation_rejects_preexisting_empty_directory(tmp_path):
    adapter = _adapter(tmp_path, _config(tmp_path))
    run_id = _run_id(tmp_path, "atomic-reservation")
    reserved = None
    try:
        reserved = adapter._prepare_run_dir(run_id)
        assert reserved.is_dir()
        with pytest.raises(RuntimeAdapterError, match="reserved|reuse|existing"):
            adapter._prepare_run_dir(run_id)
    finally:
        if reserved is not None and reserved.exists():
            reserved.rmdir()


def test_one_turn_chat_conversion_has_no_runtime_labels_or_evidence(tmp_path):
    config = _config(tmp_path)
    result = _adapter(tmp_path, config).execute(["My own query"], _run_id(tmp_path, "single-conversion"))
    runner = FakeRunner.instances[-1]
    sequence = runner.calls[1][2]
    assert sequence.turn_type == "multi"
    assert sequence.sample_id == INTERACTIVE_SAMPLE_ID
    assert sequence.attack_tags == {}
    assert sequence.seq_label == ""
    assert sequence.primary_type is None
    assert sequence.turns[0].sql_gt is None
    assert sequence.turns[0].turn_label == ""
    assert not FORBIDDEN_VALUES.intersection(json.dumps(sequence, default=lambda x: x.__dict__))
    assert result.final_output["turn_id"] == 1


def test_six_turn_prefix_and_complete_history_are_passed_and_manifest_is_selected_only(tmp_path):
    config = _config(tmp_path)
    result = _adapter(tmp_path, config).execute(CHAT_TURNS, _run_id(tmp_path, "six-turn"))
    runner = FakeRunner.instances[-1]
    manifest = runner.calls[0]
    execution = runner.calls[1]
    assert len(manifest[2]) == 1
    assert len(manifest[2][0].turns) == 6
    assert execution[3] == INTERACTIVE_SAMPLE_ID
    assert [turn.turn_id for turn in execution[2].turns] == [1, 2, 3, 4, 5, 6]
    assert execution[4] == []
    assert execution[5] == list(REQUIRED_MODULES)
    assert len(runner.persisted) == 6
    assert len(runner.checkpoints) == 6
    assert runner.summary_calls == 1
    assert result.final_output["turn_id"] == 6
    assert result.output_dir.is_relative_to(REPOSITORY_ROOT / "demo" / "runs")


def test_manifest_config_contains_only_demo_owned_selected_prefix_dataset(tmp_path):
    config = _config(tmp_path)
    result = _adapter(tmp_path, config).execute(CHAT_TURNS[:2], _run_id(tmp_path, "selected-dataset"))
    runner = FakeRunner.instances[-1]
    datasets = runner.config.datasets.get("datasets", runner.config.datasets)
    assert list(datasets) == ["selected_sequence"]
    dataset_path = runner.config.resolve_path(datasets["selected_sequence"]["path"])
    assert dataset_path.is_relative_to(result.output_dir)
    assert dataset_path.name == "selected-sequence.json"
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert [turn["turn_id"] for turn in payload[0]["turns"]] == [1, 2]
    assert "attack_kind" not in json.dumps(payload)
    assert "turn_label" not in json.dumps(payload)
    assert "unrelated" not in json.dumps(datasets)


def test_readiness_omits_secret_values_and_reports_nonzero_temperature(tmp_path):
    secret = "postgresql://name:super-secret@db.example/private"
    config = _config(tmp_path, database_url=secret)
    config.modules["M3"]["vertex"]["temperature"] = 0.25
    result = _adapter(tmp_path, config).check_readiness()
    serialized = json.dumps(result.to_dict())
    assert not result.ready
    assert "temperature_not_pinned" in result.errors
    assert "super-secret" not in serialized
    assert secret not in serialized


def test_readiness_uses_effective_m1_m3_m6_models_without_global_vertex_model(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.raw.pop("vertex", None)
    config.modules = {
        module_id: {"llm": {"model": "effective-model", "temperature": 0.0}}
        for module_id in REQUIRED_MODULES
    }
    monkeypatch.setattr(TrustedSqlRuntimeAdapter, "_gnn_check", staticmethod(lambda _config: True))
    result = _adapter(tmp_path, config).check_readiness()
    assert result.ready
    assert result.checks["model_assets"]
    assert "parent_config_validation_failed" not in result.errors


def test_readiness_missing_effective_model_is_safe(tmp_path, monkeypatch):
    secret = "postgresql://user:super-secret@db.example/private"
    config = _config(tmp_path, database_url=secret)
    config.raw.pop("vertex", None)
    config.modules["M3"] = {"llm": {"temperature": 0.0}}
    monkeypatch.setattr(TrustedSqlRuntimeAdapter, "_gnn_check", staticmethod(lambda _config: True))
    result = _adapter(tmp_path, config).check_readiness()
    serialized = json.dumps(result.to_dict())
    assert not result.ready
    assert "model_asset_missing" in result.errors
    assert secret not in serialized
    assert "super-secret" not in serialized


def test_readiness_missing_gnn_assets_is_safe(tmp_path):
    secret = "postgresql://user:super-secret@db.example/private"
    config = _config(tmp_path, database_url=secret)
    result = _adapter(tmp_path, config).check_readiness()
    serialized = json.dumps(result.to_dict())
    assert not result.ready
    assert "gnn_asset_missing" in result.errors
    assert secret not in serialized
    assert "super-secret" not in serialized


def test_missing_db_and_assets_are_safe_readiness_failures(tmp_path):
    config = _config(tmp_path, database_url="")
    config.ddl_path = tmp_path / "missing-ddl"
    result = _adapter(tmp_path, config).check_readiness()
    assert not result.ready
    assert "database_url_missing" in result.errors
    assert "schema_asset_missing" in result.errors
    assert all("missing-ddl" not in reason for reason in result.errors)


def test_runner_exception_is_propagated_without_synthetic_output(tmp_path):
    FakeRunner.fail = RuntimeError("provider secret should not be serialized")
    with pytest.raises(RuntimeError, match="provider secret"):
        _adapter(tmp_path, _config(tmp_path)).execute(["User query"], _run_id(tmp_path, "runner-error"))
    runner = FakeRunner.instances[-1]
    assert runner.persisted == []
    assert runner.summary_calls == 1
    raw_path = runner.run_dir / "runtime" / "raw_turn_outputs.jsonl"
    assert not raw_path.exists()


def test_partial_sequence_is_rejected_after_real_outputs_are_persisted(tmp_path):
    FakeRunner.partial = True
    with pytest.raises(RuntimeAdapterError, match="final selected turn"):
        _adapter(tmp_path, _config(tmp_path)).execute(CHAT_TURNS, _run_id(tmp_path, "partial-sequence"))
    runner = FakeRunner.instances[-1]
    assert [output.turn_id for output in runner.persisted] == [1, 2, 3, 4, 5]
    assert runner.summary_calls == 1


def test_all_generated_paths_stay_under_demo_runs(tmp_path):
    result = _adapter(tmp_path, _config(tmp_path)).execute(["User query"], _run_id(tmp_path, "path-boundary"))
    runs_root = (REPOSITORY_ROOT / "demo" / "runs").resolve()
    assert result.output_dir.is_relative_to(runs_root)
    assert result.manifest_path.is_relative_to(runs_root)
    assert result.runtime_dir.is_relative_to(runs_root)
