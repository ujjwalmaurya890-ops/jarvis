"""Plugin architecture tests: manifests, registry, permissions, error handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.settings import Settings
from core.assistant import Jarvis
from memory.database import MemoryDB
from plugins.base import PluginError, PluginSpec, load_manifest, validate_manifest
from plugins.registry import PluginRegistry
from security.permissions import Capability, PermissionBroker
from tools.plugin_tools import register_plugin_tools
from tools.registry import ToolRegistry
from tools.system_tools import ToolContext, register_system_tools


def _broker(grant_memory=True):
    broker = PermissionBroker([])
    if grant_memory:
        broker.grant(Capability.FS_READ)
    return broker


# --------------------------------------------------------------------- manifests
def test_validate_manifest_rejects_missing_keys():
    with pytest.raises(PluginError):
        validate_manifest({"name": "x", "description": "d"})


def test_validate_manifest_rejects_bad_name():
    with pytest.raises(PluginError):
        validate_manifest({"name": "Not Valid", "description": "d", "schema": {}, "capability": "fs.read"})


def test_validate_manifest_rejects_unknown_capability():
    with pytest.raises(PluginError):
        validate_manifest({"name": "ok", "description": "d", "schema": {}, "capability": "system.root"})


def test_validate_manifest_ok():
    spec = validate_manifest({
        "name": "shout", "description": "d", "schema": {"type": "object", "properties": {"text": {"type": "string"}}},
        "capability": "fs.read", "module": "plugins.example_plugins", "entrypoint": "uppercase",
    })
    assert spec.name == "shout" and spec.arguments == ["text"]


def test_load_manifest_reads_file(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({
        "name": "noop", "description": "d", "schema": {}, "capability": "fs.read",
    }), encoding="utf-8")
    spec = load_manifest(path)
    assert spec.name == "noop"


def test_load_manifest_bad_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PluginError):
        load_manifest(path)


# --------------------------------------------------------------------- registry
def _spec(name="shout", capability="fs.read", module="plugins.example_plugins", entrypoint="uppercase"):
    return PluginSpec(name=name, description="d", schema={"type": "object"}, capability=capability,
                      module=module, entrypoint=entrypoint)


def test_registry_register_and_run():
    reg = PluginRegistry(_broker())
    reg.register(_spec(), lambda **kw: "YELL")
    out = reg.run("shout", {"text": "hi"})
    assert out["ok"] and out["result"] == "YELL"


def test_registry_disable_blocks_run():
    reg = PluginRegistry(_broker())
    reg.register(_spec(), lambda **kw: "ok")
    reg.disable("shout")
    out = reg.run("shout", {})
    assert not out["ok"] and "disabled" in out["error"]


def test_registry_import_and_run_bundled():
    reg = PluginRegistry(_broker())
    rec = reg.load_manifest(Path(__file__).resolve().parent.parent / "plugins" / "uppercase.json")
    assert rec.loaded
    out = reg.run("uppercase", {"text": "hello"})
    assert out["ok"] and out["result"] == "HELLO"


def test_registry_handles_crashing_plugin():
    reg = PluginRegistry(_broker())
    reg.register(_spec(entrypoint="boom"), None)
    # import the bundled boom handler directly
    import plugins.example_plugins as ep
    reg.plugins["shout"].handler = ep.boom
    reg.plugins["shout"].loaded = True
    out = reg.run("shout", {})
    assert not out["ok"] and "example plugin failure" in out["error"]


def test_registry_handles_bad_arguments():
    reg = PluginRegistry(_broker())
    reg.register(_spec(), lambda text: text)  # requires text
    out = reg.run("shout", {"wrong": 1})
    assert not out["ok"] and "Bad arguments" in out["error"]


def test_registry_permission_denied():
    reg = PluginRegistry(_broker(grant_memory=False))
    reg.register(_spec(), lambda **kw: "ok")
    out = reg.run("shout", {})
    assert not out["ok"] and "not granted" in out["error"]


def test_load_folder_skips_broken_manifests(tmp_path):
    (tmp_path / "good.json").write_text(json.dumps({
        "name": "good", "description": "d", "schema": {}, "capability": "fs.read",
    }), encoding="utf-8")
    (tmp_path / "bad.json").write_text("{oops", encoding="utf-8")
    reg = PluginRegistry(_broker())
    loaded = reg.load_folder(tmp_path)
    assert loaded == ["good"]
    assert "good" in reg.plugins and "bad" not in reg.plugins


# --------------------------------------------------------------------- tools
def _registry(plugin_registry, grant_memory=True) -> ToolRegistry:
    settings = Settings()
    broker = PermissionBroker([])
    broker.grant(Capability.FS_READ)
    broker.grant(Capability.PLUGINS)
    if grant_memory:
        broker.grant(Capability.FS_READ)
    reg = ToolRegistry(broker, confirm=lambda *a: True)
    register_system_tools(reg, ToolContext(settings=settings, launcher=None, files=None,
                                           notes=None, memory=None, browser=None, index=None))
    register_plugin_tools(reg, plugin_registry)
    return reg


def test_plugin_status_and_run_tools():
    plugin_registry = PluginRegistry(_broker())
    plugin_registry.register(_spec(), lambda **kw: "YELL")
    reg = _registry(plugin_registry)
    status = reg.execute("plugin_status", {})
    assert status.ok and status.data["plugins"][0]["name"] == "shout"
    run = reg.execute("plugin_run", {"name": "shout", "arguments": {"text": "x"}})
    assert run.ok and run.data["result"] == "YELL"


def test_plugin_run_tool_reports_disabled():
    plugin_registry = PluginRegistry(_broker())
    plugin_registry.register(_spec(), lambda **kw: "YELL")
    plugin_registry.disable("shout")
    reg = _registry(plugin_registry)
    run = reg.execute("plugin_run", {"name": "shout", "arguments": {}})
    assert not run.ok


# --------------------------------------------------------------------- boot
class _FakeProvider:
    name = "fake-local"

    def probe(self):
        return True, "mock"

    def models(self):
        return ["mock"]

    def complete(self, messages, **kwargs):
        from ai.base_provider import Completion

        return Completion(text="ok", provider=self.name)


def test_boot_loads_bundled_plugins_and_registers_tools(tmp_path):
    settings = Settings()
    settings.automation.allowed_dirs = [str(tmp_path)]
    jarvis = Jarvis(settings)
    db = MemoryDB(tmp_path / "b.db")
    jarvis.boot({"db": db, "providers": [_FakeProvider()]})
    assert "plugin_status" in jarvis.registry.tools
    assert "plugin_run" in jarvis.registry.tools
    names = {p["name"] for p in jarvis.plugin_registry.list_plugins()}
    assert {"uppercase", "word_count"} <= names
    jarvis.shutdown()
