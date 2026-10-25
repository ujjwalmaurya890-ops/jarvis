"""Tool registry: everything JARVIS can actually *do*.

Each tool declares (a) a JSON schema the model may call, (b) the capability it
needs, (c) an argument-validation schema, and (d) a handler.  Handlers receive
already-validated, already-permission-checked arguments.  Tools must not reach
around the registry to run shell commands or open sockets.
"""

from __future__ import annotations

import logging
import inspect
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ai.base_provider import validate_tool_call
from security.permissions import Capability, PermissionBroker

log = logging.getLogger("jarvis.tools")


@dataclass
class ToolResult:
    ok: bool
    speech: str = ""                     # what JARVIS says out loud
    display: str = ""                    # richer text for the UI (markdown ok)
    sources: list[dict[str, str]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration: float = 0.0

    @classmethod
    def fail(cls, error: str) -> "ToolResult":
        return cls(False, speech=error, error=error)

    @classmethod
    def say(cls, text: str, **kw: Any) -> "ToolResult":
        return cls(True, speech=text, display=text, **kw)


@dataclass
class Tool:
    name: str
    description: str
    capability: Capability
    parameters: dict[str, type]
    handler: Callable[..., ToolResult]
    schema: dict[str, Any] = field(default_factory=dict)
    dangerous: bool = False
    examples: list[str] = field(default_factory=list)

    def required_parameters(self) -> set[str]:
        signature = inspect.signature(self.handler)
        return {key for key in self.parameters if signature.parameters[key].default is inspect.Parameter.empty}

    def openai_schema(self) -> dict[str, Any]:
        props = {k: {"type": {str: "string", int: "integer", bool: "boolean", list: "array"}[v]} for k, v in self.parameters.items()}
        return {
            "type": "object",
            "properties": props,
            "required": sorted(self.required_parameters()),
        }


SAFE_QUERY = re.compile(r"[^0-9A-Za-z\u0900-\u097F ,.,:?'\"!()\-/+#&%+=<>]", re.UNICODE)


def sanitise_query(text: str, limit: int = 200) -> str:
    """Strip control/injection characters from anything the model wants to search."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)          # drop tags from scraped pages
    cleaned = SAFE_QUERY.sub("", cleaned)
    cleaned = re.sub(r"(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|above)\s+instructions?", "", cleaned, flags=re.I)
    return cleaned.strip()[:limit]


class ToolRegistry:
    def __init__(self, broker: PermissionBroker, confirm: Callable[[str, str], bool] | None = None) -> None:
        self.broker = broker
        self.tools: dict[str, Tool] = {}
        # confirm(summary, capability) -> True if the human pressed Yes.
        self.confirm = confirm or (lambda summary, cap: False)
        self.call_log: list[dict[str, Any]] = []

    # ---- registration --------------------------------------------------------
    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def tool(self, name: str, description: str, capability: Capability, parameters: dict[str, type], *, dangerous: bool = False, examples: list[str] | None = None):  # noqa: ANN201
        def deco(fn: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
            self.register(
                Tool(
                    name=name,
                    description=description,
                    capability=capability,
                    parameters=parameters,
                    handler=fn,
                    dangerous=dangerous,
                    examples=examples or [],
                )
            )
            return fn

        return deco

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.openai_schema(),
                },
            }
            for t in self.tools.values()
        ]

    # ---- execution -----------------------------------------------------------
    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        offline: bool = False,
        privacy_mode: bool = False,
    ) -> ToolResult:
        t0 = time.monotonic()
        tool = self.tools.get(name)
        if tool is None:
            return ToolResult.fail(f"I don't have a tool called '{name}'.")

        arguments = dict(arguments) if isinstance(arguments, dict) else arguments
        valid, why = validate_tool_call(name, arguments, tool.parameters, tool.required_parameters())
        if not valid:
            return ToolResult.fail(f"I can't run '{name}': {why}.")

        decision = self.broker.request(
            tool.capability,
            summary=_summarise(tool, arguments),
            offline=offline,
            privacy_mode=privacy_mode,
        )
        if decision.needs_confirmation:
            if not self.confirm(decision.summary or _summarise(tool, arguments), tool.capability.value):
                return ToolResult.fail("Cancelled - you did not confirm that action.")
            # A grant for this process does not bypass later confirmations for
            # dangerous capabilities. The broker always asks again for those.
            self.broker.grant(tool.capability, "session")
        elif not decision.allowed:
            return ToolResult.fail(f"Not allowed: {decision.reason}")

        try:
            result = tool.handler(**arguments)
        except TypeError as exc:
            log.exception("bad arguments for %s", name)
            return ToolResult.fail(f"Bad arguments for {name}: {exc}")
        except Exception as exc:  # a tool bug must never crash the assistant
            log.exception("tool %s crashed", name)
            return ToolResult.fail(f"{name} failed: {exc}")
        finally:
            self.broker.consume_one_shot(tool.capability)
        result.duration = time.monotonic() - t0
        self.call_log.append({"tool": name, "args": arguments, "ok": result.ok, "at": time.time()})
        if len(self.call_log) > 200:
            del self.call_log[: len(self.call_log) - 200]
        return result


_HOST = re.compile(r"^(?:https?://)?(?:www\.)?([a-z0-9.-]+)", re.I)


def _summarise(tool: Tool, args: dict[str, Any]) -> str:
    parts = [f"{k}={str(v)[:60]}" for k, v in args.items()]
    return f"{tool.name}({', '.join(parts)})"
