"""Tools to inspect and run plugins through the approved registry."""

from __future__ import annotations

import logging

from security.permissions import Capability
from tools.registry import ToolResult, ToolRegistry

log = logging.getLogger("jarvis.plugins.tools")


def register_plugin_tools(reg: ToolRegistry, plugin_registry) -> None:
    """Register the plugin_status and plugin_run tools against a registry."""

    @reg.tool(
        "plugin_status",
        "List installed plugins and whether they are enabled.",
        Capability.FS_READ,
        {},
    )
    def plugin_status() -> ToolResult:
        plugins = plugin_registry.list_plugins()
        if not plugins:
            return ToolResult.say("No plugins are installed yet.")
        enabled = sum(1 for p in plugins if p["enabled"])
        lines = [f"- {p['name']} (enabled={p['enabled']}, loaded={p['loaded']})" for p in plugins]
        return ToolResult(
            True,
            speech=f"{enabled} of {len(plugins)} plugin(s) enabled.",
            display="\n".join(lines),
            data={"plugins": plugins},
        )

    @reg.tool(
        "plugin_run",
        "Run an enabled plugin by name.",
        Capability.PLUGINS,
        {"name": str, "arguments": dict},
    )
    def plugin_run(name: str, arguments: dict) -> ToolResult:
        result = plugin_registry.run(str(name), arguments if isinstance(arguments, dict) else {})
        if not result.get("ok"):
            return ToolResult.fail(str(result.get("error", "Plugin failed.")))
        return ToolResult(
            True,
            speech="Plugin ran.",
            data={"plugin": result.get("plugin"), "result": result.get("result")},
        )
