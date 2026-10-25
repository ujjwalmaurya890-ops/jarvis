"""Regressions for errors that the original unit suite missed."""

from ai.base_provider import Completion
from config.settings import Settings
from core.assistant import Jarvis
from memory.database import MemoryDB
from security.permissions import Capability, PermissionBroker
from tools.registry import Tool, ToolRegistry, ToolResult


class OfflineProvider:
    name = "fake-local"

    def probe(self):
        return True, "mock provider"

    def models(self):
        return ["mock"]

    def complete(self, messages, **kwargs):
        return Completion(text='{"action":"chat","reply":"hello"}', provider=self.name)


def test_completed_command_emits_reply_without_crashing(tmp_path):
    settings = Settings()
    settings.automation.allowed_dirs = [str(tmp_path)]
    db = MemoryDB(tmp_path / "memory.db")
    jarvis = Jarvis(settings)
    events = []
    jarvis.on_event(lambda event, data: events.append((event, data)))
    jarvis.boot({"db": db, "providers": [OfflineProvider()]})
    try:
        answer = jarvis.handle_text("help", spoken=False)
        assert "I can:" in answer
        assert any(event == "reply" and data["kind"] == "control" for event, data in events)
    finally:
        jarvis.shutdown()


def test_unapproved_model_tool_cannot_grant_itself(tmp_path):
    broker = PermissionBroker([str(tmp_path)])
    called = []
    registry = ToolRegistry(broker, confirm=lambda *args: False)
    registry.register(Tool("proof", "safe fake write", Capability.FS_WRITE, {}, lambda: called.append(1) or ToolResult.say("ran")))
    result = registry.execute("proof", {})
    assert not result.ok
    assert not called
    assert not broker.is_granted(Capability.FS_WRITE)


def test_dangerous_action_requires_confirmation_every_time():
    broker = PermissionBroker([])
    approvals = iter([True, False])
    called = []
    registry = ToolRegistry(broker, confirm=lambda *args: next(approvals))
    registry.register(Tool("proof", "fake power", Capability.POWER, {}, lambda: called.append(1) or ToolResult.say("ran")))
    assert registry.execute("proof", {}).ok
    assert not registry.execute("proof", {}).ok
    assert called == [1]


def test_forget_memory_must_use_permission_gate(tmp_path):
    settings = Settings()
    settings.automation.allowed_dirs = [str(tmp_path)]
    db = MemoryDB(tmp_path / "memory.db")
    db.add_turn("prior", "user", "keep this")
    jarvis = Jarvis(settings)
    jarvis.boot({"db": db, "providers": [OfflineProvider()]})
    jarvis.request_confirmation = lambda *args: False
    try:
        answer = jarvis.handle_text("forget my memory", spoken=False)
        assert "Cancelled" in answer
        assert db.search_turns("keep this")
    finally:
        jarvis.shutdown()