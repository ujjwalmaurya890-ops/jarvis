"""Ollama provider - the default local brain.

Talks to the local HTTP API at 127.0.0.1:11434 (no SDK, no telemetry, no key).
Endpoints used (Ollama 0.3+):
    GET  /api/tags      -> which models are installed
    POST /api/generate  -> completion (stream=false)
    POST /api/chat      -> multi-turn chat

Structured output is requested with "format": "json" when the caller needs
JSON, which qwen2.5 follows far more reliably than prompt-only instructions.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from ai.base_provider import Completion, extract_json

log = logging.getLogger("jarvis.ollama")


class OllamaError(Exception):
    pass


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        host: str = "http://127.0.0.1:11434",
        model: str = "qwen2.5:1.5b",
        fallback_model: str = "llama3.2:1b",
        keep_alive: str = "2m",
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.fallback_model = fallback_model
        self.keep_alive = keep_alive
        self.last_error: str | None = None
        self._session = requests.Session()
        self._session.trust_env = False  # never send loopback traffic through a proxy

    # ---- availability --------------------------------------------------------
    def probe(self) -> tuple[bool, str]:
        try:
            r = self._session.get(f"{self.host}/api/tags", timeout=2.5, allow_redirects=False)
        except requests.exceptions.ConnectionError:
            self.last_error = "Ollama is not running."
            return False, "Ollama service not reachable on 127.0.0.1:11434. Start it: `ollama serve`"
        except requests.RequestException as exc:
            self.last_error = str(exc)
            return False, f"Ollama check failed: {exc}"
        if r.status_code != 200:
            return False, f"Ollama answered HTTP {r.status_code}."
        try:
            installed = [m.get("name", "") for m in r.json().get("models", [])]
        except (ValueError, AttributeError):
            return False, "Ollama returned unexpected JSON."
        for candidate in (self.model, self.fallback_model):
            if any(candidate == n or candidate in n for n in installed):
                self.last_error = None
                return True, f"Ollama ready - using {candidate}"
        return (
            False,
            f"Ollama is running but '{self.model}' is not downloaded. "
            f"Run: ollama pull {self.model}",
        )

    def models(self) -> list[str]:
        try:
            r = self._session.get(f"{self.host}/api/tags", timeout=2.5, allow_redirects=False)
            return [m.get("name", "") for m in r.json().get("models", [])]
        except Exception:
            return []

    # ---- generation ----------------------------------------------------------
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.4,
        max_tokens: int = 400,
        timeout: float = 45.0,
        expect_json: bool = False,
    ) -> Completion:
        t0 = time.monotonic()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 2048},
        }
        if expect_json:
            payload["format"] = "json"
        if tools:
            payload["tools"] = tools

        tried: list[str] = []
        for model in (self.model, self.fallback_model):
            if not model or model in tried:
                continue
            tried.append(model)
            payload["model"] = model
            try:
                r = self._session.post(f"{self.host}/api/chat", json=payload, timeout=timeout, allow_redirects=False)
            except requests.exceptions.Timeout:
                self.last_error = f"Ollama timed out after {timeout:.0f}s with '{model}'."
                continue
            except requests.RequestException as exc:
                self.last_error = f"Ollama request failed: {exc}"
                continue
            if r.status_code == 404:
                self.last_error = f"Model '{model}' not found. Run: ollama pull {model}"
                continue
            if r.status_code >= 400:
                self.last_error = f"Ollama HTTP {r.status_code}: {r.text[:200]}"
                continue
            try:
                data = r.json()
            except ValueError:
                self.last_error = "Ollama returned invalid JSON."
                continue
            message = data.get("message") or {}
            text = (message.get("content") or "").strip()
            raw_calls = message.get("tool_calls") or []
            calls = _parse_tool_calls(raw_calls)
            if not calls and expect_json and text:
                parsed = extract_json(text)
                if parsed and parsed.get("tool"):
                    calls = [_tool_call_from(parsed)]
                    text = str(parsed.get("say") or "").strip()
            return Completion(
                text=text,
                tool_calls=calls,
                provider="ollama",
                model=model,
                latency=time.monotonic() - t0,
                tokens=int(data.get("eval_count") or 0),
                used_fallback=model != self.model,
            )
        return Completion(
            error=self.last_error or "Ollama did not answer.",
            provider="ollama",
            model=self.model,
            latency=time.monotonic() - t0,
        )

    def unload(self) -> None:
        """Free the model's RAM when the user goes idle (keeps 8 GB machines sane)."""
        try:
            self._session.post(
                f"{self.host}/api/generate",
                json={"model": self.model, "keep_alive": "0"},
                timeout=5,
            )
        except requests.RequestException:
            pass


def _tool_call_from(parsed: dict[str, Any]):  # noqa: ANN202
    from ai.base_provider import ToolCall

    args = parsed.get("arguments") or parsed.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    return ToolCall(name=str(parsed.get("tool", "")), arguments=args, raw=json.dumps(parsed))


def _parse_tool_calls(raw: list[dict[str, Any]]) -> list[Any]:
    out = []
    for call in raw:
        fn = (call or {}).get("function") or {}
        name = str(fn.get("name") or "")
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"text": args}
        if not isinstance(args, dict):
            args = {}
        if name:
            out.append(_tool_call_from({"tool": name, "arguments": args}))
    return out
