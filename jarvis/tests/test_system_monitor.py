"""System monitoring tests: memory, disk, services, models, low-memory mode."""

from __future__ import annotations

import pytest

from config.settings import Settings
from security.permissions import Capability, PermissionBroker
from tools.registry import ToolRegistry
from tools.system_monitor import (
    ResourceGuard,
    disk_status,
    is_low_memory,
    memory_status,
    model_availability,
    service_status,
    system_snapshot,
)
from tools.system_tools import ToolContext, register_system_tools


def test_memory_status_shape():
    mem = memory_status()
    assert mem["ram_available_mb"] >= 0
    assert mem["ram_total_mb"] >= 0
    assert "ram_percent" in mem


def test_is_low_memory_thresholds():
    assert is_low_memory(threshold_mb=10**9) is True
    assert is_low_memory(threshold_mb=0) is False


def test_disk_status_reads_space():
    disk = disk_status()
    assert "free_gb" in disk and "total_gb" in disk


def test_service_status_is_windows_only():
    result = service_status("SomeService")
    if result.get("supported"):
        pytest.skip("running on Windows, covered by the Windows path")
    assert result["supported"] is False


def test_model_availability_parses_ollama(monkeypatch):
    import requests

    class _Resp:
        status_code = 200

        def json(self):
            return {"models": [{"name": "llama3.2"}, {"name": "nomic-embed-text"}]}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    result = model_availability(names=["llama3.2", "missing"])
    assert result["available"] is True
    assert result["models"] == ["llama3.2"]
    assert result["missing"] == ["missing"]


def test_model_availability_unreachable(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", boom)
    result = model_availability()
    assert result["available"] is False and "reason" in result


def test_system_snapshot_has_sections():
    snap = system_snapshot()
    assert {"platform", "memory", "low_memory", "disk"} <= set(snap)


def test_resource_guard_gates_heavy_features():
    guard = ResourceGuard(threshold_mb=10**9)  # pretend RAM is very tight
    assert guard.is_low is True
    assert guard.heavy_features_allowed() is False
    loose = ResourceGuard(threshold_mb=0)
    assert loose.heavy_features_allowed() is True


# --------------------------------------------------------------------- tools
def _registry() -> ToolRegistry:
    settings = Settings()
    broker = PermissionBroker([])
    broker.grant(Capability.FS_READ)
    reg = ToolRegistry(broker, confirm=lambda *a: True)
    register_system_tools(reg, ToolContext(settings=settings, launcher=None, files=None,
                                           notes=None, memory=None, browser=None, index=None))
    return reg


def test_monitor_status_tool():
    reg = _registry()
    result = reg.execute("monitor_status", {})
    assert result.ok and "RAM free" in result.speech
    assert "memory" in result.data


def test_service_status_tool_reports_support():
    reg = _registry()
    result = reg.execute("service_status", {"name": "SomeService"})
    # On Linux the tool explains it is Windows-only via the say() path.
    assert result.ok
    assert "Windows-only" in result.speech or result.data.get("supported") is False
