"""Memory & privacy tests: inspect, export, delete, cloud consent."""

from __future__ import annotations

import json

from config.settings import Settings
from memory.database import MemoryDB
from security.permissions import Capability, PermissionBroker
from tools.file_manager import FileManager
from tools.memory_tools import register_memory_tools
from tools.registry import ToolRegistry
from tools.system_tools import ToolContext, register_system_tools


class _Confirm:
    def __init__(self, result=True):
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def __call__(self, summary, capability):
        self.calls.append((summary, capability))
        return self.result


def _registry(tmp_path, confirm_result=True):
    settings = Settings()
    settings.automation.allowed_dirs = [str(tmp_path)]
    broker = PermissionBroker([str(tmp_path)])
    broker.grant(Capability.FS_READ)
    broker.grant(Capability.FS_WRITE)
    confirm = _Confirm(confirm_result)
    reg = ToolRegistry(broker, confirm=confirm)
    db = MemoryDB(tmp_path / "m.db")
    files = FileManager([str(tmp_path)], tmp_path / "workspace")
    ctx = ToolContext(settings=settings, launcher=None, files=files, notes=None,
                      memory=db, browser=None, index=None)
    register_system_tools(reg, ctx)
    register_memory_tools(reg, ctx)
    return reg, db, files, confirm


def test_memory_stats(tmp_path):
    reg, db, files, confirm = _registry(tmp_path)
    db.add_turn("s", "user", "hello world")
    res = reg.execute("memory_stats", {})
    assert res.ok and res.data["turns"] == 1


def test_memory_list_shows_turns(tmp_path):
    reg, db, files, confirm = _registry(tmp_path)
    db.add_turn("s", "user", "what time is it")
    res = reg.execute("memory_list", {"limit": 5})
    assert res.ok and res.data["turns"][0]["text"] == "what time is it"


def test_memory_export_writes_json(tmp_path):
    reg, db, files, confirm = _registry(tmp_path)
    db.add_turn("s", "user", "remember this")
    res = reg.execute("memory_export", {"name": "backup"})
    assert res.ok
    path = res.data["path"]
    payload = json.loads(open(path, encoding="utf-8").read())
    assert payload["turns"][0]["text"] == "remember this"
    assert "backup" in path


def test_memory_delete_single_turn(tmp_path):
    reg, db, files, confirm = _registry(tmp_path)
    tid = db.add_turn("s", "user", "delete me")
    db.add_turn("s", "assistant", "kept")
    res = reg.execute("memory_delete", {"scope": "turn", "turn_id": tid})
    assert res.ok
    remaining = db.recent_turns(limit=10)
    assert all(t["text"] != "delete me" for t in remaining)
    assert any(t["text"] == "kept" for t in remaining)


def test_memory_delete_conversation(tmp_path):
    reg, db, files, confirm = _registry(tmp_path)
    db.add_turn("s", "user", "one")
    db.add_turn("s", "user", "two")
    res = reg.execute("memory_delete", {"scope": "conversation"})
    assert res.ok
    assert db.recent_turns(limit=10) == []


def test_memory_delete_memory_keeps_grants(tmp_path):
    reg, db, files, confirm = _registry(tmp_path)
    db.add_turn("s", "user", "one")
    db.remember("favourite", "tea")
    res = reg.execute("memory_delete", {"scope": "memory"})
    assert res.ok
    assert db.recent_turns(limit=10) == []
    assert db.all_facts() == []


def test_cloud_status_reports_disabled(tmp_path):
    reg, db, files, confirm = _registry(tmp_path)
    res = reg.execute("cloud_status", {})
    assert res.ok and res.data["cloud_ai"] is False


def test_cloud_consent_disable(tmp_path):
    reg, db, files, confirm = _registry(tmp_path)
    res = reg.execute("cloud_consent", {"allow": False})
    assert res.ok and res.data["cloud_ai"] is False


def test_cloud_consent_requires_confirmation(tmp_path):
    reg, db, files, confirm = _registry(tmp_path, confirm_result=False)
    res = reg.execute("cloud_consent", {"allow": True, "reason": "try gemini"})
    assert "consent" in res.speech.lower()
    assert not reg.broker.is_granted(Capability.CLOUD_AI)


def test_cloud_consent_grants_when_confirmed(tmp_path):
    reg, db, files, confirm = _registry(tmp_path, confirm_result=True)
    res = reg.execute("cloud_consent", {"allow": True, "reason": "try gemini"})
    assert res.ok and reg.broker.is_granted(Capability.CLOUD_AI)
