"""Actual loopback HTTP requests against the authenticated API, no Ollama/audio."""

import http.client
import json
import threading
from contextlib import contextmanager
from types import SimpleNamespace

from api.server import LocalAPI


class FakeAssistant:
    def __init__(self):
        self.mic = None
        self.state = SimpleNamespace(state=SimpleNamespace(value="offline"))
        self.commands = []

    def handle_text(self, text, *, spoken):
        self.commands.append((text, spoken))
        return f"handled: {text}"


@contextmanager
def running_api(tmp_path):
    assistant = FakeAssistant()
    server = LocalAPI(assistant, {"providers": [{"name": "ollama", "ok": False, "status": "not running"}]}, tmp_path / "api-token", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, assistant
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def send(server, method, path, *, origin=None, host=None, token=None, body=None):
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    if host is not None:
        headers["Host"] = host
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    response = conn.getresponse()
    code, data, cors = response.status, response.read(), response.getheader("Access-Control-Allow-Origin")
    conn.close()
    return code, json.loads(data) if data else {}, cors


def test_health_and_session_authentication(tmp_path):
    with running_api(tmp_path) as (server, assistant):
        assert server.server_address[0] == "127.0.0.1"
        assert (tmp_path / "api-token").read_text().strip() == server.token
        origin = "http://127.0.0.1:5173"
        code, health, cors = send(server, "GET", "/api/health", origin=origin)
        assert code == 200 and health["mic"] is False and cors == origin
        assert "token" not in json.dumps(health)
        assert send(server, "GET", "/api/session", origin=origin)[0] == 401
        assert send(server, "GET", "/api/session", origin=origin, token=server.token)[1]["authorized"]


def test_command_requires_token_and_valid_body(tmp_path):
    with running_api(tmp_path) as (server, assistant):
        origin = "http://127.0.0.1:5173"
        assert send(server, "POST", "/api/command", origin=origin, body={"text": "help"})[0] == 401
        assert not assistant.commands
        assert send(server, "POST", "/api/command", origin=origin, token=server.token, body={"text": ""})[0] == 400
        code, data, _ = send(server, "POST", "/api/command", origin=origin, token=server.token, body={"text": "help"})
        assert code == 200 and data["reply"] == "handled: help"
        assert assistant.commands == [("help", False)]


def test_origin_and_host_rebinding_blocked(tmp_path):
    with running_api(tmp_path) as (server, assistant):
        for origin in ("https://evil.example", "null", "http://127.0.0.1:6666"):
            code, data, cors = send(server, "POST", "/api/command", origin=origin, token=server.token, body={"text": "hello"})
            assert code == 403 and cors is None
        assert send(server, "GET", "/api/health", host="evil.example")[0] == 403
        assert not assistant.commands


def test_preflight_only_accepts_local_dashboard(tmp_path):
    with running_api(tmp_path) as (server, _):
        assert send(server, "OPTIONS", "/api/command", origin="http://localhost:5173")[0] == 204
        assert send(server, "OPTIONS", "/api/command", origin="https://evil.example")[0] == 403


def test_command_burst_is_rate_limited(tmp_path):
    with running_api(tmp_path) as (server, assistant):
        origin = "http://127.0.0.1:5173"
        codes = [
            send(server, "POST", "/api/command", origin=origin, token=server.token, body={"text": f"cmd {i}"})[0]
            for i in range(25)
        ]
        assert codes[:20] == [200] * 20
        assert all(code == 429 for code in codes[20:])
        # The burst beyond the limit must not have been executed.
        assert len(assistant.commands) == 20