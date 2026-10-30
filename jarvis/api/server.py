"""Authenticated loopback API for the existing React/Vite dashboard.

Only two operations are exposed: status and typed commands. Mic, permissions,
secrets, shutdown and arbitrary tool endpoints are deliberately NOT exposed.
The command path goes through Jarvis.handle_text(), so the same router, allow-
lists and confirmation rules apply. With no GUI confirmation handler attached,
dangerous actions are denied by default instead of auto-approved.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.api")

ALLOWED_ORIGINS = {
    "http://127.0.0.1:5173", "http://localhost:5173",
    "http://127.0.0.1:4173", "http://localhost:4173",
}
MAX_BODY = 4096
MAX_COMMAND = 2000
# Commands are expensive (they run the model), so the API gets a small token
# bucket: 20 commands per minute per token.  Health checks are not limited.
RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW = 60.0


class LocalAPI(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self, assistant: Any, boot_report: dict[str, Any],
        token_path: Path, port: int = 8765,
    ) -> None:
        if port != 0 and not 1024 <= port <= 65535:
            raise ValueError("The API port must be between 1024 and 65535.")
        self.assistant = assistant
        self.boot_report = boot_report
        self.token_path = Path(token_path)
        self.command_gate = threading.BoundedSemaphore(1)
        self._rate_lock = threading.Lock()
        self._recent: dict[str, deque] = defaultdict(deque)
        super().__init__(("127.0.0.1", port), LocalAPIHandler)
        try:
            self.token = secrets.token_urlsafe(32)
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            # `os.open` honours 0600 on POSIX; on Windows the file inherits the
            # current user's project-folder ACL. Never place the project in Public.
            fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(self.token + "\n")
            if os.name != "nt":
                self.token_path.chmod(0o600)
        except OSError:
            self.server_close()
            raise

    def _allow(self, token: str) -> bool:
        """Sliding-window rate limit for command calls (20/min/token)."""
        now = time.monotonic()
        with self._rate_lock:
            hits = self._recent[token]
            while hits and now - hits[0] > RATE_LIMIT_WINDOW:
                hits.popleft()
            if len(hits) >= RATE_LIMIT_MAX:
                return False
            hits.append(now)
            return True


class LocalAPIHandler(BaseHTTPRequestHandler):
    server: LocalAPI
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        # Do not log Authorization, request bodies, or even user query strings.
        log.debug("API request %s", self.path.split("?", 1)[0])

    def _trusted_browser(self) -> bool:
        host = self.headers.get("Host", "")
        if host not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
            return False  # DNS-rebinding host header, even on a loopback socket
        origin = self.headers.get("Origin")
        return origin is None or origin in ALLOWED_ORIGINS

    def _authorized(self) -> bool:
        bearer = self.headers.get("Authorization", "")
        if not bearer.startswith("Bearer "):
            return False
        try:
            return hmac.compare_digest(bearer[7:], self.server.token)
        except TypeError:  # Non-ASCII header values are never valid tokens.
            return False

    def _respond(self, status: int, data: dict[str, Any] | None = None) -> None:
        body = b"" if data is None else json.dumps(data, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Vary", "Origin")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.close_connection = True
        if body:
            self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._trusted_browser() or self.headers.get("Origin") is None:
            self._respond(HTTPStatus.FORBIDDEN, {"error": "Only the local dashboard may access this API."})
            return
        self._respond(HTTPStatus.NO_CONTENT)

    def do_GET(self) -> None:  # noqa: N802
        if not self._trusted_browser():
            self._respond(HTTPStatus.FORBIDDEN, {"error": "Local dashboard origin required."})
            return
        if self.path == "/api/health":
            assistant = self.server.assistant
            self._respond(HTTPStatus.OK, {
                "app": "JARVIS", "api": "loopback-only", "mode": "typed commands",
                "state": assistant.state.state.value,
                "mic": bool(assistant.mic and assistant.mic.is_running),
                "providers": self.server.boot_report.get("providers", []),
                "confirmations": "dangerous actions require the desktop UI and are denied from the API",
            })
            return
        if self.path == "/api/session":
            if not self._authorized():
                self._respond(HTTPStatus.UNAUTHORIZED, {"error": "The token is missing or incorrect. Read data/api-token on this PC."})
                return
            self._respond(HTTPStatus.OK, {"authorized": True})
            return
        self._respond(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint."})

    def do_POST(self) -> None:  # noqa: N802
        if not self._trusted_browser():
            self._respond(HTTPStatus.FORBIDDEN, {"error": "Local dashboard origin required."})
            return
        if self.path != "/api/command":
            self._respond(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint."})
            return
        if not self._authorized():
            self._respond(HTTPStatus.UNAUTHORIZED, {"error": "The token is missing or incorrect."})
            return
        if not self.server._allow(self.server.token):
            self._respond(HTTPStatus.TOO_MANY_REQUESTS, {"error": "Too many commands. Wait a moment and try again."})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            self._respond(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Send application/json."})
            return
        try:
            size = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            size = -1
        if size < 1 or size > MAX_BODY or self.headers.get("Transfer-Encoding"):
            self._respond(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Body is empty or too large (max 4096 bytes)."})
            return
        try:
            data = json.loads(self.rfile.read(size).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self._respond(HTTPStatus.BAD_REQUEST, {"error": "Invalid UTF-8 JSON."})
            return
        text = data.get("text", "") if isinstance(data, dict) else ""
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_COMMAND:
            self._respond(HTTPStatus.BAD_REQUEST, {"error": "'text' must be a nonempty string of at most 2000 characters."})
            return
        try:
            if not self.server.command_gate.acquire(blocking=False):
                self._respond(HTTPStatus.TOO_MANY_REQUESTS, {"error": "JARVIS is already handling a command. Try again in a moment."})
                return
            metadata: dict[str, Any] = {}
            assistant = self.server.assistant
            unsubscribe = lambda: None
            try:
                if hasattr(assistant, "on_event"):
                    unsubscribe = assistant.on_event(
                        lambda event, data: metadata.update(data) if event == "reply" else None
                    )
                reply = assistant.handle_text(text.strip(), spoken=False)
            finally:
                try:
                    unsubscribe()
                finally:
                    self.server.command_gate.release()
        except Exception:
            log.exception("Local command failed (input omitted)")
            self._respond(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Command failed. See the local terminal for details."})
            return
        info = metadata.get("meta") or {}
        self._respond(HTTPStatus.OK, {
            "reply": reply, "source": str(info.get("provider") or metadata.get("source") or "python"),
            "mode": "local-first", "sources": info.get("sources") or [],
        })


def serve_api(assistant: Any, boot_report: dict[str, Any], token_path: Path, port: int = 8765) -> None:
    server = LocalAPI(assistant, boot_report, token_path, port=port)
    print(f"JARVIS API listening on http://127.0.0.1:{port}")
    print(f"Local dashboard token saved to: {token_path}")
    print("No tool confirmations are accepted from the API. Press Ctrl+C to stop.")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nStopping the local API...")
    finally:
        server.server_close()