"""Common AI provider interface.

The rest of the app talks to `AIProvider`, never to Ollama or Gemini directly.
That keeps model swapping trivial and makes the fallback chain readable:

    router -> provider chain -> first available provider answers

Every provider returns a `Completion`.  Providers must never raise into the
assistant loop; they return `Completion(error=...)` instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw: str = ""


@dataclass
class Completion:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    latency: float = 0.0
    tokens: int = 0
    error: str | None = None
    used_fallback: bool = False
    grounded: bool = False           # answer included live web results
    sources: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and (bool(self.text.strip()) or bool(self.tool_calls))


@runtime_checkable
class AIProvider(Protocol):
    name: str

    def probe(self) -> tuple[bool, str]:
        """Cheap availability check. Returns (available, human_status)."""

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
        ...

    def models(self) -> list[str]:
        ...


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model reply.

    Small local models wrap JSON in prose or fences far more often than large
    ones, so we try: whole string -> fenced block -> outermost braces.
    Returns None instead of raising; the router then falls back to intent
    heuristics.
    """
    if not text:
        return None
    candidates: list[str] = [text.strip()]
    candidates += [m.strip() for m in _JSON_BLOCK.findall(text)]
    fenced = re.findall(r"```(.*?)```", text, re.S)
    candidates += [f.strip() for f in fenced]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for cand in candidates:
        cand = cand.strip().rstrip(",")
        if not cand.startswith("{"):
            continue
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def validate_tool_call(
    name: str, arguments: dict[str, Any], schema: dict[str, type],
    required: set[str] | None = None,
) -> tuple[bool, str]:
    """Type-check model-supplied arguments BEFORE anything executes.

    `schema` maps parameter name -> allowed python type.  Unknown keys are
    dropped, missing required keys are reported.  Nothing here executes.
    """
    if not isinstance(arguments, dict):
        return False, "arguments were not an object"
    for key, expected in schema.items():
        if key not in arguments and key in (required if required is not None else schema):
            return False, f"missing '{key}'"
        if key not in arguments:
            continue
        value = arguments[key]
        if expected is str:
            if not isinstance(value, str) or len(value) > 8000:
                return False, f"'{key}' must be a short string"
        elif expected is int and (not isinstance(value, int) or isinstance(value, bool)):
            try:
                raw = str(value).strip()
                if not re.fullmatch(r"-?\d{1,9}", raw):
                    raise ValueError("not a bounded integer")
                arguments[key] = int(raw)
            except (TypeError, ValueError):
                return False, f"'{key}' must be an integer"
        elif expected is bool and not isinstance(value, bool):
            raw = str(value).strip().lower()
            if raw not in {"1", "0", "true", "false"}:
                return False, f"'{key}' must be true or false"
            arguments[key] = raw in {"1", "true"}
        elif expected is list and (not isinstance(value, list) or len(value) > 20):
            return False, f"'{key}' must be a list (max 20 items)"
    for key in list(arguments.keys()):
        if key not in schema:
            arguments.pop(key)  # silently drop invented parameters
    return True, ""


def safe_preview(text: str, limit: int = 280) -> str:
    """Single-line, length-capped preview for logs and the UI."""
    flat = re.sub(r"\s+", " ", text or "").strip()
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
