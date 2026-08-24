"""Safe local-only HTTP API for the TrustedSQL demo.

The server uses only :mod:`http.server` primitives.  Requests are bounded to
16 KiB JSON bodies and a five-second socket read timeout; responses are
same-origin/local-demo payloads with no CORS allowance.  It also serves the
built demo frontend from its configured static directory.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
from mimetypes import types_map

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .catalog import ScenarioCatalogError
from .environment import load_env_file
from .jobs import (
    CancellationConflict,
    ConversationConflict,
    ConversationNotFound,
    DemoJobManager,
    JobCapacityError,
    JobError,
    JobNotFound,
    JobNotReady,
)

MAX_BODY_BYTES = 16 * 1024
REQUEST_TIMEOUT_SECONDS = 5.0
SSE_HEARTBEAT_SECONDS = 0.5
DEFAULT_STATIC_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"
_STATIC_MIME_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".js": "application/javascript; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def _loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        try:
            return all(ipaddress.ip_address(item).is_loopback for item in socket.getaddrinfo(host, None))
        except (OSError, ValueError):
            return False


class DemoHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, manager: DemoJobManager, static_dir: str | Path | None = None):
        self.manager = manager
        self.static_dir = Path(static_dir or DEFAULT_STATIC_DIR).expanduser().resolve()
        super().__init__(server_address, DemoRequestHandler)


def create_server(
    manager: DemoJobManager,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    allow_remote: bool = False,
    static_dir: str | Path | None = None,
) -> DemoHTTPServer:
    if not allow_remote and not _loopback(host):
        raise ValueError("non-loopback bind requires explicit allow_remote=True")
    server = DemoHTTPServer((host, port), manager, static_dir=static_dir)
    return server


class DemoRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TrustedSQLDemo/1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)

    @property
    def manager(self) -> DemoJobManager:
        return self.server.manager  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _headers(self, content_type: str, *, cache_control: str = "no-store") -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        if code == 501:
            self._error(501, "method_not_implemented", "method is not supported")
            return
        super().send_error(code, message, explain)

    def _json(self, status: int, payload: Any) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._headers("application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, status: int, code: str, message: str) -> None:
        self._json(status, {"error": {"code": code, "message": message}})

    def _serve_file(
        self,
        candidate: Path,
        *,
        allowed_root: Path,
        content_type: str,
        cache_control: str,
        missing_status: int = 404,
        missing_code: str = "not_found",
        missing_message: str = "endpoint not found",
    ) -> None:
        try:
            resolved_root = allowed_root.resolve(strict=True)
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(resolved_root)
            if not resolved.is_file():
                raise OSError("not a regular file")
            payload = resolved.read_bytes()
        except (OSError, ValueError):
            self._error(missing_status, missing_code, missing_message)
            return
        self.send_response(200)
        self._headers(content_type, cache_control=cache_control)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_index(self) -> None:
        static_dir = self.server.static_dir  # type: ignore[attr-defined]
        if not static_dir.is_dir():
            self._error(503, "frontend_unavailable", "built frontend is unavailable")
            return
        self._serve_file(
            static_dir / "index.html",
            allowed_root=static_dir,
            content_type="text/html; charset=utf-8",
            cache_control="no-store",
            missing_status=503,
            missing_code="frontend_unavailable",
            missing_message="built frontend is unavailable",
        )

    def _serve_asset(self, path: str) -> None:
        decoded = unquote(path)
        if "\x00" in decoded or "\\" in decoded:
            self._error(404, "not_found", "endpoint not found")
            return
        parts = decoded.split("/")
        if len(parts) != 3 or parts[0] != "" or parts[1] != "assets" or not parts[2]:
            self._error(404, "not_found", "endpoint not found")
            return
        filename = parts[2]
        if filename.startswith(".") or any(part.startswith(".") for part in parts if part):
            self._error(404, "not_found", "endpoint not found")
            return
        mime_type = _STATIC_MIME_TYPES.get(Path(filename).suffix.lower())
        if mime_type is None or Path(filename).name != filename:
            self._error(404, "not_found", "endpoint not found")
            return
        static_dir = self.server.static_dir  # type: ignore[attr-defined]
        assets_dir = static_dir / "assets"
        self._serve_file(
            assets_dir / filename,
            allowed_root=assets_dir,
            content_type=mime_type,
            cache_control="public, max-age=31536000, immutable",
        )

    def _read_json(self) -> dict[str, Any] | None:
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            self._error(415, "unsupported_media_type", "Content-Type must be application/json")
            return None
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            self._error(400, "invalid_request", "Content-Length is required")
            return None
        if length > MAX_BODY_BYTES:
            self._error(413, "body_too_large", "request body exceeds the bounded limit")
            return None
        try:
            raw = self.rfile.read(length)
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            self._error(400, "invalid_request", "request body must be valid JSON")
            return None
        if not isinstance(value, dict):
            self._error(400, "invalid_request", "request body must be a JSON object")
            return None
        return value

    def _run_path(self) -> tuple[str, str] | None:
        path = urlsplit(self.path).path
        prefix = "/api/runs/"
        if not path.startswith(prefix):
            return None
        suffix = path[len(prefix):]
        parts = suffix.split("/")
        if len(parts) == 1 and parts[0]:
            return parts[0], "status"
        if len(parts) == 2 and parts[0] and parts[1] == "events":
            return parts[0], "events"
        if len(parts) == 2 and parts[0] and parts[1] == "cancel":
            return parts[0], "cancel"
        return "", "invalid"

    def do_GET(self) -> None:
        parsed_url = urlsplit(self.path)
        path = parsed_url.path
        if path.startswith("/api/"):
            try:
                if path == "/api/bootstrap":
                    self._json(200, self.manager.bootstrap())
                    return
                if path == "/api/prompt-library/search":
                    query = parse_qs(parsed_url.query, keep_blank_values=True)
                    if (
                        not set(query).issubset({"q", "limit", "role"})
                        or len(query.get("q", [])) != 1
                        or len(query.get("role", ["all"])) != 1
                    ):
                        self._error(400, "invalid_request", "prompt search parameters are invalid")
                        return
                    try:
                        limit = int(query.get("limit", ["12"])[-1])
                    except ValueError:
                        self._error(400, "invalid_request", "prompt search limit is invalid")
                        return
                    role = query.get("role", ["all"])[0]
                    if role not in {"all", "student", "lecturer"}:
                        self._error(400, "invalid_request", "prompt search role is invalid")
                        return
                    self._json(
                        200,
                        self.manager.search_prompt_library(
                            query["q"][0], limit, None if role == "all" else role
                        ),
                    )
                    return
                prompt_prefix = "/api/prompt-library/scenarios/"
                if path.startswith(prompt_prefix):
                    scenario_id = unquote(path[len(prompt_prefix):])
                    try:
                        scenario = self.manager.get_prompt_library_scenario(scenario_id)
                    except KeyError:
                        self._error(404, "not_found", "dataset scenario not found")
                        return
                    self._json(200, scenario)
                    return
                route = self._run_path()
                if route is None or route[1] not in {"status", "events"}:
                    self._error(404, "not_found", "endpoint not found")
                elif route[1] == "status":
                    self._json(200, self.manager.get_job(route[0]))
                else:
                    self._sse(route[0])
            except JobNotFound:
                self._error(404, "not_found", "run not found")
            except JobError:
                self._error(409, "not_ready", "TrustedSQL demo is not ready")
            except ValueError:
                self._error(400, "invalid_request", "request parameter is invalid")
            except Exception:
                self._error(500, "internal_error", "the request could not be completed safely")
            return

        try:
            if path == "/":
                self._serve_index()
            elif path.startswith("/assets/"):
                self._serve_asset(path)
            else:
                self._error(404, "not_found", "endpoint not found")
        except Exception:
            self._error(500, "internal_error", "the request could not be completed safely")

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        try:
            if path == "/api/runs":
                body = self._read_json()
                if body is None:
                    return
                required_fields = {"message", "conversationId"}
                allowed_fields = required_fields | {"mode", "replaceTurn"}
                if not required_fields.issubset(body) or not set(body).issubset(allowed_fields):
                    self._error(400, "invalid_request", "body fields are not allowlisted")
                    return
                if not isinstance(body["message"], str):
                    self._error(400, "invalid_request", "message must be a string")
                    return
                if body["conversationId"] is not None and not isinstance(body["conversationId"], str):
                    self._error(400, "invalid_request", "conversationId must be a string or null")
                    return
                mode = body.get("mode", "trustedsql")
                if mode not in {"trustedsql", "direct"}:
                    self._error(400, "invalid_request", "mode must be trustedsql or direct")
                    return
                replace_turn = body.get("replaceTurn")
                if replace_turn is not None and (type(replace_turn) is not int or replace_turn < 1):
                    self._error(400, "invalid_request", "replaceTurn must be a positive integer")
                    return
                self._json(
                    202,
                    self.manager.submit(
                        body["message"],
                        body["conversationId"],
                        mode,
                        replace_turn,
                    ),
                )
                return
            route = self._run_path()
            if route is None or route[1] != "cancel":
                self._error(404, "not_found", "endpoint not found")
            else:
                self._json(200, self.manager.cancel(route[0]))
        except JobNotFound:
            self._error(404, "not_found", "run not found")
        except ConversationNotFound:
            self._error(404, "conversation_not_found", "conversation not found; reset the chat")
        except JobNotReady:
            self._error(409, "not_ready", "TrustedSQL demo is not ready")
        except CancellationConflict:
            self._error(409, "not_cancellable", "running jobs cannot be cancelled safely")
        except JobCapacityError:
            self._error(429, "capacity_exhausted", "demo job capacity is temporarily full")
        except ConversationConflict:
            self._error(409, "conversation_conflict", "conversation cannot accept another turn")
        except JobError:
            self._error(409, "not_ready", "TrustedSQL demo is not ready")
        except ScenarioCatalogError:
            self._error(400, "invalid_request", "the prompt library is unavailable")
        except ValueError:
            self._error(400, "invalid_request", "request is invalid")
        except Exception:
            self._error(500, "internal_error", "the request could not be completed safely")

    def do_PUT(self) -> None:
        self._error(405, "method_not_allowed", "method is not allowed")

    def do_PATCH(self) -> None:
        self._error(405, "method_not_allowed", "method is not allowed")

    def do_DELETE(self) -> None:
        self._error(405, "method_not_allowed", "method is not allowed")

    def do_OPTIONS(self) -> None:
        self._error(405, "method_not_allowed", "method is not allowed")

    def do_TRACE(self) -> None:
        self._error(501, "method_not_implemented", "method is not supported")

    def do_CONNECT(self) -> None:
        self._error(501, "method_not_implemented", "method is not supported")

    def do_HEAD(self) -> None:
        self._error(405, "method_not_allowed", "method is not allowed")

    def _sse(self, run_id: str) -> None:
        query = parse_qs(urlsplit(self.path).query)
        last_event_id = self.headers.get("Last-Event-ID")
        after_values = query.get("after") or ([last_event_id] if last_event_id else [])
        after = 0
        if after_values:
            try:
                after = int(after_values[-1])
            except ValueError:
                self._error(400, "invalid_request", "after must be a non-negative integer")
                return
            if after < 0:
                self._error(400, "invalid_request", "after must be a non-negative integer")
                return
        # Acquire before headers and hold through the final status write. This
        # prevents terminal retention from deleting the job mid-stream.
        self.manager.acquire_stream(run_id)
        try:
            self.send_response(200)
            self._headers("text/event-stream; charset=utf-8")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            last_sent = after
            status_sent = False
            while True:
                events = self.manager.events_since(run_id, last_sent)
                for event in events:
                    sequence = event.get("streamSequence")
                    event_name = str(event.get("eventType", "module"))
                    if isinstance(sequence, int):
                        last_sent = max(last_sent, sequence)
                        prefix = f"id: {sequence}\n"
                    else:
                        prefix = ""
                    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write((prefix + f"event: {event_name}\ndata: {data}\n\n").encode("utf-8"))
                    self.wfile.flush()
                snapshot = self.manager.get_job(run_id)
                if snapshot["state"] in {"complete", "denied", "error", "cancelled"}:
                    if not status_sent:
                        status = {key: snapshot[key] for key in ("runId", "state", "finalResult", "error")}
                        data = json.dumps(status, ensure_ascii=False, separators=(",", ":"))
                        self.wfile.write((f"event: status\ndata: {data}\n\n").encode("utf-8"))
                        self.wfile.flush()
                        status_sent = True
                    return
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                self.manager.wait_for_update(run_id, last_sent, timeout=SSE_HEARTBEAT_SECONDS)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            # A disconnected EventSource is a normal end to a long-lived stream.
            return
        finally:
            self.manager.release_stream(run_id)


def main(argv: list[str] | None = None) -> int:
    default_repo_root = Path(__file__).resolve().parents[3]
    load_env_file(default_repo_root / ".env")
    parser = argparse.ArgumentParser(description="TrustedSQL-only local demo API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--static-dir", default=DEFAULT_STATIC_DIR, type=Path)
    parser.add_argument("--provider-config", default=os.environ.get("TRUSTEDSQL_DEMO_PROVIDER_CONFIG"))
    parser.add_argument("--repo-root", default=default_repo_root)
    args = parser.parse_args(argv)
    manager = DemoJobManager(args.repo_root, args.provider_config)
    server = create_server(
        manager,
        host=args.host,
        port=args.port,
        allow_remote=args.allow_remote,
        static_dir=args.static_dir,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
        manager.close()
    return 0


__all__ = ["DEFAULT_STATIC_DIR", "DemoHTTPServer", "DemoRequestHandler", "create_server", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
