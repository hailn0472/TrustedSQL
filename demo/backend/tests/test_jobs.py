from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from demo.backend.app.jobs import (
    CancellationConflict,
    DemoJobManager,
    JobCapacityError,
    JobNotFound,
    JobNotReady,
)


ROOT = Path(__file__).resolve().parents[3]


class FakeRuntime:
    def __init__(self, root: Path, *, decision="ALLOW", delay=0.0, fail=None):
        self.root = root
        self.decision = decision
        self.delay = delay
        self.fail = fail
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()

    def check_readiness(self):
        return {"ready": True, "checks": {"provider_config": True}, "errors": []}

    def execute(self, turns, run_id):
        self.calls.append((turns, run_id))
        self.started.set()
        run = self.root / "demo" / "runs" / run_id
        run.mkdir(parents=True, exist_ok=True)
        (run / "runtime").mkdir()
        if self.delay:
            self.release.wait(timeout=self.delay)
        if self.fail:
            raise self.fail
        return {"run_id": run_id, "output_dir": run, "runtime_dir": run / "runtime"}


class FakeArtifacts:
    def __init__(self, *, decision="ALLOW", event_rows=None, turn_type=None, run_id="safe", sample_id="interactive-multiturn", selected_final_turn=1, **kwargs):
        self.decision = decision
        self.event_rows = list(event_rows or [])
        self.turn_type = turn_type
        self.run_id = run_id
        self.sample_id = sample_id
        self.selected_final_turn = selected_final_turn
        self.events = False
        self.final = None
        self.poll_count = 0

    def poll_events(self):
        if self.events:
            return []
        self.events = True
        return self.event_rows

    def read_final_result(self):
        self.poll_count += 1
        return {"runId": self.run_id, "scenarioId": "full_trustedsql", "sampleId": self.sample_id, "decision": self.decision, "turnId": self.selected_final_turn, "events": []}


def manager(tmp_path, runtime, *, artifact=None, ready=True, workers=1):
    def runtime_factory():
        return runtime

    def artifact_factory(*args, **kwargs):
        assert kwargs["turn_type"] in {"single", "multi"}
        if artifact is not None:
            artifact.run_id = kwargs["run_id"]
            artifact.sample_id = kwargs["sample_id"]
            artifact.selected_final_turn = kwargs["selected_final_turn"]
            return artifact
        return FakeArtifacts(**kwargs)

    return DemoJobManager(
        tmp_path,
        provider_config=tmp_path / "provider.yaml",
        runtime_factory=runtime_factory,
        artifact_factory=artifact_factory,
        catalog={"multiturn": {"key": "multiturn", "canonical_id": "MT-MAL-420", "turn_type": "multi", "turns": [{"turn_id": 1, "nlq": "reference"}]}},
        readiness=lambda: {"ready": ready, "checks": {"provider_config": ready}, "errors": [] if ready else ["provider_config_missing"]},
        worker_count=workers,
    )


def wait_state(mgr, run_id, state, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if mgr.get_job(run_id)["state"] == state:
            return mgr.get_job(run_id)
        time.sleep(0.01)
    return mgr.get_job(run_id)


def test_jobs_complete_and_deny_with_locked_identity_and_explicit_turn_type(tmp_path):
    runtime = FakeRuntime(tmp_path)
    mgr = manager(tmp_path, runtime, artifact=FakeArtifacts(decision="ALLOW"))
    try:
        allow = mgr.submit(["safe"])
        assert allow["state"] == "queued"
        assert allow["runId"] != "1"
        assert wait_state(mgr, allow["runId"], "complete")["finalResult"]["decision"] == "ALLOW"

        denied_artifacts = FakeArtifacts(decision="DENY")
        multi = manager(tmp_path, runtime, artifact=denied_artifacts)
        try:
            denied = multi.submit(["restricted"])
            assert wait_state(multi, denied["runId"], "denied")["state"] == "denied"
            assert multi.artifact_calls[0]["turn_type"] == "multi"
            assert multi.artifact_calls[0]["allowed_turn_ids"] == (1,)
        finally:
            multi.close()
    finally:
        mgr.close()


def test_runtime_failure_is_redacted_and_has_no_fabricated_result(tmp_path):
    runtime = FakeRuntime(tmp_path, fail=RuntimeError("provider secret sk-live-123"))
    mgr = manager(tmp_path, runtime)
    try:
        run = mgr.submit(["safe"])
        result = wait_state(mgr, run["runId"], "error")
        assert result["finalResult"] is None
        assert "sk-live-123" not in str(result)
        assert result["error"]["code"] == "runtime_failed"
    finally:
        mgr.close()


def test_queued_cancel_never_invokes_runtime_and_running_cancel_conflicts(tmp_path):
    first = FakeRuntime(tmp_path, delay=2)
    calls = []

    def runtimes():
        calls.append(1)
        return first

    mgr = DemoJobManager(
        tmp_path,
        provider_config=tmp_path / "provider.yaml",
        runtime_factory=runtimes,
        artifact_factory=lambda *a, **k: FakeArtifacts(),
        catalog={"multiturn": {"key": "multiturn", "canonical_id": "MT-MAL-420", "turn_type": "multi", "turns": [{"turn_id": 1}]}},
        readiness=lambda: {"ready": True, "checks": {}, "errors": []},
        worker_count=1,
    )
    try:
        running = mgr.submit(["first"])
        first.started.wait(1)
        with pytest.raises(CancellationConflict):
            mgr.cancel(running["runId"])
        queued = mgr.submit(["second"])
        cancelled = mgr.cancel(queued["runId"])
        assert cancelled["state"] == "cancelled"
        first.release.set()
        wait_state(mgr, running["runId"], "complete")
        assert len(calls) == 1
        assert mgr.get_job(queued["runId"])["state"] == "cancelled"
    finally:
        mgr.close()


def test_terminal_jobs_are_immutable_and_unknown_ids_are_safe(tmp_path):
    mgr = manager(tmp_path, FakeRuntime(tmp_path))
    try:
        run = mgr.submit(["safe"])
        wait_state(mgr, run["runId"], "complete")
        before = mgr.get_job(run["runId"])
        assert mgr.cancel(run["runId"])["state"] == "complete"
        assert mgr.get_job(run["runId"]) == before
        with pytest.raises(JobNotFound):
            mgr.get_job("../escape")
    finally:
        mgr.close()


def test_not_ready_submission_is_rejected(tmp_path):
    mgr = manager(tmp_path, FakeRuntime(tmp_path), ready=False)
    try:
        with pytest.raises(JobNotReady):
            mgr.submit(["safe"])
    finally:
        mgr.close()


def test_admission_is_bounded_and_terminal_retention_prunes_only_old_terminal_jobs(tmp_path):
    first = FakeRuntime(tmp_path, delay=2)
    mgr = DemoJobManager(
        tmp_path,
        runtime_factory=lambda: first,
        artifact_factory=lambda *a, **k: FakeArtifacts(run_id=k["run_id"], sample_id=k["sample_id"], selected_final_turn=k["selected_final_turn"]),
        catalog={"multiturn": {"key": "multiturn", "canonical_id": "MT-MAL-420", "turn_type": "multi", "turns": [{"turn_id": 1}]}},
        readiness=lambda: {"ready": True, "checks": {}, "errors": []},
        worker_count=1,
        max_queued_jobs=1,
        max_terminal_jobs=1,
    )
    try:
        running = mgr.submit(["first"])
        assert first.started.wait(1)
        queued = mgr.submit(["second"])
        with pytest.raises(JobCapacityError):
            mgr.submit(["third"])
        first.release.set()
        assert wait_state(mgr, running["runId"], "complete")["state"] == "complete"
        assert wait_state(mgr, queued["runId"], "complete")["state"] == "complete"
        with pytest.raises(JobNotFound):
            mgr.get_job(running["runId"])
        assert mgr.get_job(queued["runId"])["state"] == "complete"
    finally:
        mgr.close()


def test_configured_workers_keep_concurrent_runs_and_artifacts_isolated(tmp_path):
    active = 0
    maximum = 0
    lock = threading.Lock()
    run_dirs = {}
    release = threading.Event()

    class IsolatedRuntime:
        def execute(self, turns, run_id):
            nonlocal active, maximum
            run_dir = tmp_path / "demo" / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "runtime").mkdir()
            (run_dir / "runtime" / "owner.txt").write_text(run_id, encoding="utf-8")
            with lock:
                active += 1
                maximum = max(maximum, active)
                run_dirs[run_id] = run_dir
            release.wait(2)
            with lock:
                active -= 1

    def artifacts(*args, **kwargs):
        return FakeArtifacts(run_id=kwargs["run_id"], sample_id=kwargs["sample_id"], selected_final_turn=kwargs["selected_final_turn"])

    mgr = DemoJobManager(
        tmp_path,
        runtime_factory=IsolatedRuntime,
        artifact_factory=artifacts,
        catalog={"multiturn": {"key": "multiturn", "canonical_id": "MT-MAL-420", "turn_type": "multi", "turns": [{"turn_id": 1}]}},
        readiness=lambda: {"ready": True, "checks": {}, "errors": []},
        worker_count=2,
        max_queued_jobs=2,
    )
    try:
        runs = [mgr.submit([f"query {index}"]) for index in range(2)]
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and maximum < 2:
            time.sleep(0.01)
        assert maximum == 2
        release.set()
        for run in runs:
            assert wait_state(mgr, run["runId"], "complete")["state"] == "complete"
        assert len(set(run["runId"] for run in runs)) == 2
        assert all((run_dirs[run["runId"]] / "runtime" / "owner.txt").read_text(encoding="utf-8") == run["runId"] for run in runs)
    finally:
        release.set()
        mgr.close()


def test_revision_and_retract_records_are_preserved_in_job_events(tmp_path):
    event_rows = [
        {"eventType": "revision", "streamSequence": 1, "revision": 2, "runId": "placeholder", "moduleId": "C0", "decision": "allow"},
        {"eventType": "retract", "streamSequence": 2, "revision": 3, "runId": "placeholder", "moduleId": "M1"},
    ]
    runtime = FakeRuntime(tmp_path)
    artifact = FakeArtifacts(event_rows=event_rows)
    mgr = manager(tmp_path, runtime, artifact=artifact)
    try:
        run = mgr.submit(["safe"])
        snapshot = wait_state(mgr, run["runId"], "complete")
        assert [event["eventType"] for event in snapshot["events"]] == ["revision", "retract"]
        assert snapshot["events"][0]["revision"] == 2
        assert snapshot["events"][1]["revision"] == 3
    finally:
        mgr.close()


def test_close_does_not_block_on_full_queue_and_preserves_running_work(tmp_path):
    runtime = FakeRuntime(tmp_path, delay=10)
    mgr = DemoJobManager(
        tmp_path,
        runtime_factory=lambda: runtime,
        artifact_factory=lambda *a, **k: FakeArtifacts(**k),
        catalog={"multiturn": {"key": "multiturn", "canonical_id": "MT-MAL-420", "turn_type": "multi", "turns": [{"turn_id": 1}]}},
        readiness=lambda: {"ready": True, "checks": {}, "errors": []},
        worker_count=1,
        max_queued_jobs=1,
    )
    close_thread = None
    try:
        running = mgr.submit(["first"])
        assert runtime.started.wait(1)
        queued = mgr.submit(["second"])

        close_thread = threading.Thread(target=mgr.close)
        close_thread.start()
        close_thread.join(timeout=3)
        assert not close_thread.is_alive()
        assert mgr.get_job(queued["runId"])["state"] == "cancelled"
        assert mgr.get_job(running["runId"])["state"] == "running"

        runtime.release.set()
        assert wait_state(mgr, running["runId"], "complete")["state"] == "complete"
    finally:
        runtime.release.set()
        if close_thread is not None:
            close_thread.join(timeout=3)
        mgr.close()


def test_stream_lease_keeps_terminal_job_addressable_until_release(tmp_path):
    mgr = manager(tmp_path, FakeRuntime(tmp_path), workers=1)
    mgr.max_terminal_jobs = 1
    try:
        first = mgr.submit(["first"])
        assert wait_state(mgr, first["runId"], "complete")["state"] == "complete"
        mgr.acquire_stream(first["runId"])

        second = mgr.submit(["second"])
        assert wait_state(mgr, second["runId"], "complete")["state"] == "complete"
        assert mgr.get_job(first["runId"])["state"] == "complete"

        mgr.release_stream(first["runId"])
        with pytest.raises(JobNotFound):
            mgr.get_job(first["runId"])
    finally:
        mgr.close()
