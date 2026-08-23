from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from demo.backend.app.jobs import DemoJobManager
from demo.backend.app.main import create_server


MODULES = ["C0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "X1"]
CATALOG = {
    "multiturn": {
        "key": "multiturn", "canonical_id": "MT-MAL-420", "title": "Prompt library", "description": "Reference prompts",
        "role": "lecturer", "user_id": 1, "turn_type": "multi",
        "turns": [{"turn_id": 1, "nlq": "safe", "display_label": "Benign"}],
    },
}


class Runtime:
    def __init__(self, root):
        self.root = root

    def check_readiness(self):
        return {"ready": True, "checks": {"provider_config": True}, "errors": []}

    def execute(self, turns, run_id):
        path = self.root / "demo" / "runs" / run_id / "runtime"
        path.mkdir(parents=True)
        return {"runtime_dir": path}


class Artifacts:
    def __init__(self, *, run_id, sample_id, selected_final_turn, **kwargs):
        self.result = {"runId": run_id, "scenarioId": "full_trustedsql", "sampleId": sample_id, "turnId": selected_final_turn, "decision": "ALLOW", "events": [{"raw": "api_key=demo-value"}]}
        self.sent = False

    def poll_events(self):
        if self.sent:
            return []
        self.sent = True
        return [
            {"eventType": "revision", "streamSequence": 1, "revision": 2, "runId": self.result["runId"], "moduleId": "C0", "decision": "allow", "error": "api_key=demo-event"},
            {"eventType": "retract", "streamSequence": 2, "revision": 3, "runId": self.result["runId"], "moduleId": "M1"},
        ]

    def read_final_result(self):
        return self.result


@pytest.fixture
def api(tmp_path):
    mgr = DemoJobManager(
        tmp_path,
        runtime_factory=lambda: Runtime(tmp_path),
        artifact_factory=lambda *args, **kwargs: Artifacts(**kwargs),
        catalog=CATALOG,
        readiness=lambda: {"ready": True, "checks": {"provider_config": True}, "errors": []},
    )
    server = create_server(mgr, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base
    server.shutdown()
    server.server_close()
    mgr.close()
    thread.join(timeout=2)


def request(base, method, path, body=None, content_type="application/json", headers=None):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = Request(base + path, data=data, method=method, headers={"Content-Type": content_type, **(headers or {})})
    return urlopen(req, timeout=3)


def get_error(base, method, path, body=None, content_type="application/json", headers=None):
    with pytest.raises(HTTPError) as caught:
        request(base, method, path, body, content_type, headers)
    return caught.value.code, json.loads(caught.value.read())


def test_bootstrap_is_safe_and_post_requires_strict_json(api):
    response = request(api, "GET", "/api/bootstrap")
    payload = json.loads(response.read())
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert payload["architecture"]["modules"] == MODULES
    assert list(payload["catalog"]) == ["multiturn"]
    assert payload["catalog"]["multiturn"]["canonical_id"] == "MT-MAL-420"
    assert "provider_config" in payload["readiness"]["checks"]
    assert "/" not in json.dumps(payload["readiness"])

    status, error = get_error(api, "POST", "/api/runs", {"turns": "safe"})
    assert status == 400 and error["error"]["code"] == "invalid_request"
    status, error = get_error(api, "POST", "/api/runs", {"turns": ["safe"], "extra": 1})
    assert status == 400 and error["error"]["code"] == "invalid_request"
    status, error = get_error(api, "POST", "/api/runs", {"turns": ["safe"]}, "text/plain")
    assert status == 415 and error["error"]["code"] == "unsupported_media_type"


def test_post_rejects_oversize_and_readiness_failure(api, tmp_path):
    status, error = get_error(api, "POST", "/api/runs", b"x" * (16 * 1024 + 1))
    assert status == 413 and error["error"]["code"] == "body_too_large"

    blocked = DemoJobManager(
        tmp_path,
        runtime_factory=lambda: Runtime(tmp_path),
        artifact_factory=lambda *args, **kwargs: Artifacts(**kwargs),
        catalog=CATALOG,
        readiness=lambda: {"ready": False, "checks": {"provider_config": False}, "errors": ["provider_config_missing"]},
    )
    server = create_server(blocked, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, error = get_error(base, "POST", "/api/runs", {"turns": ["safe"]})
        assert status == 409 and error["error"]["code"] == "not_ready"
    finally:
        server.shutdown()
        server.server_close()
        blocked.close()
        thread.join(timeout=2)


def test_run_status_sse_resume_and_cancel_endpoints(api):
    created = request(api, "POST", "/api/runs", {"turns": ["safe"]})
    assert created.status == 202
    run = json.loads(created.read())
    run_id = run["runId"]
    assert "/" not in json.dumps(run)

    for _ in range(50):
        status = json.loads(request(api, "GET", f"/api/runs/{run_id}").read())
        if status["state"] == "complete":
            break
        time.sleep(0.01)
    assert status["state"] == "complete"
    with pytest.raises(HTTPError) as exc:
        request(api, "GET", "/api/runs/../escape")
    assert exc.value.code in {400, 404}

    sse = request(api, "GET", f"/api/runs/{run_id}/events?after=0")
    body = sse.read().decode()
    assert sse.headers["Content-Type"].startswith("text/event-stream")
    assert sse.headers["Cache-Control"] == "no-store"
    assert body.index("event: revision") < body.index("event: retract") < body.index("event: status")
    assert '"revision":2' in body and '"revision":3' in body
    assert "demo-event" not in body and "demo-value" not in body
    resumed = request(api, "GET", f"/api/runs/{run_id}/events?after=1").read().decode()
    assert "event: revision" not in resumed
    assert "event: retract" in resumed
    resumed_by_header = request(api, "GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "1"}).read().decode()
    assert "event: revision" not in resumed_by_header
    assert "event: retract" in resumed_by_header
    assert resumed_by_header.count("event: status\n") == 1

    cancelled = request(api, "POST", f"/api/runs/{run_id}/cancel")
    assert json.loads(cancelled.read())["state"] == "complete"


def test_concurrent_status_and_event_polling_is_safe(api):
    created = request(api, "POST", "/api/runs", {"turns": ["safe"]})
    run_id = json.loads(created.read())["runId"]
    def poll_status():
        return json.loads(request(api, "GET", f"/api/runs/{run_id}").read())

    def poll_events():
        return request(api, "GET", f"/api/runs/{run_id}/events?after=0").read().decode()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(poll_status) for _ in range(4)] + [pool.submit(poll_events) for _ in range(4)]
        results = [future.result() for future in futures]
    assert all(result["runId"] == run_id for result in results[:4])
    assert all("event: status" in body and body.count("event: status\n") == 1 for body in results[4:])


def test_sse_keeps_nonterminal_stream_open_until_terminal(monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()

    class BlockingRuntime(Runtime):
        def execute(self, turns, run_id):
            path = self.root / "demo" / "runs" / run_id / "runtime"
            path.mkdir(parents=True)
            started.set()
            release.wait(2)
            return {"runtime_dir": path}

    mgr = DemoJobManager(
        tmp_path,
        runtime_factory=lambda: BlockingRuntime(tmp_path),
        artifact_factory=lambda *args, **kwargs: Artifacts(**kwargs),
        catalog=CATALOG,
        readiness=lambda: {"ready": True, "checks": {}, "errors": []},
        heartbeat_seconds=0.05,
    )
    server = create_server(mgr, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    run_id = mgr.submit(["safe"])["runId"]
    started.wait(1)
    import http.client
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
    try:
        connection.request("GET", f"/api/runs/{run_id}/events")
        response = connection.getresponse()
        assert response.getheader("Content-Type").startswith("text/event-stream")
        def read_frame():
            lines = []
            while True:
                line = response.readline()
                if not line:
                    return b"".join(lines)
                lines.append(line)
                if line == b"\n":
                    return b"".join(lines)
        while not read_frame().startswith(b": heartbeat"):
            pass
        time.sleep(0.1)
        second_heartbeat = False
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            frame = read_frame()
            assert b"event: status" not in frame
            if frame.startswith(b": heartbeat"):
                second_heartbeat = True
                break
        assert second_heartbeat
        release.set()
        body = response.read().decode()
        assert "event: status" in body and '"state":"complete"' in body
    finally:
        release.set()
        connection.close()
        server.shutdown()
        server.server_close()
        mgr.close()
        thread.join(timeout=2)


def test_active_queued_and_running_jobs_survive_terminal_pruning(tmp_path):
    running_started = threading.Event()
    release = threading.Event()

    class BlockingRuntime(Runtime):
        def execute(self, turns, run_id):
            path = self.root / "demo" / "runs" / run_id / "runtime"
            path.mkdir(parents=True)
            if turns == ["slow"]:
                running_started.set()
                release.wait(2)
            return {"runtime_dir": path}

    mgr = DemoJobManager(
        tmp_path,
        runtime_factory=lambda: BlockingRuntime(tmp_path),
        artifact_factory=lambda *args, **kwargs: Artifacts(**kwargs),
        catalog=CATALOG,
        readiness=lambda: {"ready": True, "checks": {}, "errors": []},
        worker_count=1,
        max_queued_jobs=2,
        max_terminal_jobs=2,
    )
    try:
        first = mgr.submit(["safe"])
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and mgr.get_job(first["runId"])["state"] != "complete":
            time.sleep(0.01)
        assert mgr.get_job(first["runId"])["state"] == "complete"
        second = mgr.submit(["safe again"])
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and mgr.get_job(second["runId"])["state"] != "complete":
            time.sleep(0.01)
        assert mgr.get_job(second["runId"])["state"] == "complete"

        running = mgr.submit(["slow"])
        assert running_started.wait(1)
        queued = mgr.submit(["queued"])
        mgr.max_terminal_jobs = 0
        with mgr._changed:
            mgr._prune_terminal_locked()
        assert mgr.get_job(running["runId"])["state"] == "running"
        assert mgr.get_job(queued["runId"])["state"] == "queued"
    finally:
        release.set()
        mgr.close()


def test_unexpected_handler_failure_is_safe_500(api, monkeypatch):
    def fail(_manager):
        raise RuntimeError("provider secret sk-live-handler")

    monkeypatch.setattr(DemoJobManager, "bootstrap", fail)
    status, error = get_error(api, "GET", "/api/bootstrap")
    encoded = json.dumps(error)
    assert status == 500
    assert error == {"error": {"code": "internal_error", "message": "the request could not be completed safely"}}
    assert "«redacted:sk-…»" not in encoded and "Traceback" not in encoded


def test_unexpected_post_handler_failure_is_safe_500(api, monkeypatch):
    def fail(_manager, _turns):
        raise RuntimeError("provider secret sk-live-123")

    monkeypatch.setattr(DemoJobManager, "submit", fail)
    status, error = get_error(api, "POST", "/api/runs", {"turns": ["safe"]})
    encoded = json.dumps(error)
    assert status == 500
    assert error == {"error": {"code": "internal_error", "message": "the request could not be completed safely"}}
    assert "sk-live-123" not in encoded and "Traceback" not in encoded


def test_http_cancel_and_capacity_semantics(tmp_path):
    started = threading.Event()
    release = threading.Event()

    class BlockingRuntime(Runtime):
        def execute(self, turns, run_id):
            path = self.root / "demo" / "runs" / run_id / "runtime"
            path.mkdir(parents=True)
            started.set()
            release.wait(2)

    mgr = DemoJobManager(
        tmp_path,
        runtime_factory=lambda: BlockingRuntime(tmp_path),
        artifact_factory=lambda *args, **kwargs: Artifacts(**kwargs),
        catalog=CATALOG,
        readiness=lambda: {"ready": True, "checks": {}, "errors": []},
        worker_count=1,
        max_queued_jobs=1,
    )
    server = create_server(mgr, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        running = json.loads(request(base, "POST", "/api/runs", {"turns": ["first"]}).read())
        assert started.wait(1)
        status, error = get_error(base, "POST", f"/api/runs/{running['runId']}/cancel")
        assert status == 409 and error["error"]["code"] == "not_cancellable"
        queued = json.loads(request(base, "POST", "/api/runs", {"turns": ["second"]}).read())
        status, error = get_error(base, "POST", "/api/runs", {"turns": ["third"]})
        assert status == 429 and error["error"]["code"] == "capacity_exhausted"
        cancelled = json.loads(request(base, "POST", f"/api/runs/{queued['runId']}/cancel").read())
        assert cancelled["state"] == "cancelled"
        release.set()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        mgr.close()
        thread.join(timeout=2)


def test_method_path_and_loopback_guards(api, tmp_path):
    status, error = get_error(api, "PUT", "/api/bootstrap")
    assert status == 405 and error["error"]["code"] == "method_not_allowed"
    status, error = get_error(api, "GET", "/not-an-endpoint")
    assert status == 404 and error["error"]["code"] == "not_found"
    mgr = DemoJobManager(tmp_path, runtime_factory=lambda: Runtime(tmp_path), catalog=CATALOG, readiness=lambda: {"ready": True, "checks": {}, "errors": []})
    try:
        with pytest.raises(ValueError, match="loopback"):
            create_server(mgr, host="0.0.0.0")
    finally:
        mgr.close()


@pytest.fixture
def production_static_server(tmp_path):
    static_dir = tmp_path / "dist"
    assets_dir = static_dir / "assets"
    assets_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("<!doctype html><title>demo</title>", encoding="utf-8")
    (assets_dir / "index-abc123.js").write_text("console.log('demo')", encoding="utf-8")
    (assets_dir / "index-abc123.css").write_text("body { color: black; }", encoding="utf-8")
    manager = DemoJobManager(
        tmp_path,
        runtime_factory=lambda: Runtime(tmp_path),
        artifact_factory=lambda *args, **kwargs: Artifacts(**kwargs),
        catalog=CATALOG,
        readiness=lambda: {"ready": True, "checks": {"provider_config": True}, "errors": []},
    )
    server = create_server(manager, host="127.0.0.1", port=0, static_dir=static_dir)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base, static_dir
    server.shutdown()
    server.server_close()
    manager.close()
    thread.join(timeout=2)


def test_production_static_routes_and_api_precedence(production_static_server):
    base, _static_dir = production_static_server
    index = request(base, "GET", "/")
    assert index.status == 200
    assert index.read().startswith(b"<!doctype html>")
    assert index.headers["Content-Type"].startswith("text/html")
    assert index.headers["Cache-Control"] == "no-store"
    assert index.headers["X-Content-Type-Options"] == "nosniff"
    assert index.headers["X-Frame-Options"] == "DENY"
    assert index.headers["Referrer-Policy"] == "no-referrer"
    assert "default-src 'self'" in index.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in index.headers["Content-Security-Policy"]

    javascript = request(base, "GET", "/assets/index-abc123.js")
    assert javascript.status == 200
    assert javascript.headers["Content-Type"].startswith("application/javascript")
    assert javascript.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert javascript.read() == b"console.log('demo')"

    stylesheet = request(base, "GET", "/assets/index-abc123.css")
    assert stylesheet.headers["Content-Type"].startswith("text/css")

    bootstrap = request(base, "GET", "/api/bootstrap")
    payload = json.loads(bootstrap.read())
    assert bootstrap.headers["Content-Type"].startswith("application/json")
    assert payload["architecture"]["modules"] == MODULES

    status, error = get_error(base, "GET", "/assets/missing-abc123.js")
    assert status == 404 and error["error"]["code"] == "not_found"
    status, error = get_error(base, "GET", "/unknown")
    assert status == 404 and error["error"]["code"] == "not_found"


def test_static_routes_reject_traversal_dotfiles_and_symlink_escape(production_static_server, tmp_path):
    base, static_dir = production_static_server
    outside = tmp_path / "outside.js"
    outside.write_text("secret", encoding="utf-8")
    try:
        (static_dir / "assets" / "escape-abc123.js").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    (static_dir / "assets" / ".hidden.js").write_text("secret", encoding="utf-8")

    for path in (
        "/assets/../index.html",
        "/assets/%2e%2e/index.html",
        "/assets/%2Fetc%2Fpasswd",
        "/assets/.hidden.js",
        "/assets/escape-abc123.js",
        "/assets",
        "/assets/",
    ):
        status, error = get_error(base, "GET", path)
        assert status == 404
        assert error["error"]["code"] == "not_found"


def test_missing_dist_returns_stable_safe_failure(tmp_path):
    manager = DemoJobManager(
        tmp_path,
        runtime_factory=lambda: Runtime(tmp_path),
        catalog=CATALOG,
        readiness=lambda: {"ready": False, "checks": {}, "errors": ["provider_config_missing"]},
    )
    server = create_server(manager, host="127.0.0.1", port=0, static_dir=tmp_path / "not-built")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, error = get_error(base, "GET", "/")
        assert status == 503
        assert error == {"error": {"code": "frontend_unavailable", "message": "built frontend is unavailable"}}
    finally:
        server.shutdown()
        server.server_close()
        manager.close()
        thread.join(timeout=2)


def test_unsupported_methods_use_safe_json_security_envelope(production_static_server):
    base, _static_dir = production_static_server
    for method in ("TRACE", "CONNECT", "PROPFIND"):
        status, error = get_error(base, method, "/")
        assert status == 501
        assert error == {"error": {"code": "method_not_implemented", "message": "method is not supported"}}
        with pytest.raises(HTTPError) as caught:
            request(base, method, "/")
        assert caught.value.headers["X-Content-Type-Options"] == "nosniff"
